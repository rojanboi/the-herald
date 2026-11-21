import { useEffect, useMemo, useRef, useState } from 'react';

import Badge from '@atlaskit/badge';
import Button from '@atlaskit/button/new';
import EmptyState from '@atlaskit/empty-state';
import { FadeIn, StaggeredEntrance } from '@atlaskit/motion';
import { draggable, dropTargetForElements } from '@atlaskit/pragmatic-drag-and-drop/element/adapter';
import { Inline, Stack, Text } from '@atlaskit/primitives/compiled';
import Select from '@atlaskit/select';
import Tabs, { Tab, TabList, TabPanel } from '@atlaskit/tabs';
import Textfield from '@atlaskit/textfield';
import Toggle from '@atlaskit/toggle';

import { api, type Alert } from '../api';
import { Table } from '../Table';
import { useHerald } from '../store';
import { ago, go, openAlert, Page, Severity, Status, STATUS, Who } from '../ui';

type Option = { label: string; value: string };
const COLUMNS: { status: Alert['status']; hint: string }[] = [
  { status: 'firing', hint: 'Nobody has picked these up yet' },
  { status: 'acked', hint: 'Someone is working on these' },
  { status: 'resolved', hint: 'Closed by a person or by the system itself' },
];
const SEV_ORDER: Record<string, number> = { critical: 0, error: 1, warning: 2, info: 3 };

function Card({ alert, platform }: { alert: Alert; platform: string }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    return draggable({
      element: ref.current,
      getInitialData: () => ({ id: alert.id, status: alert.status }),
      onDragStart: () => setDragging(true),
      onDrop: () => setDragging(false),
    });
  }, [alert.id, alert.status]);
  return (
    <button ref={ref} type="button" className={`card${dragging ? ' dragging' : ''}`} onClick={() => openAlert(alert.id)} aria-label={`${alert.key}: ${alert.title}`}>
      <Text weight="medium" maxLines={2}>{alert.title}</Text>
      <div className="card-meta">
        <Severity value={alert.severity} />
        {alert.count > 1 && <Badge appearance="primary" max={9999}>{alert.count}</Badge>}
        {platform && <Text size="small" color="color.text.subtle">{platform}</Text>}
      </div>
      <div className="card-foot">
        <Text size="small" weight="semibold" color="color.text.subtle">{alert.key}</Text>
        <Inline space="space.100" alignBlock="center">
          <Text size="small" color="color.text.subtlest">{ago(alert.last_seen)}</Text>
          <Who who={alert.incident_manager} size="small" bare />
        </Inline>
      </div>
    </button>
  );
}

function Column({ status, hint, alerts, platformName, onMove }: {
  status: Alert['status']; hint: string; alerts: Alert[]; platformName: (id: string | null) => string; onMove: (id: number, to: Alert['status']) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    return dropTargetForElements({
      element: ref.current,
      canDrop: ({ source }) => source.data.status !== status && status !== 'firing',
      onDragEnter: () => setOver(true),
      onDragLeave: () => setOver(false),
      onDrop: ({ source }) => { setOver(false); onMove(Number(source.data.id), status); },
    });
  }, [status, onMove]);
  return (
    <div ref={ref} className={`column${over ? ' over' : ''}`}>
      <div className="column-head">
        <Text size="small" weight="semibold" color="color.text.subtle">{STATUS[status].toUpperCase()}</Text>
        <Badge>{alerts.length}</Badge>
      </div>
      <div className="column-body">
        <StaggeredEntrance columns={1} delayStep={40}>
          {alerts.map((a) => (
            <FadeIn key={a.id} entranceDirection="bottom">
              {(motion) => <div {...motion}><Card alert={a} platform={platformName(a.source)} /></div>}
            </FadeIn>
          ))}
        </StaggeredEntrance>
        {!alerts.length && <div className="empty-col"><Text size="small" color="color.text.subtlest">{hint}</Text></div>}
      </div>
    </div>
  );
}

