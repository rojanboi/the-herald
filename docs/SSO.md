# Herald — single sign-on

With `AUTH_MODE=oidc` Herald signs people in through the organisation's identity provider (OpenID Connect). It keeps no passwords. Roles and team memberships come from the provider, a session lives on the server, and identity headers sent by a client are ignored. The demo runs a local Keycloak as the provider. Microsoft Entra ID, or any other OIDC provider, is a configuration change: the code is the same.

`AUTH_MODE=demo` (the default for `docker compose up`) keeps the "Viewing as" switcher for walkthroughs. That mode is a mock and is labelled as such in the UI and in `docs/SECURITY.md`.

The sign-in page has two ways in:

- **Continue with SSO** (and **Continue with Microsoft** when Entra is configured): the normal way for employees.
- **Emergency account**: username, password and a 6-digit code from an authenticator app, checked by Herald itself, for when the identity provider is down during an incident (see "Emergency accounts").

Neither works from outside the allowed networks (see "Network access").

## Run it with the demo provider

```bash
AUTH_MODE=oidc HERALD_SECRET=$(openssl rand -hex 32) docker compose --profile sso up --build
```

| What | Where |
|---|---|
| Herald (sends you to the sign-in page) | http://localhost:8080 |
| Sign-in page | http://localhost:8080/login |
| Keycloak admin console (manage users, groups, roles) | http://localhost:8180 — admin user and password are in `.env.example` (`KEYCLOAK_ADMIN*`) |

The API refuses to start in this mode if `HERALD_SECRET` is the default or no provider is configured (fail closed). Open Herald at exactly `http://localhost:8080`: the callback address and the cross-site check are tied to `PUBLIC_URL`.

### Demo users

Defined in `config/keycloak/herald-realm.json`, which also holds their throwaway passwords (demo only). People and emails match `config/teams.yaml`.

| User | Keycloak groups | Herald role | Teams |
|---|---|---|---|
| priya | Settlements | responder | settlements |
| marcus | Settlements, Settlements leads | approver | settlements |
| sam | Settlements, Pricing, Rates desk, Rule editors | editor | settlements, pricing, rates |
| tomas | Pricing | responder | pricing |
| ines | Pricing, Pricing leads | approver | pricing |
| elena | Rates desk, Rates desk leads | approver | rates |
| devon | Counterparty support, Counterparty support leads | approver | counterparty-support |
| anna | Herald admins | admin | all |
| nora | none | none: signs in at the provider but is refused by Herald | — |

### Five things to show

1. **Real sign-in.** Open http://localhost:8080, press **Continue with SSO**, sign in as `priya`. Only Settlements alerts are visible.
2. **Forged headers do nothing.** `curl -i -H 'X-Herald-User: person:demo' -H 'X-Herald-Role: admin' localhost:8080/api/alerts` answers `401 sign in required`.
3. **Access is managed in the directory.** In the Keycloak console (realm `herald` → Users → priya → Groups) remove her from *Settlements*. After she signs in again she has no Herald role and gets "You don't have access to Herald yet". The refusal is in the audit trail as `login_denied`. (`nora` shows the same without changing anything.) After a refusal the button asks the provider for credentials again, so another person can sign in; in Keycloak press the arrow next to the username to change it.
4. **Approving needs a fresh sign-in.** Have `priya` run step 3 of a Settlements alert (escalate, needs approval). As `marcus`, press **Approve** more than five minutes after signing in. Herald sends him to the provider ("Please re-authenticate to continue"), brings him back to the alert, and the next press approves. The audit shows his `login` with `"step_up": true`, then `approved`.
5. **Everything is recorded.** As `anna`, `GET /api/audit` shows `login`, `logout` and `login_denied` entries in the hash-chained log.

## Access model

One claim in the ID token, `roles`, carries everything, with the same values for every provider:

