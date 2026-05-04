"""postgrest_jwt unit tests."""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from lumid_data.server.auth.security import PrincipalContext
from lumid_data.server.services.postgrest_jwt import PostgrestJwtConfig, mint


def _cfg() -> PostgrestJwtConfig:
    return PostgrestJwtConfig(
        secret="topsecret-32-bytes-please",
        ttl_sec=60,
        anon_role="anon",
        user_role="user",
        admin_role="admin",
    )


def test_admin_scope_yields_admin_role() -> None:
    p = PrincipalContext(principal_id="x", scopes=["admin:audit"])
    token = mint(_cfg(), p)
    decoded = pyjwt.decode(token, _cfg().secret, algorithms=["HS256"])
    assert decoded["role"] == "admin"
    assert decoded["principal_id"] == "x"


def test_db_read_scope_yields_user_role() -> None:
    p = PrincipalContext(principal_id="x", scopes=["db:read"])
    decoded = pyjwt.decode(mint(_cfg(), p), _cfg().secret, algorithms=["HS256"])
    assert decoded["role"] == "user"


def test_no_scope_falls_back_to_anon() -> None:
    p = PrincipalContext(principal_id="x", scopes=[])
    decoded = pyjwt.decode(mint(_cfg(), p), _cfg().secret, algorithms=["HS256"])
    assert decoded["role"] == "anon"


def test_token_carries_expiry() -> None:
    p = PrincipalContext(principal_id="x", scopes=["db:write"])
    decoded = pyjwt.decode(mint(_cfg(), p), _cfg().secret, algorithms=["HS256"])
    issued_at = datetime.fromtimestamp(decoded["iat"], tz=UTC)
    expires_at = datetime.fromtimestamp(decoded["exp"], tz=UTC)
    assert (expires_at - issued_at) == timedelta(seconds=60)


def test_org_id_is_propagated() -> None:
    p = PrincipalContext(principal_id="x", org_id="acme", scopes=["db:read"])
    decoded = pyjwt.decode(mint(_cfg(), p), _cfg().secret, algorithms=["HS256"])
    assert decoded["org_id"] == "acme"
