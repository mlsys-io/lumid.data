"""Object ID factories. 4-character type prefix + URL-safe random body."""

import secrets


def _new(prefix: str, n_bytes: int = 12) -> str:
    return prefix + secrets.token_urlsafe(n_bytes)


def new_audit_id() -> str:
    return _new("aud-")


def new_run_id() -> str:
    return _new("run-")


def new_stream_id() -> str:
    return _new("str-")


def new_stream_run_id() -> str:
    return _new("srn-")


def new_dlq_id() -> str:
    return _new("dlq-")
