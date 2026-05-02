"""OpenLineage RunEvent envelope. Thin pydantic mirror of the spec.

The full client lives in ``openlineage-python``. We use this local model so
internal call sites stay typed without importing the upstream SDK at every
hop. Conversion helpers in ``catalog/nats_publisher.py`` translate to/from
the upstream model when emitting.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER"]


class RunFacets(BaseModel):
    model_config = ConfigDict(extra="allow")


class JobFacets(BaseModel):
    model_config = ConfigDict(extra="allow")


class DatasetFacets(BaseModel):
    model_config = ConfigDict(extra="allow")


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    facets: RunFacets = Field(default_factory=RunFacets)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    name: str
    facets: JobFacets = Field(default_factory=JobFacets)


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    name: str
    facets: DatasetFacets = Field(default_factory=DatasetFacets)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventType: EventType
    eventTime: datetime
    run: Run
    job: Job
    inputs: list[Dataset] = Field(default_factory=list)
    outputs: list[Dataset] = Field(default_factory=list)
    producer: str = "https://github.com/mlsys-io/lumid.data"
    schemaURL: str = "https://openlineage.io/spec/2-0-2/OpenLineage.json"
    extra: dict[str, Any] = Field(default_factory=dict)
