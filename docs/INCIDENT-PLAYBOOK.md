# Herald and incident-management practice

Design input drawn from the Atlassian Incident Management Handbook (2019, 71 PDF pages; printed page numbers equal PDF page numbers). References look like "p26". The handbook is written for 24/7 software services; Herald is an alerting service for a bank. Where the handbook says "incident", read "a critical alert that needs a coordinated human response".

---

## 1. The handbook in one page

**What it is (p12-13).** A summary of one company's process, meant to be adapted. An incident is an event that disrupts or degrades a service and needs an emergency response. It is resolved when the service works normally again; finding the root cause is *not* part of resolution, it belongs to the postmortem.

**Mindset (p7-10).** Incidents cannot be driven to zero, because change brings risk. Judge yourself on how well you respond and how well you learn. A good process is fast, predictable and simple enough to follow under stress.

### Incident values (p14-15)

| Stage | Value in short | Idea |
|---|---|---|
| Detect | We know before customers do | Monitoring should warn before a problem becomes an incident |
| Respond | Escalate early and often | Nobody minds a needless wake-up; they do mind a missing one |
| Recover | Clean up quickly | Customers care about restoration, not the reason |
| Learn | Always blameless | Hold teams accountable, do not assign blame |
| Improve | Never have the same incident twice | Fix the whole class of problem, with named changes and dates |

### Roles (p18, p32)

| Role | Job |
|---|---|
| Incident manager (IM) | Owns the incident, has authority to do whatever is needed, pages people, keeps everyone focused. A role, not a person, so people are interchangeable |
| Tech lead | Senior technical responder: forms theories, decides on changes, runs the technical team |
| Communications manager | Writes and sends internal and external updates |
| Extra roles | IM can create more (several tech leads, split internal and external comms, a second IM as a "sanity check" who can keep the timeline) |
| Postmortem owner (p43) | One accountable person, usually from the team that owns the failing service |

### Severity (p26)

| Level | Meaning | Examples in the handbook |
|---|---|---|
| 1 | Critical, very high impact | Whole customer-facing service down; confidentiality breach; customer data loss |
| 2 | Major, significant impact | Service down for a subset of customers; core function badly impaired |
| 3 | Minor, low impact | Small inconvenience with a workaround; usable performance degradation |

How it is decided (p25-26): whoever reports picks a first guess (levels 1-2 trigger a page, p21). The IM then asks six questions: what is the customer impact, what do customers see, how many are affected, when did it start, how many support cases are open, and are there other factors (public attention, security, data loss). The IM confirms or adjusts the level and tells the team. Levels 1-2 get immediate, continuous 24/7 handling; level 3 goes to the owning team, normally in business hours. The matrix should be written down and agreed across teams (p27).

### Response stages in order (p20, p22-37)

| # | Stage | What happens | Pages |
|---|---|---|---|
| 1 | Detect | Alert, customer report or observation; log an incident record with summary, impact, severity, faulty service, affected products | p20-22 |
| 2 | Open communications | IM takes ownership, marks the incident as being fixed, creates chat room, video call and a shared "state document"; room topic shows summary, severity, who holds which role | p22-25 |
| 3 | Assess | Answer the six severity questions; confirm severity | p25-27 |
| 4 | Send initial communications | Internal and external notice quickly, from templates (cheat sheet says within 30 minutes) | p27-28, p69 |
| 5 | Escalate | Page other teams via on-call rosters; always include the incident key | p31 |
| 6 | Delegate | Hand out roles explicitly | p31-32 |
| 7 | Send follow-up communications | Regular updates in a fixed shape (see section 2) | p33-35 |
| 8 | Iterate | Observe, form theories, run experiments, repeat; watch team fatigue and plan handovers | p35-36 |
| 9 | Resolve | Business impact has ended; final comms; record start, detection and end times (gives time-to-detect and time-to-recover); start the postmortem | p36-37 |

Incident states are only three: pending (no manager yet), fixing, resolved (p17, p20, p22).

### Postmortem process (p41-66)

