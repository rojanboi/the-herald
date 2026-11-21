# Herald architecture

How the service works, why it is shaped this way, what has been measured, and what to build in the remaining hours. For the data formats see `SCHEMA.md`; for what is built and what is mocked see the table in the `README.md`.

## 1. What it is for

Every system produces failures, threshold breaches and business events. Herald gets the right one to the right people on the channel they prefer, once, and shows them what to do next.

It is a platform for many teams, not a tool for one. The brief's own examples are three teams (Settlements, Pricing, a Rates trading desk) and one person (Anna), and its users are engineers, on-call support and people who are not engineers. That drives four choices:

- Every rule, alert and runbook has an owner (a team or a person), and people see only their own.
- One event can fire rules of several owners. All matching rules fire; there is no "first match wins".
- A rule must read as a plain sentence, and a new team must get its first alert without help.
- Nothing is assumed about the shape of an event.

## 2. The flow

```
 HTTP webhook ──► API ──────────┐
 Log file tail ─► sources ──────┼──► [events.raw] ──┐
 Polled API ────► sources ──────┘                   │
 Kafka topics (workflow.events, trade.events) ──────┼──► worker × N  (one consumer group)
                                                    │      1 decode: JSON, batch, or text line
                                                    │      2 normalise: keep raw, fill aliases
                                                    │      3 match compiled rules (indexed)
                                                    │      4 group: one upsert per alert per batch
                                                    │      5 one transaction in Postgres:
                                                    │           alert (+count)  ·  outbox rows  ·  feed entry
                                                    │
                    ┌───────────────────────────────┘
                    ▼
              Postgres ──NOTIFY──► API × N ──server-sent events──► UI live feed
                    │                 ▲
                    │                 │ ack · resolve · run step · approve · save rule
                    ▼                 │
        delivery loop (in each worker)          runbook steps, approvals, audit
        claim with SKIP LOCKED                  status update · postmortem draft
        ui │ email │ sms │ phone
        retry with backoff → fallback channel
                    │
                    ▼
        Mailpit · fake SMS/phone gateway ──replies (ACK 17, YES 4821)──► API
```

Topics that are themselves sources are read in place; everything else is written to `events.raw` first. Either way an event is on the broker, durably, before anything else happens to it.

## 3. Design decisions

| # | Decision | Why |
|---|---|---|
| 1 | **No fixed event schema.** Keep the payload as it arrived; rules read any dotted path. Four optional aliases (`@type`, `@severity`, `@timestamp`, `@service`) are filled from likely paths and can be overridden per source with one line. | Judges bring a schema we have not seen. With no required fields, nothing can be "unsupported" |
| 2 | **Rules are data**: a JSON condition tree evaluated by a small Python engine with a registry of operators. No `eval`, no embedded language. | Safe, maps one-to-one onto a visual builder, and "why didn't it match?" falls out for free. We chose this over CEL because the Python CEL libraries are less mature and a tree is easier to explain |
| 3 | **A broker in front of everything** (Redpanda, Kafka API). | Bursts are absorbed, workers can be down without losing events, and adding workers adds capacity |
| 4 | **Postgres holds all durable state**; workers and API hold none. No Redis. | One place to reason about consistency, one fewer component to run and to explain |
| 5 | **Dedup is a database guarantee**: a partial unique index allows one open alert per (rule, group key); repeats are an atomic `count = count + n`. Each worker also adds up repeats within a batch first. | Correct with any number of workers, with no locks and no shared cache. 500 identical events are one row |
| 6 | **Delivery is a transactional outbox.** Notifications are written in the same transaction as the alert, claimed with `FOR UPDATE SKIP LOCKED`, retried with exponential backoff and jitter, then moved to a fallback channel. | An alert cannot exist without its notifications, and a notification cannot be lost by a crash or a channel outage |
| 7 | **Push with server-sent events**, woken through Postgres `LISTEN/NOTIFY` (ids only), with replay by `Last-Event-ID`. | One-way push is all the feed needs; it works through proxies; any API replica can serve any browser |
| 8 | **Config is hot.** Rule files are watched and re-seeded; UI edits go to the same table; every worker reloads on a `NOTIFY`, with a 30-second poll behind it. A bad rule is rejected at save and never reaches a worker. | Rules change at runtime through the API, the UI or files, as the brief asks |
| 9 | **Runbooks degrade gracefully**: shown as text with variables filled (always), split into steps by parsers (YAML, Markdown, checklists, sentences), or structured by an LLM with human review. | An unseen runbook format still shows something useful on the alert |
| 10 | **Actions have a risk level the runbook cannot lower.** High-risk actions wait for a second person, in the UI or by SMS reply. | The brief's "risky actions need a human", made impossible to bypass by editing a runbook |
| 11 | **The LLM is optional, used only while configuring, and never executes anything.** | Bank-grade posture: the event path is deterministic and auditable. See `SECURITY.md` |
| 12 | **Everything pluggable is a registry**: sources, operators, channels, runbook parsers, actions. | "Is that config or code?" Config for instances; one small function for a new kind |

