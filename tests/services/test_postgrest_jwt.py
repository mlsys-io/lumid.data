"""postgrest_jwt unit tests."""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from lumid_data.server.services.postgrest_jwt import PostgrestJwtConfig, mint


def _cfg() -> PostgrestJwtConfig:
    return PostgrestJwtConfig(
        secret="topsecret-32-bytes-please",
        ttl_sec=60,
        anon_role="anon",
        user_role="user",
        admin_role="admin",
    )


def test_token_role_is_admin() -> None:
    decoded = pyjwt.decode(mint(_cfg()), _cfg().secret, algorithms=["HS256"])
    assert decoded["role"] == "admin"


def test_token_carries_expiry() -> None:
    decoded = pyjwt.decode(mint(_cfg()), _cfg().secret, algorithms=["HS256"])
    issued_at = datetime.fromtimestamp(decoded["iat"], tz=UTC)
    expires_at = datetime.fromtimestamp(decoded["exp"], tz=UTC)
    assert (expires_at - issued_at) == timedelta(seconds=60)
