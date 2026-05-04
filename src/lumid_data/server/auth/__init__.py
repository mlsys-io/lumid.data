"""Auth: PrincipalContext, bearer resolution, identity plugin shape."""

from .security import (
    ALLOWED_SCOPES,
    PrincipalContext,
    authenticate_bearer,
)

__all__ = ["ALLOWED_SCOPES", "PrincipalContext", "authenticate_bearer"]