* Purpose (p42): record impact, actions taken, causes and follow-up actions, to learn and prevent repeats.
* Needed for severity 1 and 2, optional for 3 (p43). One owner is accountable (p43, p45).
* Blameless (p44-45): people are assumed to act in good faith; ask why the system allowed or encouraged the action; refer to roles rather than names; frame timeline and causes in terms of systems and processes.
* Steps (p46-47): create the postmortem linked to the incident, fill the fields, use "five whys" to find causes, hold a meeting, get time-bound commitments from managers afterwards, raise a ticket per action, get approval, publish what was learned.
* Fields (p48-53): summary, lead-up, fault, impact, detection, response, recovery, timeline, five whys, root cause, backlog check, recurrence, lessons learned, corrective actions.
* Proximate vs root cause (p54-55): proximate is what directly triggered it; root is the best point in the chain to fix to stop the whole class.
* Cause categories (p56-57): bug, change, scale, architecture, dependency, unknown, each with a typical remedy. External suppliers are judged against what you reasonably expected of them (p57-59).
* Action types (p60-61): investigate, mitigate, repair damage, detect future, mitigate future, prevent future. At least one of the last two should exist. Good actions are actionable, specific and bounded (p61-62).
* Meeting (p62-64): remind of blamelessness, walk the timeline, present theory, brainstorm open-ended, ask what went well / could be better / where we were lucky.
* Approval and tracking (p65-66): approvers agree with the findings and that the "priority actions" address the root cause; every action becomes a ticket linked as either priority or improvement. A target time to finish significant actions exists (p45, 8 weeks in most cases).

---

## 2. Mapping: handbook concept to Herald

Priority is for a 10-hour, two-person build: **Must** = build, **Should** = build if time remains, **Could** = mention in the pitch or stub.

