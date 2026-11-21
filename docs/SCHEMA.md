# Herald data schema

The contracts everything else is built around. Each one is defined once in code and tested; this page explains them. Where this page and another doc disagree, this page and the code win.

| Contract | Defined in | Tested in |
|---|---|---|
| Event, aliases, field paths | `alertsvc/normalize.py` | `tests/test_normalize.py` |
| Conditions and operators | `alertsvc/rules/` | `tests/test_brief_golden.py`, `tests/test_normalize.py` |
| Rule, runbook, team, source | `alertsvc/models.py` | `tests/test_brief_golden.py` |
| Routing and working hours | `alertsvc/routing.py` | `tests/test_brief_golden.py` |
| Runbook parsing and actions | `alertsvc/runbooks/` | `tests/test_runbooks_security.py` |
| Tables | `db/schema.sql` | exercised by the running stack |

## 1. Event

Herald does not require a schema. Whatever arrives is kept as it came (`raw`) and wrapped:

```
Event
  id            unique id given on arrival
  source        which entry in config/sources.yaml it came through, e.g. "trade-events"
  source_type   webhook | kafka | logtail | http_poll
  received_at   when Herald first saw it (the start of the latency clock)
  raw           the payload, untouched
  attrs         aliases filled when they can be (below)
```

**Field paths.** Rules, templates and runbooks read values by dotted path into `raw`:

| Path | Reads |
|---|---|
| `error.code` | nested object |
| `payload.notional` | nested object |
| `alerts.0.labels.service` | list by index |
| `data.positions.exposure` or `data.positions.*.exposure` | every element of a list (the condition is true if any element passes) |
| `http.status` | also finds a literal key `"http.status"` |

**Aliases.** Four optional names that work across schemas. Each is filled from the first of these paths that exists:

| Alias | Looked up in | Workflow sample | Metric sample | Trade sample |
|---|---|---|---|---|
| `@type` | `type`, `alertname`, `labels.alertname`, `meta.schema`, `event_type`, `eventType`, `event`, `kind` | `WORKFLOW_FAILED` | `HighErrorRate` | `trade.rejected.v2` |
| `@severity` | `severity`, `labels.severity`, `level`, `log.level`, `priority`, `meta.severity` | `error` | `critical` | missing |
| `@timestamp` | `timestamp`, `startsAt`, `meta.ts`, `time`, `ts`, `@timestamp`, `eventTime`, `created_at` | ISO string | ISO string | epoch ms |
| `@service` | `service`, `labels.service`, `meta.producer`, `workflowName`, `logger`, `host`, `app`, `source` | `order-settlement` | `payments-api` | `trade-gateway` |

Also available: `@source`, `@source_type`, and `@headers.<name>` for webhook request headers starting with `x-`.

**Severity** is normalised to `debug < info < warning < error < critical`. Words are matched without regard to case (`WARN`, `warning`, `ERR`, `FATAL`, `sev2`, `P1` ...). Bare numbers are not guessed, because scales run in both directions; a source says how to read them.

**A source can override any alias** in `config/sources.yaml`. This is the "one mapping line" for an unseen schema:

```yaml
- id: risk-events
  type: webhook
  map:
    severity: data.priority          # where severity lives in this schema
    severity_scale: syslog           # or severity_map: {1: critical, 2: error}
  partition_key: data.account.id     # optional: keeps one account's events in order
```

**Batches.** A top-level JSON array, or an Alertmanager webhook with `alerts[]`, becomes one event per element. A text line (from a log file, or a non-JSON webhook body) is parsed as `<time> <LEVEL> <logger> key=value ... message`, or with the source's own `pattern`.

## 2. Conditions

A condition is data, never code:

```yaml
all:                                           # AND
  - {field: meta.schema, op: glob, value: "trade.rejected.*"}
  - {field: payload.desk, op: eq, value: rates}
  - any:                                       # OR
      - {field: payload.notional, op: gt, value: 10000000}
      - {field: payload.reason, op: in, value: [LIMIT_BREACH, SANCTIONS]}
  - not: {field: payload.currency, op: eq, value: JPY}
```

Shorthand: a plain map `{type: WORKFLOW_FAILED, "error.code": TIMEOUT}` means "all of these are equal". Runbooks use it in `matches`.