| Value | Meaning |
|---|---|
| `viewer` | Read alerts of own teams |
| `responder` | Acknowledge, resolve, assign, run safe runbook steps |
| `editor` | Change rules and runbooks of own teams |
| `approver` | Approve risky runbook actions (a different person from the one who asked) |
| `admin` | Everything, all teams |
| `team.<id>` | Member of that team (`team.settlements`, …; ids from `config/teams.yaml`) |

- The highest role wins. Unknown values are ignored, and so are teams Herald does not know.
- No Herald role means no access, even if the provider signed the person in. This is the same idea as Entra's "Assignment required".
- The person is identified by `preferred_username`. If that is not a directory id, Herald matches it by email, so Entra's `priya@yourorg.com` maps to the directory entry with that email.
- Roles are read at sign-in. A change in the directory applies at the next sign-in, at the latest when the session expires (8 hours).

Why a `roles` claim and not `groups`: in Entra these are App Roles. They are scoped to this one application, have readable values, and do not hit the group overage limit, where a token for a user in more than about 200 groups carries a link instead of the groups. In Keycloak they are client roles granted through groups.

## How the sign-in works

```
Browser            Herald API                               Provider (Keycloak / Entra)
   |  GET /login       |                                              |
   |  Continue with SSO|                                              |
   |------------------>|  state, nonce, PKCE verifier → signed cookie |
   |<-- 303 to provider (code_challenge S256) ----------------------->|
   |                   |                        person signs in (MFA) |
   |<-- 303 /auth/callback?code&state --------------------------------|
   |------------------>|  state == cookie? exchange code + verifier + client secret
   |                   |-------------------------------------------->|
   |                   |<------------------------- ID token (RS256) --|
   |                   |  check signature (JWKS), iss, aud, exp, nonce
   |                   |  roles → Herald role and teams; none → refused
   |                   |  session row (hash only) + audit "login"
   |<-- 303 to the page you asked for, session cookie                  |
```

