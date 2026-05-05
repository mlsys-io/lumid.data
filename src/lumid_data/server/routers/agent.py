"""``POST /agent/v1`` — server-streamed agent tool-use loop."""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...agent import build_dispatcher_from_app, build_tool_catalog
from ...agent import run as run_agent
from ...db.models import AgentRun
from ...utils.ids import new_run_id
from ..deps import get_state
from ..services.audit import now_ms
from ..state import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/v1", tags=["agent"])


class AgentRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=20_000)
    context: dict | None = None
    tools_allowed: list[str] | None = None
    max_steps: int | None = Field(None, ge=1, le=100)
    model: str | None = None


@router.post("")
async def run_agent_endpoint(
    body: AgentRequest,
    state: AppState = Depends(get_state),
) -> StreamingResponse:
    if state.llm_adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM adapter not configured",
        )
    settings = state.settings
    run_id = new_run_id()
    started = now_ms()

    async def stream():
        async with state.sessionmaker() as session:
            row = AgentRun(
                id=run_id,
                provider=settings.llm_provider,
                model=body.model or settings.llm_model,
                goal=body.goal,
                status="running",
            )
            session.add(row)
            await session.commit()

        app = state_to_app(state)
        all_tools = build_tool_catalog(app)
        if body.tools_allowed:
            allowed = set(body.tools_allowed)
            tools = [t for t in all_tools if t.name in allowed]
        else:
            tools = all_tools
        dispatcher = build_dispatcher_from_app(
            app, base_url=settings.base_url, bearer=None
        )
        emitted_done: dict | None = None
        try:
            async for ev in run_agent(
                adapter=state.llm_adapter,
                tools=tools,
                dispatcher=dispatcher,
                goal=body.goal,
                max_steps=body.max_steps or settings.agent_max_steps,
            ):
                yield _sse(ev.type, ev.payload)
                if ev.type == "done":
                    emitted_done = ev.payload
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"error": str(exc)})
            emitted_done = {
                "status": "failed",
                "error": str(exc),
                "final_text": "",
                "steps": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "transcript": [],
            }

        async with state.sessionmaker() as session:
            row = await session.get(AgentRun, run_id)
            if row is not None and emitted_done is not None:
                row.status = emitted_done.get("status", "done")
                row.steps_taken = int(emitted_done.get("steps", 0))
                row.tokens_in = int(emitted_done.get("tokens_in", 0))
                row.tokens_out = int(emitted_done.get("tokens_out", 0))
                row.final_text = emitted_done.get("final_text") or None
                row.transcript = emitted_done.get("transcript", [])
                row.error = emitted_done.get("error")
                row.completed_at = datetime.now(UTC)
                await session.commit()

        await state.audit.record(
            surface="agent",
            op="POST",
            path="/agent/v1",
            status_code=200,
            latency_ms=now_ms() - started,
            request_meta={
                "run_id": run_id,
                "model": body.model or settings.llm_model,
                "provider": settings.llm_provider,
            },
        )

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(event: str, data: dict | str) -> bytes:
    body = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {body}\n\n".encode()


def state_to_app(state: AppState):
    """Resolve the FastAPI app handle attached during lifespan startup."""
    app = getattr(state, "_fastapi_app", None)
    if app is not None:
        return app
    raise RuntimeError("FastAPI app handle not attached to AppState")
