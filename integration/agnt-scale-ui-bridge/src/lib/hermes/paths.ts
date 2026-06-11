// ============================================================
// Hermes /agent/* routes the SaaS proxy may forward.
// ============================================================

/** POST paths under Hermes `/agent` (no leading slash). */
export const HERMES_AGENT_POST_PATHS = new Set([
  'chat',
  'run',
  'campaign/plan',
  'campaign/execute',
  'campaign/optimize',
  'meta',
  'note',
  'memory/search',
]);

/** Inject decrypted Meta token when calling Hermes. */
export const HERMES_PATHS_NEED_META_TOKEN = new Set([
  'meta',
  'campaign/execute',
  'campaign/optimize',
]);

export function normalizeHermesAgentPath(segments: string[]): string | null {
  const path = segments.map((s) => s.trim()).filter(Boolean).join('/');
  return HERMES_AGENT_POST_PATHS.has(path) ? path : null;
}
