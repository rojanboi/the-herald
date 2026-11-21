/* The reference screens: rules, runbooks, teams, sent messages and the activity log. */
import { Fragment, useState } from 'react';

import AvatarGroup from '@atlaskit/avatar-group';
import Button, { LinkButton } from '@atlaskit/button/new';
import { Code, CodeBlock } from '@atlaskit/code';
import EmptyState from '@atlaskit/empty-state';
import Heading from '@atlaskit/heading';
import Lozenge from '@atlaskit/lozenge';
import Modal, { ModalBody, ModalFooter, ModalHeader, ModalTitle, ModalTransition } from '@atlaskit/modal-dialog';
import { FadeIn, StaggeredEntrance } from '@atlaskit/motion';
import { Inline, Stack, Text } from '@atlaskit/primitives/compiled';
import SectionMessage from '@atlaskit/section-message';

import { api, type AuditEntry, type Delivery, type RuleRow, type Runbook } from '../api';
import { Table } from '../Table';
import { useHerald, useLoad } from '../store';
import { ago, channelName, clock, DeliveryState, face, Loading, openAlert, Page, pretty, sentence, Severity, Who } from '../ui';

/* ---- rules ------------------------------------------------------------------------------ */
function RuleModal({ row, onClose }: { row: RuleRow; onClose: () => void }) {
  const h = useHerald();
  const r = row.doc;
  return (
    <Modal onClose={onClose} width="large" shouldScrollInViewport>
      <ModalHeader hasCloseButton><ModalTitle>{r.name}</ModalTitle></ModalHeader>
      <ModalBody>
        <Stack space="space.200">
          <Inline space="space.100" alignBlock="center">
            <Severity value={r.severity} />
            <Lozenge appearance={r.enabled ? 'success' : 'default'}>{r.enabled ? 'On' : 'Off'}</Lozenge>
            <Text size="small" color="color.text.subtle">{h.name(r.owner)} · version {row.version} · {row.source === 'file' ? 'kept in a configuration file' : 'made in the interface'}</Text>
          </Inline>
          <dl className="kv">
            <dt>When an event</dt><dd>{sentence(r.when)}</dd>
            {r.group?.by?.length ? <><dt>One alert per</dt><dd>{r.group.by.join(', ')}{r.group.window ? `, every ${r.group.window}` : ''}</dd></> : null}
            <dt>Tell</dt><dd>{r.notify.map((n) => `${h.name(n.to)}${n.channels?.length ? ` by ${n.channels.map(channelName).join(', ').toLowerCase()}` : ' on their preferred channel'}`).join('; ')}</dd>
            {r.resolve_when && <><dt>Closes itself when</dt><dd>{sentence(r.resolve_when)}</dd></>}
            {r.runbook && <><dt>Runbook</dt><dd>{r.runbook}</dd></>}
            {r.present?.title && <><dt>Alert title</dt><dd><Code>{r.present.title}</Code></dd></>}
          </dl>
          <SectionMessage appearance="information">
            <Text>This view is read-only for now. To change the rule, open the rule editor in the classic interface.</Text>
          </SectionMessage>
        </Stack>
      </ModalBody>
      <ModalFooter>
        <LinkButton appearance="subtle" href="/">Open classic interface</LinkButton>
        <Button appearance="primary" onClick={onClose}>Close</Button>
      </ModalFooter>
    </Modal>
  );
}

