# Herald

A flexible alerting and notification service: it takes events in any shape from many sources, lets each team say in plain rules what they care about, delivers one clear alert on the channel each person prefers, and shows the runbook with the next steps filled in.

Built for the Hackathon 2026 Software Engineering brief (`docs/BRIEF.md`). "Herald" is a working name.

**Status: a working skeleton, not the finished product.** The event path runs end to end, is tested and measured, and the web UI works against it. Channels and the systems that runbook actions touch are mocked. See "What works, what is mocked, what is not built".

## Start it

```bash
docker compose up --build
```

| What | Where |
|---|---|
| Web UI and API | http://localhost:8080 — API docs at http://localhost:8080/docs |
| Email inbox (Mailpit) | http://localhost:8025 |
| Fake SMS / phone gateway: see texts, reply, simulate an outage | http://localhost:8090 |
| Kafka API (Redpanda) | `localhost:19092` |

The API port can be changed with `HERALD_PORT` (see `.env.example`). First start takes a few minutes while images download; after that about 15 seconds.

**With real sign-in.** The command above runs in demo mode: "Viewing as" switches identity, which is a mock. To sign in through an identity provider instead (a local Keycloak with demo users; Microsoft Entra ID by configuration):

```bash
AUTH_MODE=oidc HERALD_SECRET=$(openssl rand -hex 32) docker compose --profile sso up --build
```

Then open http://localhost:8080 and press **Continue with SSO**. Users, what to show and how to connect Entra: `docs/SSO.md`.

## Try it

**In the browser.** Open http://localhost:8080. Go to **Platforms**, open one, and use **Send event** to write an event to it; the page shows which rule matched, which alert was created and who was notified. The alert then appears under **Alerts**, where it can be acknowledged, resolved or handed to a colleague. "Viewing as" at the top right switches whose screen you see (demo only). For the full set of prepared cases with expected results, open the **Test lab** at http://localhost:8080/lab.html.

**From a terminal.** Send the brief's three sample events, each through a different kind of source:

```bash
# Metric breach, through the webhook (this source needs its token)
curl -X POST localhost:8080/ingest/metrics-webhook -H 'content-type: application/json' \
  -H 'X-Herald-Token: demo-metrics-token' --data-binary @samples/events/metric_breach.json

# Workflow failure and trade rejection, through Kafka topics
python3 -c "import json;print(json.dumps(json.load(open('samples/events/workflow_failed.json'))))" \
  | docker compose exec -T redpanda rpk topic produce workflow.events
python3 -c "import json;print(json.dumps(json.load(open('samples/events/trade_rejected.json'))))" \
  | docker compose exec -T redpanda rpk topic produce trade.events

# A pricing log line, through a tailed log file
cat samples/pricing.log >> demo-logs/pricing.log

# See the alerts, with who was told and how
curl -s localhost:8080/api/alerts | python3 -m json.tool
```

Then:

- **A schema Herald has never seen**: `curl -X POST localhost:8080/ingest/generic-webhook -H 'content-type: application/json' --data-binary @samples/events/unseen_margin_call.json`. Nothing alerts yet, but `GET /api/fields?source=generic-webhook` already lists its fields. Save a rule for it with `PUT /api/rules/{id}` and send it again.
- **Change a rule with no restart**: edit a file in `config/rules/`; it is live within a second.
- **Channel down, nothing lost**: press "Take gateway down" at http://localhost:8090, send the workflow failure with a new `workflowId`, watch the SMS retry, bring the gateway back.
- **Approve a risky step by text**: `POST /api/alerts/{id}/steps/3/run` asks for approval; the code arrives on the team lead's "phone" at http://localhost:8090; reply `YES <code>` from `+447700900003`.
- **Auto-close**: send `samples/events/metric_resolved.json` to the metrics webhook.
- **After an incident**: `GET /api/alerts/{id}/status-update` and `GET /api/alerts/{id}/postmortem`.

