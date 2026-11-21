import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { api, ApiError, identityHeaders, setIdentity, signInUrl, type Alert, type Directory, type Me, type Person, type Platform, type RuleRow, type Team } from './api';

export type Viewing = { team: string; role: 'responder' | 'approver' | 'editor' };
export type FlagItem = { id: number; title: string; description?: string; appearance: 'success' | 'error' | 'warning' | 'info' };
export type Scope = { id: string; name: string; owner: string; personal: boolean };

type Store = {
  me: Me;
  scopes: Scope[];                       // demo mode: the teams you can view as
  viewing: Viewing; setViewing: (v: Viewing) => void;
  owner: string | null;                  // whose alerts are shown; null = everything (admin)
  myRef: string;                         // person:<id> of the current person
  canEdit: boolean; canApprove: boolean;
  people: Record<string, Person>; teams: Record<string, Team>;
  alerts: Alert[]; platforms: Platform[]; rules: RuleRow[];
  tick: number;                          // goes up whenever something changed on the service
  ready: boolean; offline: boolean;
  refresh: () => Promise<void>;
  run: <T>(work: () => Promise<T>, done?: string | ((result: T) => string)) => Promise<T | undefined>;
  flags: FlagItem[]; notify: (flag: Omit<FlagItem, 'id'>) => void; dismissFlag: (id: number) => void;
  name: (ref: string | null | undefined) => string;
};

