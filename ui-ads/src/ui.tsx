/* Small shared pieces: routing by the address bar's hash, wording, and the lozenges every screen uses. */
import { useEffect, useState, type ReactNode } from 'react';

import Avatar from '@atlaskit/avatar';
import Heading from '@atlaskit/heading';
import Lozenge from '@atlaskit/lozenge';
import { FadeIn } from '@atlaskit/motion';
import { Box, Inline, Stack, Text } from '@atlaskit/primitives/compiled';
import Spinner from '@atlaskit/spinner';
import Tooltip from '@atlaskit/tooltip';

import type { Cond } from './api';
import { useHerald } from './store';

/* ---- routing: #/alerts?alert=9 ---------------------------------------------------------- */
export type Route = { path: string; parts: string[]; alert: number | null };
const read = (): Route => {
  const raw = location.hash.replace(/^#/, '') || '/for-you';
  const legacy = /^alert-(\d+)$/.exec(raw);                      // links in emails and texts: #alert-9
  const [path, query = ''] = legacy ? ['/alerts', `alert=${legacy[1]}`] : raw.split('?');
  const alert = Number(new URLSearchParams(query).get('alert'));
  return { path, parts: path.split('/').filter(Boolean), alert: Number.isFinite(alert) && alert > 0 ? alert : null };
};
export function useRoute(): Route {
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}
export const go = (path: string) => { location.hash = path; };
export const openAlert = (id: number | null) => {
  const { path } = read();
  location.hash = id ? `${path}?alert=${id}` : path;
};

/* ---- wording ---------------------------------------------------------------------------- */
export function ago(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
export const clock = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';

const OPS: Record<string, string> = {
  eq: 'is', ne: 'is not', gt: 'is above', gte: 'is at least', lt: 'is below', lte: 'is at most', in: 'is one of',
  contains: 'contains', glob: 'looks like', regex: 'matches', exists: 'is present', severity_gte: 'is at least as severe as',
};
const ALIAS: Record<string, string> = { '@type': 'type', '@severity': 'severity', '@service': 'service', '@source': 'platform', '@timestamp': 'time' };
/* A rule's condition as a sentence a person outside the team can read. */
export function sentence(c: Cond | null | undefined): string {
  if (!c) return '';
  if (c.all) return c.all.map(sentence).join(' and ');
  if (c.any) return `(${c.any.map(sentence).join(' or ')})`;
  if (c.not) return `not (${sentence(c.not)})`;
  const field = ALIAS[c.field ?? ''] ?? c.field ?? '';
  const value = Array.isArray(c.value) ? c.value.join(', ') : c.value === undefined || c.value === null ? '' : String(c.value);
  return `${field} ${OPS[c.op ?? ''] ?? c.op}${c.op === 'exists' ? '' : ` ${value}`}`;
}

/* ---- lozenges --------------------------------------------------------------------------- */
const SEVERITY: Record<string, { label: string; appearance: 'removed' | 'moved' | 'inprogress' | 'default'; bold: boolean }> = {
  critical: { label: 'Critical', appearance: 'removed', bold: true },
  error: { label: 'Error', appearance: 'removed', bold: false },
  warning: { label: 'Warning', appearance: 'moved', bold: false },
  info: { label: 'Info', appearance: 'inprogress', bold: false },
};
export function Severity({ value }: { value: string | null | undefined }) {
  const s = SEVERITY[value ?? ''] ?? { label: value ? value : 'Event', appearance: 'default' as const, bold: false };
  return <Lozenge appearance={s.appearance} isBold={s.bold}>{s.label}</Lozenge>;
}
export const STATUS = { firing: 'Open', acked: 'In progress', resolved: 'Resolved' } as const;
export function Status({ value }: { value: keyof typeof STATUS }) {
  const appearance = value === 'resolved' ? 'success' : value === 'acked' ? 'inprogress' : 'default';
  return <Lozenge appearance={appearance}>{STATUS[value] ?? value}</Lozenge>;
}
const CHANNEL: Record<string, string> = { ui: 'In Herald', email: 'Email', sms: 'Text message', phone: 'Phone call' };
export const channelName = (c: string) => CHANNEL[c] ?? c;
export function DeliveryState({ value }: { value: string }) {
  const map: Record<string, 'success' | 'removed' | 'inprogress' | 'default'> = { sent: 'success', failed: 'removed', dead: 'removed', pending: 'inprogress' };
  const label: Record<string, string> = { sent: 'Delivered', failed: 'Retrying', dead: 'Gave up', pending: 'Sending' };
  return <Lozenge appearance={map[value] ?? 'default'}>{label[value] ?? value}</Lozenge>;
}

/* ---- people ----------------------------------------------------------------------------- */
/* The demo has no photos, so each person gets their initials on one of the design system's accent colours.
   The colour depends only on the name, so a person looks the same on every screen. */
const ACCENTS = ['#1868DB', '#227D9B', '#22A06B', '#5E4DB2', '#AE4787', '#C25100', '#964AC0', '#1F845A'];
const faces = new Map<string, string>();
export function face(name: string): string {
  const hit = faces.get(name);
  if (hit) return hit;
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? '').join('');
  let sum = 0;
  for (const ch of name) sum = (sum * 31 + ch.charCodeAt(0)) % 9973;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="${ACCENTS[sum % ACCENTS.length]}"/>`
    + `<text x="32" y="33" font-family="ui-sans-serif,system-ui,sans-serif" font-size="26" font-weight="600" fill="#fff" text-anchor="middle" dominant-baseline="central">${initials}</text></svg>`;
  const src = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  faces.set(name, src);
  return src;
}
export function Who({ who, size = 'small', bare = false }: { who: string | null | undefined; size?: 'xsmall' | 'small' | 'medium'; bare?: boolean }) {
  const { name } = useHerald();
  if (!who) {
    return bare ? <Tooltip content="Unassigned"><Avatar size={size} name="Unassigned" /></Tooltip>
      : <Inline space="space.100" alignBlock="center"><Avatar size={size} /><Text color="color.text.subtlest">Unassigned</Text></Inline>;
  }
  const label = name(who);
  const src = who.startsWith('person:') ? face(label) : undefined;
  if (bare) return <Tooltip content={label}><Avatar size={size} name={label} src={src} /></Tooltip>;
  return <Inline space="space.100" alignBlock="center"><Avatar size={size} name={label} src={src} /><Text>{label}</Text></Inline>;
}

/* ---- page furniture --------------------------------------------------------------------- */
export function Page({ title, lead, actions, crumbs, children }: { title: string; lead?: string; actions?: ReactNode; crumbs?: ReactNode; children: ReactNode }) {
  return (
    <FadeIn entranceDirection="bottom" duration="medium">
      {(motion) => (
        <div {...motion} className={`page ${motion.className ?? ''}`}>
          <Stack space="space.300">
            <Stack space="space.100">
              {crumbs}
              <Inline spread="space-between" alignBlock="center" shouldWrap space="space.200">
                <Heading size="large" as="h1">{title}</Heading>
                {actions && <Inline space="space.100" alignBlock="center">{actions}</Inline>}
              </Inline>
              {lead && <Text color="color.text.subtle">{lead}</Text>}
            </Stack>
            {children}
          </Stack>
        </div>
      )}
    </FadeIn>
  );
}
export function Loading() {
  return <Box padding="space.600"><Inline alignInline="center"><Spinner size="large" label="Loading" /></Inline></Box>;
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <Text size="small" weight="semibold" color="color.text.subtle">{label}</Text>
      <div>{children}</div>
    </div>
  );
}
export const pretty = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value, null, 2));
