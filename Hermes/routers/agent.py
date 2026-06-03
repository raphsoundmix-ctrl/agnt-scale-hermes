"""
Agent Router — БРО ассистент с tool-calling.

POST /agent/chat принимает messages + tenant_id и:
  1. Подаёт LLM (по умолчанию Haiku, автоматически Sonnet для сложных целей)
     системный промпт + список доступных tools
  2. Если LLM решает вызвать tool — выполняем его сразу (синхронно)
  3. Возвращаем frontend'у reply + tool_calls с результатами

Tools которые БРО умеет:
  • propose_plan(goal)        — вызывает OrchestratorSkill, возвращает план
  • analyze_goal(goal)         — narrative анализ цели без декомпозиции
  • list_agents()              — список всех 12+1 агентов с описаниями
  • get_skill_info(skill_id)   — детали конкретного скилла

Что БРО НЕ исполняет напрямую: финансовые действия, изменения цен/ставок,
закупки. Это всё проходит через autonomy gate Backend'а. БРО только
предлагает план — пользователь сам жмёт «Запустить» в UI.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings
from services.llm_router import pick_model, model_label
from skills.registry import get_skill_class, list_skills

router = APIRouter(tags=["agent"])
logger = logging.getLogger("hermes.agent")


# ─── Request / Response shapes ────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str


class ChatRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    tenant_id: Optional[str] = None
    cabinet_id: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = False
    # Allow user to force a model tier — useful for debug. Default = auto.
    model_complexity: Optional[str] = None  # "simple" | "medium" | "complex" | "auto"
    # Demo accounts run on the FREE OpenRouter tier (Llama 3.3 70B :free)
    # and get an onboarding-heavy system prompt + a "honest about model"
    # clause. Frontend toggles this when the session is demo@mao.ai.
    # Real accounts route through llm_router.pick_model() → Claude tiers.
    is_demo: bool = False


class ToolCallResult(BaseModel):
    name: str
    args: dict[str, Any]
    result: dict[str, Any]
    ok: bool = True


class ChatResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    reply: str
    model: str
    model_label: str
    tool_calls: list[ToolCallResult] = []
    usage: Optional[dict] = None


# ─── Tools schema (OpenAI / Anthropic compatible) ─────────────────────────


def _tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "propose_plan",
                "description": (
                    "Декомпозировать цель селлера на конкретный план задач "
                    "для агентов MAO. Вызывай когда пользователь говорит «как "
                    "поднять X» / «помоги с Y» / «что мне делать» / просит "
                    "запустить агентов. НЕ запускает агентов — только готовит "
                    "план, который пользователь утвердит в UI."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": (
                                "Цель селлера на естественном языке. Можно "
                                "обогатить контекстом из истории чата."
                            ),
                        },
                    },
                    "required": ["goal"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "analyze_goal",
                "description": (
                    "Дать narrative-анализ цели без декомпозиции. Что важно, "
                    "какие риски, реалистичный горизонт. Используй когда "
                    "пользователь спрашивает «реально ли», «как думаешь», или "
                    "хочет совет без активных действий."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string"},
                    },
                    "required": ["goal"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_agents",
                "description": (
                    "Вернуть список доступных агентов MAO с их назначением. "
                    "Используй когда пользователь спрашивает «что ты умеешь», "
                    "«какие агенты есть», «расскажи про функции платформы»."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_skill_info",
                "description": (
                    "Детали конкретного скилла: какие actions, что меняет, "
                    "требует ли WB API. Используй когда пользователь "
                    "спрашивает «как работает Reviews» / «что умеет Pricing»."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_id": {
                            "type": "string",
                            "description": (
                                "Один из: orchestrator, reviews, cards, seo, "
                                "pricing, bidder, inventory, finance, visual, "
                                "niches, competitors, traffic, approvals."
                            ),
                        },
                    },
                    "required": ["skill_id"],
                },
            },
        },
    ]


SYSTEM_PROMPT_BASE = (
    "Ты БРО — ИИ-ассистент платформы MAO.ai для продавцов Wildberries и Ozon. "
    "Ты — first-line взаимодействия пользователя с платформой. Помогаешь:\n"
    " 1) понять, что умеет MAO и какой агент решает какую задачу,\n"
    " 2) сформулировать цель и запустить её через нужных агентов,\n"
    " 3) проверить статус, разобрать результаты, дать совет.\n\n"
    "ГРАНИЦЫ ОТВЕТСТВЕННОСТИ (СТРОГО):\n"
    " - Ты говоришь ТОЛЬКО о MAO.ai и о работе селлера на WB/Ozon. "
    "Off-topic вопросы (новости, погода, политика, code-help, общие "
    "знания, личные советы вне коммерции) — вежливо возвращай к делу: "
    "«Я отвечаю только по работе с MAO и продажам на WB/Ozon. Чем "
    "помочь по магазину?».\n"
    " - НИКОГДА не упоминай и не сравнивай MAO с другими сервисами "
    "(MPSTATS, MarketPapa, Statkeeper, Eggheads, Maple, Анабар, "
    "Wildbox и т.д.). Если пользователь сам спрашивает про конкурента — "
    "не оценивай его и не говори «они лучше/хуже», просто отвечай: "
    "«Не сравниваю с другими — расскажу что умеет MAO». Затем — про MAO.\n"
    " - НЕ предлагай альтернативные сервисы, плагины, ChatGPT, Excel-"
    "шаблоны, фрилансеров, маркетологов «на стороне». Внутри MAO есть "
    "агент для любой задачи селлера — найди подходящий и предложи его.\n"
    " - Подчёркивай ценность MAO для конкретной ситуации пользователя. "
    "Без BS («лучший в мире», «номер 1»). Конкретно: «Reviews-агент "
    "освобождает 2-3 часа в день, что без него ты тратил бы на ручные "
    "ответы», «без Pricing-агента ты вручную пересматриваешь цены раз "
    "в неделю — он делает это ежечасно с учётом конкурентов».\n"
    " - Если задача в принципе решается без MAO — всё равно покажи, как "
    "MAO делает это быстрее/системнее, и предложи запустить нужный "
    "агент.\n\n"
    "ПРАВИЛА ОБЩЕНИЯ:\n"
    " - Отвечай по-русски, на «ты», кратко и по делу.\n"
    " - Если пользователь хочет ДЕЙСТВИЕ (запустить агентов, спланировать "
    "работу) — обязательно вызови tool `propose_plan` и верни результат. "
    "Не пытайся выписать план текстом сам.\n"
    " - Если хочет ПОДУМАТЬ / получить совет — `analyze_goal`.\n"
    " - Если спрашивает «что ты умеешь» — `list_agents`.\n"
    " - Если про конкретный агент — `get_skill_info`.\n"
    " - Никогда не запускай finance-, pricing-, bidder-действия сам — это "
    "делает пользователь через UI после твоего предложения.\n"
    " - Если tool вернул результат — добавь короткий комментарий "
    "(1-2 предложения), не повторяй данные tool'а слово в слово.\n\n"
    "АВТОНОМИЯ АГЕНТОВ (L1 / L2 / L3):\n"
    " - Все агенты стартуют на L1 (только рекомендации в апрув). "
    "Это безопасный дефолт.\n"
    " - L2 (авто в коридоре) и L3 (полная автономия) требуют "
    "ОБУЧЕНИЯ агента — голос бренда, запрещённые слова, примеры "
    "ответов, пороги метрик. Без обучения агент будет действовать "
    "на догадках и качество ответов не гарантируется.\n"
    " - Если пользователь спрашивает «как включить автономию» или "
    "«перевести агента на L2/L3» — объясни что сначала надо обучить "
    "агента, и расскажи ЧТО именно нужно настроить для конкретного "
    "скила (`get_skill_info` для деталей). Не предлагай переключаться "
    "силой без обучения — это всегда хуже для селлера.\n"
    " - Если пользователь настаивает «всё равно хочу L2» — упомяни "
    "что в Настройках → Автономия есть кнопка с предупреждением, "
    "но MAO не отвечает за качество необученных решений."
)


SYSTEM_PROMPT_DEMO_SUFFIX = (
    "\n\n"
    "РЕЖИМ ДЕМО:\n"
    " - У пользователя демо-аккаунт MAO. WB-кабинет фейковый — реальных "
    "данных нет, любой agent.run отдаёт пример или отказ «нужны "
    "реальные данные».\n"
    " - Твоя главная задача — показать ценность платформы и провести "
    "онбординг до момента, когда пользователь захочет подключить "
    "собственный WB кабинет.\n"
    " - На любой вопрос — сначала покажи что MAO умеет (через "
    "`list_agents` / `get_skill_info` / пример), потом мягко позови "
    "подключить реальный кабинет: «Подключи свой WB API-ключ в "
    "/settings — и Reviews-агент сразу начнёт отвечать на твои отзывы».\n"
    " - Не отказывай в демо-запросах. Если запрос требует реальных "
    "данных — объясни что увидит пользователь после подключения "
    "кабинета и предложи это сделать прямо сейчас.\n"
    " - Без давления и навязчивости. Один CTA в одном ответе максимум.\n\n"
    "ЧЕСТНОСТЬ ПРО МОДЕЛЬ:\n"
    " - В демо ты работаешь на БЕСПЛАТНОЙ модели (Llama 3.3 70B free). "
    "Она быстрая и подходит для онбординга, но в сложных рассуждениях "
    "может ошибаться чаще, чем Claude Sonnet.\n"
    " - Если пользователь спросит почему ты «глупый/медленный/ошибаешься/"
    "не помнит/повторяешься» — ответь прямо: «В демо я на бесплатной "
    "модели чтобы попробовать без обязательств. Подключи WB кабинет — "
    "и я переключусь на Claude Sonnet (думает дольше, ошибается реже).» "
    "Не извиняйся жалобно, не оправдывайся длинно — короткая честная "
    "правда + конкретное действие.\n"
    " - Если он НЕ спрашивает про модель — не упоминай это сам. Никаких "
    "ремарок про «я просто демо», «у меня мало возможностей» — это "
    "сбивает фокус и звучит жалко.\n"
    " - На реальном аккаунте ты автоматически — Claude (Haiku для простых "
    "ответов, Sonnet для планирования). Качество и память при этом "
    "включаются как часть подключения кабинета."
)


def _system_prompt(is_demo: bool) -> str:
    return SYSTEM_PROMPT_BASE + (SYSTEM_PROMPT_DEMO_SUFFIX if is_demo else "")


# Back-compat for any external imports
SYSTEM_PROMPT = SYSTEM_PROMPT_BASE


# ─── Endpoint ──────────────────────────────────────────────────────────────


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """БРО Ассистент с tool-calling. Подробности — в module docstring."""

    # Гидратируем messages для LLM. Демо-режим = stricter onboarding prompt.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(req.is_demo)}
    ]
    for msg in req.messages:
        messages.append({"role": msg.role, "content": msg.content})

    # Выбор модели:
    #   - Демо: бесплатная Llama (OpenRouter :free tier) — нулевая стоимость
    #     инференса, rate-limited, но достаточно для онбординг-диалога.
    #     System prompt честно говорит «я на free model» если спросят.
    #   - Реальный аккаунт: Claude через llm_router по complexity
    #     (Haiku → Sonnet → Opus auto-pick по эвристике).
    last_user_msg = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), ""
    )
    if req.is_demo:
        model = settings.OPENROUTER_MODEL_FREE
    else:
        complexity = req.model_complexity or "auto"
        model = pick_model(complexity, text=last_user_msg)  # type: ignore[arg-type]

    # Первый LLM call — может вернуть tool_calls
    try:
        first = await _llm_call(model=model, messages=messages, tools=_tools_schema())
    except _LLMError as e:
        # Hard fail instead of fake-success — the frontend must surface the
        # real error state (network blip, OpenRouter outage, model down)
        # so the user retries with intent rather than getting a stale
        # "Не получилось…" string they assume is a model opinion.
        logger.error("BRO first LLM call failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="LLM upstream unavailable, try again",
        )

    tool_calls_raw = (first.get("message") or {}).get("tool_calls") or []
    if not tool_calls_raw:
        # Простой текстовый ответ
        reply = (first.get("message") or {}).get("content") or ""
        return ChatResponse(
            reply=reply,
            model=model,
            model_label=model_label(model),
            usage=first.get("usage"),
        )

    # Выполняем каждый tool_call по очереди
    tool_results: list[ToolCallResult] = []
    tool_messages: list[dict[str, Any]] = []  # для follow-up call

    # Добавляем assistant-сообщение с tool_calls в историю
    messages.append(
        {
            "role": "assistant",
            "content": (first.get("message") or {}).get("content") or "",
            "tool_calls": tool_calls_raw,
        }
    )

    for tc in tool_calls_raw:
        name = (tc.get("function") or {}).get("name", "")
        raw_args = (tc.get("function") or {}).get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}

        result = await _execute_tool(
            name,
            args,
            tenant_id=req.tenant_id or "",
            cabinet_id=req.cabinet_id,
        )
        ok = bool(result.get("success", True))
        tool_results.append(
            ToolCallResult(name=name, args=args, result=result, ok=ok)
        )
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.get("id") or "",
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    messages.extend(tool_messages)

    # Follow-up call — пусть LLM сформирует финальный текст с учётом
    # tool-результатов. tool_choice="none" чтобы модель НЕ запрашивала
    # ещё один раунд tool_calls (мы их в этом turn'е уже не исполняем —
    # они бы тихо отбросились и пользователь получил бы пустой ответ).
    try:
        second = await _llm_call(
            model=model,
            messages=messages,
            tools=_tools_schema(),
            tool_choice="none",
        )
        reply = (second.get("message") or {}).get("content") or ""
    except _LLMError as e:
        logger.warning(f"BRO follow-up call failed: {e}")
        # Fallback: соберём короткий саммари по tool results
        reply = _fallback_summary(tool_results)

    return ChatResponse(
        reply=reply,
        model=model,
        model_label=model_label(model),
        tool_calls=tool_results,
        usage=first.get("usage"),
    )


# ─── Tool dispatcher ───────────────────────────────────────────────────────


async def _execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    tenant_id: str,
    cabinet_id: Optional[str],
) -> dict[str, Any]:
    """Запускает один tool и возвращает его structured result."""
    try:
        if name == "propose_plan":
            cls = get_skill_class("orchestrator")
            if cls is None:
                return {"success": False, "summary": "orchestrator не найден"}
            skill = cls(tenant_id=tenant_id, cabinet_id=cabinet_id)
            res = await skill.run(action="plan", goal=str(args.get("goal") or ""))
            return res.model_dump()

        if name == "analyze_goal":
            cls = get_skill_class("orchestrator")
            if cls is None:
                return {"success": False, "summary": "orchestrator не найден"}
            skill = cls(tenant_id=tenant_id, cabinet_id=cabinet_id)
            res = await skill.run(action="analyze", goal=str(args.get("goal") or ""))
            return res.model_dump()

        if name == "list_agents":
            return {"success": True, "agents": list_skills()}

        if name == "get_skill_info":
            sid = str(args.get("skill_id") or "")
            cls = get_skill_class(sid)
            if cls is None:
                return {"success": False, "summary": f"skill '{sid}' not found"}
            return {
                "success": True,
                "id": cls.id,
                "name": cls.name,
                "description": cls.description,
                "requires_wb_api": cls.requires_wb_api,
            }

        return {"success": False, "summary": f"Unknown tool: {name}"}
    except Exception as e:  # noqa: BLE001 — Tool failures shouldn't crash chat
        logger.exception(f"Tool '{name}' execution failed")
        return {"success": False, "summary": f"Tool '{name}' failed: {e}"}


def _fallback_summary(results: list[ToolCallResult]) -> str:
    """Если follow-up LLM call упал — собираем компактное саммари по tool-результатам."""
    if not results:
        return "Не удалось получить ответ."
    bits: list[str] = []
    for r in results:
        if r.name == "propose_plan":
            tasks = (r.result.get("data") or {}).get("tasks") or []
            bits.append(
                f"Готов план из {len(tasks)} задач. "
                f"Открой Командный центр чтобы запустить."
            )
        elif r.name == "analyze_goal":
            insight = (r.result.get("data") or {}).get("analysis", {}).get("insight")
            if insight:
                bits.append(str(insight))
        elif r.name == "list_agents":
            agents = r.result.get("agents") or []
            bits.append(f"У меня в распоряжении {len(agents)} агентов MAO.")
        elif r.name == "get_skill_info":
            bits.append(
                f"{r.result.get('name') or r.result.get('id', '')} — "
                f"{r.result.get('description', '')}"
            )
    return " ".join(bits) or "Готово."


# ─── HTTP helpers ──────────────────────────────────────────────────────────


class _LLMError(Exception):
    pass


async def _llm_call(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str = "auto",
) -> dict[str, Any]:
    """Возвращает choices[0] из OpenRouter ответа.

    `tool_choice` defaults to "auto" for the initial call so the model can
    pick a tool. Pass "none" for follow-up turns where we've already
    executed tools and only want the model to compose the final reply —
    otherwise the model may emit a fresh round of tool_calls that we'd
    silently drop, producing an empty/confused reply.
    """
    # OpenRouter supports a `models` array for automatic fallback when the
    # primary returns 429 / 503 / model_down. Free-tier models are heavily
    # shared and rate-limit often, so we always include a paid fallback
    # (Haiku is ~$0.25/1M input, trivial cost per chat call) — keeps the
    # demo experience smooth without forcing every demo user to hit a paid
    # tier when the free pool has capacity.
    fallbacks: list[str] = []
    if model.endswith(":free"):
        # OpenRouter caps the `models` chain at 3 entries (primary + 2
        # fallbacks). Pick one alternative free model + paid Haiku as the
        # always-works floor. Haiku is ~$0.25/1M input — trivial per
        # demo chat call, way better UX than 503 when the free pool is hot.
        fallbacks = [
            "meta-llama/llama-3.1-405b-instruct:free",
            settings.OPENROUTER_MODEL_HAIKU,
        ]
    # Dedupe while preserving order. OpenRouter caps the chain at 3.
    seen: set[str] = set()
    model_chain = [m for m in [model, *fallbacks] if not (m in seen or seen.add(m))][:3]

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://mao.ai",
                "X-Title": "MAO.ai BRO",
            },
            json={
                "model": model,
                "models": model_chain,  # OpenRouter auto-fallback on 429/503
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "max_tokens": 1024,
                "temperature": 0.5,
            },
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise _LLMError(
                f"OpenRouter {e.response.status_code}: {e.response.text[:200]}"
            )
        data = r.json()

    choices = data.get("choices") or []
    if not choices:
        raise _LLMError("OpenRouter вернул пустой choices[]")
    return {
        "message": choices[0].get("message") or {},
        "usage": data.get("usage"),
    }