| Operator | True when | Notes |
|---|---|---|
| `eq`, `ne` | equal / not equal | Text is case-sensitive. `25000000` and `"25000000"` are equal |
| `gt`, `gte`, `lt`, `lte` | numeric or text comparison | Numeric strings compare as numbers; `"25,000,000"` does not |
| `in` | the value is one of a list | |
| `contains` | a list contains the value, or text contains the text | |
| `glob` | shell-style pattern: `pricing-engine-*`, `trade.rejected.*` | Case-sensitive |
| `regex` | the pattern is found | Pattern length is capped |
| `exists` | the field is present (`value: false` for absent) | |
| `severity_gte` | at least this severity | Works on any vocabulary: `WARN`, `warning`, `sev3` |

**Decisions that are easy to get wrong**

- Every operator except `exists` is false when the field is missing, including `ne`. To say "missing or not X", write `not: {field: f, op: eq, value: X}`.
- If the field is a list, the condition is true when any element passes.
- A rule with an unknown operator or a malformed tree is rejected when saved; it never reaches a worker.
- Adding an operator is one decorated function in `alertsvc/rules/operators.py`.

## 3. Rule

```yaml
id: rates-large-trade-rejected
owner: team:rates                  # team:<id> or person:<id>; decides who can see and edit it
name: Large rates trade rejected
enabled: true
when: { ...condition... }          # which events fire it
severity: warning                  # fixed; leave out to take the event's own severity
present:                           # how the alert looks
  title: "Rejected {{payload.currency}} {{payload.notional}} — {{payload.reason}}"
  body: "Trade {{payload.tradeId}} from {{meta.producer}}"
  fields: [payload.tradeId, payload.notional]     # shown on the alert
  highlight: [payload.reason]                     # shown in the rule's colour
  colour: amber
group:                             # dedup: same values for `by` = same alert, counter goes up
  by: [payload.tradeId]            # default: [@type, @service]
  window: 5m                       # optional: re-notify at most once per window while it keeps firing
notify:                            # who is told, and how
  - to: team:rates                 # team:<id> | team:<id>/on-call | person:<id>
    channels: [email, ui]          # or `preferences` to use the recipient's own settings by severity
  - to: person:anna
    channels: [sms]
    hours: {from: "08:00", to: "20:00", tz: Europe/London}   # from inclusive, to exclusive
    else: [email]                  # used outside those hours
runbook: settlement-timeout        # optional; else the first runbook whose `matches` fits the event
resolve_when: { ...condition... }  # optional: an event matching this, with the same group key, closes the alert
```

Templates are `{{path}}` with an optional filter (`| number`, `| upper`, `| lower`). There is no logic in templates. A missing value renders as `⟨missing: path⟩` so it is noticed. Values are escaped wherever the output is HTML.

The brief's four example rules are in `config/rules/` in exactly this format, and `tests/test_brief_golden.py` checks each against the brief's sample events.

**Where rules live.** Files in `config/rules/` are loaded at start and again whenever a file changes (`source: file`). Rules saved through `PUT /api/rules/{id}` are `source: ui`. A file reload never overwrites a UI rule; saving a file rule in the UI turns it into a UI rule. Every save bumps `version` and is audited. Workers pick up a change within about a second, without a restart.

## 4. Alert and notification

```
Alert                                    Notification (the delivery outbox)
  id, key (AL-0042)                        alert, recipient, channel
  rule, owner                              status: pending → sending → sent
  fingerprint  (rule + group.by values)                         ↘ retrying ↗   → dead (then next channel)
  status: firing → acked → resolved        attempts, next attempt, last error
  severity, title, body, fields, colour    idempotency key (never sent twice for the same reason)
  count, first seen, last seen             ingest time, delivered time  → the latency numbers
  sample event, rendered runbook
  step state, incident manager
```

- **One open alert per fingerprint.** A partial unique index in Postgres guarantees it. Repeats add to `count`; they do not create alerts or notifications.
- **A resolved alert frees its fingerprint**, so the same problem tomorrow is a new alert.
- **Fallback chain** when a channel is still failing after all retries: phone → SMS → email → UI.
- **Incident manager**: whoever acknowledges a `critical` or `error` alert is recorded as the person in charge.

## 5. Runbook

Any text is accepted. What Herald does with it depends on what it can read:

| Level | Input | Result |
|---|---|---|
| 0 | Anything, including a bare wiki link | Shown as written, with `{{variables}}` filled from the event |
| 1 | YAML steps, Markdown lists and checklists, plain sentences | Split into steps by the built-in parsers |
| 2 | Any prose, with the LLM switched on | Structured steps with actions proposed, marked "needs review" until a person accepts |

