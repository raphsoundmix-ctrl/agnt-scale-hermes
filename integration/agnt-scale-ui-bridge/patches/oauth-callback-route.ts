// Replace src/app/api/meta/oauth/callback/route.ts scopes block + create() with:

import { META_OAUTH_SCOPES } from '@/lib/meta/oauth-scopes';

// ... inside GET after extendUserToken:

await prisma.$transaction(async (tx) => {
  await tx.metaConnection.updateMany({
    where: { workspaceId, status: 'ACTIVE' },
    data: { status: 'REVOKED', accessToken: '' },
  });
  await tx.metaConnection.create({
    data: {
      workspaceId,
      accessToken: encrypt(long.access_token),
      tokenExpiresAt: expiresIn ? new Date(Date.now() + expiresIn * 1000) : null,
      scopes: [...META_OAUTH_SCOPES],
      status: 'ACTIVE',
    },
  });
});

// Also update oauth/start to pass scopes: [...META_OAUTH_SCOPES] to getAuthUrl().
