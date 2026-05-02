"""Stream-cadence source bootstrap.

Wires Redpanda topic creation, RisingWave source/MV/sink bootstrap, UC
table registration, and the initial ``DatasetReady`` event into a single
idempotent operation. Called from the sources router when a descriptor
arrives with ``cadence='stream'``; safe to invoke again on the same
source (every step is idempotent).
"""

import logging
from datetime import UTC, datetime

import pyarrow as pa
from fastapi import HTTPException

from ...agent import plan_for_stream_source
from ...catalog import unity as uc
from ...db.models import Dataset, IngestPlanRow, Source
from ...schemas.descriptors import DatasetReady, SourceDescriptor
from ...sinks.delta import table_uri as delta_table_uri
from ...sinks.risingwave import StreamPipelineSpec, ensure_stream_pipeline
from ...utils.ids import new_dataset_id, new_event_id
from ..state import AppState

logger = logging.getLogger(__name__)

_STREAM_FIELDS: dict[str, pa.DataType] = {
    "payload": pa.string(),
    "ts": pa.timestamp("us", tz="UTC"),
}
_STREAM_ARROW_SCHEMA = pa.schema(_STREAM_FIELDS)


async def bootstrap_stream_source(
    state: AppState, descriptor: SourceDescriptor, source_row: Source, session
) -> dict[str, str]:
    if (
        not state.streaming_enabled
        or state.redpanda is None
        or state.risingwave_cfg is None
    ):
        raise HTTPException(
            status_code=503,
            detail="streaming is not enabled (set LUMID_DATA_STREAMING_ENABLED=true)",
        )
    if descriptor.cadence != "stream":
        raise HTTPException(
            status_code=400, detail="bootstrap_stream requires cadence='stream'"
        )

    plan = plan_for_stream_source(descriptor)
    if plan.target_topic is None or plan.target_table is None:
        raise HTTPException(status_code=500, detail="agent did not return topic/table")

    state.redpanda.ensure_topic(plan.target_topic)
    table_uri = delta_table_uri(state.delta_cfg, plan.target_table)
    state.unity.ensure_catalog(state.settings.uc_catalog)
    catalog, schema_name, _ = plan.target_table.split(".", 2)
    state.unity.ensure_schema(catalog, schema_name)
    state.unity.register_external_delta(
        full_name=plan.target_table,
        storage_location=table_uri,
        columns=uc.arrow_columns_to_uc(_STREAM_ARROW_SCHEMA),
    )
    ensure_stream_pipeline(
        state.risingwave_cfg,
        StreamPipelineSpec(
            pipeline_id=source_row.source_id.replace("-", "_"),
            topic=plan.target_topic,
            redpanda_brokers=state.settings.redpanda_brokers or "",
            delta_location=table_uri,
            s3_endpoint=state.delta_cfg.s3_endpoint,
            s3_access_key=state.delta_cfg.s3_access_key,
            s3_secret_key=state.delta_cfg.s3_secret_key,
            s3_region=state.delta_cfg.s3_region,
        ),
    )

    plan_row = IngestPlanRow(
        plan_id=plan.plan_id,
        ingest_id=plan.ingest_id,
        source_id=source_row.source_id,
        received_at=plan.received_at,
        modality_resolved=plan.modality_resolved,
        route=plan.route,
        target_table=plan.target_table,
        target_topic=plan.target_topic,
        quality_report={},
        policy_applied=plan.policy_applied,
        status="succeeded",
        completed_at=datetime.now(UTC),
    )
    session.add(plan_row)

    dataset = Dataset(
        dataset_id=new_dataset_id(),
        source_id=source_row.source_id,
        table_uri=table_uri,
        topic=plan.target_topic,
        modality=plan.modality_resolved,
        version=1,
        rows_total=0,
    )
    session.add(dataset)
    await session.flush()

    await state.nats.publish_dataset_ready(
        DatasetReady(
            event_id=new_event_id(),
            dataset_id=dataset.dataset_id,
            source_id=source_row.source_id,
            table_uri=table_uri,
            topic=plan.target_topic,
            modality=plan.modality_resolved,
            version=1,
            is_first=True,
            rows_added=None,
        )
    )
    return {
        "topic": plan.target_topic,
        "table_uri": table_uri,
        "dataset_id": dataset.dataset_id,
        "plan_id": plan.plan_id,
    }
