"""
Product class detection — wearable / appliance / other.

Determines the right "final wish" in a customer-facing reply:
  wearable  → "Носите с удовольствием!"
  appliance → "Пользуйтесь с удовольствием!" / "Пусть служит долго!"
  other     → neutral "Рады, что покупка понравилась"

Without this, the agent says "wear with pleasure" about a vacuum cleaner.

Strategy distilled from Alisar v1 spec (2026-05-21). Keywords are root-only
(without endings) so they catch all morphological forms.

Use:
    from services.product_class import detect_product_class
    cls = detect_product_class("Аэрогриль GURTER GR-AF-001 — 6 литров")
    # → "appliance"
"""
from __future__ import annotations

import re
from typing import Literal

ProductClass = Literal["wearable", "appliance", "other"]

# Roots (no endings) — case-insensitive substring match.
# Order matters: appliance checked first (more specific keywords first).
WEARABLE_KEYWORDS = (
    "одежд", "платье", "рубашк", "блуз", "юбк", "брюк", "джинс", "шорт",
    "кофт", "свитер", "толстовк", "худи", "куртк", "пиджак", "плащ",
    "пальто", "шуб", "очк", "оправ", "линз", "перчатк", "варежк", "шапк",
    "шарф", "бандан", "берет", "носк", "колгот", "чулк", "гольф",
    "кроссовк", "ботинк", "сапог", "туфл", "кед", "сандал", "тапочк",
    "пляжн", "босонож", "мокасин", "купальник", "плавк", "ласт", "ремень",
    "ремн", "сумк", "рюкзак", "кошел", "портмоне", "клатч", "барсет",
    "бельё", "белье", "трус", "лифчик", "бюстг", "бра ", "комбинац",
    "пиж", "халат", "ночнушк", "сорочк", "костюм", "комбинезон",
    "спортивн", "спорткостюм", "галстук", "бабоч", "запонк", "платок",
    "бижут", "кольц", "серьг", "цеп", "браслет", "часы", "брелок",
    "аксессуар",
)

APPLIANCE_KEYWORDS = (
    "вентилятор", "чайник", "кофевар", "кофеварк", "кофемашин", "аэрогрил",
    "мультиварк", "блендер", "миксер", "тостер", "утюг", "отпариватель",
    "пылесос", "увлажнитель", "очиститель", "ионизатор", "обогреват",
    "обогрев", "вафельниц", "бутербродниц", "сэндвичниц", "гриль",
    "духовк", "духов", "плит", "свч", "микроволнов", "холодильник",
    "морозильник", "посудомоечн", "стиральн", "сушильн", "машинк", "фен",
    "плойк", "выпрямит", "стайлер", "бритв", "электробритв", "эпилятор",
    "триммер", "ирригатор", "зубн", "ингалятор", "массажёр", "массажер",
    "тренажёр", "тренажер", "весы", "термометр", "тонометр",
    "пульсоксиметр", "глюкометр", "робот-пылесос", "электрочайник",
)

# Pre-compile for speed (regex with alternation)
_WEARABLE_RE = re.compile(
    "|".join(re.escape(k) for k in WEARABLE_KEYWORDS), re.IGNORECASE
)
_APPLIANCE_RE = re.compile(
    "|".join(re.escape(k) for k in APPLIANCE_KEYWORDS), re.IGNORECASE
)


def detect_product_class(product_name: str | None) -> ProductClass:
    """Detect product class from name. Defaults to 'other' if nothing matches."""
    if not product_name:
        return "other"
    # Check appliance first — more specific keywords, less collision risk
    if _APPLIANCE_RE.search(product_name):
        return "appliance"
    if _WEARABLE_RE.search(product_name):
        return "wearable"
    return "other"


def final_wish_for(product_class: ProductClass, purchase_status: str | None = None) -> str:
    """Get an appropriate closing wish for the given product class + purchase status.

    purchase_status: "buyout" | "rejected" | "returned" | None
    For rejected/returned — customer doesn't have the product, so no "wear with
    pleasure" type wishes. Use a neutral "будем рады видеть вас снова".
    """
    if purchase_status in ("rejected", "returned"):
        return "Будем рады видеть вас снова."

    if product_class == "wearable":
        return "Носите с удовольствием!"
    if product_class == "appliance":
        return "Пользуйтесь с удовольствием!"
    return "Рады, что покупка понравилась."
