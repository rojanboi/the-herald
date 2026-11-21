/* Talking to the Herald API. Two ways of saying who you are, decided by the service (GET /api/me):
   single sign-on (a session cookie; nothing to send) or demo mode (the "Viewing as" choice, sent as headers). */

export type Me = { mode: 'oidc' | 'demo'; user: string; name: string; role: string; teams: string[]; provider?: string | null };

export type Delivery = {
  id?: number; alert_id?: number; title?: string; recipient: string; channel: string; status: string;
  attempts: number; last_error?: string | null; error?: string | null; created_at?: string; latency_ms?: string | null;
};
export type Alert = {
  id: number; key: string; rule_id: string; owner: string; status: 'firing' | 'acked' | 'resolved'; severity: string;
  title: string; body: string; fields: Record<string, unknown>; highlight: string[]; colour: string; count: number;
  first_seen: string; last_seen: string; acked_by: string | null; resolved_by: string | null;
  incident_manager: string | null; source: string | null; deliveries: Delivery[];
};
export type Step = {
  id: number; kind: string; text: string; action: string | null; params: Record<string, unknown>;
  approval: string; condition: string | null; suggest?: string | null;
};
export type Approval = {
  id: number; step_id: number; action: string; required: string; requested_by: string; approver: string | null;
  status: string; decided_by: string | null; decided_via: string | null; expires_at: string;
};
export type Why = { passed: boolean; all?: Why[]; any?: Why[]; not?: Why; field?: string; op?: string; value?: unknown; actual?: unknown; missing?: boolean };
export type AlertDetail = Alert & {
  sample_event: unknown;
  runbook: { id: string; title: string; link: string | null; text: string | null; steps: Step[] } | null;
  step_state: Record<string, { state: string; by?: string; approved_by?: string; result?: { ok?: boolean; message?: string; simulated?: boolean } }>;
  why: Why | null;
  timeline: { at: string; actor: string; action: string; detail: Record<string, unknown> }[];
  approvals: Approval[];
};
export type Platform = {
  id: string; name: string; icon: string | null; type: string; topic: string | null; path: string | null; url?: string | null;
  managed: string; needs_token: boolean; firing: number; acked: number; resolved: number; last_event_at: string | null;
};
export type RuleDoc = {
  id: string; name: string; owner: string; enabled: boolean; severity: string | null; when: Cond;
  group?: { by: string[]; window: string | null } | null; notify: { to: string; channels?: string[]; hours?: unknown }[];
  present?: { title?: string; body?: string; colour?: string; fields?: string[] }; runbook: string | null; resolve_when?: Cond | null;
};
export type Cond = { all?: Cond[]; any?: Cond[]; not?: Cond; field?: string; op?: string; value?: unknown };
export type RuleRow = { doc: RuleDoc; version: number; source: string; updated_at: string; updated_by: string | null; alerts: number; last_fired: string | null };
export type Runbook = {
  id: string; title: string; source_format: string; source_text: string; steps: Step[]; parsed_by: string;
  reviewed: boolean; link: string | null; used_by: string[];
};
export type Team = {
  id: string; name: string; members: string[]; lead: string | null; on_call: string | null; rota: string[];
  shift_days: number; preferences: Record<string, string[]>; has_rules: boolean;
};
export type Person = { id: string; name: string; tz?: string; preferences: Record<string, string[]>; has_rules: boolean };
export type Directory = { teams: Team[]; people: Person[] };
export type RecentEvent = {
  id: number; source: string; received_at: string; raw: unknown;
  attrs: { type?: string | null; severity?: string | null; service?: string | null };
  fired: unknown[]; closed: unknown[];
};
export type Explained = {
  attrs: Record<string, unknown>;
  rules: { id: string; name: string; owner: string; enabled: boolean; passed: boolean; closes: boolean; failed_because: { field?: string; op?: string; value?: unknown; actual?: unknown; missing?: boolean }[] }[];
};
export type AuditEntry = { id: number; at: string; actor: string; action: string; target: string; detail: Record<string, unknown>; hash: string };

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail?: unknown) { super(message); }
}

let headers: Record<string, string> = {};
let mode: Me['mode'] = 'demo';
export function setIdentity(nextMode: Me['mode'], identity: Record<string, string>) { mode = nextMode; headers = identity; }
export function identityHeaders() { return mode === 'demo' ? headers : {}; }

function message(status: number, detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${d.loc?.length ? `${d.loc.join('.')}: ` : ''}${String(d.msg).replace(/^Value error, /, '')}`).join('; ');
  }
  return `The service answered ${status}`;
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const h: Record<string, string> = { Accept: 'application/json', ...identityHeaders() };
  if (opts.body !== undefined) h['Content-Type'] = 'application/json';
  const r = await fetch(path, {
    method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
    headers: h,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    credentials: 'same-origin',
  });
  let data: any = null;
  try { data = await r.json(); } catch { /* no body */ }
  if (!r.ok) throw new ApiError(message(r.status, data?.detail), r.status, data?.detail);
  return data as T;
}

/* Single sign-on: where to go when the session has ended, or when an approval needs a fresh sign-in. */
export function signInUrl(stepUp: boolean, provider?: string | null): string {
  const next = location.pathname + location.search + location.hash;
  return stepUp
    ? `/auth/login?${new URLSearchParams({ provider: provider || 'sso', step_up: '1', next })}`
    : `/login?${new URLSearchParams({ error: 'expired', next })}`;
}
