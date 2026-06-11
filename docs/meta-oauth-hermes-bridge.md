# AGNT SCALE — Meta OAuth → Hermes (live campaigns)

Часть единого продукта **AGNT SCALE**. Hermes — мозг; app (`agnt_scale_meta`) — UI и OAuth.

См. обзор: `docs/agnt-scale-architecture.md`

## Как это работает

```
Пользователь → App Settings → Connect Meta
    → Meta OAuth (ads_read, ads_management, …)
    → App сохраняет long-lived token (AES-256-GCM в Postgres app)

UI → POST /api/hermes/agent/campaign/optimize
    → App (server): account_id = workspaceId, meta_token = decrypt(token)
    → Hermes POST /agent/campaign/optimize + X-Internal-Token
    → Meta Graph API
```

**Важно:** Hermes не хранит Meta-токены — только исполняет логику. Токен передаётся на каждый live-запрос из app (уже реализовано в Hermes).

---

## 1. Meta Developer Console

1. [developers.facebook.com](https://developers.facebook.com) → **Create App** → тип **Business**.
2. Добавить продукт **Marketing API** / **Facebook Login for Business**.
3. **App ID** и **App Secret** → в Vercel env **agnt_scale_meta**:
   - `META_APP_ID`
   - `META_APP_SECRET`
4. **Valid OAuth Redirect URIs:**
   - Prod: `https://<your-vercel-domain>/api/meta/oauth/callback`
   - Local: `http://localhost:3000/api/meta/oauth/callback`
5. **Permissions** (App Review для production):
   - `ads_read` — insights, кампании
   - `ads_management` — create/pause/budget (execute в Hermes)
   - `business_management` — список ad accounts
   - `pages_read_engagement` — Page ID для креативов
   - Не запрашивать `read_insights` (это Page Insights, не Marketing API).

6. Режим **Live** + пройти App Review для scopes выше.

---

## 2. Переменные app (Vercel, agnt_scale_meta)

Уже нужны для OAuth:

| Variable | Example |
|----------|---------|
| `META_APP_ID` | from Meta |
| `META_APP_SECRET` | from Meta |
| `META_REDIRECT_URI` | `https://app.example.com/api/meta/oauth/callback` |
| `ENCRYPTION_KEY` | `openssl rand -hex 32` |
| `NEXT_PUBLIC_DEMO_MODE` | `false` (prod) |

**Добавить для Hermes:**

| Variable | Example |
|----------|---------|
| `HERMES_URL` | Tailscale Funnel `:8443` → Hermes `:7778` (см. `ISOLATION.md`) |
| `HERMES_INTERNAL_TOKEN` | тот же секрет, что в `Hermes/.env` на сервере |

`account_id` в Hermes = **workspace id** из app (`cmq10…`).

---

## 3. Сеть: Vercel app → Hermes (AGNT SCALE prod)

На сервере AGNT SCALE уже зарезервировано (`ISOLATION.md`):

- Hermes: `127.0.0.1:7778`
- Публично: **Tailscale Funnel :8443** → `127.0.0.1:7778` (token-gated)

`HERMES_URL` в Vercel = ваш Tailscale Funnel URL на :8443.

Проверка:

```bash
curl -s https://<tailscale-host>:8443/health
curl -s -H "X-Internal-Token: $TOKEN" https://<tailscale-host>:8443/agent/agents
```

---

## 4. Подключение Meta (пользователь)

1. Войти в AGNT SCALE app (Google OAuth).
2. **Settings → Connections → Connect Meta**.
3. Разрешить scopes в Meta.
4. Redirect на `/settings?connected=1`.
5. **Sync now** (или дождаться cron) — подтянуть ad accounts.

После этого `MetaConnection.status = ACTIVE` и токен доступен для прокси.

---

## 5. Вызов агентов из UI

Скопируйте файлы из `integration/agnt-scale-ui-bridge/` в репозиторий **agnt_scale_meta**.

Примеры (браузер → ваш SaaS, не Hermes напрямую):

```typescript
// Optimizer proposals
await fetch('/api/hermes/agent/campaign/optimize', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    ad_account_id: 'act_123',
    target_roas: 2.0,
  }),
});

// Chat (без meta_token — только LLM + memory)
await fetch('/api/hermes/agent/chat', {
  method: 'POST',
  body: JSON.stringify({
    agent_id: 'optimizer',
    message: 'Что делать с кампанией X?',
    locale: 'ru',
  }),
});
```

`meta_token` и `account_id` прокси подставляет сам.

---

## 6. Известные дыры в app (исправить при подключении bridge)

- OAuth callback: убрать `read_insights`, добавить `ads_management` (см. patched `callback/route.ts`).
- Reconnect: `upsert` вместо `create()` (см. patched callback).
- После OAuth: опционально `triggerSync()` в callback.
- UI: toast на `?connected=1` / `?error=` на Settings.

---

## 7. Hermes endpoints и meta_token

| Endpoint | meta_token |
|----------|------------|
| `POST /agent/chat` | нет |
| `POST /agent/campaign/plan` | нет |
| `POST /agent/campaign/optimize` | **да** |
| `POST /agent/campaign/execute` | **да** |
| `POST /agent/meta` | **да** |

Без токена Hermes отвечает `409` — это ожидаемо.
