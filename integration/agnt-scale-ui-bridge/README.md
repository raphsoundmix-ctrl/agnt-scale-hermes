# AGNT SCALE — UI → Hermes bridge

Код для репозитория **agnt_scale_meta** (UI-слой). Hermes остаётся мозгом — здесь только прокси.

Copy `src/**` into `agnt_scale_meta` preserving paths.

1. Patch `src/lib/env.ts` — see `patches/env.ts.snippet`
2. Patch OAuth callback — see `patches/oauth-callback-route.ts`
3. Vercel env: `HERMES_URL` (Tailscale :8443), `HERMES_INTERNAL_TOKEN`

See `docs/agnt-scale-architecture.md`.