## 4. Guarantees, and where each one comes from

| Promise | Mechanism | Evidence |
|---|---|---|
| No event is lost once accepted | The webhook answers 202 only after the broker acknowledges; workers commit their position only after the database transaction | Load runs count exactly the events sent |
| The same error many times is one alert with a counter | Partial unique index + atomic increment | 500 identical events in one second → one alert, counter 500, one set of notifications |
| No alert is lost when a channel is down | Outbox rows persist; retries with backoff; fallback channel; "dead" rows are kept and visible | Gateway switched off: SMS and phone retried, delivered after it came back, nothing outstanding |
| A crashed worker loses nothing | Uncommitted batches are redelivered; rows stuck in "sending" are reclaimed after a minute | By design; the kill-a-worker test in `TEST-SCENARIOS.md` has not been run yet |
| A bad message cannot block the pipeline | A failing batch is retried message by message; the bad one is parked in `dead_events` | By design; not yet triggered in a test |
| A rule change needs no restart | File watch / API → table → `NOTIFY` → workers recompile | Threshold changed in a file; the next event followed the new rule |
| People see only their own alerts | Owner on every row, filter in every query | A Pricing responder gets 403 / 404 for Settlements alerts |
| Risky steps need a second person | Risk on the action, approval record, requester ≠ approver, single-use code bound to the approver | Wrong person, wrong code and reuse all refused on the running stack |
| History cannot be quietly changed | Append-only trigger + hash chain + verify endpoint | `UPDATE audit_log` is refused by the database |

**What "at least once" means here.** After a crash between the database commit and the broker commit, a batch is processed again. Alerts and notifications are not duplicated (the fingerprint and the idempotency key prevent it), but the counter can over-count by that batch. Storing the broker position in the same transaction would remove this; it is not done.

## 5. Scaling and state

