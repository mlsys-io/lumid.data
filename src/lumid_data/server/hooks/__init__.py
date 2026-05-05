"""Plugin hooks for extending the server.

- `IdentityProvider` — resolve a bearer token to a `PrincipalContext`.

Plugins append to the module-level list; core iterates it at call time.
"""

from .identity import IdentityProvider

IDENTITY_PROVIDERS: list[IdentityProvider] = []

__all__ = [
    "IDENTITY_PROVIDERS",
    "IdentityProvider",
]
