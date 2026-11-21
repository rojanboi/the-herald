-- Herald: all durable state lives here. Applied on first start of the postgres container
-- and safe to re-run (everything is IF NOT EXISTS).

-- Rules. `doc` is the whole rule (see alertsvc/models.py). `source` says who owns the row:
-- 'file' rows are replaced from config/rules on reload, 'ui' rows are only changed through the API.
CREATE TABLE IF NOT EXISTS rules (
    id          text PRIMARY KEY,
    owner       text NOT NULL,
    name        text NOT NULL,
    enabled     boolean NOT NULL DEFAULT true,
    source      text NOT NULL DEFAULT 'ui' CHECK (source IN ('file', 'ui')),
    version     integer NOT NULL DEFAULT 1,
    doc         jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text NOT NULL DEFAULT 'system:config'
);

-- Teams, people, runbooks and sources, kept as documents.
CREATE TABLE IF NOT EXISTS config_docs (
    kind        text NOT NULL CHECK (kind IN ('team', 'person', 'runbook', 'source')),
    id          text NOT NULL,
    doc         jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, id)
);

-- One row per open problem. The partial unique index is the dedup guarantee: however many
-- workers upsert the same (rule, fingerprint) at once, there is one open alert and its
-- counter is incremented atomically.
CREATE TABLE IF NOT EXISTS alerts (
    id              bigserial PRIMARY KEY,
    rule_id         text NOT NULL,
    owner           text NOT NULL,
    fingerprint     text NOT NULL,
    status          text NOT NULL DEFAULT 'firing' CHECK (status IN ('firing', 'acked', 'resolved')),
    severity        text NOT NULL,
    title           text NOT NULL,
    body            text NOT NULL DEFAULT '',
    fields          jsonb NOT NULL DEFAULT '{}',
    highlight       jsonb NOT NULL DEFAULT '[]',
    colour          text,
    count           bigint NOT NULL DEFAULT 1,
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    sample_event    jsonb NOT NULL,
    runbook         jsonb,
    step_state      jsonb NOT NULL DEFAULT '{}',
    notify_seq      integer NOT NULL DEFAULT 0,
    next_notify_at  timestamptz,
    source          text NOT NULL DEFAULT 'unknown',   -- the platform the first event came from
    incident_manager text,                             -- the assignee: the one person in charge
    acked_by        text,
    acked_at        timestamptz,
    resolved_by     text,
    resolved_at     timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_open_per_fingerprint
    ON alerts (rule_id, fingerprint) WHERE status <> 'resolved';
CREATE INDEX IF NOT EXISTS alerts_feed ON alerts (owner, last_seen DESC);
CREATE INDEX IF NOT EXISTS alerts_by_source ON alerts (source, status);

-- The delivery outbox. Rows are written in the same transaction as the alert, so an alert
-- can never exist without its notifications, and are claimed with FOR UPDATE SKIP LOCKED.
CREATE TABLE IF NOT EXISTS notifications (
    id               bigserial PRIMARY KEY,
    alert_id         bigint NOT NULL REFERENCES alerts (id),
    recipient        text NOT NULL,
    channel          text NOT NULL,
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'sending', 'retrying', 'sent', 'dead')),
    attempts         integer NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    locked_at        timestamptz,
    idempotency_key  text NOT NULL UNIQUE,
    payload          jsonb NOT NULL,
    provider_id      text,
    last_error       text,
    ingest_ts        timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    delivered_ts     timestamptz
);
CREATE INDEX IF NOT EXISTS notifications_due
    ON notifications (next_attempt_at) WHERE status IN ('pending', 'retrying', 'sending');
CREATE INDEX IF NOT EXISTS notifications_by_alert ON notifications (alert_id);
CREATE INDEX IF NOT EXISTS notifications_delivered ON notifications (delivered_ts) WHERE status = 'sent';

-- Requests to run a risky runbook step. Only a hash of the reply code is stored.
CREATE TABLE IF NOT EXISTS approvals (
    id            bigserial PRIMARY KEY,
    alert_id      bigint NOT NULL REFERENCES alerts (id),
    step_id       integer NOT NULL,
    action        text NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}',
    required      text NOT NULL,
    requested_by  text NOT NULL,
    approver      text,
    code_hash     text,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    expires_at    timestamptz NOT NULL DEFAULT now() + interval '15 minutes',
    decided_by    text,
    decided_via   text,
    decided_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Signed-in sessions (AUTH_MODE=oidc). The browser holds a random token; only its hash is stored, so a
-- copy of this table cannot be used to sign in. Revoking a row ends the session at once.
-- The API also creates these tables at start-up (store.ensure_auth_tables), for databases made before it existed.
CREATE TABLE IF NOT EXISTS sessions (
    id_hash     text PRIMARY KEY,
    user_ref    text NOT NULL,
    name        text NOT NULL,
    role        text NOT NULL,
    teams       text[] NOT NULL DEFAULT '{}',
    provider    text NOT NULL,
    auth_time   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_ref) WHERE revoked_at IS NULL;

-- Emergency-account sign-in attempts (alertsvc/localauth.py): lockout after repeated failures, per username
-- (existing or not) and per address. Kept out of the hash-chained audit log so guessing cannot flood it.
CREATE TABLE IF NOT EXISTS login_attempts (
    id        bigserial PRIMARY KEY,
    username  text NOT NULL,
    ip        text NOT NULL,
    ok        boolean NOT NULL,
    at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_attempts_by_user ON login_attempts (username, at);
CREATE INDEX IF NOT EXISTS login_attempts_by_ip ON login_attempts (ip, at);

-- The last one-time-code step each emergency account used, so a code cannot be used twice.
CREATE TABLE IF NOT EXISTS local_account_state (
    username        text PRIMARY KEY,
    last_totp_step  bigint NOT NULL
);

-- What was done and by whom. Append-only (trigger below) and hash-chained (alertsvc/security.py).
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    actor      text NOT NULL,
    action     text NOT NULL,
    target     text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}',
    prev_hash  text NOT NULL,
    hash       text NOT NULL
);
CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

-- Everything the UI feed needs to replay after a reconnect (SSE Last-Event-ID).
CREATE TABLE IF NOT EXISTS feed (
    id        bigserial PRIMARY KEY,
    owner     text NOT NULL,
    kind      text NOT NULL,
    alert_id  bigint,
    at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feed_by_owner ON feed (owner, id);

-- Fields seen per source: feeds the rule editor's field picker and makes unseen schemas usable at once.
CREATE TABLE IF NOT EXISTS seen_fields (
    source     text NOT NULL,
    path       text NOT NULL,
    type       text NOT NULL,
    example    text,
    seen       bigint NOT NULL DEFAULT 1,
    last_seen  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, path)
);

-- The last events per source, for "matches N of your last 50 events" and "why didn't it match?".
CREATE TABLE IF NOT EXISTS recent_events (
    id           bigserial PRIMARY KEY,
    source       text NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    raw          jsonb NOT NULL,
    attrs        jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS recent_events_by_source ON recent_events (source, id DESC);

-- Messages the pipeline could not process (a bug, or a payload that breaks a rule). Kept, not dropped,
-- so they can be inspected and replayed.
CREATE TABLE IF NOT EXISTS dead_events (
    id      bigserial PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    source  text NOT NULL,
    error   text NOT NULL,
    raw     bytea NOT NULL
);
