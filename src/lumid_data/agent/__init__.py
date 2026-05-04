"""Chat-with-data agent: provider-agnostic LLM tool-use over the CRUD surface."""

from .providers import LLMAdapter, ToolCall, ToolDef, make_adapter
from .runner import (
    RunnerEvent,
    RunResult,
    ToolDispatcher,
    build_dispatcher_from_app,
    run,
)
from .tools import build_tool_catalog

__all__ = [
    "LLMAdapter",
    "RunResult",
    "RunnerEvent",
    "ToolCall",
    "ToolDef",
    "ToolDispatcher",
    "build_dispatcher_from_app",
    "build_tool_catalog",
    "make_adapter",
    "run",
]
