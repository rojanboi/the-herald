import { useMemo, useState } from 'react';

import Breadcrumbs, { BreadcrumbsItem } from '@atlaskit/breadcrumbs';
import Button from '@atlaskit/button/new';
import { Code, CodeBlock } from '@atlaskit/code';
import EmptyState from '@atlaskit/empty-state';
import { ErrorMessage, HelperMessage, Label } from '@atlaskit/form';
import Heading from '@atlaskit/heading';
import { IconTile } from '@atlaskit/icon';
import AddIcon from '@atlaskit/icon/core/add';
import DataFlowIcon from '@atlaskit/icon/core/data-flow';
import LinkIcon from '@atlaskit/icon/core/link';
import PageIcon from '@atlaskit/icon/core/page';
import RefreshIcon from '@atlaskit/icon/core/refresh';
import Lozenge from '@atlaskit/lozenge';
import Modal, { ModalBody, ModalFooter, ModalHeader, ModalTitle } from '@atlaskit/modal-dialog';
import { FadeIn, StaggeredEntrance } from '@atlaskit/motion';
import { Inline, Stack, Text } from '@atlaskit/primitives/compiled';
import SectionMessage from '@atlaskit/section-message';
import Select from '@atlaskit/select';
import Tabs, { Tab, TabList, TabPanel } from '@atlaskit/tabs';
import TextArea from '@atlaskit/textarea';
import Textfield from '@atlaskit/textfield';

import { api, type Explained, type Platform, type RecentEvent } from '../api';
import { Table } from '../Table';
import { useHerald, useLoad } from '../store';
import { ago, go, Loading, Page, pretty, sentence, Severity } from '../ui';
import { Alerts } from './Alerts';

type Option = { label: string; value: string };

const KIND: Record<string, { label: string; how: string; icon: typeof LinkIcon; colour: 'blue' | 'purple' | 'teal' | 'green' }> = {
  webhook: { label: 'Webhook', how: 'The platform posts each event to Herald over HTTPS.', icon: LinkIcon, colour: 'blue' },
  kafka: { label: 'Message broker', how: 'Herald reads the platform’s topic on the message broker.', icon: DataFlowIcon, colour: 'purple' },
  logtail: { label: 'Log file', how: 'Herald follows the platform’s log file line by line.', icon: PageIcon, colour: 'teal' },
  http_poll: { label: 'Polled address', how: 'Herald asks the platform’s address for news on a timer.', icon: RefreshIcon, colour: 'green' },
};
const kind = (p: Platform) => KIND[p.type] ?? KIND.webhook;
const SAMPLE_FOR: Record<string, string> = {
  'workflow-events': 'workflow_failed', 'metrics-webhook': 'metric_breach', 'trade-events': 'trade_rejected', 'pricing-logs': 'pricing_log',
};
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);

export function Platforms({ onConnect }: { onConnect: () => void }) {
  const h = useHerald();
  return (
    <Page title="Platforms" lead="The systems that send events to Herald. Open one to see its problems, its recent events, and how it is connected."
      actions={<Button appearance="primary" iconBefore={AddIcon} onClick={onConnect}>Connect a platform</Button>}>
      {h.platforms.length ? (
        <div className="tiles">
          <StaggeredEntrance columns="responsive" delayStep={40}>
            {h.platforms.map((p) => {
              const k = kind(p);
              return (
                <FadeIn key={p.id} entranceDirection="bottom">
                  {(motion) => (
                    <div {...motion}>
                      <a className="tile" href={`#/platforms/${p.id}`}>
                        <Inline space="space.150" alignBlock="center">
                          <IconTile icon={k.icon} label="" appearance={k.colour} size="medium" />
                          <Stack space="space.0">
                            <Heading size="small" as="h2">{p.name}</Heading>
                            <Text size="small" color="color.text.subtle">{k.label} · last event {ago(p.last_event_at)}</Text>
                          </Stack>
                        </Inline>
                        <div className="tile-stats">
                          <div className="stat"><b>{p.firing}</b><Text size="small" color="color.text.subtle">Open</Text></div>
                          <div className="stat"><b>{p.acked}</b><Text size="small" color="color.text.subtle">In progress</Text></div>
                          <div className="stat"><b>{p.resolved}</b><Text size="small" color="color.text.subtle">Resolved</Text></div>
                        </div>
                        <Inline>{p.firing ? <Lozenge appearance="removed">Needs attention</Lozenge> : p.acked ? <Lozenge appearance="inprogress">Being handled</Lozenge> : <Lozenge appearance="success">All clear</Lozenge>}</Inline>
                      </a>
                    </div>
                  )}
                </FadeIn>
              );
            })}
          </StaggeredEntrance>
        </div>
      ) : (
        <EmptyState header="No platforms yet" description="Connect the first system and send it one event to see what Herald makes of it."
          primaryAction={<Button appearance="primary" onClick={onConnect}>Connect a platform</Button>} />
      )}
    </Page>
  );
}

