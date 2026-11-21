import { useState } from 'react';

import Badge from '@atlaskit/badge';
import Button from '@atlaskit/button/new';
import { Code } from '@atlaskit/code';
import { DropdownItem, DropdownItemGroup } from '@atlaskit/dropdown-menu';
import { DropdownMenu } from '@atlaskit/dropdown-menu/dropdown-menu';
import Heading from '@atlaskit/heading';
import Lozenge, { LozengeDropdownTrigger } from '@atlaskit/lozenge';
import Modal, { ModalBody, ModalFooter, ModalHeader, ModalTitle } from '@atlaskit/modal-dialog';
import { FadeIn, StaggeredEntrance } from '@atlaskit/motion';
import { Box, Inline, Stack, Text } from '@atlaskit/primitives/compiled';
import SectionMessage from '@atlaskit/section-message';
import Select from '@atlaskit/select';
import TextArea from '@atlaskit/textarea';

import { api, type AlertDetail, type Approval, type Step, type Why } from '../api';
import { useHerald, useLoad } from '../store';
import { ago, channelName, clock, DeliveryState, Field, Loading, pretty, Severity, STATUS, Who } from '../ui';

type Option = { label: string; value: string };

const ACTIONS: Record<string, string> = {
  ack: 'started work', acked: 'started work', resolve: 'resolved this', assigned: 'handed this over', note: 'wrote a note',
  approval_requested: 'asked for approval', approved: 'approved a step', rejected: 'refused a step', incident_manager: 'took charge', action_run: 'ran a runbook action', step_done: 'finished a runbook step', manual: 'finished a runbook step',
};

/* The conditions of the rule, one line each, with what the event actually had. */
function leaves(why: Why | null): Why[] {
  if (!why) return [];
  if (why.all) return why.all.flatMap(leaves);
  if (why.any) return why.any.flatMap(leaves);
  if (why.not) return leaves(why.not);
  return [why];
}

function StepRow({ step, index, detail, onRun, busy }: { step: Step; index: number; detail: AlertDetail; onRun: (step: Step) => void; busy: boolean }) {
  const { name } = useHerald();
  const state = detail.step_state?.[String(step.id)];
  const done = state?.state === 'done';
  const waiting = state?.state === 'awaiting_approval';
  const needsApproval = step.approval && step.approval !== 'none';
  const label = step.action ? (needsApproval ? 'Ask for approval' : 'Run') : needsApproval ? 'Ask for approval' : 'Mark done';
  return (
    <div className={`step${done ? ' done' : ''}`}>
      <div className="step-num">{done ? '✓' : index + 1}</div>
      <Stack space="space.050">
        <Text>{step.text}</Text>
        <Inline space="space.100" alignBlock="center" shouldWrap>
          {step.action ? <Lozenge appearance="new">Automatic: {step.action.replace(/_/g, ' ')}</Lozenge> : <Lozenge>Done by hand</Lozenge>}
          {needsApproval && <Lozenge appearance="moved">Needs a second person</Lozenge>}
          {waiting && <Lozenge appearance="inprogress">Waiting for approval</Lozenge>}
          {state?.state === 'rejected' && <Lozenge appearance="removed">Refused</Lozenge>}
        </Inline>
        {state?.result?.message && <Text size="small" color="color.text.subtle">Result: {state.result.message}{state.result.simulated ? ' (simulated)' : ''}</Text>}
        {done && state?.by && <Text size="small" color="color.text.subtlest">Done by {name(state.by)}{state.approved_by ? `, approved by ${name(state.approved_by)}` : ''}</Text>}
      </Stack>
      {!done && !waiting && detail.status !== 'resolved' && <Button onClick={() => onRun(step)} isLoading={busy}>{label}</Button>}
    </div>
  );
}

