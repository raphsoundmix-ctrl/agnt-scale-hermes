"""
Review classifier — pre-generation analysis pass.

Runs cheap (Haiku) model to classify the review BEFORE expensive generation:
  - category drives autonomy decisions (toxic/legal skip generation entirely)
  - severity drives model selection (high → Sonnet/Opus, low → Haiku)
  - is_off_product flags reviews where operator should complain to WB
  - is_escalate marks force-escalate cases (threats, abuse)

Distilled from Alisar v1 spec §4.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Literal

from services.llm_router import call_llm, pick_model

logger = logging.getLogger("hermes.classifier")

Category = Literal[
    "thank", "question", "complaint", "defect",
    "logistics", "toxic", "legal"
]
Severity = Literal["low", "medium", "high"]


@dataclass
class ReviewClassification:
    category: Category
    severity: Severity
    topics: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    is_escalate: bool = False
    is_off_product: bool = False
    reason_short: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


CLASSIFY_SYSTEM_PROMPT = """Ты — классификатор отзывов покупателей на Wildberries.
Отвечай ТОЛЬКО валидным JSON-объектом, без преамбулы.

Категории:
  thank      — благодарственный отзыв
  question   — вопрос покупателя
  complaint  — жалоба на товар (качество, размер, дизайн)
  defect     — конкретная претензия к браку / поломке
  logistics  — про доставку / упаковку
  toxic      — мат, оскорбления, агрессия
  legal      — упоминание суда, Роспотребнадзора, требование компенсации с угрозами

Severity: low | medium | high

Topics (короткие теги, выбирай 0-3):
  размер, материал, качество, доставка, упаковка, цвет, запах, цена,
  реклама_не_соответствует, сборка, инструкция, не_тот_товар, опоздание

sentiment_score: число от -1.0 до +1.0

is_escalate=true ТОЛЬКО при:
  - угрозы судом / Роспотребнадзором / прокуратурой
  - прямые оскорбления личности оператора или бренда (мат)
  - требование вернуть деньги с угрозами публичной дискредитации
Жалобы на качество / брак / вопросы — is_escalate=false.

is_off_product=true когда негатив (1-3★) НЕ относится к товару:
  - долгая доставка / опоздание
  - мятая / повреждённая упаковка (но не товар)
  - "прислали не то / перепутали"
  - проблемы ПВЗ / курьера / WB
Смешанная жалоба (товар + доставка) → is_off_product=false.
Оценка 4-5★ → is_off_product=false.

Формат ответа:
{"category":"...","severity":"...","topics":[...],"sentiment_score":0.0,
 "is_escalate":false,"is_off_product":false,"reason_short":"..."}"""


async def classify_review(
    review_text: str,
    rating: int,
    product_name: str = "",
) -> ReviewClassification:
    """Classify a single review using cheap LLM. Returns structured result.

    On any failure (parse error, LLM error) — returns a safe default
    (complaint/medium with empty topics) so generation can proceed.
    """
    # Empty review fast-path — no LLM needed.
    if not review_text or not review_text.strip():
        return _fallback_for_empty(rating)

    text = review_text[:500]
    name = product_name[:200]

    user_msg = (
        f"<untrusted_customer_review>\n"
        f"Товар: {name}\n"
        f"Оценка: {rating}/5\n"
        f"Отзыв: {text}\n"
        f"</untrusted_customer_review>\n\n"
        f"Классифицируй. Только JSON."
    )

    try:
        resp = await call_llm(
            [
                {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=pick_model("simple"),  # Haiku — дёшево
            max_tokens=300,
            temperature=0.0,             # детерминированно
            timeout=15,
        )
        from services.llm_json import parse_llm_json
        raw = resp["choices"][0]["message"]["content"]
        data = parse_llm_json(raw)
        return ReviewClassification(
            category=data.get("category", "complaint"),
            severity=data.get("severity", "medium"),
            topics=list(data.get("topics", []))[:5],
            sentiment_score=float(data.get("sentiment_score", 0.0)),
            is_escalate=bool(data.get("is_escalate", False)),
            is_off_product=bool(data.get("is_off_product", False)),
            reason_short=str(data.get("reason_short", ""))[:200],
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"classify_review parse failed: {e} | raw={raw!r}")
        return _fallback_for_rating(rating)
    except Exception as e:
        logger.exception(f"classify_review LLM failed: {e}")
        return _fallback_for_rating(rating)


def _fallback_for_empty(rating: int) -> ReviewClassification:
    """Default classification when text is empty (no LLM call)."""
    if rating >= 4:
        return ReviewClassification(
            category="thank",
            severity="low",
            sentiment_score=0.8,
            reason_short="пустой отзыв с 4-5★",
        )
    return ReviewClassification(
        category="complaint",
        severity="low",
        sentiment_score=-0.3,
        reason_short="пустой отзыв с 1-3★",
    )


def _fallback_for_rating(rating: int) -> ReviewClassification:
    """Safe default when classifier fails — based on rating only."""
    if rating >= 5:
        return ReviewClassification(category="thank", severity="low", sentiment_score=0.7)
    if rating == 4:
        return ReviewClassification(category="thank", severity="low", sentiment_score=0.4)
    if rating == 3:
        return ReviewClassification(category="complaint", severity="medium", sentiment_score=-0.2)
    if rating == 2:
        return ReviewClassification(category="complaint", severity="medium", sentiment_score=-0.6)
    return ReviewClassification(category="complaint", severity="high", sentiment_score=-0.9)


# ── Helpers used by skill code ─────────────────────────────────────

def should_skip_generation(cls: ReviewClassification) -> bool:
    """toxic/legal → don't publish anything, escalate to human."""
    return cls.category in ("toxic", "legal")


def model_tier_for(cls: ReviewClassification) -> str:
    """Pick LLM tier based on category + severity. Maps to pick_model() arg."""
    if cls.category in ("legal", "toxic"):
        return "complex"  # most accurate, even though we won't publish
    if cls.category in ("complaint", "defect") and cls.severity == "high":
        return "complex"
    if cls.category in ("complaint", "defect"):
        return "medium"
    if cls.category == "question" and cls.severity == "high":
        return "medium"
    # thank / logistics / question(low|medium) → cheap model is fine
    return "simple"
