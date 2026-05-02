"""Unity Catalog OSS REST client (minimal surface).

We only use the bits we need: ensure namespace, register external Delta
table, get table by full name. UC OSS exposes a Databricks-shaped REST
API at ``/api/2.1/unity-catalog/...``; the OSS server accepts unauth or
a bearer token depending on configuration.

Reference: https://github.com/unitycatalog/unitycatalog
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


@dataclass(frozen=True)
class UnityClient:
    base_url: str
    token: str | None = None

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def ensure_catalog(self, name: str, comment: str | None = None) -> None:
        body = {"name": name}
        if comment:
            body["comment"] = comment
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base_url}/api/2.1/unity-catalog/catalogs",
                json=body,
                headers=self._headers(),
            )
            if r.status_code in (200, 201):
                return
            if r.status_code == 409:
                return  # already exists
            r.raise_for_status()

    def ensure_schema(self, catalog: str, schema: str) -> None:
        body = {"name": schema, "catalog_name": catalog}
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base_url}/api/2.1/unity-catalog/schemas",
                json=body,
                headers=self._headers(),
            )
            if r.status_code in (200, 201, 409):
                return
            r.raise_for_status()

    def register_external_delta(
        self,
        full_name: str,
        storage_location: str,
        columns: list[dict[str, Any]],
        comment: str | None = None,
    ) -> None:
        catalog, schema, name = full_name.split(".", 2)
        body: dict[str, Any] = {
            "name": name,
            "catalog_name": catalog,
            "schema_name": schema,
            "table_type": "EXTERNAL",
            "data_source_format": "DELTA",
            "columns": columns,
            "storage_location": storage_location,
        }
        if comment:
            body["comment"] = comment
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base_url}/api/2.1/unity-catalog/tables",
                json=body,
                headers=self._headers(),
            )
            if r.status_code in (200, 201, 409):
                return
            logger.warning("UC register failed: %s %s", r.status_code, r.text)
            r.raise_for_status()

    def get_table(self, full_name: str) -> dict[str, Any] | None:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(
                f"{self.base_url}/api/2.1/unity-catalog/tables/{full_name}",
                headers=self._headers(),
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()


def arrow_columns_to_uc(schema: Any) -> list[dict[str, Any]]:
    """Convert pyarrow schema fields to UC column descriptors."""
    cols: list[dict[str, Any]] = []
    for i, field in enumerate(schema):
        cols.append(
            {
                "name": field.name,
                "type_text": str(field.type),
                "type_name": _arrow_to_uc_type(str(field.type)),
                "type_json": "{}",
                "position": i,
                "nullable": field.nullable,
            }
        )
    return cols


def _arrow_to_uc_type(arrow_type: str) -> str:
    s = arrow_type.lower()
    if s.startswith("int64"):
        return "LONG"
    if s.startswith("int32"):
        return "INT"
    if s.startswith("int"):
        return "INT"
    if "float64" in s or "double" in s:
        return "DOUBLE"
    if "float" in s:
        return "FLOAT"
    if "bool" in s:
        return "BOOLEAN"
    if "timestamp" in s:
        return "TIMESTAMP"
    if "date" in s:
        return "DATE"
    if "binary" in s:
        return "BINARY"
    if "string" in s or "utf8" in s:
        return "STRING"
    return "STRING"
