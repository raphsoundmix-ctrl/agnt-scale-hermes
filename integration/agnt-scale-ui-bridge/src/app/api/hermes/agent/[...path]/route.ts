// ============================================================
// Hermes agent proxy: POST /api/hermes/agent/{chat|campaign/optimize|...}
//
// Injects workspace context server-side:
//   account_id  → active workspace id (Hermes memory scope)
//   meta_token  → decrypted Meta OAuth token (when route needs live Graph API)
//
// Browser never sees meta_token or HERMES_INTERNAL_TOKEN.
// ============================================================

import { NextResponse } from 'next/server';
import { requireActiveWorkspace, assertRoleAtLeast } from '@/lib/auth/workspace';
import { postHermesAgent, HermesConfigError, HermesRequestError } from '@/lib/hermes/client';
import {
  HERMES_PATHS_NEED_META_TOKEN,
  normalizeHermesAgentPath,
} from '@/lib/hermes/paths';
import { getWorkspaceMetaAccessToken, MetaNotConnectedError } from '@/lib/meta/workspace-token';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type RouteCtx = { params: Promise<{ path: string[] }> };

export async function POST(req: Request, ctx: RouteCtx): Promise<Response> {
  const { path: segments } = await ctx.params;
  const agentPath = normalizeHermesAgentPath(segments ?? []);
  if (!agentPath) {
    return NextResponse.json({ error: 'forbidden_path' }, { status: 404 });
  }

  let workspaceId: string;
  try {
    const ws = await requireActiveWorkspace();
    assertRoleAtLeast(ws.role, 'OPERATOR');
    workspaceId = ws.workspaceId;
  } catch {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const payload: Record<string, unknown> = {
    ...body,
    account_id: workspaceId,
  };

  if (HERMES_PATHS_NEED_META_TOKEN.has(agentPath)) {
    try {
      payload.meta_token = await getWorkspaceMetaAccessToken(workspaceId);
    } catch (e) {
      if (e instanceof MetaNotConnectedError) {
        return NextResponse.json(
          { error: 'meta_not_connected', detail: 'Connect Meta in Settings → Connections' },
          { status: 409 },
        );
      }
      throw e;
    }
  }

  try {
    const data = await postHermesAgent(agentPath, payload);
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof HermesConfigError) {
      return NextResponse.json({ error: 'hermes_not_configured' }, { status: 503 });
    }
    if (e instanceof HermesRequestError) {
      let detail: unknown = e.body;
      try {
        detail = JSON.parse(e.body);
      } catch {
        /* keep string */
      }
      return NextResponse.json(
        { error: 'hermes_error', status: e.status, detail },
        { status: e.status >= 400 && e.status < 600 ? e.status : 502 },
      );
    }
    throw e;
  }
}