function curl(p: Platform, token: string | null, body: string) {
  return [`curl -X POST ${location.origin}/ingest/${p.id} \\`,
    ...(p.needs_token || token ? [`  -H "X-Herald-Token: ${token ?? '<the token for this platform>'}" \\`] : []),
    '  -H "Content-Type: application/json" \\', `  -d '${body}'`].join('\n');
}

function Events({ platform }: { platform: Platform }) {
  const { data } = useLoad(() => api<RecentEvent[]>('/api/events/recent?limit=50'), []);
  const [why, setWhy] = useState<RecentEvent | null>(null);
  if (!data) return <Loading />;
  const rows = data.filter((e) => e.source === platform.id);
  return (
    <>
      <Table
        head={{ cells: [
          { key: 'at', content: 'Received', isSortable: true, width: 14 },
          { key: 'type', content: 'Type', isSortable: true, width: 24 },
          { key: 'severity', content: 'Severity', width: 12 },
          { key: 'result', content: 'What Herald did' },
          { key: 'why', content: '', width: 12 },
        ] }}
        rows={rows.map((e) => ({
          key: String(e.id),
          cells: [
            { key: Date.parse(e.received_at), content: ago(e.received_at) },
            { key: e.attrs.type ?? '', content: <Code>{e.attrs.type ?? 'no type'}</Code> },
            { key: e.attrs.severity ?? '', content: e.attrs.severity ? <Severity value={e.attrs.severity} /> : <Text color="color.text.subtlest">none</Text> },
            { key: 'r', content: e.fired.length ? <Lozenge appearance="removed">Raised {e.fired.length === 1 ? 'an alert' : `${e.fired.length} alerts`}</Lozenge>
              : e.closed.length ? <Lozenge appearance="success">Closed an alert</Lozenge> : <Lozenge>No rule matched</Lozenge> },
            { key: 'w', content: <Button appearance="subtle" spacing="compact" onClick={() => setWhy(e)}>Why?</Button> },
          ],
        }))}
        rowsPerPage={10}
        defaultSortKey="at"
        defaultSortOrder="DESC"
        emptyView={<EmptyState header="No events yet" description="Send a test event from the next tab, or point the platform at the address under Setup." />}
      />
      {why && <WhyModal event={why.raw} source={platform.id} onClose={() => setWhy(null)} />}
    </>
  );
}

function Verdict({ result }: { result: Explained }) {
  return (
    <Stack space="space.100">
      {[...result.rules].sort((a, b) => Number(b.passed) - Number(a.passed) || Number(b.closes) - Number(a.closes)).map((r) => (
        <Inline key={r.id} space="space.100" alignBlock="center" shouldWrap>
          <Lozenge appearance={r.passed ? 'removed' : r.closes ? 'success' : 'default'}>{r.passed ? 'Raises an alert' : r.closes ? 'Closes an alert' : 'No match'}</Lozenge>
          <Text weight="medium">{r.name}</Text>
          {!r.passed && !r.closes && r.failed_because[0] && (
            <Text size="small" color="color.text.subtle">
              {r.failed_because[0].missing
                ? `the event has no ${sentence({ field: r.failed_because[0].field, op: 'exists' }).replace(' is present', '')}`
                : `the rule needs “${sentence(r.failed_because[0])}”, the event has ${pretty(r.failed_because[0].actual)}`}
            </Text>
          )}
          {!r.enabled && <Lozenge appearance="moved">Switched off</Lozenge>}
        </Inline>
      ))}
      {!result.rules.length && <Text color="color.text.subtle">Your team has no rules yet, so nothing can match.</Text>}
    </Stack>
  );
}

function WhyModal({ event, source, onClose }: { event: unknown; source: string; onClose: () => void }) {
  const { data, error } = useLoad(() => api<Explained>('/api/events/explain', { body: { event, source } }), [source]);
  return (
    <Modal onClose={onClose} width="large" shouldScrollInViewport>
      <ModalHeader hasCloseButton><ModalTitle>Why Herald did this</ModalTitle></ModalHeader>
      <ModalBody>
        <Stack space="space.200">
          {error ? <SectionMessage appearance="error"><Text>{error}</Text></SectionMessage> : data ? <Verdict result={data} /> : <Loading />}
          <Heading size="xsmall" as="h3">The event as it arrived</Heading>
          <CodeBlock language="json" text={pretty(event)} showLineNumbers={false} />
        </Stack>
      </ModalBody>
      <ModalFooter><Button appearance="primary" onClick={onClose}>Close</Button></ModalFooter>
    </Modal>
  );
}