| Control | What it stops |
|---|---|
| Authorization code flow with PKCE (S256) and a confidential client | A stolen authorization code is useless without the verifier and the client secret |
| `state` and `nonce` kept in a short-lived, HMAC-signed, HttpOnly cookie | Login CSRF (finishing someone else's sign-in in your browser) and replayed ID tokens |
| ID token checked against the provider's published keys: RS256 only, issuer, audience, expiry, nonce | Forged or swapped tokens, `alg: none`, HMAC tokens signed with a guessable key |
| `next` accepts only a path on this site | Open redirect to a phishing page after sign-in |
| Session token is random; the database stores only its SHA-256 | A copy of the sessions table cannot be used to sign in |
| Cookie `HttpOnly`, `SameSite=Strict`, `Secure` when `PUBLIC_URL` is https | Theft by page scripts, cross-site requests |
| `Origin` must match `PUBLIC_URL` on every write | Cross-site request forgery, as a second layer |
| Idle timeout 30 minutes, absolute 8 hours (`SESSION_IDLE_SECONDS`, `SESSION_MAX_SECONDS`) | Unattended or long-lived sessions |
| Sign-out revokes the row at once, then ends the provider session (Keycloak asks "Do you want to log out?", because Herald does not keep the ID token to send as a hint) | A copied cookie working after sign-out |
| Fail closed at start-up | Running SSO with the demo secret or with no provider |
| `login`, `logout`, `login_denied` in the hash-chained audit log | Untraceable access |

**Step-up for risky actions.** Saying yes to a risky runbook action needs a sign-in in the last `STEP_UP_MAX_AGE` seconds (300 by default), not just a live session. Otherwise the API answers `401 step_up_required` with `WWW-Authenticate: Bearer error="insufficient_user_authentication", max_age=300` (RFC 9470). The UI then sends the person to the provider with `prompt=login&max_age=0`, which makes the provider check their credentials again. Rejecting needs no step-up.

## Connect Microsoft Entra ID

1. **App registrations → New registration.** Name *Herald*. Supported account types: *Accounts in this organizational directory only* (single tenant). Redirect URI, platform *Web*: `https://<herald host>/auth/callback`.
2. **Certificates & secrets → New client secret.** Put it in the secret manager and give it to Herald as `ENTRA_CLIENT_SECRET`.
3. **App roles → Create app role**, one per value in the table above (`viewer` … `admin`, `team.settlements`, …). Allowed member types: *Users/Groups*.
4. **Enterprise applications → Herald → Properties → Assignment required? → Yes.** Only people given a role can sign in at all.
5. **Users and groups → Add assignment.** Assign security groups to roles (the Settlements group → `team.settlements` and `responder`, its leads group → `approver`). Assigning groups needs Entra ID P1 or P2; on the free tier only individual users can be assigned.
6. **Conditional Access** (Entra ID P1): a policy for Herald requiring MFA, and optionally a compliant device. Herald does not check MFA itself; the provider enforces it.
7. Configure Herald and restart:

   ```bash
   ENTRA_TENANT_ID=<tenant ID, a GUID>   # never "common" or "organizations"
   ENTRA_CLIENT_ID=<application (client) ID>
   ENTRA_CLIENT_SECRET=<from step 2>
   PUBLIC_URL=https://<herald host>      # must match the redirect URI
   ```

   A **Continue with Microsoft** button appears on the sign-in page. To use Entra only, leave `OIDC_*` unset. Herald accepts tokens only from that one tenant (issuer `https://login.microsoftonline.com/<tenant>/v2.0`).

For production, a certificate or workload identity federation is better than a client secret, and an Entra *authentication context* (for example "c1: approve risky action", required through Conditional Access) is the stronger form of step-up. Neither is built.

Any other OIDC provider (Okta, Ping, ADFS) works through `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`, given a claim mapping that puts the values above into `roles`.

## Emergency accounts

An alerting service must still open when single sign-on is the thing that broke. Herald therefore keeps a few break-glass accounts of its own, defined in `config/local-accounts.yaml` and switched on with `LOCAL_LOGIN=on`.

| Control | What it stops |
|---|---|
| Password stored only as a scrypt hash (N=2^14, r=8, p=5, salted), compared in constant time | A leaked environment or config revealing passwords |
| A second factor is mandatory: a TOTP code (RFC 6238, 30 s, ±1 step for clock drift) | A guessed or phished password being enough |
| A code works once: the step is claimed in the database in the same transaction as the sign-in | Replaying a code seen over someone's shoulder or in a log |
| One answer for every failure ("Wrong username, password or code"), and a dummy hash check for unknown usernames | Finding out which usernames exist |
| Lockout after 5 failures per username (existing or not) or 20 per address in 15 minutes; a success resets the count | Password guessing and credential stuffing |
| Failures go to a separate `login_attempts` table, not the hash-chained audit log | Guessing flooding the audit log |
| Each success is audited as `login` with `"break_glass": true`, the account and the address; each lockout as `login_locked` | Quiet use of a powerful account |
| `Origin` must be this site | Login CSRF |
| The config holds the names of the environment variables with the hash and secret, never the values | Secrets in the repository |

**Demo account:** `breakglass` (accountable person: anna, role admin). Its password and TOTP secret are throwaway demo values in `.env.example`; add the secret to any authenticator app by hand.

**Adding an account:**

```bash
python -m alertsvc.localauth hash-password          # prompts twice, prints the hash
python -m alertsvc.localauth new-totp oncall-lead   # prints the secret and an otpauth:// URI for the app
```

Put both values in the secret manager under two variable names, list the account in `config/local-accounts.yaml` with those names, and restart the API. An account whose variables are missing cannot be used.

Approving a risky action from an emergency account asks for the password and a new code again (step-up).

## Network access

"Even with a valid password, not from just any computer" is enforced in three layers.

1. **Herald itself.** `ALLOWED_NETWORKS` lists the networks people may use (CIDR, IPv4 and IPv6). Anything else gets `403 Herald is not available from this network` before any password is looked at: the UI, the API, sign-in and the API docs all sit behind it. Machine routes (`/ingest/*`, `/inbound/*`, `/healthz`) are not, because they carry their own credentials. `X-Forwarded-For` is believed only from addresses in `TRUSTED_PROXIES`, and then the right-most address that is not a proxy counts, so a client cannot claim to be inside by sending the header itself. Refusals are logged, and audited as `network_denied` at most once per address every 10 minutes. With `AUTH_MODE=oidc` the API refuses to start without `ALLOWED_NETWORKS`.
2. **The deployment.** In `docker-compose.yml` every published port listens on `127.0.0.1` only, so other computers on the same network cannot connect at all. Postgres has no published port and sits on an internal Docker network (`core`) that only the API and the workers join. In production the same idea is a private subnet behind the organisation's reverse proxy or WAF, with that proxy in `TRUSTED_PROXIES`.
3. **The identity provider.** Only the provider can tell a managed laptop from a personal one. In Entra, a Conditional Access policy for Herald with *named locations* (corporate egress IPs and VPN) and *Require device to be marked as compliant* or *Require hybrid Azure AD joined device* stops sign-in from a personal computer even with the right password. This needs Entra ID P1 and is configuration, not code.

**Show it:** start the API with `ALLOWED_NETWORKS=10.99.0.0/16` (a network you are not on). The browser gets the "not available from this network" page even with a valid session, while webhooks still arrive.

## Tests

```bash
.venv/bin/python -m pytest -q                                    # unit tests, no services
HERALD_E2E=1 .venv/bin/python -m pytest -q tests/test_e2e_signin.py  # against the running SSO stack
```

`tests/test_auth.py` covers ID tokens, PKCE, the login cookie and roles; `tests/test_local_login.py` covers password hashing, TOTP against the RFC 6238 vectors, code reuse, lockout, client address behind proxies, and the network rule as the app applies it; `tests/test_e2e_signin.py` runs the real flows against the stack (forged headers, identical failures, lockout of unknown usernames, emergency sign-in and code reuse, Keycloak sign-in, ports listening on localhost only).

## Not built

- **SCIM provisioning.** The routing directory (`config/teams.yaml`: who is on call, phone numbers) is still a file. Access comes from the provider, contact details from the file.
- **Back-channel logout.** Ending the session at the provider does not end the Herald session; idle and absolute timeouts do.
- **MFA check inside Herald** (`amr` / `acr` claims). The provider's policy enforces MFA.
- **Tokens for service accounts** (client credentials) for automation calling the API. Webhooks use per-source HMAC (see `docs/SECURITY.md`).
- **Cutting an open live feed on revocation.** A revoked session cannot make new requests, but an already open feed stream runs until it reconnects.
- **SMS approvals bypass step-up.** A reply from the approver's phone still counts, so in production risky actions should be approvable in the UI only.
- **Alerting on emergency use.** A break-glass sign-in is audited and logged, but it does not yet page security and inform management; it should raise a Herald alert of its own.
- **TOTP enrolment screen.** Secrets are created with the CLI and entered in the app by hand; there is no QR-code page.
- **Clean-up of `login_attempts`.** Rows older than the lockout window are kept.
- **Service-to-service tokens.** Systems calling Herald (and Herald calling them) should use the OAuth client-credentials flow: credentials from the secret manager, exchanged at the identity provider for a short-lived JWT checked for `iss`, `aud` and `exp`, or mutual TLS. Today webhooks use per-source HMAC.
- **Agents acting for a person (on-behalf-of).** If an AI agent works on alerts for someone, it should present an on-behalf-of token (OAuth token exchange, RFC 8693): the agent is the subject and an `act` claim names the person who started it, so every action stays accountable to a human and the agent sees only what that person may see.
