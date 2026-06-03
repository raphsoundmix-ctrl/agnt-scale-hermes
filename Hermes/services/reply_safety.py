"""
Safety check for LLM-generated customer-facing replies.

Blocks any reply containing banned words/phrases that would damage brand or
break WB rules. Banned list comes from tenant_memory.brand_voice.banned_words
(per-cabinet override) merged with the platform-wide defaults below.

Distilled from Alisar v1 spec §14.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# Платформенные defaults — банятся для всех тенантов.
# Tenant может добавить свои через brand_voice.banned_words (merge, не override).
PLATFORM_BANNED_PHRASES: tuple[str, ...] = (
    # ── Штампы (рекламная плесень) ──
    "индивидуальный подход",
    "команда профессионалов",
    "гибкая система скидок",
    "гарантия качества",
    "динамично развивающаяся",
    "бесконечно приятно",
    "искренне рады",
    "душой вкладываем",
    "лучшая награда",
    "ваше мнение очень важно",
    "отзывы говорят сами за себя",
    "высокое качество",
    "работаем над улучшением",
    "следим за качеством",

    # ── Канцеляризмы ──
    "осуществляем",
    "производим",
    "в кратчайшие сроки",
    "обратитесь по любым вопросам",
    "выражаем благодарность",
    "приносим извинения",
    "на сегодняшний день",
    "в связи с возникновением",
    "по причине отсутствия",

    # ── Запреты контента ──
    "100%",
    "лучший на рынке",
    "нет аналогов",
    "наша вина",
    "виноваты мы",
    "роспотребнадзор",
    "прокуратур",
    "обращайтесь в суд",

    # ── Жаргон / фамильярность ──
    "братиш",
    "детка",
    "солнышко",
    "котик",
)


@dataclass
class SafetyResult:
    """Result of a safety check on a generated reply."""
    passed: bool
    violations: list[str]  # list of matched banned phrases

    @property
    def violation_summary(self) -> str:
        if not self.violations:
            return ""
        return "Найдены запрещённые фразы: " + ", ".join(
            f'"{v}"' for v in self.violations
        )


def check_reply_safety(
    reply: str,
    extra_banned_words: Iterable[str] | None = None,
) -> SafetyResult:
    """Check if reply contains any banned phrases.

    Args:
        reply: generated reply text
        extra_banned_words: per-cabinet additions from brand_voice.banned_words

    Returns:
        SafetyResult.passed=True if no violations, False otherwise.
    """
    if not reply:
        return SafetyResult(passed=True, violations=[])

    reply_lower = reply.lower()
    all_banned = list(PLATFORM_BANNED_PHRASES)
    if extra_banned_words:
        all_banned.extend(str(w).lower() for w in extra_banned_words)

    violations: list[str] = []
    for phrase in all_banned:
        if phrase.lower() in reply_lower:
            violations.append(phrase)

    # Special structural checks (regex-style)
    if "!!" in reply:
        violations.append("повтор «!!»")
    # CAPS — 3+ uppercase Cyrillic letters in a row
    import re as _re
    if _re.search(r"[А-ЯЁ]{3,}", reply):
        violations.append("КАПС (3+ прописных подряд)")
    # Phone / email / URL leaks
    if _re.search(r"\b(?:\+?\d[\d\-\s]{7,})\b", reply):
        violations.append("номер телефона")
    if _re.search(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", reply, _re.IGNORECASE):
        violations.append("email адрес")
    if _re.search(r"https?://", reply, _re.IGNORECASE):
        violations.append("URL")

    return SafetyResult(passed=len(violations) == 0, violations=violations)