function SendEvent({ platform }: { platform: Platform }) {
  const h = useHerald();
  const { data: samples } = useLoad(() => api<Record<string, unknown>>('/api/sim/samples').catch((): Record<string, unknown> => ({})), []);
  const options = useMemo<Option[]>(() => Object.keys(samples ?? {}).map((k) => ({ label: k.replace(/_/g, ' '), value: k })), [samples]);
  const [picked, setPicked] = useState<Option | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Explained | null>(null);
  const [problem, setProblem] = useState('');

  const guess = options.find((o) => o.value === SAMPLE_FOR[platform.id]) ?? options.find((o) => o.value === 'unseen_margin_call') ?? null;
  const current = picked ?? guess;
  const body = text || (current && samples ? JSON.stringify(samples[current.value], null, 2) : '');

  const send = async () => {
    let event: unknown;
    try { event = JSON.parse(body); } catch { setProblem('This is not valid JSON. Check the brackets and quotes.'); return; }
    setProblem(''); setBusy(true); setResult(null);
    const sent = await h.run(() => api('/api/sim/send', { body: { source: platform.id, event, count: 1 } }), 'Event sent');
    if (sent !== undefined) {
      try { setResult(await api<Explained>('/api/events/explain', { body: { event, source: platform.id } })); } catch { /* shown as a flag by run() on the next action */ }
    }
    setBusy(false);
  };

  return (
    <Stack space="space.200">
      <Text color="color.text.subtle">Send one event the way {platform.name} would, and see which rules react. Nothing here touches the real platform.</Text>
      <div style={{ maxWidth: 360 }}>
        <Label htmlFor="sample">Start from an example</Label>
        <Select<Option> inputId="sample" options={options} value={current} onChange={(o) => { setPicked(o); setText(''); setResult(null); }} placeholder="Choose an example" />
      </div>
      <div>
        <Label htmlFor="payload">Event (JSON)</Label>
        <TextArea id="payload" isMonospaced minimumRows={12} value={body} onChange={(e) => setText(e.currentTarget.value)} isInvalid={!!problem} />
        {problem ? <ErrorMessage>{problem}</ErrorMessage> : <HelperMessage>Change any value, or paste an event from your own system.</HelperMessage>}
      </div>
      <Inline><Button appearance="primary" onClick={send} isLoading={busy} isDisabled={!body.trim()}>Send event</Button></Inline>
      {result && (
        <FadeIn entranceDirection="bottom">
          {(motion) => (
            <div {...motion}>
              <SectionMessage appearance={result.rules.some((r) => r.passed) ? 'warning' : result.rules.some((r) => r.closes) ? 'success' : 'information'}
                title={result.rules.some((r) => r.passed) ? 'This event raises an alert' : result.rules.some((r) => r.closes) ? 'This event closes an open alert' : 'No rule reacts to this event'}>
                <Verdict result={result} />
              </SectionMessage>
            </div>
          )}
        </FadeIn>
      )}
    </Stack>
  );
}

function Setup({ platform }: { platform: Platform }) {
  const k = kind(platform);
  return (
    <Stack space="space.200">
      <dl className="kv">
        <dt>How it is connected</dt><dd>{k.label}. {k.how}</dd>
        {platform.type === 'webhook' && <><dt>Address</dt><dd><Code>{location.origin}/ingest/{platform.id}</Code></dd></>}
        {platform.type === 'webhook' && <><dt>Token</dt><dd>{platform.needs_token ? 'Required, sent in the X-Herald-Token header. It was shown once when the platform was connected.' : 'Not required for this platform in the demo.'}</dd></>}
        {platform.topic && <><dt>Topic</dt><dd><Code>{platform.topic}</Code></dd></>}
        {platform.path && <><dt>File</dt><dd><Code>{platform.path}</Code></dd></>}
        {platform.url && <><dt>Polled address</dt><dd><Code>{platform.url}</Code></dd></>}
        <dt>Set up in</dt><dd>{platform.managed === 'file' ? 'A configuration file (config/sources.yaml), reviewed like code' : 'This interface'}</dd>
      </dl>
      {platform.type === 'webhook' && (
        <Stack space="space.100">
          <Heading size="xsmall" as="h3">Try it from a terminal</Heading>
          <CodeBlock language="bash" showLineNumbers={false} text={curl(platform, null, '{"type":"EXAMPLE","severity":"WARN","message":"hello"}')} />
        </Stack>
      )}
    </Stack>
  );
}

const TABS = ['problems', 'events', 'send', 'setup'];

