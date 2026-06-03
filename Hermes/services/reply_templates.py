"""
Deterministic templates for empty-review fast-path.

When customer left no text (just stars), there's nothing for the LLM to
interpret — it would hallucinate. Use pre-vetted templates instead.

Distilled from Alisar v1 spec §10 + §17.
"""
from __future__ import annotations

import random
from typing import Literal

ProductClass = Literal["wearable", "appliance", "other"]


# 4-5★ + empty text → thank template (deterministic, by product class)
THANK_TEMPLATES: dict[ProductClass, dict[str, list[str]]] = {
    "wearable": {
        "named": [
            "Здравствуйте, {name}! Спасибо за такую приятную оценку — мы очень рады, "
            "что вы остались довольны покупкой. Желаем, чтобы вещь прижилась и "
            "радовала каждый раз, когда вы её достаёте. {signature}",

            "Здравствуйте, {name}! Благодарим за высокую оценку — для нас это знак, "
            "что мы движемся в правильном направлении. Пусть покупка станет источником "
            "радости и хорошего настроения. {signature}",

            "Здравствуйте, {name}! Спасибо за вашу оценку — нам приятно, что покупка "
            "пришлась по душе. Носите с удовольствием! {signature}",
        ],
        "anon": [
            "Здравствуйте! Спасибо за такую приятную оценку — мы очень рады, что вы "
            "остались довольны покупкой. Желаем, чтобы вещь прижилась и радовала "
            "каждый раз, когда вы её достаёте. {signature}",

            "Здравствуйте! Благодарим за высокую оценку — это знак, что мы движемся в "
            "правильном направлении. Пусть покупка станет источником радости. {signature}",

            "Здравствуйте! Спасибо за оценку. Носите с удовольствием! {signature}",
        ],
    },
    "appliance": {
        "named": [
            "Здравствуйте, {name}! Большое спасибо за высокую оценку — для нас это "
            "огромная радость. Желаем, чтобы покупка служила долго и приносила "
            "удовольствие в быту. {signature}",

            "Здравствуйте, {name}! Благодарим за такую приятную оценку — лучший знак, "
            "что покупка вас порадовала. Пусть служит долго и помогает в повседневных "
            "делах. {signature}",

            "Здравствуйте, {name}! Спасибо за оценку — нам приятно, что покупка "
            "пришлась по душе. Пользуйтесь с удовольствием! {signature}",
        ],
        "anon": [
            "Здравствуйте! Большое спасибо за высокую оценку. Желаем, чтобы покупка "
            "служила долго и приносила удовольствие в быту. {signature}",

            "Здравствуйте! Благодарим за оценку. Пусть служит долго и помогает в "
            "повседневных делах. {signature}",

            "Здравствуйте! Спасибо за оценку. Пользуйтесь с удовольствием! {signature}",
        ],
    },
    "other": {
        "named": [
            "Здравствуйте, {name}! Спасибо за высокую оценку — рады, что покупка "
            "понравилась. Будем стараться и дальше радовать качеством. {signature}",

            "Здравствуйте, {name}! Благодарим за оценку. Очень приятно, что вы "
            "остались довольны. {signature}",
        ],
        "anon": [
            "Здравствуйте! Спасибо за высокую оценку — рады, что покупка понравилась. "
            "Будем стараться и дальше радовать качеством. {signature}",

            "Здравствуйте! Благодарим за оценку. Очень приятно, что вы остались "
            "довольны. {signature}",
        ],
    },
}


# 1-3★ + empty text → ask for details (deterministic)
EMPTY_LOW_TEMPLATES = {
    "named": [
        "Здравствуйте, {name}! Спасибо, что нашли время оценить наш товар. Жаль, "
        "что покупка оставила не самое приятное впечатление. К сожалению, вы не "
        "оставили комментарий, поэтому мы не можем понять причину разочарования. "
        "Возможно, не подошёл размер, дизайн, качество или возникли сложности с "
        "доставкой? Напишите нам в чат с продавцом — для нас это бесценная "
        "обратная связь, которая помогает становиться лучше. {signature}",

        "Здравствуйте, {name}! Спасибо за оценку. Жаль, что покупка не оправдала "
        "ожиданий. Без вашего комментария трудно понять, что именно пошло не так. "
        "Напишите нам в чат с продавцом — разберёмся вместе. {signature}",
    ],
    "anon": [
        "Здравствуйте! Спасибо, что нашли время оценить наш товар. Жаль, что "
        "покупка оставила не самое приятное впечатление. К сожалению, вы не "
        "оставили комментарий — напишите нам в чат с продавцом, разберёмся "
        "вместе. {signature}",

        "Здравствуйте! Спасибо за оценку. Жаль, что покупка не оправдала ожиданий. "
        "Напишите нам в чат с продавцом — для нас это ценная обратная связь. "
        "{signature}",
    ],
}


def is_canonical_name(name: str | None) -> bool:
    """Detect if name is safe to use in greeting.

    Skip: empty, ALL_CAPS, contains digits, single letter, common screen names.
    """
    if not name or not name.strip():
        return False
    n = name.strip()
    if len(n) < 2:
        return False
    if any(ch.isdigit() for ch in n):
        return False
    if n.upper() == n and len(n) > 2:  # likely ALL CAPS / nickname
        return False
    # Heuristic: real Russian names start with Cyrillic capital letter
    first = n[0]
    if not (first.isalpha() and first.isupper()):
        return False
    return True


def render_thank_template(
    product_class: ProductClass,
    name: str | None,
    signature: str,
) -> str:
    """Render an empty 4-5★ thank reply. Deterministic per product class."""
    name_ok = is_canonical_name(name)
    bucket = "named" if name_ok else "anon"
    templates = THANK_TEMPLATES[product_class][bucket]
    tmpl = random.choice(templates)
    return tmpl.format(name=name or "", signature=signature)


def render_empty_low_template(
    name: str | None,
    signature: str,
) -> str:
    """Render an empty 1-3★ ask-for-details reply."""
    name_ok = is_canonical_name(name)
    bucket = "named" if name_ok else "anon"
    templates = EMPTY_LOW_TEMPLATES[bucket]
    tmpl = random.choice(templates)
    return tmpl.format(name=name or "", signature=signature)