export function Rules() {
  const h = useHerald();
  const [open, setOpen] = useState<RuleRow | null>(null);
  return (
    <Page title="Rules" lead="A rule says which events become alerts and who is told. Rules are plain data, so a new one works within a second and needs no deployment.">
      <Table
        head={{ cells: [
          { key: 'name', content: 'Rule', isSortable: true, width: 22 },
          { key: 'when', content: 'When an event' },
          { key: 'severity', content: 'Severity', width: 9 },
          { key: 'owner', content: 'Team', isSortable: true, width: 13 },
          { key: 'alerts', content: 'Alerts', isSortable: true, width: 7 },
          { key: 'fired', content: 'Last fired', isSortable: true, width: 12 },
          { key: 'on', content: 'State', width: 7 },
        ] }}
        rows={h.rules.map((row) => ({
          key: row.doc.id,
          className: 'clickable-row',
          onClick: () => setOpen(row),
          cells: [
            { key: row.doc.name, content: <Text weight="medium">{row.doc.name}</Text> },
            { key: 'w', content: <Text color="color.text.subtle" maxLines={2}>{sentence(row.doc.when)}</Text> },
            { key: 's', content: <Severity value={row.doc.severity} /> },
            { key: h.name(row.doc.owner), content: h.name(row.doc.owner) },
            { key: row.alerts, content: row.alerts },
            { key: row.last_fired ? Date.parse(row.last_fired) : 0, content: ago(row.last_fired) },
            { key: 'on', content: <Lozenge appearance={row.doc.enabled ? 'success' : 'default'}>{row.doc.enabled ? 'On' : 'Off'}</Lozenge> },
          ],
        }))}
        rowsPerPage={15}
        emptyView={<EmptyState header="No rules yet" description="Without rules, events are stored but nobody is told." />}
      />
      <ModalTransition>{open && <RuleModal row={open} onClose={() => setOpen(null)} />}</ModalTransition>
    </Page>
  );
}

/* ---- runbooks --------------------------------------------------------------------------- */
const FORMAT: Record<string, string> = { yaml: 'Structured steps', markdown: 'Markdown page', text: 'Free text', link: 'Link only' };

export function Runbooks() {
  const { data } = useLoad(() => api<Runbook[]>('/api/runbooks'), []);
  const [open, setOpen] = useState<Runbook | null>(null);
  return (
    <Page title="Runbooks" lead="What to do when an alert fires. Herald accepts a runbook in any form, from structured steps to a paragraph someone wrote in a hurry.">
      {!data ? <Loading /> : (
        <div className="tiles">
          <StaggeredEntrance columns="responsive" delayStep={40}>
            {data.map((rb) => (
              <FadeIn key={rb.id} entranceDirection="bottom">
                {(motion) => (
                  <div {...motion}>
                    <a className="tile" href={location.hash} onClick={(e) => { e.preventDefault(); setOpen(rb); }}>
                      <Heading size="small" as="h2">{rb.id}</Heading>
                      <Text color="color.text.subtle" maxLines={2}>{rb.title}</Text>
                      <Inline space="space.100" shouldWrap>
                        <Lozenge>{FORMAT[rb.source_format] ?? rb.source_format}</Lozenge>
                        <Lozenge appearance="inprogress">{rb.steps.length} {rb.steps.length === 1 ? 'step' : 'steps'}</Lozenge>
                        {rb.steps.some((s) => s.approval && s.approval !== 'none') && <Lozenge appearance="moved">Has an approval</Lozenge>}
                      </Inline>
                      <Text size="small" color="color.text.subtlest">{rb.used_by.length ? `Used by: ${rb.used_by.join(', ')}` : 'Not used by a rule yet'}</Text>
                    </a>
                  </div>
                )}
              </FadeIn>
            ))}
          </StaggeredEntrance>
        </div>
      )}
      <ModalTransition>
        {open && (
          <Modal onClose={() => setOpen(null)} width="large" shouldScrollInViewport>
            <ModalHeader hasCloseButton><ModalTitle>{open.id}</ModalTitle></ModalHeader>
            <ModalBody>
              <Stack space="space.200">
                <Heading size="xsmall" as="h3">Steps as Herald understood them</Heading>
                <Stack space="space.100">
                  {open.steps.map((s, i) => (
                    <div key={s.id} className="step">
                      <div className="step-num">{i + 1}</div>
                      <Stack space="space.050">
                        <Text>{s.text}</Text>
                        <Inline space="space.100" shouldWrap>
                          {s.action ? <Lozenge appearance="new">Automatic: {s.action.replace(/_/g, ' ')}</Lozenge> : <Lozenge>Done by hand</Lozenge>}
                          {s.approval && s.approval !== 'none' && <Lozenge appearance="moved">Needs a second person</Lozenge>}
                          {s.condition && <Lozenge appearance="inprogress">Only if: {s.condition}</Lozenge>}
                        </Inline>
                      </Stack>
                      <span />
                    </div>
                  ))}
                </Stack>
                <Heading size="xsmall" as="h3">As it was written</Heading>
                <CodeBlock language={open.source_format === 'yaml' ? 'yaml' : open.source_format === 'markdown' ? 'markdown' : 'text'} showLineNumbers={false} text={open.source_text || ''} />
              </Stack>
            </ModalBody>
            <ModalFooter><Button appearance="primary" onClick={() => setOpen(null)}>Close</Button></ModalFooter>
          </Modal>
        )}
      </ModalTransition>
    </Page>
  );
}