export function PlatformPage({ id, tab }: { id: string; tab?: string }) {
  const h = useHerald();
  const platform = h.platforms.find((p) => p.id === id);
  if (!platform) {
    return <Page title="Platform not found"><EmptyState header="There is no platform with this address" description="It may belong to another team, or it was never connected."
      primaryAction={<Button appearance="primary" onClick={() => go('/platforms')}>All platforms</Button>} /></Page>;
  }
  const k = kind(platform);
  const selected = Math.max(0, TABS.indexOf(tab ?? 'problems'));
  const pad = { width: '100%', paddingTop: 16 } as const;
  return (
    <Page title={platform.name}
      crumbs={<Breadcrumbs><BreadcrumbsItem href="#/platforms" text="Platforms" /><BreadcrumbsItem href={`#/platforms/${platform.id}`} text={platform.name} /></Breadcrumbs>}
      lead={`${k.label} · ${platform.firing} open, ${platform.acked} in progress · last event ${ago(platform.last_event_at)}`}>
      <Tabs id="platform-tabs" selected={selected} onChange={(index) => go(`/platforms/${platform.id}/${TABS[index]}`)}>
        <TabList>
          <Tab>Problems</Tab>
          <Tab>Events</Tab>
          <Tab>Send a test event</Tab>
          <Tab>Setup</Tab>
        </TabList>
        <TabPanel><div style={pad}><Alerts source={platform.id} /></div></TabPanel>
        <TabPanel><div style={pad}><Events platform={platform} /></div></TabPanel>
        <TabPanel><div style={pad}><SendEvent platform={platform} /></div></TabPanel>
        <TabPanel><div style={pad}><Setup platform={platform} /></div></TabPanel>
      </Tabs>
    </Page>
  );
}

export function ConnectPlatform({ onClose }: { onClose: () => void }) {
  const h = useHerald();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<{ platform: Platform; token: string } | null>(null);
  const id = slug(name);
  const taken = h.platforms.some((p) => p.id === id);

  const connect = async () => {
    setBusy(true);
    const r = await h.run(() => api<Record<string, any>>(`/api/platforms/${id}`, { method: 'PUT', body: { name: name.trim() } }), `${name.trim()} is connected`);
    setBusy(false);
    if (r) {
      setMade({ token: String(r.token ?? ''), platform: { id, name: name.trim(), icon: null, type: 'webhook', topic: null, path: null, managed: 'ui', needs_token: true, firing: 0, acked: 0, resolved: 0, last_event_at: null } });
    }
  };

  return (
    <Modal onClose={onClose} width="medium" shouldScrollInViewport>
      <ModalHeader hasCloseButton><ModalTitle>{made ? `${made.platform.name} is connected` : 'Connect a platform'}</ModalTitle></ModalHeader>
      <ModalBody>
        {made ? (
          <Stack space="space.200">
            <SectionMessage appearance="warning" title="Copy the token now">
              <Text>Herald keeps only a fingerprint of it, so it cannot be shown again.</Text>
            </SectionMessage>
            <CodeBlock language="bash" showLineNumbers={false} text={curl(made.platform, made.token, '{"type":"EXAMPLE","severity":"WARN","message":"hello"}')} />
            <Text color="color.text.subtle">Send this once. The event appears under the platform’s Events tab, and its fields become available when you write a rule.</Text>
          </Stack>
        ) : (
          <Stack space="space.150">
            <Text color="color.text.subtle">Any system that can post JSON over HTTPS can send events. You do not need to describe its format first: Herald learns the fields from the first event.</Text>
            <div>
              <Label htmlFor="platform-name">Name</Label>
              <Textfield id="platform-name" autoFocus value={name} onChange={(e) => setName(e.currentTarget.value)} placeholder="For example: Risk engine" isInvalid={taken} />
              {taken ? <ErrorMessage>A platform with this name is already connected.</ErrorMessage>
                : <HelperMessage>{id ? `Address: ${location.origin}/ingest/${id}` : 'The name people will see in the sidebar.'}</HelperMessage>}
            </div>
            {!h.canEdit && <SectionMessage appearance="information"><Text>Connecting a platform needs the rule editor role or higher.</Text></SectionMessage>}
          </Stack>
        )}
      </ModalBody>
      <ModalFooter>
        {made ? (
          <Button appearance="primary" onClick={() => { onClose(); go(`/platforms/${made.platform.id}/events`); }}>Go to the platform</Button>
        ) : (
          <>
            <Button appearance="subtle" onClick={onClose}>Cancel</Button>
            <Button appearance="primary" onClick={connect} isLoading={busy} isDisabled={id.length < 2 || taken}>Connect</Button>
          </>
        )}
      </ModalFooter>
    </Modal>
  );
}
