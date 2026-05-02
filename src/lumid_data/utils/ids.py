"""Object ID factories. 3-character type prefix + URL-safe random body."""

import secrets

_PREFIXES: dict[str, str] = {
    "source": "src-",
    "ingest": "ing-",
    "plan": "pln-",
    "dataset": "dst-",
    "dlq": "dlq-",
    "event": "evt-",
}


def _new(prefix: str, n_bytes: int = 12) -> str:
    return prefix + secrets.token_urlsafe(n_bytes)


def new_source_id() -> str:
    return _new(_PREFIXES["source"])


def new_ingest_id() -> str:
    return _new(_PREFIXES["ingest"])


def new_plan_id() -> str:
    return _new(_PREFIXES["plan"])


def new_dataset_id() -> str:
    return _new(_PREFIXES["dataset"])


def new_dlq_id() -> str:
    return _new(_PREFIXES["dlq"])


def new_event_id() -> str:
    return _new(_PREFIXES["event"])