/* ---- teams ------------------------------------------------------------------------------ */
const SEVERITIES = ['critical', 'error', 'warning', 'info'];

export function Teams() {
  const h = useHerald();
  const teams = Object.values(h.teams).filter((t) => t.id !== 'loadtest');
  return (
    <Page title="Teams" lead="Who is on call, and how each team wants to be told. A critical alert rings a phone; a warning waits in email.">
      <div className="tiles">
        <StaggeredEntrance columns="responsive" delayStep={40}>
          {teams.map((t) => (
            <FadeIn key={t.id} entranceDirection="bottom">
              {(motion) => (
                <div {...motion}>
                  <div className="tile">
                    <Inline spread="space-between" alignBlock="center">
                      <Heading size="small" as="h2">{t.name}</Heading>
                      <AvatarGroup appearance="stack" size="small" maxCount={4} data={t.members.map((m) => ({ key: m, name: h.people[m]?.name ?? m, src: face(h.people[m]?.name ?? m) }))} />
                    </Inline>
                    <dl className="kv">
                      <dt>On call now</dt><dd>{t.on_call ? <Who who={`person:${t.on_call}`} /> : 'Nobody'}</dd>
                      <dt>Team lead</dt><dd>{t.lead ? <Who who={`person:${t.lead}`} /> : 'Nobody'}</dd>
                      {t.rota.length > 1 && <><dt>Rota</dt><dd>{t.rota.map((m) => h.people[m]?.name ?? m).join(' → ')}, {t.shift_days} days each</dd></>}
                    </dl>
                    <Stack space="space.075">
                      <Text size="small" weight="semibold" color="color.text.subtle">How they are told, in this order</Text>
                      <div className="prefs">
                        {SEVERITIES.filter((s) => t.preferences?.[s]?.length).map((s) => (
                          <Fragment key={s}>
                            <span><Severity value={s} /></span>
                            <Text size="small">{t.preferences[s].map(channelName).join(' → ')}</Text>
                          </Fragment>
                        ))}
                      </div>
                    </Stack>
                  </div>
                </div>
              )}
            </FadeIn>
          ))}
        </StaggeredEntrance>
      </div>
    </Page>
  );
}

