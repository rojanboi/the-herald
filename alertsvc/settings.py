"""Everything configurable comes from the environment (see .env.example). No secrets in code or in rules."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("DATABASE_URL", "postgresql://herald:herald@localhost:5432/herald")
    kafka_bootstrap: str = os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092")
    raw_topic: str = os.environ.get("RAW_TOPIC", "events.raw")
    consumer_group: str = os.environ.get("CONSUMER_GROUP", "herald-workers")
    config_dir: str = os.environ.get("CONFIG_DIR", "config")
    smtp_host: str = os.environ.get("SMTP_HOST", "localhost")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "1025"))
    mail_from: str = os.environ.get("MAIL_FROM", "herald@example.com")
    gateway_url: str = os.environ.get("GATEWAY_URL", "http://localhost:8090")
    mailpit_url: str = os.environ.get("MAILPIT_URL", "http://localhost:8025")
    mailpit_public_url: str = os.environ.get("MAILPIT_PUBLIC_URL", "http://localhost:8025")
    gateway_public_url: str = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost:8090")
    samples_dir: str = os.environ.get("SAMPLES_DIR", "samples/events")
    public_url: str = os.environ.get("PUBLIC_URL", "http://localhost:8080")
    secret: str = os.environ.get("HERALD_SECRET", "dev-only-change-me")
    max_attempts: int = int(os.environ.get("MAX_ATTEMPTS", "5"))
    retry_base_seconds: float = float(os.environ.get("RETRY_BASE_SECONDS", "2"))
    llm_provider: str = os.environ.get("LLM_PROVIDER", "none")
    keep_recent_events: int = int(os.environ.get("KEEP_RECENT_EVENTS", "200"))
    # demo = identity from headers set by the UI's switcher (mock). oidc = single sign-on (alertsvc/auth.py).
    auth_mode: str = os.environ.get("AUTH_MODE", "demo")
    step_up_max_age: int = int(os.environ.get("STEP_UP_MAX_AGE", "300"))  # approving needs a sign-in this recent
    session_idle_seconds: int = int(os.environ.get("SESSION_IDLE_SECONDS", "1800"))
    session_max_seconds: int = int(os.environ.get("SESSION_MAX_SECONDS", "28800"))  # one shift
    demo_tools_env: str = os.environ.get("DEMO_TOOLS", "")
    # Networks people may reach the UI and API from (CIDR, comma separated). Empty = anywhere (demo mode only).
    allowed_networks: str = os.environ.get("ALLOWED_NETWORKS", "")
    # Reverse proxies whose X-Forwarded-For is believed. Empty = the header is ignored.
    trusted_proxies: str = os.environ.get("TRUSTED_PROXIES", "")
    # Emergency accounts (username + password + one-time code) next to single sign-on. config/local-accounts.yaml
    local_login: bool = os.environ.get("LOCAL_LOGIN", "off").lower() in ("1", "on", "true", "yes")

    @property
    def demo_tools(self) -> bool:
        """The /api/sim/* helpers: on in demo mode unless switched off, off otherwise unless switched on."""
        if self.demo_tools_env:
            return self.demo_tools_env.lower() in ("1", "on", "true", "yes")
        return self.auth_mode == "demo"


settings = Settings()