export function Alerts({ mine = false, source }: { mine?: boolean; source?: string }) {
  const h = useHerald();
  const [text, setText] = useState('');
  const [platform, setPlatform] = useState<Option | null>(null);
  const [assignee, setAssignee] = useState<Option | null>(null);
  const [resolved, setResolved] = useState(!mine);
  const [tab, setTab] = useState(() => { try { return Number(localStorage.getItem('herald.ads.tab') || 0); } catch { return 0; } });

  const platformName = (id: string | null) => h.platforms.find((p) => p.id === id)?.name ?? '';
  const shown = useMemo(() => {
    const s = text.trim().toLowerCase();
    return h.alerts
      .filter((a) => !mine || a.incident_manager === h.myRef)
      .filter((a) => !source || a.source === source)
      .filter((a) => resolved || a.status !== 'resolved')
      .filter((a) => !platform || a.source === platform.value)
      .filter((a) => !assignee || (assignee.value === 'none' ? !a.incident_manager : a.incident_manager === assignee.value))
      .filter((a) => !s || `${a.key} ${a.title} ${a.body}`.toLowerCase().includes(s))
      .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9) || Date.parse(b.last_seen) - Date.parse(a.last_seen));
  }, [h.alerts, h.myRef, mine, source, resolved, platform, assignee, text]);

  const move = (id: number, to: Alert['status']) => {
    const alert = h.alerts.find((a) => a.id === id);
    if (!alert || alert.status === to) return;
    void h.run(() => api(`/api/alerts/${id}/${to === 'acked' ? 'ack' : 'resolve'}`, { method: 'POST' }),
      `${alert.key} moved to ${STATUS[to]}`);
  };

  const people = Object.values(h.people).filter((p) => p.id !== 'loadbot');
  const filters = (
    <div className="filters">
      <div className="grow"><Textfield isCompact aria-label="Filter alerts" placeholder="Filter by title or number" value={text} onChange={(e) => setText(e.currentTarget.value)} /></div>
      {!source && (
        <div className="pick">
          <Select<Option> spacing="compact" isClearable placeholder="Platform" aria-label="Platform" value={platform} onChange={setPlatform}
            options={h.platforms.map((p) => ({ label: p.name, value: p.id }))} />
        </div>
      )}
      {!mine && (
        <div className="pick">
          <Select<Option> spacing="compact" isClearable placeholder="Assignee" aria-label="Assignee" value={assignee} onChange={setAssignee}
            options={[{ label: 'Unassigned', value: 'none' }, ...people.map((p) => ({ label: p.name, value: `person:${p.id}` }))]} />
        </div>
      )}
      <Inline space="space.050" alignBlock="center">
        <Toggle id="show-resolved" isChecked={resolved} onChange={() => setResolved((v) => !v)} label="Show resolved" />
        <label htmlFor="show-resolved"><Text color="color.text.subtle">Show resolved</Text></label>
      </Inline>
    </div>
  );

  const board = (
    <div className="board">
      {COLUMNS.filter((c) => resolved || c.status !== 'resolved').map((c) => (
        <Column key={c.status} status={c.status} hint={c.hint} alerts={shown.filter((a) => a.status === c.status)} platformName={platformName} onMove={move} />
      ))}
    </div>
  );

  const table = (
    <Table
      head={{ cells: [
        { key: 'key', content: 'Number', isSortable: true, width: 9 },
        { key: 'title', content: 'Summary', isSortable: true },
        { key: 'severity', content: 'Severity', isSortable: true, width: 10 },
        { key: 'status', content: 'Status', isSortable: true, width: 11 },
        { key: 'platform', content: 'Platform', isSortable: true, width: 14 },
        { key: 'assignee', content: 'Assignee', isSortable: true, width: 15 },
        { key: 'count', content: 'Events', isSortable: true, width: 7 },
        { key: 'seen', content: 'Last seen', isSortable: true, width: 10 },
      ] }}
      rows={shown.map((a) => ({
        key: String(a.id),
        className: 'clickable-row',
        onClick: () => openAlert(a.id),
        cells: [
          { key: a.id, content: <Text weight="semibold" color="color.text.subtle">{a.key}</Text> },
          { key: a.title, content: <Text>{a.title}</Text> },
          { key: SEV_ORDER[a.severity] ?? 9, content: <Severity value={a.severity} /> },
          { key: a.status, content: <Status value={a.status} /> },
          { key: platformName(a.source), content: platformName(a.source) },
          { key: h.name(a.incident_manager) || 'zzz', content: <Who who={a.incident_manager} /> },
          { key: a.count, content: a.count },
          { key: Date.parse(a.last_seen), content: ago(a.last_seen) },
        ],
      }))}
      rowsPerPage={12}
      defaultSortKey="seen"
      defaultSortOrder="DESC"
      isRankable={false}
      emptyView={<EmptyState header="No alerts here" description={mine ? 'Nothing is assigned to you right now.' : 'Nothing matches these filters.'} />}
    />
  );

  const content = (
    <Stack space="space.200">
      {filters}
      <Tabs id="alert-views" selected={tab} onChange={(index) => { setTab(index); try { localStorage.setItem('herald.ads.tab', String(index)); } catch { /* private window */ } }}>
        <TabList>
          <Tab>Board</Tab>
          <Tab>List</Tab>
        </TabList>
        <TabPanel><div style={{ width: '100%', paddingTop: 16 }}>{board}</div></TabPanel>
        <TabPanel><div style={{ width: '100%', paddingTop: 16 }}>{table}</div></TabPanel>
      </Tabs>
    </Stack>
  );

  if (source) return content;
  if (mine && !h.alerts.some((a) => a.incident_manager === h.myRef)) {
    return (
      <Page title="For you" lead="Alerts that someone handed to you appear here.">
        <EmptyState header="Nothing is assigned to you" description="When a colleague hands you an alert, you are told on your usual channel and it shows up here."
          primaryAction={<Button appearance="primary" onClick={() => go('/alerts')}>See all alerts</Button>} />
      </Page>
    );
  }
  const scope = h.owner ? h.name(h.owner) : 'every team';
  return (
    <Page
      title={mine ? 'For you' : 'Alerts'}
      lead={mine ? 'Alerts handed to you. Drag a card to In progress when you start, to Resolved when it is fixed.' : `Everything that needs attention for ${scope}. Open a card to see why it fired, who was told and what to do.`}
    >
      {content}
    </Page>
  );
}
