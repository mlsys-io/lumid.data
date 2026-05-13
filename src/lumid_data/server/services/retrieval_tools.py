"""Deterministic data-retrieval tools exposed through the lumid.data agent."""

import tempfile
from pathlib import Path
from typing import Any, Literal

from lumid_data.sdk.schemas import RetrievalPlan, RetrievalResult
from pydantic import BaseModel, Field
from sqlalchemy.engine.url import make_url

from ...agent.providers.base import ToolDef
from ...retrieve.card_builder import SchemaCardBuilder
from ...retrieve.card_store import SchemaCardCache
from ...retrieve.replay import PlanReplayer
from ...retrieve.schema_card import render_bundle_for_prompt
from ...utils.ids import new_run_id
from ..auth.security import PrincipalContext
from ..state import AppState
from . import s3 as s3_svc
from .audit import now_ms

_PRESIGN_TTL_SEC = 300


class SchemaCardsArgs(BaseModel):
    schema_scope: str = Field(default="*", min_length=1, max_length=512)


class ReplayPlanArgs(BaseModel):
    plan: RetrievalPlan
    output_format: Literal["csv", "jsonl", "raw"] = "jsonl"


def retrieval_tool_defs() -> list[ToolDef]:
    return [
        ToolDef(
            name="get_schema_cards",
            description=(
                "Return compact schema cards for SQL planning. Use this before "
                "composing SQL for a natural-language data request. The result "
                "contains table/column names, useful stats, samples, foreign "
                "keys, verified query hints, glossary, and join hints."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "schema_scope": {
                        "type": "string",
                        "description": "Optional schema/table scope; use * by default.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        ToolDef(
            name="replay_retrieval_plan",
            description=(
                "Execute and materialize a structured retrieval plan after you "
                "have inspected schema cards and probe tools. The plan must be "
                "a JSON object with a plan array containing sql or storage_get "
                "ops. Returns a signed URL, materialized S3 URI, access chain, "
                "row count, and size."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "object",
                        "description": (
                            "Retrieval plan: {plan: [{op: 'sql', query: ...} "
                            "or {op: 'storage_get', bucket: ..., key: ...}]}"
                        ),
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["csv", "jsonl", "raw"],
                        "description": "Materialized output format.",
                    },
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        ),
    ]


def retrieval_tool_handlers(
    *,
    state: AppState,
    principal: PrincipalContext,
    agent_run_id: str,
) -> dict[str, Any]:
    return {
        "get_schema_cards": _get_schema_cards_handler(state),
        "replay_retrieval_plan": _replay_retrieval_plan_handler(
            state=state,
            principal=principal,
            agent_run_id=agent_run_id,
        ),
    }


def retrieval_system_addendum() -> str:
    return (
        "\n\nFor natural-language data retrieval, use this workflow: call "
        "get_schema_cards first, use bounded preview/stat probes such as SQL "
        "explain, count, sample, storage list, and storage stat when needed, "
        "compose the final SQL/storage retrieval plan yourself, then call "
        "replay_retrieval_plan to materialize the result. SQL identifiers in "
        "schema cards are already SQL-ready; preserve double quotes exactly for "
        "mixed-case or special-character names. If replay reports an identifier "
        "or SQL error, correct the plan and call replay_retrieval_plan again. "
        "Do not call tools that return full rows or object bytes to the model; "
        "only preview/stat tools and replay are allowed, and only replay should "
        "touch the full materialized result."
    )


def _get_schema_cards_handler(state: AppState):
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        args = SchemaCardsArgs.model_validate(arguments)
        builder = SchemaCardBuilder(
            dsn=_libpq(state.settings.database_url),
            role=state.settings.db_admin_role,
        )
        cache = SchemaCardCache(
            builder=builder,
            s3_client=state.s3_client,
            bucket=state.settings.s3_default_bucket,
        )
        bundle = await cache.get(args.schema_scope.strip())
        return {
            "schema_scope": bundle.scope,
            "table_count": len(bundle.tables),
            "schema_cards": render_bundle_for_prompt(bundle),
        }

    return handler


def _replay_retrieval_plan_handler(
    *,
    state: AppState,
    principal: PrincipalContext,
    agent_run_id: str,
):
    async def handler(arguments: dict[str, Any]) -> RetrievalResult:
        args = ReplayPlanArgs.model_validate(arguments)
        started = now_ms()
        run_id = new_run_id()
        bucket = state.settings.s3_default_bucket
        replayer = PlanReplayer(
            database_url=state.settings.database_url,
            s3_client=state.s3_client,
            admin_role=state.settings.db_admin_role,
        )

        with tempfile.TemporaryDirectory(prefix="retrieve-") as tmp:
            out_path = Path(tmp) / f"result.{args.output_format}"
            replay_result = await replayer.replay(
                plan=args.plan,
                out_path=out_path,
                output_format=args.output_format,
            )
            materialized_key = f"retrievals/{run_id}/result.{args.output_format}"
            s3_svc.put_idempotent(
                state.s3_client,
                bucket,
                materialized_key,
                out_path.read_bytes(),
                content_type=_content_type(args.output_format),
            )

        await state.audit.record(
            principal=principal,
            surface="agent",
            op="TOOL",
            path="tool:replay_retrieval_plan",
            status_code=200,
            latency_ms=now_ms() - started,
            request_meta={"run_id": run_id, "agent_run_id": agent_run_id},
        )

        return RetrievalResult(
            run_id=run_id,
            materialized_uri=f"s3://{bucket}/{materialized_key}",
            signed_url=s3_svc.presign_get(
                state.s3_client, bucket, materialized_key, _PRESIGN_TTL_SEC
            ),
            output_format=args.output_format,
            access_chain=replay_result.access_chain,
            rowcount=replay_result.rowcount,
            size_bytes=replay_result.size_bytes,
            tokens_in=0,
            tokens_out=0,
            steps_taken=0,
            replay_latency_ms=replay_result.elapsed_ms,
            transcript_url=(
                f"{state.settings.base_url.rstrip('/')}/v1/admin/runs/{agent_run_id}"
            ),
        )

    return handler


def _libpq(url: str) -> str:
    parsed = make_url(url)
    if "+" in parsed.drivername:
        parsed = parsed.set(drivername=parsed.drivername.split("+", 1)[0])
    return parsed.render_as_string(hide_password=False)


def _content_type(fmt: str) -> str:
    return {
        "csv": "text/csv",
        "jsonl": "application/x-ndjson",
        "raw": "application/octet-stream",
    }.get(fmt, "application/octet-stream")
