"""Plugin hook protocols + registries.

Mirrors the Lumilake / FlowMesh shape: plugin modules expose ``install()``
that appends adapters to these module-level lists. Plugins are loaded
from ``LUMID_DATA_PLUGINS`` at FastAPI lifespan startup.
"""

import logging
from typing import Protocol, runtime_checkable

from ..schemas.descriptors import IngestPlan, SourceDescriptor
from .auth.security import PrincipalContext


@runtime_checkable
class IdentityProvider(Protocol):
    name: str

    async def resolve(
        self, raw_token: str, logger: logging.Logger
    ) -> PrincipalContext | None: ...


@runtime_checkable
class SourcePolicyHook(Protocol):
    name: str

    async def check(
        self,
        descriptor: SourceDescriptor,
        principal: PrincipalContext,
        logger: logging.Logger,
    ) -> None: ...


@runtime_checkable
class LineageSink(Protocol):
    name: str

    async def emit(self, plan: IngestPlan, logger: logging.Logger) -> None: ...


IDENTITY_PROVIDERS: list[IdentityProvider] = []
SOURCE_POLICY_HOOKS: list[SourcePolicyHook] = []
LINEAGE_SINKS: list[LineageSink] = []
