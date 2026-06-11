# AGNT SCALE — архитектура

Один продукт, два слоя:

```
┌─────────────────────────────────────────────────────────────┐
│  agnt_scale_meta (Next.js, Vercel)                          │
│  UI · auth · Meta OAuth · dashboards · cron sync            │
│  Не содержит логику агентов — только вызывает Hermes        │
└──────────────────────────┬──────────────────────────────────┘
                           │ X-Internal-Token
                           │ account_id = workspaceId
                           │ meta_token = decrypt(OAuth) [live Meta]
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Hermes (этот репозиторий) — мозг AGNT SCALE                │
│  agents · memory (RLS) · optimizer · campaign execute       │
│  llm_router · platform knowledge · Cal.com context          │
│  :7778 host · Tailscale Funnel :8443 (prod)               │
└──────────────────────────┬──────────────────────────────────┘
                           │ Graph API
                           ▼
                      Meta Marketing API
```

## Роли

| Компонент | Репозиторий | Роль |
|-----------|-------------|------|
| **Hermes** | `agnt-scale-hermes` | Вся agent-логика, память, Kill/Scale, blueprint, MCP |
| **App** | `agnt_scale_meta` | Интерфейс, OAuth Meta, хранение зашифрованных токенов, прокси в Hermes |

Hermes **не** хранит Meta OAuth-токены workspace — их держит app DB (encrypted).
На каждый live-запрос app **расшифровывает** токен и передаёт `meta_token` в Hermes.
Так мозг остаётся stateless по credentials и не дублирует секреты.

## Live Meta — цепочка внутри AGNT SCALE

1. **Connect Meta** (app): Settings → OAuth → `MetaConnection` в Postgres app.
2. **Sync** (app): cron / Sync now → Graph API напрямую (иерархия кампаний в UI DB).
3. **Агенты** (Hermes): UI → `POST /api/hermes/agent/...` → Hermes `/agent/...` + `meta_token`.

Эндпоинты Hermes, которым нужен `meta_token`:
- `POST /agent/campaign/optimize`
- `POST /agent/campaign/execute`
- `POST /agent/meta`

Без токена: `409` — пользователь не подключил Meta в Settings.

## Сеть (prod)

По `ISOLATION.md`:
- Hermes на сервере: `localhost:7778`
- Публичный доступ app → Hermes: **Tailscale Funnel :8443** → `127.0.0.1:7778`

В Vercel env app:
```
HERMES_URL=https://<tailscale-host>:8443
HERMES_INTERNAL_TOKEN=<тот же, что в Hermes/.env>
```

## Что ещё не связано (gap)

- App repo: прокси `/api/hermes/agent/*` — см. `integration/agnt-scale-ui-bridge/`
- App OAuth: добавить `ads_management`, убрать `read_insights` из callback
- UI: кнопки Optimize / Plan / Chat должны бить в прокси, не в OpenRouter напрямую

См. также: `docs/meta-oauth-hermes-bridge.md` (пошаговый setup Meta App + env).