| Handbook concept | Herald today | Gap | Proposed change | Priority |
|---|---|---|---|---|
| Severity levels 1-3 with written definitions (p26-27) | Five levels `debug..critical`, set by rule | No written meaning; people will pick levels inconsistently. Handbook has no equivalent for debug/info | Keep our five. Write a one-line definition and an example per level, and align: critical = sev 1, error = sev 2, warning = sev 3, info/debug = no page. Show it next to the severity field in the rule editor (addition A5) | Must |
| Severity decided by impact questions (p25) | Rule sets severity from event data | Nothing captures "how many customers affected" or "support cases" | Optional rule fields `impact_note` and `affected_count` taken from the event; show them in alert detail. Do not try to automate judgement | Could |
| Incident states pending / fixing / resolved (p17, p20) | `firing / acked / resolved` | Close match already. "Pending" (no owner yet) is the only extra idea | Map firing=pending, acked=fixing, resolved=resolved. Add one derived label "unowned" for firing alerts with no ack after N minutes; no new status value | Should |
| Incident manager (p18, p22) | Owner (team/person) plus the person who acks | No single named person in charge of a critical alert; ack is just a click | `incident_manager` field, set automatically to whoever acks a critical alert, shown in header, changeable (A1) | Must |
| Other roles: tech lead, communications manager (p32) | Team lead exists in routing | No way to name who writes updates | Optional `comms_owner` and `tech_lead` fields on critical alerts, default empty. Reuse "role:team-lead" approval for IM changes | Could |
| Role changes recorded in room topic (p24, p32) | Audit log | Role changes would not be visible | Every change of IM or role writes an audit entry; alert header shows current holders | Should |
| Escalation and paging (p31) | Rota, team lead, channel fallback, `escalate` action | Escalation is a runbook step, not a timer | Ack deadline by severity (e.g. critical 5 minutes); if still `firing`, auto-escalate to the team lead then to the next channel. Reuse existing `wait` + `escalate` | Must |
| Escalate early without hesitation (p14) | Retries and fallback per delivery | Fallback is about delivery, not about "get more people" | Add a visible "Escalate / bring in someone" button on alert detail that calls the existing action | Should |
| Initial and follow-up communications, internal and external (p27-28, p33-35) | Deliveries to UI, email, SMS, phone | Herald notifies responders, but has no concept of a status update to stakeholders | Communication steps in runbooks plus a status-update template rendered from the alert (A2, A3). Internal only in the build; external is a rendered draft a human sends | Must |
| Update cadence (p33, p69 cheat sheet: initial within 30 min, then at most every 180 min) | `wait` step exists | No reminders to post updates | Runbook step kind `check` with `repeat_every` plus a reminder delivery to the IM until resolved (A2). Choose cadence by severity, e.g. critical every 30 min for a demo | Must |
| Update templates (p27, p33-34) | Title/body rendering from event | No update template or completeness checklist | One template with: summary, current status (2-4 bullets), next steps (2-4 bullets), next update time, plus a five-point checklist (impact, how many affected, root cause if known, ETA, next update) shown as ticks (A3). Say "unknown" explicitly (p34) | Must |
| Stable incident key everywhere (p21-24) | Alert id | Id may not be human-friendly | Give each alert a short key like `AL-0042` used in titles, SMS, emails and audit. Costs minutes | Should |
| Incident timeline (p24, p51) | Hash-chained audit log; first/last seen; per-step state | Audit is by action, not a readable story; no free-text notes | Alert detail "Timeline" tab that merges audit entries, deliveries and step changes in time order, plus a note box ("noted observation / decision") that writes to the audit log (A4 relies on it) | Must |
| Handover (p36, p70) | Rota, team lead | Nothing helps a new person pick up a live critical alert | "Hand over" button: choose a person, write who holds which role, current theory, next check; writes an audit entry and notifies them with a generated summary | Should |
| Fatigue watch (p36) | None | Ack time is known, so alert age can be shown | Show "owner on this for X h" in the header; amber after a threshold. No automation | Could |
| Detect: monitoring before customers notice (p14, p20) | Events from webhooks, Kafka, logs | None; this is Herald's core job | Pitch point only | Must (exists) |
| Resolve, with times recorded (p36-37) | Auto-resolve on "resolved" event; first/last seen | No explicit start-of-impact, detection and end times; no final communication | On resolve, compute time-to-detect and time-to-recover from `impact_started_at` (optional, from the event, default first seen) and show them; add a final-update step to the runbook | Should |
| Resolution vs cleanup (p13, p36) | Resolved is final | Follow-up work lost after resolve | Allow follow-up actions to stay open after the alert is resolved (see below) | Should |
| Postmortem for severity 1-2 (p43) | None | No review step | When a critical or error alert resolves, generate a postmortem draft from the audit timeline (A4) | Must |
| Postmortem template and fields (p48-53) | None | No structure | Draft uses a trimmed set of fields: summary, impact, detection, response, recovery, timeline, causes (blank), lessons (blank), actions. Fields needing human judgement stay as prompts | Must |
| Blameless practice (p44-45) | Audit log records "who did what" | Direct clash if the draft shows names as faults | Draft refers to roles ("the on-call responder", "the incident manager") by default, with an option to show names; wording avoids fault ("why did the system allow..."). Audit log itself stays factual and attributable, which is a compliance need in a bank | Must |
| Proximate vs root cause, five whys (p52, p54) | None | Needs human thinking | Two empty prompts in the draft: "what directly triggered it" and "what to change so the whole class stops". Optional five-whys list as free text | Should |
| Cause categories (p56-57) | None | Useful for trends | A single select on the draft: bug, change, scale, architecture, dependency, unknown. Free to add, enables a count by category | Could |
| Follow-up actions, priority vs improvement (p60-66) | `open_ticket` action | Actions not linked to the alert, no distinction | On the draft, each action has text, owner, due date, type `priority` or `improvement`; stored with the alert; created through existing `open_ticket`. Rule of thumb in the UI hint: start with a verb, be specific, be bounded (p61-62) | Should |
| Completion target for priority actions (p45) | None | Actions can be forgotten | Show open priority actions on the dashboard with age; reminder after the due date | Could |
| Postmortem approval (p65) | Approval mechanism exists for risky actions | Not applied to documents | Reuse `role:team-lead` approval to "publish" a postmortem. Self-approval already blocked | Could |
| Learning runbooks from incidents (optional feature) | Not designed | Handbook supports it: runbook gaps and mitigations are action types (p60-61) | The "what to change in the runbook" section of the draft lists steps taken that were not in the runbook, as suggestions only, for human review | Could |
| Noisy rules (optional feature) | Not designed | Handbook: monitoring should warn before incidents (p14) | Count alerts per rule with no ack; show on the rule editor. Idea only | Could |
| Chat room, video call, state document (p23) | UI only | Different environment | Do not build; the alert detail page plays the role of the shared state document | Skip |