/* ---- sent messages ---------------------------------------------------------------------- */
export function Sent() {
  const h = useHerald();
  const { data, error } = useLoad(() => api<Delivery[]>(`/api/deliveries?limit=100${h.owner ? `&owner=${encodeURIComponent(h.owner)}` : ''}`), [h.owner]);
  return (
    <Page title="Sent messages" lead="Every message Herald tried to deliver, with how long it took from the event arriving to the person being told.">
      {error && <SectionMessage appearance="error"><Text>{error}</Text></SectionMessage>}
      {!data ? !error && <Loading /> : (
        <Table
          head={{ cells: [
            { key: 'at', content: 'When', isSortable: true, width: 13 },
            { key: 'to', content: 'To', isSortable: true, width: 17 },
            { key: 'channel', content: 'Channel', isSortable: true, width: 13 },
            { key: 'alert', content: 'About' },
            { key: 'status', content: 'Result', isSortable: true, width: 11 },
            { key: 'latency', content: 'Took', isSortable: true, width: 9 },
          ] }}
          rows={data.map((d) => ({
            key: String(d.id),
            className: 'clickable-row',
            onClick: () => d.alert_id && openAlert(d.alert_id),
            cells: [
              { key: Date.parse(d.created_at ?? ''), content: clock(d.created_at) },
              { key: h.name(d.recipient), content: <Who who={d.recipient} /> },
              { key: d.channel, content: channelName(d.channel) },
              { key: d.title ?? '', content: <Stack space="space.0"><Text>{d.title}</Text>{d.last_error && <Text size="small" color="color.text.danger">{d.last_error}</Text>}</Stack> },
              { key: d.status, content: <DeliveryState value={d.status} /> },
              { key: Number(d.latency_ms ?? 0), content: d.latency_ms ? `${Math.round(Number(d.latency_ms))} ms` : '' },
            ],
          }))}
          rowsPerPage={15}
          defaultSortKey="at"
          defaultSortOrder="DESC"
          emptyView={<EmptyState header="Nothing sent yet" description="Messages appear here as soon as a rule tells someone about an alert." />}
        />
      )}
    </Page>
  );
}

/* ---- activity log ----------------------------------------------------------------------- */
const DID: Record<string, string> = {
  ack: 'started work on', acked: 'started work on', resolve: 'resolved', assigned: 'handed over', note: 'wrote a note on',
  approval_requested: 'asked for approval on', action_run: 'ran a runbook action on', step_done: 'finished a runbook step on',
  incident_manager: 'took charge of', approved: 'approved a step on', rejected: 'refused a step on',
  rule_saved: 'saved the rule', runbook_saved: 'saved the runbook', platform_connected: 'connected the platform', login: 'signed in', logout: 'signed out', login_denied: 'was refused sign-in',
};

export function Activity() {
  const h = useHerald();
  const { data, error } = useLoad(() => api<{ intact: boolean; entries: AuditEntry[] }>('/api/audit?limit=300'), [h.myRef]);
  const target = (t: string) => {
    const [kind, id] = t.split(':');
    if (kind === 'alert') return h.alerts.find((a) => String(a.id) === id)?.key ?? `alert ${id}`;
    return id ?? t;
  };
  return (
    <Page title="Activity log" lead="Who did what, and when. Entries are chained together, so a changed or removed entry is detected.">
      {error ? (
        <SectionMessage appearance="information" title="The activity log is for team leads">
          <Text>{error}. Sign in, or view as a team lead, to read it.</Text>
        </SectionMessage>
      ) : !data ? <Loading /> : (
        <Stack space="space.200">
          <SectionMessage appearance={data.intact ? 'success' : 'error'} title={data.intact ? 'The log is intact' : 'The log has been tampered with'}>
            <Text>{data.intact ? `All ${data.entries.length} entries link to the one before, as they should.` : 'At least one entry does not link to the one before it.'}</Text>
          </SectionMessage>
          <Table
            head={{ cells: [
              { key: 'at', content: 'When', isSortable: true, width: 14 },
              { key: 'actor', content: 'Who', isSortable: true, width: 18 },
              { key: 'what', content: 'What' },
              { key: 'detail', content: 'Detail', width: 30 },
            ] }}
            rows={data.entries.map((e) => ({
              key: String(e.id),
              cells: [
                { key: Date.parse(e.at), content: clock(e.at) },
                { key: h.name(e.actor), content: <Who who={e.actor} /> },
                { key: e.action, content: <Text>{DID[e.action] ?? e.action.replace(/_/g, ' ')} <Text weight="medium">{target(e.target)}</Text></Text> },
                { key: 'd', content: <Text size="small" color="color.text.subtle" maxLines={2}>{Object.entries(e.detail ?? {}).map(([k, v]) => `${k}: ${k === 'to' && v ? h.name(String(v)) : pretty(v)}`).join(' · ')}</Text> },
              ],
            }))}
            rowsPerPage={15}
            defaultSortKey="at"
            defaultSortOrder="DESC"
          />
        </Stack>
      )}
    </Page>
  );
}