const Ctx = createContext<Store | null>(null);
export const useHerald = () => {
  const store = useContext(Ctx);
  if (!store) throw new Error('useHerald outside the provider');
  return store;
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const saved = (): Viewing | null => { try { return JSON.parse(localStorage.getItem('herald.ads.viewing') || 'null'); } catch { return null; } };

export function HeraldProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me>({ mode: 'demo', user: 'person:demo', name: 'Demo', role: 'admin', teams: [] });
  const [dir, setDir] = useState<Directory>({ teams: [], people: [] });
  const [viewing, setViewingState] = useState<Viewing>(saved() ?? { team: '', role: 'responder' });
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [tick, setTick] = useState(0);
  const [ready, setReady] = useState(false);
  const [offline, setOffline] = useState(false);
  const [flags, setFlags] = useState<FlagItem[]>([]);
  const flagId = useRef(0);
  const leaving = useRef(false);

  const people = useMemo(() => Object.fromEntries(dir.people.map((p) => [p.id, p])), [dir]);
  const teams = useMemo(() => Object.fromEntries(dir.teams.map((t) => [t.id, t])), [dir]);
  const scopes = useMemo<Scope[]>(() => [
    ...dir.teams.filter((t) => t.has_rules && t.id !== 'loadtest').map((t) => ({ id: t.id, name: t.name, owner: `team:${t.id}`, personal: false })),
    ...dir.people.filter((p) => p.has_rules).map((p) => ({ id: p.id, name: `${p.name} (personal)`, owner: `person:${p.id}`, personal: true })),
  ], [dir]);

  /* Who am I, and whose alerts do I see. */
  const scope = scopes.find((s) => s.id === viewing.team) ?? scopes[0];
  const demoPerson = useMemo(() => {
    if (!scope) return '';
    if (scope.personal) return scope.id;
    const t = teams[scope.id];
    if (!t) return '';
    if (viewing.role === 'approver') return t.lead || t.on_call || t.members[0];
    if (viewing.role === 'editor') return t.members.find((m) => m !== t.on_call && m !== t.lead) || t.lead || t.on_call || t.members[0];
    return t.on_call || t.lead || t.members[0];
  }, [scope, teams, viewing.role]);

  const sso = me.mode === 'oidc';
  const myRef = sso ? me.user : `person:${demoPerson}`;
  const role = sso ? me.role : viewing.role;
  const owner = sso ? (me.role === 'admin' ? null : me.teams[0] ? `team:${me.teams[0]}` : me.user) : scope?.owner ?? null;
  // Set during render so every request made by this render's effects carries the right identity.
  setIdentity(me.mode, sso || !scope ? {} : {
    'X-Herald-User': myRef, 'X-Herald-Role': role, 'X-Herald-Teams': scope.personal ? '' : scope.id,
  });

  const notify = useCallback((flag: Omit<FlagItem, 'id'>) => {
    const id = ++flagId.current;
    setFlags((all) => [{ ...flag, id }, ...all].slice(0, 4));
    window.setTimeout(() => setFlags((all) => all.filter((f) => f.id !== id)), 7000);
  }, []);
  const dismissFlag = useCallback((id: number) => setFlags((all) => all.filter((f) => f.id !== id)), []);

  const failed = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401 && sso && !leaving.current) {
      leaving.current = true;
      const stepUp = error.detail === 'step_up_required';
      notify({ appearance: 'info', title: stepUp ? 'Confirm it is you' : 'Your session has ended', description: 'Taking you to sign in…' });
      window.setTimeout(() => location.assign(signInUrl(stepUp, me.provider)), 900);
      return;
    }
    notify({ appearance: 'error', title: 'That did not work', description: error instanceof Error ? error.message : String(error) });
  }, [me.provider, notify, sso]);

  const refresh = useCallback(async () => {
    if (!ready) return;
    try {
      const q = owner ? `&owner=${encodeURIComponent(owner)}` : '';
      const [a, p] = await Promise.all([api<Alert[]>(`/api/alerts?limit=200${q}`), api<Platform[]>('/api/platforms')]);
      setAlerts(a); setPlatforms(p); setOffline(false); setTick((t) => t + 1);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) failed(error); else setOffline(true);
    }
  }, [failed, owner, ready]);

  const run = useCallback(async <T,>(work: () => Promise<T>, done?: string | ((result: T) => string)) => {
    try {
      const result = await work();
      if (done) notify({ appearance: 'success', title: typeof done === 'function' ? done(result) : done });
      await refresh();
      return result;
    } catch (error) {
      failed(error);
      await refresh();
      return undefined;
    }
  }, [failed, notify, refresh]);

  /* Start: who is signed in, then the directory. */
  useEffect(() => {
    (async () => {
      let who: Me | null = null;
      try {
        const r = await fetch('/api/me', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (r.status === 401) { location.assign(signInUrl(false)); return; }
        const d = await r.json();
        if (r.ok && d?.mode === 'oidc') who = d;
      } catch { /* demo mode */ }
      if (who) { setMe(who); setIdentity('oidc', {}); }
      try {
        setDir(await api<Directory>('/api/directory'));
        setReady(true);
      } catch { setOffline(true); }
    })();
  }, []);

  /* Load, and reload when the person or team changes. */
  useEffect(() => {
    if (!ready) return;
    void refresh();
    api<RuleRow[]>('/api/rules').then(setRules).catch(() => setRules([]));
  }, [ready, owner, myRef, role, refresh]);

  /* Live updates: the service pushes a line whenever an alert changes; a slow poll covers a dropped connection. */
  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    let timer = 0;
    const soon = () => { window.clearTimeout(timer); timer = window.setTimeout(() => void refresh(), 150); };
    (async () => {
      while (!ctl.signal.aborted) {
        try {
          const r = await fetch(`/api/stream${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`, {
            headers: { Accept: 'text/event-stream', ...identityHeaders() }, signal: ctl.signal, credentials: 'same-origin',
          });
          if (!r.ok || !r.body) throw new Error('no stream');
          const reader = r.body.getReader();
          const decoder = new TextDecoder();
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (decoder.decode(value, { stream: true }).includes('data:')) soon();
          }
        } catch { /* reconnect below */ }
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
      }
    })();
    const poll = window.setInterval(() => void refresh(), 15000);
    return () => { ctl.abort(); window.clearInterval(poll); window.clearTimeout(timer); };
  }, [ready, owner, myRef, refresh]);

  const setViewing = useCallback((v: Viewing) => {
    setViewingState(v);
    try { localStorage.setItem('herald.ads.viewing', JSON.stringify(v)); } catch { /* private window */ }
  }, []);

  const name = useCallback((ref: string | null | undefined) => {
    const r = String(ref ?? '');
    if (!r) return '';
    if (r.startsWith('system:')) return 'Herald (automatic)';
    if (r.startsWith('person:')) return people[r.slice(7)]?.name ?? cap(r.slice(7));
    if (r.startsWith('team:')) {
      const id = r.slice(5).replace('/on-call', '');
      const n = teams[id]?.name ?? cap(id);
      return r.endsWith('/on-call') ? `${n} on-call person` : `${n} team`;
    }
    return r;
  }, [people, teams]);

  const value: Store = {
    me: sso ? me : { ...me, user: myRef, name: people[demoPerson]?.name ?? 'Demo', role },
    scopes, viewing: { team: scope?.id ?? '', role: viewing.role }, setViewing, owner, myRef,
    canEdit: ['editor', 'approver', 'admin'].includes(role), canApprove: ['approver', 'admin'].includes(role),
    people, teams, alerts, platforms, rules, tick, ready, offline, refresh, run, flags, notify, dismissFlag, name,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/* Load something for one screen, and load it again when the service says something changed. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): { data: T | undefined; error: string | null; loading: boolean } {
  const { tick, ready } = useHerald();
  const [state, setState] = useState<{ data: T | undefined; error: string | null; loading: boolean }>({ data: undefined, error: null, loading: true });
  useEffect(() => {
    if (!ready) return;
    let live = true;
    load().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error) => live && setState((s) => ({ data: s.data, error: error instanceof Error ? error.message : String(error), loading: false })),
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ready, ...deps]);
  return state;
}
