# Herald — security

Herald will sit between a bank's production systems and the phones of the people who run them. It receives data from many systems, stores business payloads, sends messages outside the network, and can trigger actions such as restarting a service. This document says what can go wrong, what we do about it, and what the demo does not do.

**Status of this document.** The "Status" column records what the skeleton in this repository does today (checked on 20 November 2026 against the running stack and the tests), not what is planned. "Built" means the code exists and was exercised; "Not built" means it is design only. Update it whenever that changes.

## 1. Threat model

| # | Asset | Threat | Control | Status |
|---|---|---|---|---|
| 1 | Ingest endpoint | **Forged webhook**: someone who can reach the endpoint posts fake events to page people or hide a real incident in noise. | Each source has its own secret (named by `token_env`). A request must carry it as `X-Herald-Token`, or sign the raw body with it (HMAC-SHA256 over `<timestamp>.<body>`, headers `X-Herald-Timestamp` and `X-Herald-Signature: sha256=…`), compared in constant time. Otherwise 401. | **Built** and tested. Not built: an audit entry for rejected requests. A platform connected from the UI gets its own random token, shown once and stored only as a hash. A source with no secret configured is open, which the demo uses for `generic-webhook` |
| 2 | Ingest endpoint | **Replayed webhook**: a captured, validly signed request is sent again. | The signature covers a timestamp; requests more than 5 minutes old are rejected. | **Built** for signed requests (a plain token has no replay protection). Not built: two active secrets for rotation |
| 3 | Workers, database | **Oversized or deeply nested payload** exhausts memory or CPU. | Body limit of 1 MB and a nesting limit of 32, checked at the webhook before the event is queued. | **Built**. Not built: a cap on the number of fields (only the field catalogue is capped, at 200 per sample), the same limits on events arriving from Kafka topics, and per-source counts of rejected events |
| 4 | Alerts, rules, runbooks | **Cross-team access**: a user reads or edits another team's alerts or rules. | Every alert and rule has an owner. The API filters by the caller's teams and returns 404 for another team's alert. Roles: viewer, responder, editor, approver, admin. The live feed is scoped the same way. | **Built** in each endpoint and checked on the running stack; the audit trail shows a team only entries about its own alerts and rules. Not built: one shared data-access layer, Postgres row-level security as a second layer. Raw events and field names from a source are visible to every signed-in user unless the source lists `visible_to` teams, and no source in the demo does. Runbooks have no owner, so any rule editor can replace any runbook. In demo mode a request with no identity headers is treated as admin; with `AUTH_MODE=oidc` identity comes only from a signed-in session (rows 16 to 18) |
| 5 | UI, email | **Template injection from event values**: a payload value contains `{{…}}`, HTML or script and ends up rendered. | Templates only substitute `{{path}}` with three fixed filters; substitution is single-pass, so a value containing `{{…}}` is printed as text. Email HTML escapes every value. | **Built** and tested for templates and email. The web UI builds its pages from strings and passes every value from the service through one escaping helper; that has been checked by reading the code, not by an automated test |
| 6 | Internal network | **SSRF through runbook actions**: an `http_get` or webhook step is pointed at an internal address or the cloud metadata service. | Only http/https; no credentials in the URL; a host must be on the allowlist or resolve only to public addresses (private, loopback and link-local ranges, including 169.254.169.254, are refused); redirects are off; 5-second timeout. | **Built** and tested. Not built: a per-team allowlist (it is one list for the service), connecting to the address that was checked (so DNS rebinding is not covered), a response size cap, a separate network segment for actions |
| 7 | Target systems | **Command injection through runbook actions**: an event value is passed into a shell. | There is no free-form command action. Actions are functions in a registry; event values are passed as data. | **Built** |
| 8 | Runbook actions | **Approval spoofing**: someone approves a risky action by guessing a code or pretending to be the approver. | The code is random, single-use, expires after 15 minutes and is stored only as a hash bound to one approval and one approver. A reply counts only from the approver's own number. | **Built** and checked on the running stack (wrong person, wrong code and reuse are all refused). Not built: verifying the SMS provider's signature on inbound replies (the gateway is a mock), limiting and auditing failed attempts. With 4-digit codes the attempt limit matters |
| 9 | Runbook actions | **Approval replay**: a used or expired code is sent again. | The approval is claimed in the same transaction that records the decision; a second use finds nothing pending. | **Built** |
| 10 | Runbook actions | **Self-approval**: the person who asked for a risky action approves it. | The approver must be a different person from the requester and hold the approver role; a step marked `role:team-lead` needs that team's lead. | **Built** |
| 11 | Audit trail | **Tampering**: someone edits or deletes history to hide what was done. | Each entry stores the hash of the previous one, and `GET /api/audit` reports whether the chain is intact. A database trigger refuses UPDATE and DELETE on the table. | **Built**. Not built: a database role without those rights, export to a write-once store |
| 12 | Provider credentials | **Secrets leakage** through config files, logs or the UI. | Secrets come only from environment variables. Rules, runbooks and source config hold the names of secrets, never values, and the API never returns them. | **Built**. Not built: explicit masking in logs, a secret manager with rotation. The compose file ships demo defaults that must be changed |
| 13 | Business data | **Sensitive data on SMS and voice**: trade or client details travel over channels we do not control. | SMS and voice carry severity, the alert title and a link. The payload stays behind the API. | **Built**, with one caveat: the title comes from the rule's template, so a rule can put payload values into it. Not built: per-source redaction lists, retention limits |
| 14 | Whole service, people | **Alert storm**: a failing system sends a flood that slows the service or buries people in messages. | The broker absorbs bursts. Identical events become one alert with a counter. A rule with a group window re-notifies at most once per window. | **Built** and measured. Not built: a per-recipient throttle, a rate limit on the webhook (it answers 503 with `Retry-After` only when the broker is unavailable) |
| 16 | Who is calling | **Forged identity**: a client claims to be someone else, or uses a stolen session. | With `AUTH_MODE=oidc`, identity comes only from single sign-on (OIDC authorization code with PKCE, ID token checked against the provider's keys) and a server-side session; identity headers are ignored. The browser holds a random token, the database only its hash. Cookie HttpOnly, SameSite=Strict, Secure on https; `Origin` must match on writes; idle 30 min, absolute 8 h; sign-out revokes at once. Roles and teams come from the provider's `roles` claim; no Herald role means refused. See `docs/SSO.md` | **Built** and tested (`tests/test_auth.py`), run end to end against Keycloak. Not built: SCIM, back-channel logout, MFA check in Herald (the provider enforces it), service-account tokens. Demo mode keeps the header switcher |
| 17 | Runbook actions | **Approval from an unattended or stolen session**: someone uses a signed-in browser to approve a risky action. | Approving needs a credential check in the last 5 minutes (step-up, RFC 9470); otherwise the person is sent to the provider to sign in again. | **Built** for UI approvals. SMS replies still bypass it (row 8) |
| 18 | Sign-in | **Login CSRF and open redirect**: a victim's browser is made to finish the attacker's sign-in, or is sent to a phishing page after signing in. | `state`, `nonce` and the PKCE verifier live in a 10-minute, HMAC-signed, HttpOnly cookie and must match on return; the page to return to must be a path on this site. | **Built** and tested |
| 19 | Emergency accounts | **Password guessing, credential stuffing, code replay** against the break-glass accounts. | scrypt hashes; mandatory TOTP; a code works once (step claimed in the database); one answer for every failure and a dummy hash check for unknown usernames; lockout after 5 failures per username or 20 per address in 15 minutes; `Origin` check; every success audited as `break_glass`, every lockout as `login_locked`. See `docs/SSO.md` | **Built** and tested (`tests/test_local_login.py`, `tests/test_e2e_signin.py`). Not built: paging security on each use, a TOTP enrolment page, clean-up of old attempts |
| 20 | Whole service | **Access from outside the corporate network**, for example from a personal computer with a valid password. | `ALLOWED_NETWORKS` refuses people-facing routes from other networks before any credential is checked; `X-Forwarded-For` believed only from `TRUSTED_PROXIES`; compose publishes ports on 127.0.0.1 only and keeps Postgres on an internal network; in production, Entra Conditional Access with named locations and compliant devices | **Built** (layers 1 and 2) and tested; layer 3 is provider configuration, described not run. Machine routes are not behind the rule (they have their own credentials); a per-source network allowlist is not built |
| 15 | Supply chain | **Dependency risk**: a vulnerable or malicious package. | A short dependency list; fixed image tags for Postgres and Redpanda. | **Partly**. Python dependencies are lower bounds, not pinned, and the Mailpit image is `latest`. To do: commit a lock file and install from it, pin Mailpit, add vulnerability scanning |

Two further points that are not attacks but matter to a bank:

- **Login.** `AUTH_MODE=oidc` is real single sign-on: a local Keycloak in the demo (`docker compose --profile sso`), the organisation's provider (Microsoft Entra ID, or any OIDC provider) in production, with roles and teams from the directory. `AUTH_MODE=demo` keeps a role switcher for walkthroughs and is not authentication. See `docs/SSO.md` and section 5.
- **Transport.** The demo runs on one machine over plain HTTP between containers. In production, traffic between services is encrypted and mutually authenticated.

## 2. The language model

Herald can use a language model for three things, all done by a person while **configuring** the service:

1. Turning a runbook written as Markdown or free text into structured steps.
2. Drafting a rule from a sentence such as "email the Rates team when a rates trade over 10 million is rejected".
3. Suggesting which fields of a new event schema hold the type, severity, time and service.

**Status today:** the guardrails (what may be sent, validation of what comes back, risk levels that cannot be lowered) are written and tested in `alertsvc/llm.py`. The call to a provider is not implemented, so the demo runs without a model. Auditing of model calls is not built.

The model is **never on the event path** and **never executes anything**. Events are matched, deduplicated and delivered by ordinary code whether or not a model is configured.

| Concern | What we do |
|---|---|
| Availability and dependence | The model is off by default. Every feature works without it: runbooks are shown as text with values filled in, rules are built by hand, field mappings are typed in. |
| Where data goes | The provider is a configuration switch: `none`, `anthropic`, `bedrock` or `internal-gateway`. A bank points it at its approved private endpoint; nothing in the code assumes a public API. |
| What is sent | Runbook text the user submits, the user's sentence, and the **shape** of a schema (field paths, types, and redacted sample values). Live event payloads are not sent. |
| Prompt injection | Event values are untrusted and are never placed in instructions. Text to be converted is passed as clearly delimited data, and the model has no tools. |
| What can come back | Output must match a fixed JSON schema. Steps may only use actions that exist in the registry; anything else is rejected, not guessed. |
| Risk levels | The risk level of an action is set in the action registry. The model cannot lower it: if it proposes "no approval" for a risky action, the registry's level wins. |
| Human control | Every result is a draft. A person sees it next to the original text and accepts, edits or discards it. Nothing a model writes is saved or run without that step. |
| Accountability | Every call is written to the audit trail: who asked, which provider and model, a hash of the prompt, and whether the draft was accepted. |

## 3. What is built, what is mocked

This table goes into the README, as the brief asks for a "description of what has been mocked".

| Area | In the demo | In production |
|---|---|---|
| Login | `AUTH_MODE=oidc`: real OIDC sign-in against a local Keycloak with demo users. `AUTH_MODE=demo`: identity headers from the UI's "Viewing as" switcher, no headers means admin | The organisation's provider (for example Entra ID, single tenant), roles from the directory, MFA by provider policy |
| Demo identity provider | Keycloak in dev mode, realm imported from `config/keycloak/herald-realm.json` (throwaway passwords and client secret) | Removed; the organisation's provider |
| Emergency accounts | One demo account (`breakglass`) whose password hash and TOTP secret are compose defaults, documented in `.env.example` | Values from the secret manager, a handful of named accounts, each use paging security |
| Network | Ports on 127.0.0.1; "corporate network" stood in for by loopback and private ranges | Private subnet behind the organisation's proxy/WAF; real corporate ranges in `ALLOWED_NETWORKS` |
| Demo tools | `/api/sim/*` lets the UI send sample events, read the fake phone and mailbox, and take the gateway down. On in demo mode; under SSO only with `DEMO_TOOLS=on` (the compose file sets it) and a signed-in person; otherwise 404 | Removed or disabled |
| Service-to-service traffic | Plain HTTP inside Docker Compose | Mutual TLS |
| Secrets | Environment variables from a local file | Secret manager with rotation |
| Database | One Postgres container | Highly available Postgres with backups |
| Broker | One Redpanda node | Three or more nodes |
| Email | Mailpit (captures mail, shows it in a web page) | The bank's mail relay: one channel class to change |
| SMS and phone | A fake gateway that shows messages and calls on a "phone" page and can send replies back | A real provider: one channel class and one inbound handler to change |
| Runbook action targets | Mock endpoints in the fake gateway (health check, workflow retry); restart, ticket and escalate return simulated results | The real systems, each behind one action class |
| Language model | Off (provider call not implemented) | The bank's approved private endpoint |

## 4. How we used AI tools — draft for the submission

> **Draft. Edit this to match what the team really did before submitting.** The brief asks teams that used AI tools to say how they used them and how they addressed security and privacy concerns.

**Which tools and for what.** We used Claude (through Claude Code) during development. We used it to read the brief and research existing products, to propose the architecture and data model, to generate a project skeleton, a clickable UI mockup and documentation, and to write and refactor code and tests under our direction. *[Add or remove tools and uses.]*

**What data the tools saw.** The tools were given the hackathon brief and the sample events, rules and runbooks printed in it, a publicly available incident-management handbook, plus the code we wrote for this project. They were given no firm data, no client or trade data, no internal documents and no credentials. *[Confirm, and confirm that sharing the brief with an external tool is allowed by the hackathon rules.]*

**How we kept control of the code.** Generated code was read and tested by the team before it was kept. The rule engine is covered by tests built from the brief's own examples. We did not accept dependencies we did not recognise. *[Pin versions with a lock file before saying they are pinned.]* *[Confirm what was reviewed and by whom.]*

**Secrets.** No secrets appear in prompts, in the repository or in generated files. The demo uses local, throwaway values loaded from the environment.

**AI inside the product.** Herald can optionally use a language model, with the limits described in section 2: configuration time only, off by default, redacted inputs, validated outputs, and a human accepts every result. In the submitted build the model is switched off: the guardrails exist, the provider call does not. *[Update if you wire one up.]*

## 5. Sign-in and access

The full design is in `docs/SSO.md`; this is the summary for reviewers.

- **One source of identity.** People sign in at the organisation's identity provider. Herald stores no passwords. The provider's `roles` claim carries the Herald role (`viewer` to `admin`) and team memberships (`team.<id>`). Someone the provider signs in but who holds no Herald role is refused, and the refusal is audited.
- **Least privilege and separation of duties.** The API filters every read and write by the caller's teams (row 4). A risky action needs an approver who is not the requester (row 10) and who has signed in within the last five minutes (row 17).
- **Leavers.** Removing someone from a group or role at the provider removes their access at their next sign-in, at the latest when the session expires after 8 hours. Revoking a session row ends it at once. Back-channel logout and SCIM, which would make this immediate and automatic, are not built.
- **Single tenant.** With Entra ID, Herald accepts tokens from one tenant only and rejects `common`. Assignment is required, so only people given a role can sign in.
- **MFA.** Enforced by the provider (Entra Conditional Access, a Keycloak authentication flow). Herald does not read `amr` or `acr` yet.
- **Fail closed.** In SSO mode the API does not start with the default secret or without a provider.

## 6. Chat channels (Slack, Teams): design, not built

Chat is a delivery channel, not a way to sign in. A Slack or Teams account does not carry the directory's roles. A Slack account may not be under the organisation's MFA policy, and disabling someone in the directory does not close a chat-based route into Herald. The sign-in page therefore has no "Continue with Slack" button. A chat channel would follow these rules:

| Concern | Design |
|---|---|
| Message content | Same as SMS: severity, alert title and a link. Payloads and runbook values stay behind the API |
| Requests from the chat platform | Verified before anything else: Slack's signing secret (HMAC over timestamp and body, 5-minute window), the same pattern as Herald's webhooks; for Teams, the Bot Framework JWT |
| Who pressed the button | A chat user id counts only after a one-time **Connect Slack / Teams** step, done while signed in to Herald through SSO, which stores the link. Unlinked users get a "sign in to Herald" message. In a Microsoft tenant, Teams users already are Entra identities, so no link step is needed |
| Acknowledge, resolve | Allowed from chat for linked users with the right role on that team, audited with `via: slack` |
| Risky actions | Never approved from chat. The button opens the alert in Herald, where step-up applies (row 17) |
| Network | Slack Socket Mode or the Teams bot service make outbound connections only, so no inbound endpoint is exposed for chat |

