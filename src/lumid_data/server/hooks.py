"""Plugin hook protocols + registries.

Plugin modules expose ``install()`` (sync or async-context-manager) that
appends adapters to these module-level lists. Plugins are loaded from
``LUMID_DATA_PLUGINS`` at FastAPI lifespan startup.

v1 keeps a single hook (``IdentityProvider``); other extension points
(submission guards, usage sinks) can land later behind the same shape.
"""

import logging
from typing import Protocol, runtime_checkable

from .auth.security import PrincipalContext


@runtime_checkable
class IdentityProvider(Protocol):
    name: str

    async def resolve(
        self, raw_token: str, logger: logging.Logger
    ) -> PrincipalContext | None: ...


IDENTITY_PROVIDERS: list[IdentityProvider] = []