export function AlertModal({ id, onClose }: { id: number; onClose: () => void }) {
  const h = useHerald();
  const { data: detail, error } = useLoad(() => api<AlertDetail>(`/api/alerts/${id}`), [id]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (what: string, work: () => Promise<unknown>, done?: string) => {
    setBusy(what);
    await h.run(work, done);
    setBusy(null);
  };

  if (!detail) {
    return (
      <Modal onClose={onClose} width="x-large">
        <ModalHeader hasCloseButton><ModalTitle>Alert</ModalTitle></ModalHeader>
        <ModalBody>{error ? <SectionMessage appearance="error" title="This alert cannot be shown"><Text>{error}</Text></SectionMessage> : <Loading />}</ModalBody>
        <ModalFooter><Button appearance="subtle" onClick={onClose}>Close</Button></ModalFooter>
      </Modal>
    );
  }

  const platform = h.platforms.find((p) => p.id === detail.source);
  const rule = h.rules.find((r) => r.doc.id === detail.rule_id);
  const team = detail.owner.startsWith('team:') ? h.teams[detail.owner.slice(5)] : undefined;
  const candidates = [...new Set([...(team?.members ?? []), ...Object.keys(h.people)])].filter((p) => p !== 'loadbot' && h.people[p]);
  const options: Option[] = candidates.map((p) => ({ label: h.people[p].name + (team?.on_call === p ? ' (on call)' : ''), value: `person:${p}` }));
  const pending = detail.approvals.filter((a: Approval) => a.status === 'pending');
  const conditions = leaves(detail.why);

  return (
    <Modal onClose={onClose} width="x-large" shouldScrollInViewport>
      <ModalHeader hasCloseButton>
        <Stack space="space.050">
          <Inline space="space.100" alignBlock="center">
            <Text size="small" weight="semibold" color="color.text.subtle">{platform?.name ?? 'Alert'} / {detail.key}</Text>
            <Severity value={detail.severity} />
            {detail.count > 1 && <Badge appearance="primary" max={9999}>{detail.count}</Badge>}
          </Inline>
          <ModalTitle>{detail.title}</ModalTitle>
        </Stack>
      </ModalHeader>
      <ModalBody>
        <div className="issue">
          <Stack space="space.300">
            {detail.body && <Text>{detail.body}</Text>}

            {pending.map((a) => {
              const step = detail.runbook?.steps.find((s) => s.id === a.step_id);
              const own = a.requested_by === h.myRef;
              return (
                <SectionMessage key={a.id} appearance="warning" title={`${h.name(a.requested_by)} asks for approval`}>
                  <Stack space="space.100">
                    <Text>{step?.text ?? a.action}</Text>
                    {h.canApprove && !own ? (
                      <Inline space="space.100">
                        <Button appearance="primary" isLoading={busy === `ok${a.id}`} onClick={() => act(`ok${a.id}`, () => api(`/api/approvals/${a.id}/decide`, { body: { approve: true } }), 'Approved. The step has run.')}>Approve</Button>
                        <Button isLoading={busy === `no${a.id}`} onClick={() => act(`no${a.id}`, () => api(`/api/approvals/${a.id}/decide`, { body: { approve: false } }), 'Refused')}>Refuse</Button>
                      </Inline>
                    ) : (
                      <Text size="small" color="color.text.subtle">
                        {own ? 'You asked for this, so another person has to approve it.' : `Waiting for ${a.approver ? h.name(a.approver) : 'an approver'}. They can also reply to the text message they received.`}
                      </Text>
                    )}
                  </Stack>
                </SectionMessage>
              );
            })}

            <Stack space="space.100">
              <Heading size="small" as="h3">What happened</Heading>
              <dl className="kv">
                {Object.entries(detail.fields).map(([k, v]) => (
                  <FieldRow key={k} k={k} v={v} strong={detail.highlight?.includes(k)} />
                ))}
              </dl>
            </Stack>

            {detail.runbook && (
              <Stack space="space.100">
                <Inline space="space.100" alignBlock="center">
                  <Heading size="small" as="h3">What to do</Heading>
                  <Text size="small" color="color.text.subtle">Runbook: {detail.runbook.title}</Text>
                </Inline>
                {detail.runbook.steps.length ? (
                  <StaggeredEntrance columns={1} delayStep={50}>
                    {detail.runbook.steps.map((s, i) => (
                      <FadeIn key={s.id} entranceDirection="left">
                        {(motion) => (
                          <div {...motion} style={{ ...motion.style, marginBottom: 8 }}>
                            <StepRow step={s} index={i} detail={detail} busy={busy === `step${s.id}`}
                              onRun={(step) => act(`step${step.id}`, () => api<{ state: string }>(`/api/alerts/${id}/steps/${step.id}/run`, { method: 'POST' }))} />
                          </div>
                        )}
                      </FadeIn>
                    ))}
                  </StaggeredEntrance>
                ) : <Text>{detail.runbook.text ?? 'This runbook has no steps yet.'}</Text>}
              </Stack>
            )}

            {conditions.length > 0 && (
              <Stack space="space.100">
                <Heading size="small" as="h3">Why this fired</Heading>
                <Text size="small" color="color.text.subtle">Rule: {rule?.doc.name ?? detail.rule_id}</Text>
                {conditions.map((c, i) => (
                  <Inline key={i} space="space.100" alignBlock="center" shouldWrap>
                    <Lozenge appearance={c.passed ? 'success' : 'removed'}>{c.passed ? 'Yes' : 'No'}</Lozenge>
                    <Code>{c.field} {c.op} {pretty(c.value)}</Code>
                    <Text size="small" color="color.text.subtle">{c.missing ? 'the event had no such field' : `the event had ${pretty(c.actual)}`}</Text>
                  </Inline>
                ))}
              </Stack>
            )}

            <Stack space="space.150">
              <Heading size="small" as="h3">Activity</Heading>
              <Stack space="space.100">
                <TextArea aria-label="Add a note" placeholder="Add a note for the next person: what you saw, what you decided" value={note} onChange={(e) => setNote(e.currentTarget.value)} minimumRows={2} />
                <Inline>
                  <Button isDisabled={!note.trim()} isLoading={busy === 'note'}
                    onClick={() => act('note', async () => { await api(`/api/alerts/${id}/notes`, { body: { text: note.trim() } }); setNote(''); })}>Save note</Button>
                </Inline>
              </Stack>
              <div className="timeline">
                {[...detail.timeline].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).map((t, i) => (
                  <div key={i} className="timeline-row">
                    <Who who={t.actor} size="small" bare />
                    <Stack space="space.025">
                      <Text>
                        <Text weight="medium">{h.name(t.actor)}</Text> {ACTIONS[t.action] ?? t.action.replace(/_/g, ' ')}
                        {t.action === 'assigned' ? ` to ${t.detail.to ? h.name(String(t.detail.to)) : 'nobody'}` : ''}
                        <Text color="color.text.subtlest"> · {ago(t.at)}</Text>
                      </Text>
                      {typeof t.detail.text === 'string' && <Text color="color.text.subtle">“{t.detail.text}”</Text>}
                    </Stack>
                  </div>
                ))}
                {!detail.timeline.length && <Text color="color.text.subtlest">Nobody has touched this alert yet.</Text>}
              </div>
            </Stack>
          </Stack>

          <div className="panel">
            <Stack space="space.150">
              <Heading size="xsmall" as="h3">Details</Heading>
              <Field label="Status">
                <DropdownMenu<HTMLButtonElement> shouldRenderToParent
                  trigger={({ triggerRef, ...props }) => (
                    <LozengeDropdownTrigger ref={triggerRef} {...props} appearance={detail.status === 'resolved' ? 'success' : detail.status === 'acked' ? 'information' : 'neutral'} isLoading={busy === 'status'}>
                      {STATUS[detail.status]}
                    </LozengeDropdownTrigger>
                  )}>
                  <DropdownItemGroup>
                    <DropdownItem isDisabled={detail.status !== 'firing'} onClick={() => act('status', () => api(`/api/alerts/${id}/ack`, { method: 'POST' }), `${detail.key} is in progress`)} description="Tells the team someone is on it">In progress</DropdownItem>
                    <DropdownItem isDisabled={detail.status === 'resolved'} onClick={() => act('status', () => api(`/api/alerts/${id}/resolve`, { method: 'POST' }), `${detail.key} resolved`)} description="The problem is fixed">Resolved</DropdownItem>
                  </DropdownItemGroup>
                </DropdownMenu>
              </Field>
              <Field label="Assignee">
                <Select<Option> spacing="compact" isClearable placeholder="Unassigned" aria-label="Assignee" menuPosition="fixed"
                  value={options.find((o) => o.value === detail.incident_manager) ?? null} options={options} isLoading={busy === 'assign'}
                  onChange={(o) => act('assign', () => api(`/api/alerts/${id}/assign`, { body: { to: o?.value ?? null } }), o ? `Handed to ${o.label.replace(' (on call)', '')}. They have been told.` : 'Unassigned')} />
              </Field>
              <Field label="Team"><Text>{h.name(detail.owner)}</Text></Field>
              <Field label="Platform">{platform ? <a href={`#/platforms/${platform.id}`}>{platform.name}</a> : <Text color="color.text.subtlest">Unknown</Text>}</Field>
              <Field label="Events"><Text>{detail.count}</Text></Field>
              <Field label="First seen"><Text>{clock(detail.first_seen)}</Text></Field>
              <Field label="Last seen"><Text>{clock(detail.last_seen)}</Text></Field>
              <Box paddingBlockStart="space.100"><Heading size="xxsmall" as="h4">Who was told</Heading></Box>
              <Stack space="space.100">
                {detail.deliveries.map((d, i) => (
                  <Inline key={i} spread="space-between" alignBlock="center" space="space.100">
                    <Stack space="space.0">
                      <Text>{h.name(d.recipient)}</Text>
                      <Text size="small" color="color.text.subtle">{channelName(d.channel)}{d.attempts > 1 ? `, ${d.attempts} tries` : ''}</Text>
                    </Stack>
                    <DeliveryState value={d.status} />
                  </Inline>
                ))}
                {!detail.deliveries.length && <Text color="color.text.subtlest">Nobody yet.</Text>}
              </Stack>
            </Stack>
          </div>
        </div>
      </ModalBody>
      <ModalFooter>
        <Button appearance="subtle" onClick={onClose}>Close</Button>
        {detail.status === 'firing' && <Button isLoading={busy === 'ack'} onClick={() => act('ack', () => api(`/api/alerts/${id}/ack`, { method: 'POST' }), `${detail.key} is in progress`)}>Start work</Button>}
        {detail.status !== 'resolved' && <Button appearance="primary" isLoading={busy === 'resolve'} onClick={() => act('resolve', () => api(`/api/alerts/${id}/resolve`, { method: 'POST' }), `${detail.key} resolved`)}>Resolve</Button>}
      </ModalFooter>
    </Modal>
  );
}

function FieldRow({ k, v, strong }: { k: string; v: unknown; strong?: boolean }) {
  return (
    <>
      <dt>{k}</dt>
      <dd><Text weight={strong ? 'semibold' : 'regular'}>{pretty(v)}</Text></dd>
    </>
  );
}
