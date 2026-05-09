"""Response schemas for lumid.data REST surfaces.

The same models are used server-side as ``response_model`` so the wire
contract is enforced from both ends.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class StorageBucketResult(BaseModel):
    bucket: str
    created: bool


class SignedUrl(BaseModel):
    url: str
    expires: int


class SqlResult(BaseModel):
    rows: list[dict[str, Any]]
    rowcount: int


# ── agent retrieval tool models ────────────────────────────────────────


class RetrievalSqlOp(BaseModel):
    op: Literal["sql"]
    query: str = Field(..., min_length=1, max_length=200_000)


class RetrievalStorageGetOp(BaseModel):
    op: Literal["storage_get"]
    bucket: str
    key: str


RetrievalOp = RetrievalSqlOp | RetrievalStorageGetOp


class RetrievalPlan(BaseModel):
    """JSON plan the agent emits in its final_text."""

    plan: list[RetrievalOp]
    expected_rowcount_or_size: int | None = None
    rationale: str | None = None


class AccessStep(BaseModel):
    """One materialized access step — what the replay actually executed."""

    op: Literal["sql", "storage_get"]
    query: str | None = None
    bucket: str | None = None
    key: str | None = None
    rows_or_bytes: int


class RetrievalRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=20_000)
    schema_scope: str | None = None
    output_format: Literal["csv", "jsonl", "raw"] | None = None
    max_steps: int | None = Field(None, ge=1, le=100)
    model: str | None = None


class RetrievalResult(BaseModel):
    run_id: str
    materialized_uri: str
    signed_url: str
    output_format: Literal["csv", "jsonl", "raw"]
    access_chain: list[AccessStep]
    rowcount: int
    size_bytes: int
    tokens_in: int
    tokens_out: int
    steps_taken: int
    replay_latency_ms: int
    transcript_url: str