---

## 3. Additions to propose (each under an hour)

### A1. Incident manager on critical alerts (about 40 minutes)
* Data: `alert.incident_manager` (user id or null), optional `comms_owner`, `tech_lead`.
* Behaviour: when a `critical` alert is acked, the acker becomes incident manager; shown as a badge in the alert header and on the live feed row. A "Take over" action changes it and writes an audit entry (also covers handover, p36).
* UI: header badge "IM: name", small role editor in the side panel.
* Handbook basis: p18, p22, p32.

### A2. Communication steps and reminders in runbooks (about 50 minutes)
* Data: step `kind: manual` with optional fields `repeat_every` (minutes) and `template: status_update`; step state gets `last_done_at`.
* Behaviour: while the alert is not resolved, a scheduler delivers a reminder "post an update, last one X minutes ago" to the incident manager (falls back to the owner) through the owner's normal channel; completing the step resets the clock. Initial update step has `due_within` (e.g. 10 minutes in a demo).
* UI: step shows a countdown and an amber/red state when overdue.
* Handbook basis: p27, p33, p69-70.

### A3. Status-update template rendered from the alert (about 45 minutes)
* Data: a template string using the same `{{field}}` rendering Herald already has, plus `{{summary}}`, `{{severity}}`, `{{impact}}`, `{{status_bullets}}`, `{{next_steps}}`, `{{next_update_at}}`, `{{key}}`.
* Shape (from p33-34): subject `<key> - <severity> - <summary>`; one or two sentences on state and impact; "Current status" 2-4 bullets; "Next steps" 2-4 bullets; when the next update will arrive.
* UI: a "Draft update" button that fills a text box, with the five-point checklist as tick boxes (impact, how many affected, root cause if known, ETA, next update). Unknowns render as the word "unknown". Copy or send to the team's email channel. Separate short external variant (one or two sentences, p34-35) is a second template.
* Status bullets can be pre-filled from step states (done steps become status, pending steps become next steps).

### A4. Postmortem draft from the audit timeline (about 60 minutes)
* Trigger: alert with severity `critical` (and optionally `error`) moves to `resolved`.
* Data: `alert.postmortem` with `summary`, `impact`, `detection`, `response`, `recovery`, `timeline[]`, `causes_proximate`, `causes_root`, `cause_category`, `lessons`, `actions[] {text, owner, due, type: priority|improvement}`, `runbook_changes[]`, `status: draft|approved`.
* Generation (no AI needed, plain templates): summary from title, severity, first/last seen and counter; detection from first event time and first delivery time; response from acks, step completions and approvals, using roles instead of names unless toggled; recovery from the resolving event and last step; timeline copied from audit entries with timestamps; `runbook_changes` listing manual notes and actions that were run but were not in the runbook (suggestion only). Cause and lessons stay blank as prompts.
* UI: "Postmortem" tab on resolved alerts; editable fields; an action list; "Mark approved" restricted to team lead.
* Handbook basis: p42, p46-53, p60-66; blameless framing p44-45.

### A5. Severity definitions in the rule editor (about 25 minutes)
* Data: static table in config: level, one-line meaning, example, expected response (page now / during hours / feed only), postmortem required yes/no.
* UI: a help panel beside the severity dropdown; selecting a level shows its meaning; warning label when someone picks critical for a rule that has no runbook or no on-call team.
* Handbook basis: p26-27.

### Ranked by value for effort
1. A5 severity definitions (cheapest, prevents bad rules, strong demo line)
2. A3 status-update template
3. A4 postmortem draft (most impressive, reuses the audit chain)
4. A1 incident manager
5. A2 reminders

Add alongside: ack deadline with auto-escalation (mapping table), the Timeline tab with a note box, and the short alert key. Those are Must items that A3 and A4 depend on.

---

## 4. Runbook templates (Herald YAML)

Written to follow the handbook stages: detect, open communications, assess, initial comms, escalate, delegate, follow-up comms, iterate, resolve. `{{...}}` placeholders come from the event or the alert; the names below are examples and should be adjusted to the real fields. `approval` values follow Herald's rules; risky actions need approval.

### 4.1 Critical service outage

