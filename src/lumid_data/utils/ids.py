"""Object ID factories. 4-character type prefix + URL-safe random body."""

import secrets


def _new(prefix: str, n_bytes: int = 12) -> str:
    return prefix + secrets.token_urlsafe(n_bytes)


def new_audit_id() -> str:
    return _new("aud-")


def new_run_id() -> str:
    return _new("run-")
