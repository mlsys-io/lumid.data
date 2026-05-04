"""Write-ahead audit log: insert into audit_log + optional NATS fan-out."""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.models import AuditLog
from ...utils.ids import new_audit_id
from ..auth.security import PrincipalContext

logger = logging.getLogger(__name__)

_AUDIT_SUBJECT = "lumid.data.audit"


@dataclass
class AuditWriter:
    sessionmaker: async_sessionmaker[AsyncSession]
    nats_client: NATSClient | None = None

    async def record(
        self,
        *,
        principal: PrincipalContext,
        surface: str,
        op: str,
        path: str,
        status_code: int,
        latency_ms: float,
        error: str | None = None,
        request_meta: dict[str, Any] | None = None,
    ) -> str:
        row = AuditLog(
            id=new_audit_id(),
            principal_id=principal.principal_id,
            org_id=principal.org_id,
            surface=surface,
            op=op,
            path=path[:1024],
            status_code=status_code,
            latency_ms=latency_ms,
            error=error,
            request_meta=request_meta or {},
        )
        async with self.sessionmaker() as session:
            session.add(row)
            await session.commit()
        if self.nats_client is not None:
            try:
                await self.nats_client.publish(
                    f"{_AUDIT_SUBJECT}.{op.lower()}",
                    json.dumps(
                        {
                            "id": row.id,
                            "principal_id": row.principal_id,
                            "surface": surface,
                            "op": op,
                            "path": row.path,
                            "status_code": status_code,
                        }
                    ).encode("utf-8"),
                )
            except Exception:
                logger.exception("audit nats publish failed")
        return row.id


async def connect_nats(url: str | None) -> NATSClient | None:
    if not url:
        return None
    return await nats.connect(url)


def now_ms() -> float:
    return time.monotonic() * 1000.0