## Tests

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e . pytest
.venv/bin/python -m pytest -q
```

141 tests, a few seconds, no services needed; `HERALD_E2E=1 .venv/bin/python -m pytest -q tests/test_e2e_signin.py` adds 9 end-to-end sign-in tests against the running SSO stack. `tests/test_auth.py` covers sign-in: forged and expired ID tokens, PKCE, the signed login cookie, redirects, roles from the provider. `tests/test_brief_golden.py` is the brief turned into tests: its four example rules against its three sample events, the rendered title, working hours at the boundary, the 10,000,000 boundary, dedup keys, auto-resolve.

## Load test

```bash
.venv/bin/python loadtest/run.py --rate 500 --duration 1 --same-key        # 500 identical errors → one alert ×500
.venv/bin/python loadtest/run.py --rate 10000 --duration 15 --match-ratio 0.02 --source kafka
docker compose up -d --scale worker=4                                      # then run it again
```

Run it against the demo-mode stack (`docker compose up`): under SSO, `/api/stats` needs a signed-in session. It sends at a fixed rate through the real sources and reads latency from the delivery table: from the moment an event reached the webhook or the broker to the moment the SMS gateway accepted the notification. It refuses to print a result if anything was not processed and delivered. Measured results are in `docs/ARCHITECTURE.md`.

## What works, what is mocked, what is not built

| Area | State |
|---|---|
| Sources: HTTP webhook, Kafka topics, tailed log file, polled HTTP API | Working (polled API is written but has not been run against a live endpoint) |
| Any-schema ingest, aliases, batches, text lines | Working, tested |
| Rules: any field, AND / OR / NOT, 12 operators, templates, colour, grouping, working hours, on-call | Working, tested |
| Rules changed at runtime: API and hot-reloaded files | Working |
| Dedup with a counter; one repeat notification per window | Working, measured |
| Delivery: outbox, retries with backoff, fallback channel, nothing dropped | Working, tested with a simulated outage |
| Live feed push (server-sent events, resume after reconnect) | Working, used by the UI |
| Acknowledge / resolve from the API and by SMS reply; auto-resolve | Working |
| Runbooks: YAML, Markdown, checklists, free text, link; variables filled | Working, tested |
| Runbook actions with approval (UI call or SMS reply, no self-approval, single-use codes) | Working |
| Audit log (append-only, hash-chained) | Working |
| Incident practice: person in charge, status update, postmortem draft, severity definitions | Working (API) |
| Web UI (`ui/index.html`, plain files, no build step), laid out on Atlassian Design System conventions with Microsoft Fluent icons | Working against the API. The shell follows Atlassian's current navigation: a top bar with search and Create, and a sidebar that lists every platform with its open problems. **Alerts**: a board of cards (Open, Acknowledged, Resolved; drag a card to change its status) or an issue-style list (key, summary, status, assignee, platform), and an issue view with description, runbook steps, activity and a details panel; an alert can be handed to a colleague, who is notified. **Platforms**: every connected system with its open problems; inside a platform its problems, its events (with "why no alert?"), a form to write an event to it, and its setup. A new webhook platform can be connected from the UI and gets its own token. Also: rule editor with dry-run and channel previews, runbooks (paste, parse, save), sent messages with phone and mailbox stand-ins, activity log. Read-only in the UI: channel preferences and the on-call rota. Not in the UI: rule history |
| Test lab (`ui/lab.html`, at `/lab.html`) | Working. Pages that play the source systems from the brief (workflow engine, monitoring, trade gateway, pricing engine, a new system). Each has cases with the expected outcome; a case is sent through the real route, the lab shows what Herald did and marks it as expected or not, and keeps a case log that can be copied as a table |
| Logs page and brand in the web UI (`ui/index.html`, `ui/brand/`) | Working. **Logs** (sidebar): every event Herald kept (the latest 200, at most one per platform per second) with a search box (plain words and `severity:error`, `platform:…`, `type:…`, `-word`), filters with counts, a timeline whose bars narrow the time range, CSV download (not tested by clicking), and the event's "why" view on a row. It reads `/api/events/recent`; it is not connected to the platforms' own log stores. **Platform page header in two versions** for comparison, switched at the top right of a platform page and remembered in the browser: A, a poster with the name in the display face, the figures and the animated trumpeter; B, the plain header. **Brand**: The Herald wordmark in the top bar and on the sign-in page, indigo instead of blue, labels in sentence case (no small capitals). The interface font is IBM Plex Sans (`ui/brand/fonts/ibm-plex-sans.ttf`, SIL Open Font Licence, text in `ibm-plex-OFL.txt`); page titles use the display face Dayohe. The sidebar can be dragged wider or narrower. There is no "Technical details" switch any more: field names and raw events are shown from level L2 up, and hidden from reporters and support |
| Access levels, reporting and escalation flows in the web UI (`ui/index.html`, `ui-flow/`, `config/rules/reports.yaml`) | **Access levels L0 to L4**: reporter, support team, engineering team, senior engineers, management. The level follows the role the service gives a person (viewer L0, responder L2, editor and approver L3, admin L4) and decides which screens and how much detail they get; support sees warnings, info and what is handed to it; management sees everything. The avatar menu can preview another level. **This is a difference in the interface only: the service still decides access by team and role, and does not know the levels.** **Report a problem** (working): a form in plain words; the report travels as an event and becomes an alert for the support team through `config/rules/reports.yaml`. It is sent through the demo sending route (`/api/sim/send`); a production route for reports is not built. **Escalation flows** (a design, not executed; dragging boxes, drawing connections, adding and editing steps checked by hand in the browser): one flow per kind of problem (bug, vulnerability) and severity, drawn with React Flow: who is told first, how many minutes they have to take the ticket, who is next, and who is only kept informed. Flows are saved in the browser. The service does not step up to the next person on a timer yet. **On an alert**: Assign to me, hand over to a colleague (both working), and Start a call, which opens the Teams meeting form with the subject and a link to the alert and keeps the pasted meeting link on the alert; Herald does not create the meeting. **Approvals**: each request opens to show what would change and links to where the change lives (example data) |
| Screens from the UI handoff (`ui/index.html`: Approvals, On-call, Metrics, Knowledge, Settings with Escalation, My notifications, Trust levels, Integrations, Organisation) | **Example data, not connected.** Each screen calls the `/v1/...` endpoint named in the handoff's `openapi.yaml`; this service has none of them, so the screen shows the handoff's examples under a banner that says so, and nothing done there is saved (Approve and Reject, and the notification switches, change the page only). Metrics is the exception: without `/v1/metrics/summary` it shows this service's real numbers from `/api/stats` and leaves empty what is not measured. Not built from the handoff: the AI suggestion panel, the suspicious-content banner, the escalation countdown and the hard/soft split on an alert; the onboarding wizard with AI field mapping; the Describe, Visual and YAML tabs of the rule editor; creating shifts, rotations and overrides; changing escalation ladders; sign-in with `/v1/auth/login` and the `/v1/stream` updates |
| Second interface on Atlassian's own components (`ui-ads/`, built into `ui/ads/`, at `/ads/`) | Working against the same API, for comparison with the plain-file UI. React with Atlaskit packages: the navigation system (top bar, sidebar), dynamic tables, lozenges, modal dialogs, tabs, flags, forms, Pragmatic drag and drop for the board, the motion package for entrances, design tokens for every colour and space. Covers: For you, Alerts (board and list), the alert view (hand over, start work, resolve, runbook steps, approvals, notes), Platforms (problems, events with "why?", send a test event, setup, connect a platform), and read-only Rules, Runbooks, Teams, Sent messages and Activity log. **Not rebuilt there**: the rule editor, the runbook editor and the phone and mailbox stand-ins (use the plain-file UI at `/`). Works with single sign-on and with demo mode. No automated UI tests |
| Demo tools (`/api/sim/*`: send sample events, show the fake phone and mailbox, take the gateway down) | Working; on in demo mode, and under SSO when `DEMO_TOOLS=on` (set by the compose file) for signed-in people |
| `ui-mockup/index.html` | The earlier self-contained prototype with simulated data. Kept for showing the design without the service |
| **Email, SMS, phone providers** | **Mocked**: Mailpit and a fake gateway. A real provider is one function in `alertsvc/channels/__init__.py` |
| **Systems that actions touch** (workflow engine, restart, tickets, escalation) | **Mocked** |
| Sign-in with `AUTH_MODE=oidc`: OIDC with PKCE, server-side sessions, roles and teams from the provider, step-up before approving risky actions, sign-in page | Working against a local Keycloak (`--profile sso`); Microsoft Entra ID by configuration, not run against a live tenant. See `docs/SSO.md` |
| Emergency accounts (username + password + TOTP code) next to SSO, for when the provider is down | Working: scrypt hashes, one-time codes that work once, lockout, identical failures, audited as `break_glass` |
| Network rule: people reach Herald only from `ALLOWED_NETWORKS`; ports on 127.0.0.1; Postgres on an internal network | Working, tested; device checks belong to the provider (Entra Conditional Access), described in `docs/SSO.md` |
| **Sign-in with `AUTH_MODE=demo`** (the default) | **Mocked**: identity comes from request headers set by the "Viewing as" switcher. Without headers the caller is treated as admin |
| **LLM help** (structure a runbook, draft a rule) | Guardrails written and tested; the provider call is not implemented. Off by default |
| Inbound SMS signature check, TLS between services, secret manager, HA Postgres, multi-node broker | Not built; described in `docs/SECURITY.md` |
| Channel preferences editor, on-call schedule editor, suggestions for noisy rules | Not built |

## Layout

```
alertsvc/
  normalize.py        any payload → Event; aliases; severity; batches; log lines
  rules/              condition trees, operators, rule index
  models.py           Rule, Runbook, Team, Source (the contracts)
  routing.py          who is told and how: preferences, hours, on-call, fallback
  templating.py       {{path}} templates, no logic
  runbooks/           parsers for any runbook text; actions and their risk
  channels/           ui, email, sms, phone
  sources/            pull sources: log tail, polled API
  worker/             pipeline (event → alert + outbox) and delivery (outbox → channels)
  api/                webhook ingest, live feed, alerts, rules, approvals, stats
  incident.py         severity guide, status update, postmortem draft
  security.py         webhook signatures, approval codes, audit hash chain
  auth.py             single sign-on: OIDC with PKCE, ID token checks, roles from the provider
  localauth.py        emergency accounts (scrypt, TOTP, lockout) and the network rule
  llm.py              optional LLM help: guardrails only
  gateway/            the fake SMS / phone gateway and mock systems
config/               rules, runbooks, teams, sources — the brief's examples, hot-reloaded
config/keycloak/      the demo identity provider's realm: users, groups, roles (throwaway values)
db/schema.sql         all durable state
samples/              the brief's events, practice events, runbook templates
loadtest/run.py       the load test
ui/                   the web UI (index.html) and the test lab (lab.html), served by the API
ui-ads/               source of the second interface (React + Atlaskit); `npm run build` writes ui/ads/
ui-mockup/            the earlier simulated prototype
docs/                 BRIEF, ARCHITECTURE, SCHEMA, TEST-SCENARIOS, RESEARCH, SECURITY, INCIDENT-PLAYBOOK
```

**Adding things**

| To add | Do this |
|---|---|
| A source of an existing kind | An entry in `config/sources.yaml` |
| A new kind of source | One function in `alertsvc/sources/__init__.py` |
| A schema | Nothing. Optionally one `map:` line on its source |
| A rule | A file in `config/rules/`, or `PUT /api/rules/{id}` |
| An operator | One function in `alertsvc/rules/operators.py` |
| A channel | One function in `alertsvc/channels/__init__.py` |
| A runbook | A file of any format in `config/runbooks/` |
| A runbook format | One function in `alertsvc/runbooks/parsers.py` |
| An action | One function in `alertsvc/runbooks/actions.py` |

## Docs

- `docs/BRIEF.md` — the hackathon brief as text
- `docs/ARCHITECTURE.md` — how it works, measured numbers, what to build next, the video outline
- `docs/SCHEMA.md` — events, rules, runbooks, API
- `docs/TEST-SCENARIOS.md` — acceptance, unseen-schema and unseen-runbook practice sets, failure, security and load tests
- `docs/RESEARCH.md` — existing products, ideas borrowed, UX patterns, alert-fatigue figures
- `docs/SECURITY.md` — threat model, LLM posture, sign-in and chat-channel design, AI-usage statement draft
- `docs/SSO.md` — single sign-on: demo users, what to show, access model, connecting Microsoft Entra ID
- `docs/INCIDENT-PLAYBOOK.md` — incident-management practice applied to Herald

## Third-party material

The icons in the UI are Fluent UI System Icons by Microsoft, MIT licence (`ui/fluent-icons-LICENSE.txt`). The UI follows Atlassian Design System conventions for layout and components; it does not use Atlassian's code, logos or icons.

`ui/brand/` holds the project's own brand files from its brandbook: the one-line wordmark, the lockup with the hackathon host's logo (used on the sign-in page only, as in the brandbook's sign-in design; that logo is the host's trademark and its use has not been cleared), the trumpeter component (`herald-trumpeter.js`; animated on the sign-in page, a still picture inside the product) and the display font Dayohe (`fonts/dayohe.ttf`). The font file names The Differentialtype as the trademark owner and carries no licence text; its licence for use in a product has not been checked.

The escalation-flow board (`ui-flow/`, built into `ui/flow/`) uses React Flow (`@xyflow/react` 12, MIT licence) with React 18, bundled into one file so the page makes no outside requests. The small "React Flow" attribution on the board is left in place, as the library asks of projects without a subscription. Rebuild with `npm install && npm run build` in `ui-flow/`.

The second interface (`ui-ads/`, served at `/ads/`) is different: it is built with Atlassian's published component packages (`@atlaskit/*`, Apache-2.0 according to each package's metadata) and their icons. It does not use the Atlassian logo or product names, and it does not ship the Atlassian Sans font: text falls back to the system font. Whether Atlassian's terms allow this look in a product shown outside the hackathon has not been checked.
