"""``POST /retrieve/v1`` — NL-driven data retrieval as a service.

Pipeline (slim agent context, server-owned replay):

    schema_card_cache.get(scope)
        -> RetrievalPlanner.plan(description, cards) [/agent/v1 internals,
            probe-only tools]
        -> RetrievalPlan parsed from final_text JSON
        -> PlanReplayer.replay(plan) [psycopg + boto3 directly]
        -> upload result to s3://<bucket>/retrievals/<run_id>/result.<fmt>
        -> return signed GET URL + access chain + lineage

The agent never reads full result rows — only probes (EXPLAIN, COUNT,
SAMPLE ≤20, storage_list, storage_stat). The replayer runs canonical
SQL / storage_get directly. On a SQL replay error, the router retries
the plan once with the error fed back to the planner.
"""

import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from lumid_data.sdk.schemas import RetrievalRequest, RetrievalResult

from ...db.models import AgentRun
from ...retrieve.card_builder import SchemaCardBuilder
from ...retrieve.card_store import SchemaCardCache
from ...retrieve.planner import PlannerError, PlanningResult, RetrievalPlanner
from ...retrieve.replay import PlanReplayer, ReplayError
from ...utils.ids import new_run_id
from ..auth.security import PrincipalContext, default_principal
from ..deps import get_state
from ..services import s3 as s3_svc
from ..services.audit import now_ms
from ..state import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve/v1", tags=["retrieve"])

_PRESIGN_TTL_SEC = 300


@router.post("", response_model=RetrievalResult)
async def retrieve(
    body: RetrievalRequest,
    state: AppState = Depends(get_state),
) -> RetrievalResult:
    if state.llm_adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM adapter not configured",
        )
    if not body.schema_scope:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="schema_scope is required (e.g. 'star.*' or 'star.x,star.y')",
        )

    settings = state.settings
    principal = default_principal()
    run_id = new_run_id()
    started = now_ms()
    bucket = settings.s3_default_bucket
    output_format = body.output_format or "jsonl"

    builder = SchemaCardBuilder(
        dsn=_libpq(state.settings.database_url),
        role=settings.postgrest_admin_role,
    )
    cache = SchemaCardCache(builder=builder, s3_client=state.s3_client, bucket=bucket)
    bundle = await cache.get(body.schema_scope)

    app = _resolve_app(state)
    planner = RetrievalPlanner(
        adapter=state.llm_adapter,
        app=app,
        base_url=settings.base_url,
        max_steps_default=settings.agent_max_steps,
    )
    replayer = PlanReplayer(
        database_url=settings.database_url,
        s3_client=state.s3_client,
        admin_role=settings.postgrest_admin_role,
    )

    plan_description = body.description
    last_error: Exception | None = None
    plan_result = None
    replay_result = None

    with tempfile.TemporaryDirectory(prefix="retrieve-") as tmp:
        out_path = Path(tmp) / f"result.{output_format}"

        for attempt in range(2):
            try:
                plan_result = await planner.plan(
                    description=plan_description,
                    bundle=bundle,
                    max_steps=body.max_steps,
                    run_id=run_id if attempt == 0 else None,
                )
            except PlannerError as exc:
                last_error = exc
                logger.warning("planner failed (attempt %s): %s", attempt + 1, exc)
                break

            try:
                replay_result = await replayer.replay(
                    plan=plan_result.plan,
                    out_path=out_path,
                    output_format=output_format,
                )
                break
            except ReplayError as exc:
                last_error = exc
                logger.info(
                    "replay failed (attempt %s): %s — retrying once with error context",
                    attempt + 1,
                    exc,
                )
                plan_description = (
                    f"{body.description}\n\n"
                    f"Previous plan FAILED on replay:\n"
                    f"  {plan_result.plan.model_dump_json()}\n"
                    f"Error: {exc}\n\n"
                    "Emit a corrected plan that fixes the issue (e.g. cast to "
                    "numeric for ROUND on doubles, quote case-sensitive names, "
                    "use the right table from the schema cards)."
                )

        if replay_result is None:
            await _persist_run(
                state, run_id, principal, body, plan_result, error=str(last_error)
            )
            await _audit(
                state,
                principal,
                run_id,
                started,
                status_code=502,
                error=str(last_error),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"retrieval failed: {last_error}",
            )

        materialized_key = f"retrievals/{run_id}/result.{output_format}"
        body_bytes = out_path.read_bytes()
        s3_svc.put_idempotent(
            state.s3_client,
            bucket,
            materialized_key,
            body_bytes,
            content_type=_content_type(output_format),
        )

    signed_url = s3_svc.presign_get(
        state.s3_client, bucket, materialized_key, _PRESIGN_TTL_SEC
    )
    transcript_url = f"{settings.base_url.rstrip('/')}/v1/admin/runs/{run_id}"

    assert plan_result is not None  # for type-narrowing — break-on-success ensures
    await _persist_run(state, run_id, principal, body, plan_result, error=None)
    await _audit(state, principal, run_id, started, status_code=200, error=None)

    return RetrievalResult(
        run_id=run_id,
        materialized_uri=f"s3://{bucket}/{materialized_key}",
        signed_url=signed_url,
        output_format=output_format,
        access_chain=replay_result.access_chain,
        rowcount=replay_result.rowcount,
        size_bytes=replay_result.size_bytes,
        tokens_in=plan_result.tokens_in,
        tokens_out=plan_result.tokens_out,
        steps_taken=plan_result.steps_taken,
        replay_latency_ms=replay_result.elapsed_ms,
        transcript_url=transcript_url,
    )


def _resolve_app(state: AppState):
    app = getattr(state, "_fastapi_app", None)
    if app is None:
        raise HTTPException(
            status_code=500, detail="FastAPI app handle not attached to AppState"
        )
    return app


def _libpq(url: str) -> str:
    from sqlalchemy.engine.url import make_url

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


async def _persist_run(
    state: AppState,
    run_id: str,
    principal: PrincipalContext,
    body: RetrievalRequest,
    plan_result: PlanningResult | None,
    error: str | None,
) -> None:
    async with state.sessionmaker() as session:
        row = await session.get(AgentRun, run_id)
        if row is None:
            row = AgentRun(
                id=run_id,
                principal_id=principal.principal_id,
                provider=state.settings.llm_provider,
                model=body.model or state.settings.llm_model,
                goal=body.description,
                status="failed" if error else "done",
            )
            session.add(row)
        else:
            row.status = "failed" if error else "done"
        if plan_result is not None:
            row.steps_taken = int(plan_result.steps_taken)
            row.tokens_in = int(plan_result.tokens_in)
            row.tokens_out = int(plan_result.tokens_out)
            row.final_text = plan_result.final_text or None
            row.transcript = plan_result.transcript
        if error:
            row.error = error
        row.completed_at = datetime.now(UTC)
        await session.commit()


async def _audit(
    state: AppState,
    principal: PrincipalContext,
    run_id: str,
    started: float,
    *,
    status_code: int,
    error: str | None,
) -> None:
    await state.audit.record(
        principal=principal,
        surface="retrieve",
        op="POST",
        path="/retrieve/v1",
        status_code=status_code,
        latency_ms=now_ms() - started,
        error=error,
        request_meta={"run_id": run_id},
    )