All three end up as the same step:

```yaml
id: 3
text: "Fail traffic over to the other region"
kind: manual          # check | action | manual | wait
action: null          # a registered action, or null when a person does it
params: {}            # action parameters; templates are filled from the event
condition: one region # when to do it, in the author's words
approval: role:team-lead      # none | required | role:<name>
suggest: null         # an action the parser guessed from the wording; never runs until reviewed
```

The structured YAML from the brief is read as written (`check`, `action`, `if`, `approval`, any other key becomes a parameter). The fuller form with `text`, `kind`, `params` and `condition` is read too. The three samples from the brief are in `config/runbooks/`; three longer templates that follow incident-response stages are in `samples/runbooks/`.

**Actions** (`alertsvc/runbooks/actions.py`):

| Action | Risk | Does |
|---|---|---|
| `http_get` | low | GET a URL, report the status. Guarded against internal addresses |
| `retry_workflow` | low | Ask the workflow engine to retry (mock target) |
| `open_ticket` | low | Open a ticket (mock) |
| `wait` | low | Pause |
| `restart_service` | high | Restart an instance (mock) |
| `webhook` | high | POST to a URL. Guarded |
| `escalate` | high | Bring in another team |

A high-risk action always needs approval, whatever the runbook or an LLM draft says. Approval comes from the UI or from replying `YES <code>` to the SMS; the person who asked cannot approve their own request; a code works once, for one approver, for 15 minutes. Adding an action is one decorated function.

## 6. People and teams

`config/teams.yaml`: teams (members, lead, mailbox, weekly on-call rota, channel preferences by severity) and people (email, phone, time zone, preferences). `team:<id>/on-call` resolves to whoever is on the rota now. Default preferences: critical → phone + SMS, error → SMS + email, warning → email, info → UI only.

## 7. API

Interactive docs at `/docs`. With `AUTH_MODE=oidc` the caller is the signed-in session (cookie `herald_session`, set by `/auth/login` → `/auth/callback`; `GET /api/me` says who that is, `POST /auth/logout` ends it) and identity headers are ignored; see `docs/SSO.md`. In demo mode identity is sent in headers (`X-Herald-User`, `X-Herald-Role`, `X-Herald-Teams`) by the UI's "Viewing as" switcher.

| Method and path | Purpose | Role |
|---|---|---|
| `POST /ingest/{source}` | Accept any JSON or a text line (token or signed) | source token |
| `GET /api/stream?owner=` | Live feed (server-sent events, resumes with `Last-Event-ID`) | viewer |
| `GET /api/alerts`, `GET /api/alerts/{id}` | Feed and detail, with deliveries, timeline, "why it matched" | viewer |
| `POST /api/alerts/{id}/ack`, `/resolve` | Acknowledge, resolve | responder |
| `POST /api/alerts/{id}/steps/{n}/run` | Tick a step, run an action, or request approval | responder |
| `POST /api/approvals/{id}/decide` | Approve or reject | approver |
| `POST /api/alerts/{id}/notes` | Add an observation to the timeline | responder |
| `GET /api/alerts/{id}/status-update` | A status update to edit and send | viewer |
| `GET /api/alerts/{id}/postmortem` | A blameless postmortem draft from the audit trail | viewer |
| `GET /api/rules`, `PUT /api/rules/{id}` | List, create or change a rule at runtime | editor |
| `POST /api/rules/test` | Dry-run a condition on recent or given events, with the reason for each miss | any |
| `GET /api/fields?source=` | Field paths seen so far, for the field picker | any |
| `POST /api/runbooks/parse` | Any runbook text → steps | any |
| `GET /api/meta` | Severity definitions, operators, channels, actions | any |
| `GET /api/stats?since=` | Counts and p50 / p99 ingest-to-delivery latency | any |
| `GET /api/audit` | Audit log and whether its hash chain is intact | approver |
| `POST /inbound/sms` | Replies from the SMS provider: `ACK 17`, `YES 4821` | provider |

## 8. Tables

`rules`, `config_docs` (teams, people, runbooks, sources), `alerts`, `notifications`, `approvals`, `audit_log` (append-only, hash-chained), `feed` (for live-feed replay), `seen_fields`, `recent_events`, `dead_events` (messages that could not be processed, kept for replay). See `db/schema.sql`; every table has a comment saying what guarantee it carries.
