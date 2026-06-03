"""
Prompt Builder — turns a structured Visual brief into a Higgsfield-style
Nano Banana Pro prompt with mandatory Geometry Lock + Identity Lock blocks.

The output follows the AI Design дep instructions verbatim:

    Model → Goal → References → Geometry Lock → Identity Lock →
    Scene → Shot → Text → Exclude → Acceptance Criteria → Format

LLM does the creative phrasing for Goal/Scene/Shot/Text/Exclude; the
deterministic blocks (Geometry/Identity/Acceptance/Format) come from the
brief + avatar verbatim, never paraphrased — that's the whole point of
the "lock" concept.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("hermes.prompt_builder")


# Slide types — mirrors AI Design docx, "Слайды 1-9"
SLIDE_GOALS: dict[str, str] = {
    "hero":          "Главный кадр — захват внимания в выдаче, максимальный CTR. Товар читается за 0.5 сек в превью.",
    "lifestyle":     "Lifestyle-сцена. Покажи товар в реальном контексте использования. Эмоция + ценность.",
    "infographic":   "Инфографика — выгода/характеристика крупным акцентом. Текст ≤7 слов, контрастный шрифт.",
    "warning":       "Предупреждающий слайд — снятие возражения. Текст и иконка проблема→решение.",
    "comparison":    "Сравнение 'до/после' или 'мы/они' — продемонстрировать превосходство.",
    "size_guide":    "Размерная сетка — таблица или визуальная шкала с размерами.",
    "video_main":    "Видео главное — виральное и цепляющее, отвечает на основные вопросы покупателя.",
}


@dataclass
class BriefInput:
    product_name: str
    product_description: Optional[str]
    key_attributes: list[str]
    geometry_lock: str
    color_variations: list[dict] = field(default_factory=list)
    competitor_summary: str = ""


@dataclass
class AvatarInput:
    name: str
    identity_lock: str
    gender: Optional[str] = None
    age_range: Optional[str] = None
    style_keywords: list[str] = field(default_factory=list)


@dataclass
class MoodboardInput:
    name: str
    style_description: str


@dataclass
class PromptResult:
    prompt: str
    negative_prompt: str
    acceptance_criteria: list[str]


# ───── Public API ───────────────────────────────────────────────────


async def build_image_prompt(
    *,
    brief: BriefInput,
    slide_type: str,
    slide_index: int,
    avatar: Optional[AvatarInput] = None,
    moodboard: Optional[MoodboardInput] = None,
    extra_intent: Optional[str] = None,
    aspect: str = "3:4",
    resolution: str = "1080×1440",
    watermark_position: str = "bottom-left",
) -> PromptResult:
    """
    Assemble the full prompt for one image slide.

    The deterministic blocks (Geometry/Identity/Acceptance/Format) are
    composed locally; the creative blocks (Scene/Shot/Text/Exclude) are
    drafted by the LLM and then injected into the canonical template.
    """
    goal = SLIDE_GOALS.get(slide_type, "Generic product slide.")
    if extra_intent:
        goal = f"{goal} {extra_intent}"

    creative = await _llm_draft_creative_blocks(
        brief=brief,
        slide_type=slide_type,
        goal=goal,
        avatar=avatar,
        moodboard=moodboard,
    )

    # Compose final prompt in the exact docx-specified order
    sections: list[str] = []
    sections.append(f"GOAL: {goal}")

    if brief.competitor_summary:
        sections.append(f"REFERENCES: {brief.competitor_summary}")

    sections.append(f"GEOMETRY LOCK:\n{brief.geometry_lock}")

    if brief.color_variations:
        cv_lines = [
            f"  - {cv.get('name', '?')} → {cv.get('override_colors_block', cv.get('hex', '?'))}"
            for cv in brief.color_variations[:4]
        ]
        sections.append("COLORS:\n" + "\n".join(cv_lines))

    if avatar:
        sections.append(f"IDENTITY LOCK ({avatar.name}):\n{avatar.identity_lock}")

    if moodboard:
        sections.append(f"MOODBOARD ({moodboard.name}): {moodboard.style_description}")

    sections.append(f"SCENE: {creative.scene}")
    sections.append(f"SHOT: {creative.shot}")

    if creative.text_on_slide:
        sections.append(f"TEXT ON SLIDE: {creative.text_on_slide}")

    sections.append(f"EXCLUDE: {creative.exclude}")

    accept = _default_acceptance(slide_type, brief, avatar, watermark_position)
    sections.append("ACCEPTANCE CRITERIA:\n" + "\n".join(f"  • {c}" for c in accept))

    sections.append(
        f"FORMAT: {aspect} aspect, {resolution} px, "
        f"watermark {watermark_position}, no QR/contacts/competitor logos."
    )

    prompt = "\n\n".join(sections)
    negative = creative.exclude or (
        "blurry, distorted product geometry, watermark on top, text in English, "
        "QR codes, contact details, competitor logos, low resolution"
    )
    return PromptResult(
        prompt=prompt,
        negative_prompt=negative,
        acceptance_criteria=accept,
    )


# ───── LLM draft of creative blocks ─────────────────────────────────


@dataclass
class CreativeDraft:
    scene: str
    shot: str
    text_on_slide: str
    exclude: str


async def _llm_draft_creative_blocks(
    *,
    brief: BriefInput,
    slide_type: str,
    goal: str,
    avatar: Optional[AvatarInput],
    moodboard: Optional[MoodboardInput],
) -> CreativeDraft:
    """Ask Claude (via OpenRouter) for scene/shot/text/exclude."""
    avatar_note = (
        f"Avatar: {avatar.name}, gender={avatar.gender}, age={avatar.age_range}, "
        f"style={', '.join(avatar.style_keywords)}"
        if avatar
        else "No avatar — pure product shot."
    )
    moodboard_note = (
        f"Moodboard: {moodboard.name} — {moodboard.style_description}"
        if moodboard
        else "No moodboard."
    )

    system = (
        "Ты — арт-директор маркетплейса. По брифу товара придумай creative blocks "
        "для одного слайда карточки Wildberries. Отвечай СТРОГО в JSON-формате: "
        '{"scene": str, "shot": str, "text_on_slide": str, "exclude": str}. '
        "Без markdown, без пояснений вокруг JSON."
    )
    user = (
        f"Тип слайда: {slide_type}\n"
        f"Цель слайда: {goal}\n\n"
        f"Товар: {brief.product_name}\n"
        f"Описание: {brief.product_description or '—'}\n"
        f"Ключевые атрибуты: {', '.join(brief.key_attributes) or '—'}\n\n"
        f"{avatar_note}\n{moodboard_note}\n\n"
        f"Конкуренты: {brief.competitor_summary or 'не задано'}\n\n"
        "Сформулируй:\n"
        "- scene: фон + освещение + атмосфера + боке (1-2 предложения)\n"
        "- shot: тип кадра (chest-up / full body / close-up / overhead) + угол + framing\n"
        "- text_on_slide: что написать (≤7 слов) — или пустая строка если без текста\n"
        "- exclude: 4-6 элементов, которых не должно быть в кадре"
    )

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://mao.ai",
        "X-Title": "MAO.ai Visual Prompt Builder",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.OPENROUTER_PROMPT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 600,
        "temperature": 0.6,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        import json
        # Strip code-fence if model added it despite instruction
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        parsed = json.loads(text)
        return CreativeDraft(
            scene=str(parsed.get("scene", "")),
            shot=str(parsed.get("shot", "")),
            text_on_slide=str(parsed.get("text_on_slide", "")),
            exclude=str(parsed.get("exclude", "")),
        )
    except Exception as e:
        logger.warning(f"creative-block LLM draft failed: {e}; using fallback")
        return CreativeDraft(
            scene="Neutral studio backdrop, soft natural lighting, shallow depth of field.",
            shot="Three-quarter view, eye-level, centered framing.",
            text_on_slide="",
            exclude="QR codes, contact information, competitor logos, distorted geometry, blurry text",
        )


def _default_acceptance(
    slide_type: str,
    brief: BriefInput,
    avatar: Optional[AvatarInput],
    watermark_position: str,
) -> list[str]:
    base = [
        f"Product geometry matches GEOMETRY LOCK for «{brief.product_name}» exactly",
        f"Aspect 3:4, 1080×1440 px, watermark at {watermark_position}",
        "No QR codes, contacts, competitor logos, or watermarks of other brands",
        "Lighting is realistic — no plastic/over-rendered look",
    ]
    if avatar:
        base.append(f"Identity matches IDENTITY LOCK for «{avatar.name}» — same face across all slides")
    if slide_type == "infographic":
        base.append("Text on slide is readable in 200×260 px preview (mobile WB feed)")
    return base
