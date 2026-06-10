"""
LLM Router — выбор модели по сложности задачи.

Default model = Claude Haiku (быстрая, недорогая). Routing работает по
явному параметру `complexity` или auto-эвристикой по содержимому.

Tiers:
  simple   → Haiku  — chat-ответы, классификация, короткие ответы
  medium   → Sonnet — planning, декомпозиция, structured JSON
  complex  → Opus   — мульти-step reasoning, дорогие решения
  auto     → выбирается на основе msg/goal heuristic

Use:
    from services.llm_router import pick_model, call_llm

    model = pick_model("medium")              # → Sonnet slug
    model = pick_model("auto", text=user_msg) # → Haiku/Sonnet/Opus

    # Centralized OpenRouter call with retry + fallback:
    resp = await call_llm(
        [{"role": "user", "content": "Hi"}],
        complexity="medium",
        system="You are a helpful assistant.",
        max_tokens=400,
    )
    text = resp["choices"][0]["message"]["content"]

Никаких рантайм-зависимостей кроме config + httpx + tenacity.
Безопасно вызывать из любого skill / router.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings

log = logging.getLogger("hermes.llm")

Complexity = Literal["simple", "medium", "complex", "auto"]


def pick_model(
    complexity: Complexity = "auto",
    *,
    text: Optional[str] = None,
    override: Optional[str] = None,
) -> str:
    """
    Возвращает OpenRouter model slug.

    Если передан `override` — он используется как есть (для UI-выбора
    пользователем). Иначе — по правилам tier'а.
    """
    if override:
        return override

    if complexity == "simple":
        return settings.OPENROUTER_MODEL_HAIKU
    if complexity == "medium":
        return settings.OPENROUTER_MODEL_SONNET
    if complexity == "complex":
        return settings.OPENROUTER_MODEL_OPUS

    # auto — эвристика по тексту
    return _auto_pick(text or "")


def _auto_pick(text: str) -> str:
    """
    Эвристика выбора модели по содержимому. Дёшево по умолчанию,
    повышаемся только при явных сигналах сложности.

    Сигналы medium (→ Sonnet):
      - длина > 280 символов (≈ короткий абзац)
      - наличие явных глаголов планирования: «спланируй», «разложи», «декомпозируй»
      - перечисление целей / SKU / метрик с цифрами (>=2 числа)

    Сигналы complex (→ Opus):
      - длина > 1200 символов
      - наличие «обдумай», «проанализируй комплексно», «несколько вариантов»
      - множественные nested условия

    Иначе — Haiku.
    """
    if not text:
        return settings.OPENROUTER_MODEL_HAIKU

    lower = text.lower()
    length = len(text)

    # Complex signals
    complex_kw = (
        "обдумай",
        "комплексн",
        "несколько вариантов",
        "пошагов",
        "взвесь",
        "сравни всех",
        "развёрнут",
    )
    if length > 1200 or any(k in lower for k in complex_kw):
        return settings.OPENROUTER_MODEL_OPUS

    # Medium signals
    medium_kw = (
        "спланируй",
        "разложи",
        "декомпозируй",
        "построй план",
        "запусти агентов",
        "стратеги",
        "что мне делать",
        "как достичь",
    )
    number_count = sum(1 for c in text if c.isdigit())
    if (
        length > 280
        or any(k in lower for k in medium_kw)
        or number_count >= 6
    ):
        return settings.OPENROUTER_MODEL_SONNET

    return settings.OPENROUTER_MODEL_HAIKU


def _is_anthropic(model: str) -> bool:
    m = model.lower()
    return m.startswith("anthropic/") or "claude" in m


def _system_message(
    static: str,
    model: str,
    *,
    suffix: Optional[str] = None,
) -> dict[str, Any]:
    """Build system message; Anthropic static block gets ephemeral cache_control."""
    if not _is_anthropic(model):
        return {"role": "system", "content": static + (suffix or "")}

    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": static,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if suffix:
        blocks.append({"type": "text", "text": suffix})
    return {"role": "system", "content": blocks}


def cache_usage(resp: dict) -> dict[str, int]:
    """Extract prompt-cache metrics from an OpenRouter response."""
    usage = resp.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "cached_tokens": int(details.get("cached_tokens") or 0),
        "cache_write_tokens": int(details.get("cache_write_tokens") or 0),
    }


def model_label(slug: str) -> str:
    """Pretty label для UI / логов («Haiku» вместо «anthropic/claude-haiku-4-5»)."""
    if "haiku" in slug:
        return "Haiku"
    if "sonnet" in slug:
        return "Sonnet"
    if "opus" in slug:
        return "Opus"
    return slug.split("/")[-1]


# ───── Centralized LLM call (retry + fallback) ────────────────────


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    reraise=True,
)
async def _post_openrouter(model: str, messages: list, **kw) -> dict:
    """
    Single POST to OpenRouter chat/completions with retry on 5xx/429/timeout.

    `kw` is forwarded into the JSON body (max_tokens, temperature,
    response_format, tools, tool_choice, …). `timeout` is consumed
    here and not forwarded.
    """
    timeout = kw.pop("timeout", 60)
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "HTTP-Referer": getattr(settings, "OPENROUTER_REFERER", "https://mao.ai"),
        "X-Title": "MAO.ai",
        "Content-Type": "application/json",
    }
    body = {"model": model, "messages": messages, **kw}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=body,
        )
        if r.status_code >= 500 or r.status_code == 429:
            r.raise_for_status()
        if r.status_code >= 400:
            log.error("openrouter %s: %s", r.status_code, r.text[:300])
            raise httpx.HTTPStatusError(
                f"OpenRouter {r.status_code}", request=r.request, response=r
            )
        return r.json()


async def call_llm(
    messages: list,
    *,
    complexity: str = "medium",
    model: Optional[str] = None,
    system: Optional[str] = None,
    system_suffix: Optional[str] = None,
    **kw: Any,
) -> dict:
    """
    Centralized LLM call with retry + fallback. Returns OpenRouter response JSON.

    Args:
        messages: list of {role, content} dicts.
        complexity: "simple" | "medium" | "complex" | "auto" — picked via pick_model.
        model: explicit OpenRouter slug; overrides `complexity` when provided.
        system: static system prompt (Anthropic: cached with cache_control ephemeral).
        system_suffix: dynamic per-request system tail (never cached).
        **kw: forwarded to the request body (max_tokens, temperature,
              response_format, tools, tool_choice, timeout, …).

    On primary model failure, falls back to OPENROUTER_MODEL_FALLBACK
    (if set and distinct). Re-raises the last error if fallback also fails.
    """
    chosen = model or pick_model(complexity)  # type: ignore[arg-type]
    if system:
        messages = [
            _system_message(system, chosen, suffix=system_suffix),
            *messages,
        ]
    try:
        return await _post_openrouter(chosen, messages, **kw)
    except Exception as e:
        fb = getattr(settings, "OPENROUTER_MODEL_FALLBACK", None)
        if fb and fb != chosen:
            log.warning(
                "primary model %s failed (%s) — falling back to %s",
                chosen,
                e,
                fb,
            )
            return await _post_openrouter(fb, messages, **kw)
        raise
