"""Small security primitives: webhook signatures, approval codes, the audit hash chain."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

MAX_BODY_BYTES = 1_000_000
MAX_DEPTH = 32
SIGNATURE_TOLERANCE_SECONDS = 300


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """`sha256=<hex>` over `<timestamp>.<body>`. Senders put it in X-Herald-Signature."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, timestamp: str | None, signature: str | None, now: float | None = None) -> bool:
    """Constant-time check, and the timestamp must be recent so a captured request cannot be replayed later."""
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs((now if now is not None else time.time()) - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, body, int(timestamp)), signature)


def depth(value: Any, limit: int = MAX_DEPTH) -> int:
    """Nesting depth, stopping early past the limit so a hostile payload cannot make this expensive."""
    stack, deepest = [(value, 1)], 0
    while stack:
        item, level = stack.pop()
        deepest = max(deepest, level)
        if level > limit:
            return level
        if isinstance(item, dict):
            stack.extend((child, level + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, level + 1) for child in item)
    return deepest


# --- approval codes -------------------------------------------------------------------

# Approval codes are 4 to 8 random digits; ACK / RESOLVE carry the alert number, which can be short.
_REPLY = re.compile(
    r"^\s*(?:(yes|approve|no|reject)\s+#?(\d{4,8})|(ack|resolve)\s+#?(\d{1,12}))\s*$", re.IGNORECASE
)
_VERBS = {"yes": "approve", "approve": "approve", "no": "reject", "reject": "reject", "ack": "ack", "resolve": "resolve"}


def new_code(digits: int = 4) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def hash_code(secret: str, code: str, approval_id: int | str, approver: str) -> str:
    """Stored instead of the code. Binding the approver in means a code sent to one
    person does not work when replied from another number."""
    message = f"{approval_id}|{approver}|{code}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def parse_reply(text: str) -> tuple[str, str] | None:
    """`YES 4821` -> ("approve", "4821"). Anything else -> None."""
    match = _REPLY.match(text or "")
    if not match:
        return None
    verb, code = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    return _VERBS[verb.lower()], code


# --- audit hash chain -----------------------------------------------------------------

GENESIS = "0" * 64


def entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    """Each audit row carries the hash of the row before it, so an edited or deleted row is detectable."""
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def verify_chain(entries: list[dict[str, Any]]) -> int | None:
    """Returns None if the chain is intact, else the index of the first bad entry."""
    prev = GENESIS
    for index, row in enumerate(entries):
        body = {k: v for k, v in row.items() if k not in ("hash", "prev_hash", "id")}
        if row.get("prev_hash") != prev or row.get("hash") != entry_hash(prev, body):
            return index
        prev = row["hash"]
    return None
