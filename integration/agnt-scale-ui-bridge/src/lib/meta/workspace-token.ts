// ============================================================
// Decrypt the active Meta user token for a workspace (Hermes proxy).
// Never log or return the raw token to the browser.
// ============================================================

import 'server-only';

import { prisma } from '@/lib/db';
import { decrypt } from '@/lib/db/crypto';
import { isDemoMode } from '@/lib/env';

export class MetaNotConnectedError extends Error {
  constructor() {
    super('no_active_meta_connection');
    this.name = 'MetaNotConnectedError';
  }
}

/** First ACTIVE connection for the workspace; throws if missing (live mode). */
export async function getWorkspaceMetaAccessToken(workspaceId: string): Promise<string> {
  if (isDemoMode()) return 'demo';

  const conn = await prisma.metaConnection.findFirst({
    where: { workspaceId, status: 'ACTIVE' },
    orderBy: { updatedAt: 'desc' },
    select: { accessToken: true, tokenExpiresAt: true },
  });
  if (!conn?.accessToken) throw new MetaNotConnectedError();

  if (conn.tokenExpiresAt && conn.tokenExpiresAt.getTime() < Date.now()) {
    throw new MetaNotConnectedError();
  }

  return decrypt(conn.accessToken);
}
