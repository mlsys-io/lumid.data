"""``POST /agent/v1`` — server-streamed agent tool-use loop."""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...agent import build_dispatcher_from_app, build_tool_catalog
from ...agent import run as run_agent
from ...db.models import AgentRun
from ...utils.ids import new_run_id
from ..auth.security import default_principal
from ..deps import get_state
from ..services.audit import now_ms
from ..services.retrieval_tools import (
    retrieval_tool_defs,
    retrieval_tool_handlers,
)
from ..skills import UnknownSkillError, render_skill_prompt, skill_tool_allowlist
from ..skills import (
    skill_required_success_tools,
    skill_required_tools_message,
    skill_tool_result_visibility,
)
from ..state import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/v1", tags=["agent"])

_AGENT_SYSTEM = (
    "You are the lumid.data agent. The user describes a data-management "
    "intent in natural language; you accomplish it by calling the registered "
    "tools. Each tool corresponds to a data-plane operation on the same "
    "service. Prefer bounded inspection before materializing data, and explain "
    "what you did at the end."
)


class AgentRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=20_000)
    context: dict | None = None
    skills: list[str] | None = None
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
    principal = default_principal()
    run_id = new_run_id()
    started = now_ms()
    try:
        system_prompt = _AGENT_SYSTEM + render_skill_prompt(body.skills)
        skill_tools = skill_tool_allowlist(body.skills)
        required_success_tools = skill_required_success_tools(body.skills)
        required_tools_message = skill_required_tools_message(body.skills)
        tool_result_visibility = skill_tool_result_visibility(body.skills)
    except UnknownSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def stream():
        async with state.sessionmaker() as session:
            row = AgentRun(
                id=run_id,
                principal_id=principal.principal_id,
                provider=settings.llm_provider,
                model=body.model or settings.llm_model,
                goal=body.goal,
                status="running",
            )
            session.add(row)
            await session.commit()

        app = state_to_app(state)
        all_tools = [*build_tool_catalog(app), *retrieval_tool_defs()]
        if skill_tools is not None and body.tools_allowed:
            allowed = skill_tools & set(body.tools_allowed)
        elif skill_tools is not None:
            allowed = skill_tools
        elif body.tools_allowed:
            allowed = set(body.tools_allowed)
        else:
            allowed = None
        if allowed is not None:
            tools = [t for t in all_tools if t.name in allowed]
        else:
            tools = all_tools
        dispatcher = build_dispatcher_from_app(
            app, base_url=settings.base_url, bearer=None
        )
        dispatcher.local_tools.update(
            retrieval_tool_handlers(
                state=state,
                principal=principal,
                agent_run_id=run_id,
            )
        )
        emitted_done: dict | None = None
        try:
            async for ev in run_agent(
                adapter=state.llm_adapter,
                tools=tools,
                dispatcher=dispatcher,
                goal=body.goal,
                max_steps=body.max_steps or settings.agent_max_steps,
                system=system_prompt,
                required_success_tools=required_success_tools,
                required_tools_message=required_tools_message,
                tool_result_visibility=tool_result_visibility,
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
            principal=principal,
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
    body = data if isinstance(data, str) else json.dumps(jsonable_encoder(data))
    return f"event: {event}\ndata: {body}\n\n".encode()


def state_to_app(state: AppState):
    """Resolve the FastAPI app handle attached during lifespan startup."""
    app = getattr(state, "_fastapi_app", None)
    if app is not None:
        return app
    raise RuntimeError("FastAPI app handle not attached to AppState")