- **API**: stateless. Run more behind a load balancer. One process measured about 1,800 webhook requests per second with `ab` (each waits for the broker's acknowledgement).
- **Workers**: stateless. `docker compose up -d --scale worker=4`. The broker shares 8 partitions per topic among them, so up to 8 workers per topic as configured; more partitions allow more.
- **State**: rules, dedup, delivery status, approvals and audit are all in Postgres. Dedup windows are a timestamp column, not a timer in a process, so they survive restarts.
- **Ordering**: events for one alert may be handled by different workers. Counters are safe regardless. A source can name a `partition_key` (for example `workflowId`) to keep related events in order, which matters when "failed" and "succeeded" arrive close together.

### Measured

Run on one laptop (Apple M1 Pro, 10 cores, Docker Desktop with 8 GB), everything in containers, load generator on the host. Each run sends at a fixed rate for 15 seconds, and is only reported if every matching event was counted and every notification delivered. Latency is from the event reaching the webhook or broker to the SMS gateway accepting the notification. "Match" is the share of events that become alerts; every matching event here is a new alert with its own SMS, which is the expensive case. Reproduce with `loadtest/run.py`; raw results are written to `loadtest/results/`.

| Run | Events sent | Sent /s | Processed /s | Alerts created | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| 500 identical errors in 1 s | 500 | 495 | 6,518 | 1 | 55 | 55 |
| 1 worker, webhook 1500/s, 5% match | 22,500 | 1,499 | 1,502 | 1,126 | 11 | 70 |
| 1 worker, webhook+kafka 3000/s, 5% match | 45,000 | 2,998 | 2,998 | 2,251 | 18 | 103 |
| 1 worker, kafka 10000/s, 2% match | 150,000 | 9,996 | 9,995 | 3,001 | 14 | 252 |
| 1 worker, kafka 20000/s, 2% match | 300,000 | 19,993 | 16,986 | 6,001 | 1,594 | 2,891 |
| 1 worker, kafka 2000/s, 50% match | 30,000 | 1,999 | 1,279 | 15,001 | 4,992 | 9,604 |
| 2 workers, kafka 20000/s, 2% match | 300,000 | 19,835 | 19,712 | 6,001 | 3 | 142 |
| 2 workers, kafka 2000/s, 50% match | 30,000 | 1,999 | 1,834 | 15,001 | 124 | 1,914 |
| 4 workers, kafka 20000/s, 2% match | 300,000 | 19,990 | 19,333 | 6,001 | 15 | 186 |
| 4 workers, kafka 2000/s, 50% match | 30,000 | 1,999 | 1,996 | 15,001 | 26 | 841 |
| 4 workers, kafka 4000/s, 50% match | 60,000 | 3,998 | 3,183 | 30,001 | 1,429 | 4,887 |

**Reading the table**

- **Mostly non-matching traffic** (2% become alerts): one worker keeps up with 10,000 events per second and saturates at about 17,000. Two workers keep up with 20,000, the highest rate the load generator was asked for.
- **Alert-heavy traffic** (50% become new alerts, each with its own SMS): one worker processes about 640 new alerts per second, two about 920, four keep up with 1,000 and reach about 1,600 when offered 2,000. This scales, but less than linearly: the limit is database round trips per alert and the single fake gateway, not rule matching.
- **Latency when not overloaded**: p50 11 to 26 ms, p99 70 to 250 ms for the 2 to 5% match runs; p99 about 840 ms at 1,000 new alerts per second on four workers.
- Rows with latencies in seconds are overload runs: the send rate was above capacity, events queued, and "processed /s" is the number to read.
- **Accuracy**: webhook runs time both ends with the same clock. Kafka runs take the start time from the producer on the host and the end time inside Docker; those clocks can differ by some tens of milliseconds, so treat small Kafka latencies as approximate.
- The first row is the dedup check, not a throughput run: 500 identical events, one alert, counter 500, one notification.

**Known limits, stated plainly**

- The demo has one Postgres and one broker node. Both are single points of failure here; in production they would be a replicated Postgres and a three-node broker.
- Python is the slowest of the languages we considered for the event path. The design answers that with batching and more workers rather than a faster runtime.
- Creating an alert costs about three database round trips. When most events create new alerts, the database round trips are the bottleneck, not rule matching. Writing each batch with one multi-row statement is the next optimisation.
- The fake SMS gateway is one process and becomes the limit at high alert rates.
- The audit log is serialised by a lock so its hash chain has one order; that caps audited actions (human actions, not events) at a few hundred per second.

## 6. Incident practice

Herald's alert lifecycle follows recognised incident-management practice (the Atlassian Incident Management Handbook; see `INCIDENT-PLAYBOOK.md` for the full mapping and page references).

| Practice | In Herald |
|---|---|
| Know before the people affected do | Rules on any event from any system |
| Severity levels with written meanings | `critical` = level 1, `error` = level 2, `warning` = level 3; definitions served by `GET /api/meta` for the rule editor |
| One named person in charge | Whoever acknowledges a `critical` or `error` alert becomes its incident manager |
| Escalate early | On-call rota, team lead, the `escalate` action, channel fallback |
| Regular updates in a fixed shape | `GET /api/alerts/{id}/status-update`: what, since when, who is in charge, done so far, next step, next update time by severity |
| A timeline of what was done | The audit trail per alert, plus free-text notes |
| Blameless postmortem for serious incidents | `GET /api/alerts/{id}/postmortem`: a draft from the audit trail that names roles, not people, and leaves causes and actions as prompts |
| Never the same incident twice | The draft lists what was done outside the runbook, as input for changing it |

Not built: an acknowledgement deadline that escalates automatically, reminders to post updates, handover, follow-up action tracking.

## 7. Maintainability

- **One package, small modules, pure core.** Normalising, matching, routing, templating, runbook parsing, security primitives and incident drafts are pure functions with no I/O, covered by 84 tests that run in under a second. I/O lives in `store.py`, `worker/`, `api/`, `channels/`.
- **The SQL that carries guarantees is in one file** (`store.py`), with a comment on each statement.
- **The brief is the test suite**: `tests/test_brief_golden.py`.
- **Contracts are defined once** (`models.py`) and appear in the API's OpenAPI document.
- **Config is validated on load**; a bad file is reported and skipped, the rest still loads.
- To do: lock dependency versions, add linting and tests to CI, add integration tests that run against the compose stack.

## 8. What to build next (10 hours, 2 people)

The skeleton covers the event path, and the web UI is wired to it: every screen reads from and acts on the API (`ui/index.html`, one file, no build step). What remains is hardening on the pipeline side and depth on the UI side.

| Hours | Person A (pipeline) | Person B (UI) |
|---|---|---|
| 0–1 | Read the code and run the stack. Lock dependencies | Walk every screen with the "Send test events" menu; list the rough edges |
| 1–3 | Acknowledgement deadline with automatic escalation. Per-recipient throttle. Kill-a-worker and restart-Postgres tests | Channel preferences and on-call rota editable from the UI (API and screen) |
| 3–5 | Batch the alert writes; rerun the load series with 1 / 2 / 4 workers. Rate limit on the webhook | Rule editor depth: rule history and revert, clearer messages for invalid rules, keyboard use |
| 5–7 | LLM provider call for runbook structuring behind the existing guardrails (if allowed); otherwise update reminders in runbooks | Suggestions for noisy rules ("fired 400 times, never acknowledged"); add a source from the UI |
| 7–8 | Practise with unseen schemas and runbooks from `TEST-SCENARIOS.md`; fix what breaks | Editable status update and postmortem; split the single UI file if it has grown |
| 8–9 | README, the "what is mocked" table, final load numbers | Polish, empty states, phone width |
| 9–10 | Record the video together | Record the video together |

**If time runs short, cut in this order**: LLM, suggestions, preferences editor, rule history. Do not cut: the kill-a-worker test, the outage demo, measured numbers, practice with unseen schemas.

## 9. Five-minute video outline

| Time | Show | Judging point |
|---|---|---|
| 0:00 | The problem in two sentences; one alert-fatigue figure from `RESEARCH.md` | Presentation |
| 0:25 | `docker compose up`, the feed, three sources sending the brief's events | Level of implementation |
| 1:00 | A schema nobody has seen: send it, pick fields from it, build a rule, dry-run, save, alert appears | Flexibility, UX |
| 1:50 | 500 identical errors → one alert ×500. Gateway down → retries → delivered, nothing lost | "Don't flood people", reliability |
| 2:30 | The alert: runbook with values filled, a safe step run, a risky step approved by SMS reply, the audit trail | Runbooks |
| 3:15 | A free-text runbook pasted in and split into steps; status update and postmortem draft | Innovation |
| 3:45 | Scale: the load table with 1 / 2 / 4 workers, p50 and p99; where state lives | Scalability, throughput |
| 4:20 | What is mocked, how AI tools were used and controlled | Honesty, security |
| 4:45 | Close | |

## 10. Open questions for the mentors

1. May the running service call an external LLM during judging, or must it work offline? (It works offline today.)
2. Will the unseen schema arrive as JSON, or could it be XML, a FIX message or plain text?
3. Will judges run it from a fresh clone on their machine, and with which Docker resources?
4. Is the measured throughput judged on absolute numbers or on method and scaling behaviour?
5. Is a pre-built skeleton acceptable, and does the 10 hours start from the brief or from the first commit?
6. Does "phone call" need a real call in the demo, or is a simulated call screen enough?
7. Are teams expected to integrate a real chat tool (the Markdown runbook mentions a `#payments-incidents` channel)?
