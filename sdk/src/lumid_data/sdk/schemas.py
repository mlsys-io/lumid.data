"""Response schemas for lumid.data REST surfaces.

The same models are used server-side as ``response_model`` so the wire
contract is enforced from both ends.
"""

from typing import Any

from pydantic import BaseModel


class StorageObject(BaseModel):
    key: str
    size: int
    etag: str | None = None
    last_modified: str | None = None


class StorageList(BaseModel):
    items: list[StorageObject]


class StoragePutResult(BaseModel):
    uri: str
    sha256: str


class SignedUrl(BaseModel):
    url: str
    expires: int


class SqlResult(BaseModel):
    rows: list[dict[str, Any]]
    rowcount: int
