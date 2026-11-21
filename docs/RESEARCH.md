# Herald — research

What already exists, what to borrow, what the evidence says about alert fatigue, and why we chose the building blocks we did.

**How to read this.** The research was done on 20 November 2026 by three research agents using web search. They mostly saw search snippets, not full pages. Anything they could not confirm from a primary source is marked **unverified** here and collected in section 5. Vendor comparison pages are marketing and were treated as weak evidence. Do not quote a figure from this file in the video without opening its source first.

## 1. Competitor landscape

**Summary.** No public product covers the whole brief; each covers a slice. Keep (open source) is the nearest overall. Alertmanager is the reference for grouping and timing. The commercial on-call products have the best routing screens. Runbook automation tools are not connected to alert routing.

### 1.1 Status changes that matter

Several well-known products changed status recently. Citing them as current options would be a mistake.

| Product | Status in November 2026 | Source |
|---|---|---|
| Opsgenie | Atlassian stopped selling it on 4 June 2025. Support ends and data is deleted on 5 April 2027. Customers are moved to Jira Service Management or Compass. | [Atlassian announcement](https://www.atlassian.com/blog/announcements/evolution-of-it-operations), [migration FAQ](https://support.atlassian.com/opsgenie/docs/what-to-expect-from-opsgenie-migration/), [migration page](https://www.atlassian.com/software/opsgenie/migration?tab=select-plan) |
| Grafana OnCall (open source) | Maintenance mode from March 2025, archived on 24 March 2026 (read-only, AGPLv3). SMS, phone and push depended on a Grafana cloud connection that ended the same day. The supported path is the paid Grafana Cloud IRM. No established community fork was found. | [Docs](https://grafana.com/docs/oncall/latest/set-up/open-source/), [repo](https://github.com/grafana/oncall) |
| Keep (keephq) | Acquired by Elastic, completed May 2025; Elastic said it stays open source. The repo is MIT except the `ee/` folder, which is commercial. Latest release seen: v0.55.0, dated "03 Nov" (year not shown, unverified). Elastic Workflows in Kibana is the successor direction; whether it is a preview or generally available is unverified. | [Elastic](https://www.elastic.co/blog/elastic-and-keep-join-forces), [LICENSE](https://github.com/keephq/keep/blob/main/LICENSE), [releases](https://github.com/keephq/keep/releases), [Elastic Workflows](https://www.elastic.co/search-labs/blog/elastic-workflows-automation) |
| Moogsoft | Bought by Dell in 2023, renamed APEX AIOps Incident Management in 2024. Quote-only. | [BigPanda comparison page](https://www.bigpanda.io/compare/moogsoft/) (a competitor's page) |
| FireHydrant | Freshworks announced the acquisition on 15 December 2025; a January 2026 Freshworks blog says it closed. | [Press release](https://www.freshworks.com/press-releases/freshworks-to-deepen-its-it-service-and-operations-portfolio-with-acquisition-of-firehydrants-ai-native-incident-management-and-reliability-platform/) |
| Zenduty | Acquired by Xurrent in February 2025; rebranded Xurrent IMR from 20 November 2025. | [Rebrand post](https://zenduty.com/blog/zenduty-is-rebranding-to-xurrent-imr) |
| Squadcast | Now SolarWinds Incident Response. The March 2026 rebrand date is from secondary sources only (unverified). | — |
| Splunk On-Call | No end of life announced. Competitors call it "maintenance mode" (unverified). | — |
| xMatters | Active as Everbridge xMatters, with Flow Designer releases through October 2026. | [Support](https://support.xmatters.com/hc/en-us/sections/13166375751323-Flow-Designer) |
| AWS Incident Manager | Closed to new customers on 7 November 2025, no new features. | [AWS](https://docs.aws.amazon.com/incident-manager/latest/userguide/incident-manager-availability-change.html) |
| Shoreline | Reported to be bought by Nvidia in June 2024, never confirmed in what was found. Treat as unavailable (unverified). | — |
| Azure Automation | Update Management retired 31 August 2024. Older Python and PowerShell runbook runtimes retire 30 September 2026 (third-party source, unverified). | — |
| Rundeck | Community edition is Apache-2.0. The paid editions are named PagerDuty Process Automation and Runbook Automation. The latest version is unclear: the official page says 5.20.1 (May 2026), another site says 6.0.0 (unverified). | [Licensing](https://docs.rundeck.com/docs/about/licensing.html) |

Open-source projects that are actively maintained: Alertmanager 0.35.0, 9 November 2026 ([releases](https://github.com/prometheus/alertmanager/releases)); GoAlert v0.35.0, 21 September, year inferred as 2026 ([releases](https://github.com/target/goalert/releases)); HolmesGPT, a CNCF Sandbox project since October 2025 ([CNCF](https://www.cncf.io/projects/holmesgpt/)); StackStorm and Alerta 9.1.0 (both from an aggregator site, unverified).

### 1.2 Notes per product, by slice

**Alert routing and on-call**

- **Prometheus Alertmanager** (Apache-2.0). Takes alerts as flat sets of string labels, so not arbitrary JSON. Routing is a tree of label matchers in a YAML file with no runtime UI. Alerts are identified by their label set. `group_by` forms groups; `group_wait` (default 30 s) delays the first notification; `group_interval` (5 m) spaces later updates; `repeat_interval` (4 h) re-sends. It also has inhibition rules, silences and time intervals. [Configuration](https://prometheus.io/docs/alerting/latest/configuration/). *Borrow:* the group-by plus wait / interval / repeat timing model.
- **Grafana OnCall / Grafana Cloud IRM**. Per-integration webhooks. Routes are tried in order and the first match wins; they are written as Jinja2 templates or label matchers over the whole payload. Templates also define the grouping key and the resolve and acknowledge signals. Escalation chains are copied at the moment an alert group is created, so later edits do not disturb alerts in flight. IRM is a paid add-on. [Routing](https://grafana.com/docs/grafana-cloud/alerting-and-irm/irm/configure/escalation-routing/), [routing rules](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/irm/escalation-and-routing/routing-rules.md), [grouping](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/escalation-and-routing/alert-grouping/). *Borrow:* templates over arbitrary payloads for grouping key, resolve signal and title.
- **PagerDuty** (commercial). `dedup_key` is scoped per integration: an event with the key of an open incident is appended to it. Event Orchestration can set the key and resolve across services. The default Events API limit is 120 events per minute per key. Automation Actions call Rundeck jobs; a per-run approval gate could not be confirmed (unverified). Its SRE Agent claims to write and update runbooks from what responders did; whether a human reviews those updates is not documented. [Event management](https://support.pagerduty.com/docs/event-management), [Rundeck actions](https://support.pagerduty.com/docs/rundeck-actions), [Automation Actions](https://support.pagerduty.com/docs/automation-actions), [SRE Agent blog](https://www.pagerduty.com/blog/ai/we-built-an-sre-agent-with-memory-and-its-transforming-incident-response/).
- **Opsgenie / Jira Service Management**. An "alias" deduplicates alerts and increments a Count on the open alert. Matching is account-wide on the alias alone, so teams prefix it with the service or cluster. [Deduplication](https://support.atlassian.com/jira-service-management-cloud/docs/what-is-alert-deduplication/).
- **incident.io** (commercial). Alert routes evaluate payload expressions against a Catalog that maps service to team to schedule. Routing is a staged pipeline: filter, escalation, incident creation, chat. Grouping is by shared attributes and a time window. [Docs](https://docs.incident.io/on-call/alert-routing), [blog](https://incident.io/blog/intelligent-alert-routing-vs-escalation).
- **Rootly** (commercial). A generic webhook source maps arbitrary payloads to named alert fields with Liquid, with a payload preview. Routing uses JSONPath and field conditions. Dedup uses a stable ID per source with an exact-body match as a backstop. [Alert fields](https://docs.rootly.com/alerts/alert-fields), [routing](https://docs.rootly.com/alerts/alert-routing).
- **ilert** (commercial). Node-based "event flows" sit above alert sources. A Wait node can pause until support hours start. Grouping uses an alert key, with AI similarity grouping on the top plan. [Event flows](https://docs.ilert.com/alerting/overview/understanding-event-flows).
- **Better Stack** (commercial). Its AI SRE says it takes no action without approval (vendor claim). [Page](https://betterstack.com/incident-management).
- **Zenduty / Xurrent IMR**. Task templates (checklists mapped to roles) attach to services and are added to incidents automatically. The runbook is a checklist, not executable. [Docs](https://zenduty.com/docs/task-templates/).
- **GoAlert** (Apache-2.0, Go and Postgres). Rotations, escalation policies, SMS and voice through Twilio. No rule engine and no arbitrary-schema ingest. A good model for on-call and delivery state.
- **OneUptime**. Apache-2.0 core with a separately licensed `ee/` directory. [Repo](https://github.com/oneuptime/oneuptime).
- **Alerta** (open source). Deduplicates onto one screen; fixed schema (resource, event, environment).
- **Datadog On-Call and Bits AI SRE**. Bits AI SRE became generally available in December 2025 and reads runbooks when an alert fires. Metered. [Press release](https://www.datadoghq.com/about/latest-news/press-releases/datadog-launches-bits-ai-sre-agent-to-resolve-incidents-faster/).

**Alert management (AIOps)**

- **Keep**. Each provider declares the fields that make up the fingerprint (default: the alert name). Dedup rules are editable: partial (named fields) or full (all fields except ignored ones). Workflows are YAML with triggers, steps and actions. [Deduplication](https://docs.keephq.dev/overview/deduplication), [fingerprints](https://docs.keephq.dev/overview/fingerprints), [workflow triggers](https://docs.keephq.dev/workflows/syntax/triggers), [workflows](https://docs.keephq.dev/workflows/overview).
- **BigPanda, Moogsoft**. Commercial event correlation. Only their status was checked, not how they work.
- **Robusta / HolmesGPT** (Apache-2.0). Read-only investigation agent that writes findings back to chat or the source.

**Runbook and automation**

- **Rundeck**. Runbook jobs with access control. Approval features appear to be in the paid edition (unverified).
- **StackStorm** (Apache-2.0). Rules engine with trigger criteria, Orquesta YAML workflows, actions in packs. `core.ask` "inquiries" pause a workflow until a response arrives. [Inquiries](https://docs.stackstorm.com/inquiries.html), [Orquesta](https://docs.stackstorm.com/orquesta/languages/orquesta.html).
- **AWS Systems Manager Automation**. The `aws:approve` step pauses a runbook until N approvers respond; approvers are notified through SNS; requests expire after 7 days. [Docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/automation-action-approve.html).
- **New Relic Workflow Automation**. A paused run is labelled "Awaiting human approval". [Docs](https://docs.newrelic.com/docs/workflow-automation/workflow-examples/advanced/human-in-the-loop/).
- **AI SRE agents**. Cleric (seed round December 2025, private beta, human approval for fixes), Resolve AI (Series A February 2026), Traversal (Series B November 2026, one source, unverified), Aurora (Apache-2.0, [repo](https://github.com/Arvo-AI/aurora)). All closed or early; they investigate incidents and do not route alerts.

**Notification infrastructure**

- **Novu** (MIT core with an enterprise folder): digest engine and branching workflows. [Repo](https://github.com/novuhq/novu).
- **Knock, Courier** (commercial): batching, delay, throttle, and preferences per channel, workflow and category.
- **Apprise** (BSD): one URL scheme per service; the quickest way to add many channels.
- **ntfy** (Apache-2.0 and GPLv2): HTTP publish/subscribe; a possible mobile push channel.

**Generic engines.** n8n uses the Sustainable Use Licence, so it is not open source and should not be embedded. Node-RED is Apache-2.0. ElastAlert 2 is Apache-2.0 with rule types such as frequency and spike.

### 1.3 Comparison table

Y = yes, P = partial or paid tier, N = no or not found, ? = unverified.

| Product | Any-schema ingest | Runtime rule edit | AND/OR filters | Per-rule templates | Channel prefs / schedules | Dedup | Grouping / throttle | Runbook rendering | Actions with approval | Auto-resolve | Knowledge base auto-update |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Alertmanager | N (labels) | N (file reload) | P | Y | P | Y | Y | N | N | Y | N |
| Grafana Cloud IRM | Y | Y | Y | Y | Y | Y | Y | N | P | Y | N |
| PagerDuty | P | Y | Y | P | Y | Y | Y | P | Y (Rundeck) | Y | P (agent) |
| Opsgenie | P | Y | Y | P | Y | Y | P | N | N | Y | N |
| incident.io | Y | Y | Y | Y | Y | P | Y | P | P | Y | ? |
| Rootly | Y | Y | Y | Y | Y | Y | P | P | P | Y | ? |
| ilert | P | Y | Y | P | Y | Y | Y | N | P | Y | N |
| Zenduty | P | Y | P | P | Y | P | P | P (tasks) | N | P | N |
| GoAlert | N | Y | N | N | Y | P | N | N | N | N | N |
| Alerta | P | P | P | N | N | Y | P | N | N | Y | N |
| Keep | Y | Y | Y | Y | P | Y | P | N | P | Y | N |
| BigPanda / Moogsoft | P | Y | Y | ? | ? | Y | Y | ? | ? | Y | ? |
| Rundeck | N | Y | N | Y | N | N | N | Y | Y (paid) | N | N |
| StackStorm | Y | Y | Y | Y | N | N | N | N | P | N | N |
| AWS SSM Automation | N | Y | N | N | N | N | N | Y | Y | N | N |
| AI SRE agents | Y | N | N | N | N | P | P | Y | Y | P | P |
| Novu | N | Y | P | Y | Y | N | Y (digest) | N | N | N | N |
| n8n / Node-RED | Y | Y | Y | Y | N | N | N | N | P | N | N |
| ElastAlert 2 | P | N | P | Y | N | P | Y | N | N | N | N |
| **Herald (target)** | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Could |

The AI SRE row blends vendor claims from several products. Keep's use of CEL for filters comes from background knowledge, not from this research (unverified).

### 1.4 Ideas to borrow

| # | Idea | From |
|---|---|---|
| 1 | Grouping as group-by fields plus wait, interval and repeat timings, each shown as an explicit setting | Alertmanager |
| 2 | Dedup as an editable rule: key fields, or all fields except ignored ones | Keep |
| 3 | A payload preview where you click a field to use it | Rootly |
| 4 | When / If / Then rule cards with an all/any switch | Sentry |
| 5 | A rule-computed dedup key, and a "resolved" event that closes the alert | PagerDuty, Grafana IRM |
| 6 | Copy the escalation settings onto the alert when it opens, so edits do not disturb alerts in flight | Grafana OnCall |
| 7 | Wait until working hours start | ilert |
| 8 | A catalogue of service → team → schedule, so ownership changes need no rule edits | incident.io |
| 9 | An approval step with an expiry (and N of M approvers) | AWS SSM Automation |
| 10 | Reply codes in the SMS and keypad actions on the call | PagerDuty, Grafana IRM, Jira Service Management |
| 11 | An occurrence counter on the alert; prefix the key with service and environment to avoid collisions | Opsgenie |
| 12 | Tuning suggestions with evidence, impact estimate and a good/bad rating | PagerDuty AI Orchestrations |
| 13 | Turn what responders did into proposed runbook updates | PagerDuty SRE Agent, Cleric |

### 1.5 Gaps we can demo as innovation

| Gap | Why it is a gap | Our plan |
|---|---|---|
| Bring any schema | Keep needs per-provider declarations; Rootly needs manual Liquid mapping. Nobody found infers fields from a few samples. | Fields are discovered from the first event; click to build a rule; dry-run against recent events. **Must / Should.** |
| Free text → executable steps | Zenduty is a checklist; Rundeck needs hand-written jobs. No product was found that converts free text into a reviewed plan. | Three levels of runbook handling, the last one with a language model and a human review. **Should.** |
| Approval by reply | AWS SSM approves in the console or CLI only. No product was found that approves an action by replying to the SMS (unverified for PagerDuty Workflow Automation). | Reply `YES 4821` to the SMS; single-use code, audited. **Should.** |
| "Why didn't it match?" and dry-run on history | Not found widely. Grafana has a routing preview; Rootly lists three manual checks. | Show the failing condition on a real event; "would have matched 7 of your last 50". **Should.** |
| Published throughput | Few vendors publish numbers. | A reproducible load test with p50/p99 and a 1 / 2 / 4 worker table. **Must** (it is also a judging criterion). |
| Learned runbooks with review | PagerDuty claims it but documents no review step. | A diff the team accepts or rejects, linked to the incident. **Could.** |
| Auto-close with evidence | Close only when the condition has been absent for N minutes, and show the check. | **Could.** |

### 1.6 Open-source code worth reading

| Project | Licence | Verdict |
|---|---|---|
| [prometheus/alertmanager](https://github.com/prometheus/alertmanager) | Apache-2.0 | Read the dispatcher and group-timing code and imitate it. Not worth embedding: its data model is flat labels. |
| [keephq/keep](https://github.com/keephq/keep) | MIT; `ee/` commercial | Read the fingerprint, dedup-rule and YAML workflow models. Python, so the closest to our stack. Do not copy from `ee/`. |
| [grafana/oncall](https://github.com/grafana/oncall) | AGPLv3, archived | Read the templating and escalation-snapshot design only. Do not depend on it. |
| [target/goalert](https://github.com/target/goalert) | Apache-2.0 | Best reference for schedules, rotations and SMS/voice delivery state. |
| [alerta/alerta](https://github.com/alerta/alerta) | Apache-2.0 (unverified) | Read the dedup and alert state machine. Python. |
| [StackStorm/st2](https://github.com/StackStorm/st2) | Apache-2.0 | Read the rule criteria and action model. Too heavy to embed. |
| [rundeck/rundeck](https://github.com/rundeck/rundeck) | Apache-2.0 | Could be called as an action backend later. |
| [novuhq/novu](https://github.com/novuhq/novu) | MIT; `enterprise/` excluded | Could serve as a notification layer but is a large stack; not for 10 hours. |
| [caronc/apprise](https://github.com/caronc/apprise) | BSD-2 (sources conflict with BSD-3) | Easiest way to add extra channels behind our channel interface. Python. |
| [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy) | Apache-2.0 / GPLv2 | Easy mobile push channel. |
| [HolmesGPT](https://github.com/HolmesGPT/holmesgpt), [Aurora](https://github.com/Arvo-AI/aurora) | Apache-2.0 | Imitate the investigation loop if we ever add one. |
| n8n | Sustainable Use Licence | Do not embed. |

## 2. UX patterns worth copying, by screen

**Rule editor**

- When / If / Then cards with all/any/none per block and a throttle on repeats: [Sentry](https://docs.sentry.io/product/monitors-and-alerts/alerts/create-alerts/). The closest fit for non-engineers.
- Template preview against a sample payload, and stable named fields instead of raw paths: [Grafana IRM](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/irm/escalation-and-routing/routing-rules.md), [Rootly](https://docs.rootly.com/alerts/alert-routing).
- A "Preview routing" button on the policy tree: [Grafana Alerting](https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/notification-policies/). The tree itself is powerful but hard for newcomers.
- Show every rule that matched, with recipients de-duplicated, instead of first-match-wins: [Datadog](https://docs.datadoghq.com/monitors/notify/notification_rules/). This is the model we use.
- Type a sentence, get editable filter chips: [Linear AI filters](https://linear.app/changelog/2023-06-01-ai-filters), [pattern description](https://aiuxplayground.com/pattern/nl-filter).
- *For Herald:* When / If / Then cards; fields picked from a sample event; a plain-language summary that updates as you edit; a live "would have matched N of your last 50 events" panel; for any event, which condition failed. A rule written by a language model is never saved without a person confirming it.

**Live feed and alert detail**

- Severity shown by colour **and** icon **and** text label, never colour alone (WCAG 1.4.1). This is design practice, not taken from a product's docs.
- One row per group with a count badge ("×500") and first/last seen; auto-resolve on the source's resolve signal: [Grafana IRM grouping](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/escalation-and-routing/alert-grouping/).
- Acknowledge, resolve and snooze as one-click buttons on each row, with keyboard shortcuts; a timeline of every action in the detail pane; an empty state that says "No alerts. Send a test event." (design practice).

**Runbook on the alert**

- Variables filled from the incident and conditions on steps: [FireHydrant](https://docs.firehydrant.com/docs/runbook-basics).
- Keep judgement calls for humans (severity, large production changes, customer wording): [Rootly](https://docs.rootly.com/workflows/manually-running-workflows).
- A paused step labelled as waiting for approval: [New Relic](https://docs.newrelic.com/docs/workflow-automation/workflow-examples/advanced/human-in-the-loop/), [Ansible approval nodes](https://docs.redhat.com/de/documentation/red_hat_ansible_automation_platform/2.7/develop-ref_controller_approval_nodes).
- *For Herald:* a checklist where each step has a state (to do, running, awaiting approval, done, failed), shows its filled-in values, and keeps its output visible.

**Channel messages**

- SMS is cut at 160 characters and carries reply codes: [PagerDuty SMS](https://support.pagerduty.com/docs/sms-notifications).
- A call reads the alert and offers keypad actions: [PagerDuty phone](https://support.pagerduty.com/docs/phone-notifications), [Grafana IRM phone and SMS](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/notify-responders/phone-and-sms/).
- Text commands such as "ack 4421": [Jira Service Management](https://support.atlassian.com/jira-service-management-cloud/docs/set-up-sms-and-voice-notifications/).
- *For Herald:* SMS like `[CRIT] Rates: trade rejected. Reply ACK 4821 or YES 4821 to retry. herald.example/a/4821`; a call that says the severity, one sentence, and "press 1 to acknowledge"; an email with severity and title in the subject, a button, and the runbook steps. All three previews are shown in the rule editor.

**Onboarding.** No single product doc was found to cite. The pattern is: create a team, copy a `curl` command, watch a "waiting for first event" state turn green, accept a suggested rule. Show the elapsed time to first alert.

**Suggestions ("propose fixes / enhancements").** PagerDuty proposes rules from 90 days of history, each with a description, estimated impact, confidence and a good/bad rating; generally available from 1 June 2026 for AIOps customers: [AI Orchestrations](https://support.pagerduty.com/main/docs/ai-orchestrations). Suppressed alerts stay viewable: [PagerDuty blog](https://www.pagerduty.com/blog/3-steps-suppressing-alert-noise/). Advice-only examples: [Grafana IRM](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/guides/best-practices/alert-groups.md), [Sentry](https://docs.sentry.io/product/alerts/best-practices/). *For Herald:* a suggestions list driven by simple queries ("fired 412 times, never acknowledged"), each with its evidence and a one-click change.

## 3. Alert-fatigue figures for the pitch

| Figure | Source | Year | Kind of source |
|---|---|---|---|
| 57% say fewer than 30% of their alerts are actionable; 77% of on-call teams get at least 10 alerts a day; 83% ignore alerts at least some of the time; 44% had an outage linked to suppressed or ignored alerts (1,000+ respondents) | NeuBird AI, State of Production Reliability and AI Adoption, [via APMdigest](https://www.apmdigest.com/alert-fatigue-no-longer-morale-problem-its-reliability-risk-and-system-failure) | 2026 | Vendor survey, reported second-hand |
| 75% of UK IT teams had outages from ignored or suppressed alerts; 13% globally deliberately ignore or suppress alerts | Splunk, State of Observability 2025, [via ITPro](https://www.itpro.com/software/it-teams-are-battling-a-surge-in-outages-over-missed-critical-alerts) | 2025 | Vendor survey, reported second-hand |
| Alert fatigue is the biggest obstacle to faster incident response for 30% of respondents (1,363 responses, 76 countries) | [Grafana Labs Observability Survey](https://grafana.com/press/2026/03/18/grafana-labs-4th-annual-observability-survey-reveals-a-field-at-a-crossroads-ai-economics-complexity-and-the-enduring-power-of-open-source/) | 2026 | Vendor survey, primary source |
| Median toil rose from 14% to 20% of work, the first rise in five years | [Catchpoint SRE Report 2025](https://catchpoint.com/learn/sre-report-2025) | 2025 | Vendor report |
| Every page should be actionable; frequent pages lead people to skim or ignore them | [Google SRE Book, chapter 6](https://sre.google/sre-book/monitoring-distributed-systems/) | 2016 | Engineering guidance, not a statistic |

**Caveat.** These are vendor surveys: the companies that publish them sell tools for this problem, the samples are self-selected, and two of the four reached us through news articles and not the reports themselves. On a slide, say "a 2026 vendor survey found…" and name the vendor. The Grafana 2025 figure of 33% comes from a single video summary and should not be used. The often-repeated "62% of alerts are ignored" has no source we could find.

## 4. Building blocks: what we found and what we chose

The team's constraints decide most of this: Python, two people, ten hours, one `docker compose up`.

| Topic | What the research found | What we chose and why |
|---|---|---|
| Language | Go has the best throughput and the reference CEL evaluator; TypeScript shares types with the UI; Python is the slowest on the event path. [TechEmpower](https://www.techempower.com/blog/tag/tfb/) shows Go well ahead on database-backed tests, but no like-for-like numbers for this kind of pipeline were found. | **Python**, because that is what the team writes fastest. We offset the speed with stateless workers that scale out, batching of counter updates, and rules compiled once. Our throughput claim must come from our own load test. |
| Rule language | CEL is safe, pre-compilable and expressive; the Python implementation is less mature than the Go one. JSONLogic is pure data and maps directly to an AND/OR builder but lacks glob. [react-querybuilder](https://react-querybuilder.js.org/docs/utils/export) exports CEL and JSONLogic. Benchmarks: [cel-go vs expr](https://beta.pkg.go.dev/github.com/franchb/cel-go-benchmarks), [expr discussion](https://github.com/expr-lang/expr/discussions/312), [cel-js](https://www.npmjs.com/package/@marcbachmann/cel-js), [OPA performance](https://www.openpolicyagent.org/docs/latest/policy-performance/). | **Our own JSON condition tree** (`all` / `any` / `not` over `{field, op, value}`) with a registry of operators. It is about a hundred lines of Python, has no `eval`, round-trips to the visual builder with no parsing, and a new operator is one function. CEL as an "advanced expression" field is a possible later addition. |
| Event format | CloudEvents needs `id`, `source`, `specversion`, `type` ([spec](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)). Alertmanager webhooks carry `alerts[]` with labels, annotations, `startsAt`, `endsAt`, `fingerprint` ([guide](https://hookdeck.com/webhooks/platforms/guide-to-prometheus-alertmanager-webhooks-features-and-best-practices)). | **No fixed schema.** Keep the raw payload, flatten it to dotted paths, fill four optional aliases (`@type`, `@severity`, `@timestamp`, `@service`) from a per-source mapping, and keep a catalogue of seen fields per source for the rule editor. Recognise Alertmanager batches and unpack them. |
| Dedup and grouping | Alertmanager timings and PagerDuty `dedup_key` are the reference designs ([Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/), [PagerDuty](https://support.pagerduty.com/docs/event-management)). A Postgres upsert (`INSERT … ON CONFLICT … DO UPDATE SET count = count + 1`) is atomic and durable under concurrent workers. Timers must not live in process memory. | **Postgres upsert** on `(rule_id, fingerprint)` for open alerts, with counters batched in the worker for a fraction of a second before writing. Group windows are a `next_flush_at` column that workers claim, so a restart loses nothing. |
| Delivery | Transactional outbox, `FOR UPDATE SKIP LOCKED`, idempotency keys, exponential backoff with jitter, fallback channel, dead-letter. Published numbers: Graphile Worker about 15,600 jobs per second unbatched ([docs](https://worker.graphile.org/docs/performance)); River ([background](https://brandur.org/river)); BullMQ ([self-published](https://bullmq.io/articles/benchmarks/bunjs-vs-nodejs/)). | **Hand-written Postgres outbox.** Notifications are written in the same transaction as the alert, so an alert cannot exist without its notifications. A few thousand deliveries per second is far more than mock channels need. |
| Broker | Redpanda: single binary, Kafka-compatible, light ([vendor page](https://redpanda.com/redpanda-vs-kafka)). NATS JetStream is smaller. Comparisons: [dev.to](https://dev.to/young_gao/real-time-event-streaming-kafka-vs-redis-streams-vs-nats-in-2026-34o1), [mqperf](https://softwaremill.com/mqperf/). Startup and memory figures are vendor or blog claims (unverified). | **Redpanda**, for ingest only. The brief names Kafka; any Kafka client works; one container. All sources write to one topic and workers share it as a consumer group. |
| Live updates | SSE is one-way, reconnects by itself, and resumes with `Last-Event-ID`. Fan-out across replicas needs pub/sub. Postgres `LISTEN/NOTIFY` has an 8,000-byte payload limit and no durability ([SSE article](https://dev.to/devopsdaily/realtime-without-a-websocket-service-1gk3), [NOTIFY scaling](https://pgdog.dev/blog/scaling-postgres-listen-notify)). The research recommended Redis pub/sub. | **SSE with Postgres `LISTEN/NOTIFY`**, sending ids only, and replay from Postgres on reconnect. We left Redis out: one component fewer to run, explain and secure. The same channel tells workers to reload rules, with a 30-second version poll as a safety net. Redis is the first thing to add if fan-out becomes the limit. |
| Mock channels | [Mailpit](https://github.com/axllent/mailpit) captures mail, has a web page and API, and can inject SMTP errors for retry demos. Real SMS/voice providers call back with signed form posts ([Twilio webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security), [TwiML Gather](https://www.twilio.com/docs/voice/twiml/gather)). The exact Twilio field names were not verified. | **Mailpit plus one fake gateway container** with a "phone" page and a reply box that calls our inbound endpoint. Each channel is one class behind `send()`, so a real provider is one file. |
| Hot reload | No sources; design advice. Database as source of truth, publish a change notice, poll as a fallback, mark each rule as file-managed or UI-managed so the two never overwrite each other. | As advised: `source: file` rules are reloaded when the YAML changes; `source: ui` rules are edited in the UI. |
| Runbooks | StackStorm Orquesta and Keep model steps as YAML with conditions; Keep uses `{{ … }}` templates. SSRF defences: [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), [PlanetScale on webhooks](https://planetscale.com/blog/securing-webhooks). Prompt injection: [summary of OWASP LLM01](https://developer.android.com/privacy-and-security/risks/ai-risks/prompt-injection). Only secondary summaries of the OWASP pages were read. | One step format (`text, kind, action, params, condition, approval`), three levels of handling for unknown formats, logic-less templates, an action registry with risk levels. See `SECURITY.md`. |
| Load testing | Use a constant arrival rate so slow responses do not hide latency (k6 `constant-arrival-rate`, or wrk2). A custom producer is needed anyway for Kafka. [Tool comparison](https://send-now.techempower.com/posts/web-benchmark-tools-2026.html), [k6 percentiles](https://tessl.io/registry/testland/k6-load-testing/files/references/latency-percentiles.md), [wrk2](https://raw.githubusercontent.com/giltene/wrk2/master/README.md). | **`loadtest/run.py`**: a Python producer for both sources at a fixed rate. Latency is `delivered_ts − ingest_ts` read from the outbox table, so it is true ingest-to-delivery time. |

**Throughput expectations.** No credible published numbers for a pipeline of this shape were found. The research agent's own estimate for Python with asyncio was "about a thousand to a few thousand events per second" per process; that is an estimate, not a measurement, and must not appear in the video. Pipelines like this are usually limited by database writes, which is why counters are batched.

## 5. Could not verify

Competitors

- Whether PagerDuty Runbook Automation, Rundeck (community), Rootly, FireHydrant or StackStorm have a built-in per-run approval step.
- Whether PagerDuty Workflow Automation can approve by reply.
- PagerDuty Event Orchestration's test-event screen and field autocomplete.
- Any test, preview or match-explanation feature in incident.io Alert Routes.
- Keep's release years, its mapping and correlation rule docs, and its use of CEL.
- Whether Elastic Workflows is a preview or generally available.
- Squadcast's rebrand date; Splunk On-Call "maintenance mode"; Shoreline's status; Traversal's funding round; Rootly's AI SRE and MCP release dates.
- The latest Rundeck version; Alerta's and Robusta's licences; Apprise's exact BSD variant.
- How Opsgenie alert policies map to Jira Service Management or Compass.

Figures

- The Grafana 2025 "33%" figure (single video summary).
- An incident.io blog claim of "2,000 alerts weekly, about 3% need action" (no cited source).
- The "62% of alerts ignored" figure (no source found).
- Any alert-noise figure in PagerDuty's 2025 State of Digital Operations report (none found).
- The NeuBird and Splunk figures were read in news articles, not in the reports.

Building blocks

- Independent benchmarks for JSONLogic, JMESPath, gojq, json-rules-engine, pg-boss and River.
- Redpanda and NATS startup time and memory use, and the Redpanda command-line flags for a small container.
- The OpenTelemetry log data model and Twilio's inbound SMS field names (not retrieved).
- The Rundeck and PagerDuty Runbook Automation step models; the StackStorm and Keep details came from search snippets.
- Throughput per language: estimates only.
- The accessibility and keyboard-shortcut conventions in section 2 are design practice, not sourced.