```yaml
name: critical-service-outage
applies_to: severity >= critical
steps:
  # Detect / open communications
  - text: "Confirm the alert is real: check health of {{service}}"
    kind: check
    action: http_get
    params: { url: "{{health_url}}", expect_status: 200 }
    approval: none
  - text: "Take ownership: acknowledge, you are now incident manager for {{key}}"
    kind: manual
    approval: none
  # Assess
  - text: "Assess impact. Who is affected, what do they see, how many, since when ({{first_seen}}), any support cases, any data or security concern?"
    kind: manual
    approval: none
  - text: "Confirm severity {{severity}} or change it; note the reason"
    kind: manual
    approval: none
  # Initial communications
  - text: "Post initial update from the template: '{{key}} - {{severity}} - {{title}}'. Say what is known and that the next update is at {{next_update_at}}"
    kind: manual
    template: status_update
    due_within: 10
    approval: none
  # Escalate / delegate
  - text: "Page the team lead if not acknowledged by a second responder"
    kind: action
    action: escalate
    params: { target: "{{team_lead}}", reason: "{{key}} critical, {{service}}" }
    condition: "alert.acked_by_count < 2"
    approval: none
  - text: "Name a tech lead and a communications owner for this incident"
    kind: manual
    approval: none
  # Iterate
  - text: "Check what changed recently on {{service}} (deploys, config, upstream); write down observations"
    kind: check
    approval: none
  - text: "Restart {{service}} if the theory is a stuck process"
    kind: action
    action: restart_service
    params: { service: "{{service}}", env: "{{env}}" }
    approval: role:team-lead
  - text: "Verify recovery"
    kind: check
    action: http_get
    params: { url: "{{health_url}}", expect_status: 200 }
    approval: none
  # Follow-up communications
  - text: "Post a follow-up update: impact, current status (2-4 bullets), next steps (2-4 bullets), next update time. Write 'unknown' where unknown"
    kind: manual
    template: status_update
    repeat_every: 30
    approval: none
  # Resolve
  - text: "Wait for the resolved event or confirm recovery with a second person"
    kind: wait
    action: wait
    params: { until: "alert.status == resolved", timeout_min: 240 }
    approval: none
  - text: "Post final update: service restored, summary of impact, postmortem to follow"
    kind: manual
    template: status_update
    approval: none
  - text: "Open postmortem draft and name an owner"
    kind: action
    action: open_ticket
    params: { title: "Postmortem {{key}}: {{title}}", owner: "{{team}}", priority: high }
    approval: none
```

### 4.2 Degraded performance

```yaml
name: degraded-performance
applies_to: severity in [warning, error]
steps:
  - text: "Check the current value of {{metric}} against threshold {{threshold}}"
    kind: check
    action: http_get
    params: { url: "{{metrics_url}}" }
    approval: none
  - text: "Assess: is it getting worse, how many users see it, is there a workaround?"
    kind: manual
    approval: none
  - text: "Decide severity. If many users or core function affected, raise to critical and switch to the outage runbook"
    kind: manual
    approval: none
  - text: "Post initial internal note for {{key}} to {{team}}: what is slow, since {{first_seen}}, next update time"
    kind: manual
    template: status_update
    due_within: 30
    approval: none
  - text: "Ask for a second pair of eyes if not improving"
    kind: action
    action: escalate
    params: { target: "{{team_lead}}" }
    condition: "minutes_since_first_seen > 30 and alert.status == acked"
    approval: none
  - text: "Retry the stalled job or workflow if one is shown in the event"
    kind: action
    action: retry_workflow
    params: { workflow: "{{workflow_id}}" }
    condition: "event.workflow_id is not None"
    approval: required
  - text: "Wait and re-check"
    kind: wait
    action: wait
    params: { minutes: 15 }
    approval: none
  - text: "Re-check the metric; note observation and what you changed"
    kind: check
    action: http_get
    params: { url: "{{metrics_url}}" }
    approval: none
  - text: "Post update (current status, next steps, next update)"
    kind: manual
    template: status_update
    repeat_every: 60
    approval: none
  - text: "On recovery, post a one-line resolution note and record what fixed it so the runbook can improve"
    kind: manual
    approval: none
```

### 4.3 Business event needing a human decision

For example a large payment held for review, a limit breach or a sudden volume spike. The focus is a clear decision with a recorded reason, not a technical fix.

