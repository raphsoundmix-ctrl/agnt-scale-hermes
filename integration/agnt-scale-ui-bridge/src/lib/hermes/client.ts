// ============================================================
// Server-side Hermes Gateway client (SaaS → Hermes on :7778).
// ============================================================

import 'server-only';

import { env } from '@/lib/env';

export class HermesConfigError extends Error {
  constructor() {
    super('hermes_not_configured');
    this.name = 'HermesConfigError';
  }
}

export class HermesRequestError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`hermes_${status}`);
    this.name = 'HermesRequestError';
  }
}

function baseUrl(): string {
  const url = env.HERMES_URL?.replace(/\/$/, '');
  if (!url || !env.HERMES_INTERNAL_TOKEN) throw new HermesConfigError();
  return url;
}

/** POST JSON to Hermes `/agent/{agentPath}`. */
export async function postHermesAgent<T = unknown>(
  agentPath: string,
  payload: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(`${baseUrl()}/agent/${agentPath}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Internal-Token': env.HERMES_INTERNAL_TOKEN!,
    },
    body: JSON.stringify(payload),
    cache: 'no-store',
  });

  const text = await res.text();
  if (!res.ok) {
    throw new HermesRequestError(res.status, text);
  }

  return text ? (JSON.parse(text) as T) : ({} as T);
}