```yaml
name: business-event-decision
applies_to: rule.type == business
steps:
  - text: "Acknowledge and read the details: {{title}}. Amount or count: {{amount}}; account or segment: {{segment}}"
    kind: manual
    approval: none
  - text: "Assess: what is the exposure, who is affected, is there a regulatory or customer-data aspect, how long until it matters ({{deadline}})?"
    kind: manual
    approval: none
  - text: "Tell the decision owner what is happening and what they must decide, by when"
    kind: action
    action: escalate
    params: { target: "{{decision_owner}}", reason: "{{key}} decision needed by {{deadline}}" }
    approval: none
  - text: "Gather facts: open the case view"
    kind: check
    action: http_get
    params: { url: "{{case_url}}" }
    approval: none
  - text: "Decision: approve, reject or hold. Write the reason in the note box"
    kind: manual
    approval: role:team-lead
  - text: "If approved, release or continue the process"
    kind: action
    action: webhook
    params: { url: "{{release_url}}", method: POST, body: { case: "{{case_id}}", decision: "approved" } }
    condition: "decision == approved"
    approval: role:team-lead
  - text: "If rejected or held, create a ticket for the follow-up"
    kind: action
    action: open_ticket
    params: { title: "{{key}} follow-up: {{title}}", owner: "{{team}}" }
    condition: "decision != approved"
    approval: none
  - text: "Inform affected parties internally with the outcome, using the status-update template"
    kind: manual
    template: status_update
    approval: none
  - text: "Review afterwards: was the alert useful, was anything missing from this runbook?"
    kind: manual
    approval: none
```

---

## 5. What to say in the 5-minute pitch

"Herald is built around the same loop that mature incident teams use: detect early, escalate quickly, communicate clearly, resolve, then learn. We follow a published industry handbook for severity levels, roles, update cadence and blameless postmortems, and make each of those a feature rather than a document nobody reads. Every critical alert has a named incident manager, a timed update reminder and a ready-made status message, and when it resolves, Herald drafts a blameless postmortem from our tamper-evident audit trail, so the bank gets the learning step for free and a record it can show to auditors."

Optional extra line: "Because runbooks and rules are editable at runtime, each incident can make the next one shorter."

---

## 6. What does not fit, and should be left out

| Handbook item | Reason to omit |
|---|---|
| Chat room per incident, video bridge, shared state document (p23-24) | Environment-specific; Herald's alert page serves as the shared place. Link to an existing channel if wanted |
| Public status pages, external customer communications as a sent channel (p27-28, p34-35) | A bank would route external wording through a communications or compliance team; Herald should only draft the text |
| Issue tracker workflow and automation (p17, p21-22, p47) | We already have alerts and an `open_ticket` action; do not rebuild a tracker |
| Training, shadowing, role-play (p32, p35) | Organisational, not software |
| Printed cheat sheet, short links, backup systems (p68-70) | Could become a help page later; not worth build time |
| Postmortem meeting logistics, invitation template, agenda (p62-64) | Happens outside the tool. Maybe print the agenda on the postmortem tab |
| Blog post publishing of postmortems (p47) | Needs a document platform; export to Markdown is enough |
| Cause-category trend reports, recurrence tracking across incidents, repeat-risk reporting (p53, p56, p66) | Needs history and reporting; keep as a future idea |
| Approval workflow for postmortems with SLO tracking (p45, p65) | Only a simple "approved by team lead" flag is realistic |
| External dependency expectation framework (p57-59) | Process guidance; one line in the pitch at most |
| Security-incident paging and vendor lists in the cheat sheet (p69) | Internal to that company; a bank would have its own security process, so link to it rather than copy |
| Opsgenie recommendations (p16, p22, p47) | Product advertising; ignore |
| Foreword story and the guest articles (p5-10, p29-30, p38-40) | Context only; the guest pieces on customer communications and report writing reinforce the update-checklist and audience-first ideas already used |

Notes on caution: the handbook's "never hesitate to escalate" (p14) suits an engineering team; in a bank with SMS and phone costs and approval rules, keep Herald's existing approval gates and the rule that a requester cannot approve their own request. Blameless framing applies to the postmortem narrative; the audit log must stay attributable.
