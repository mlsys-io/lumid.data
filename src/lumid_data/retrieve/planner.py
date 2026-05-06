"""NL2SQL planner — drives ``/agent/v1`` runner internally with probe-only tools.

Inputs: an :class:`~lumid_data.retrieve.schema_card.SchemaCardBundle` (already
built / cached) and an NL description. Output: a parsed
:class:`RetrievalPlan` plus accounting fields (run_id, tokens, steps,
transcript).

The planner keeps the agent context slim by:
- Injecting the schema cards into the system prompt (cached by Anthropic
  ephemeral cache_control), not the user message.
- Restricting ``tools_allowed`` to probe routes only — the agent
  *cannot* call ``/sql/v1`` (full fetch) or ``/storage/v1/object/...``
  (full bytes). It only sees ``EXPLAIN``, ``COUNT``, ``SAMPLE``,
  ``storage_list``, ``storage_stat``.
- Asking for a JSON plan in the contract; the planner parses, the
  replayer executes.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from lumid_data.sdk.schemas import RetrievalPlan

from ..agent import build_dispatcher_from_app, build_tool_catalog
from ..agent import run as run_agent
from ..agent.providers.base import LLMAdapter
from ..utils.ids import new_run_id
from .schema_card import SchemaCardBundle, render_bundle_for_prompt

logger = logging.getLogger(__name__)


PROBE_TOOL_NAMES: tuple[str, ...] = (
    "post_sql_v1_explain",
    "post_sql_v1_count",
    "post_sql_v1_sample",
    "get_storage_v1_list_bucket",
    "get_storage_v1_stat_bucket_path",
)


_SYSTEM_TEMPLATE = """\
You are a NL2SQL data-access planner for lumid.data. Your only job is to PLAN \
a deterministic access chain (final SQL queries or storage_get ops) that the \
caller will replay against the data store. You MUST NOT pull full rows / object \
bytes into your context — only run probe tools.

Available probe tools (use these to validate before emitting your plan):
  - post_sql_v1_explain   — body: {{"query": "<SELECT ...>"}}; returns EXPLAIN plan
  - post_sql_v1_count     — body: {{"query": "<SELECT ...>"}}; returns scalar COUNT(*)
  - post_sql_v1_sample    — body: {{"query": "...", "limit": <=20}}; ≤20 rows
  - get_storage_v1_list_bucket — query bucket + prefix (limit ≤ 10)
  - get_storage_v1_stat_bucket_path — head an object, returns size + etag only

Schema cards (already cached; this is your reference — do NOT re-enumerate \
information_schema):

{schema_cards}

Output contract — your final response MUST be a single JSON object \
(``` ```json fence is fine):
{{
  "plan": [
    {{"op": "sql", "query": "SELECT ... FROM <schema>.<table> WHERE ... LIMIT N;"}},
    {{"op": "storage_get", "bucket": "<bucket>", "key": "<key>"}}
  ],
  "expected_rowcount_or_size": <int>,
  "rationale": "<one-line why>"
}}

Hard rules:
- Do NOT invoke full-fetch tools (post_sql_v1, get_storage_v1_object_*). Those \
will be invoked by the replay engine on your plan.
- Cap probe calls at 5; the schema cards already carry distinct counts, top-K \
values, min/max, and sample non-null values — only probe when the cards are \
ambiguous or empty.
- Canonical SQL must be Postgres-dialect-correct (cast to numeric for ROUND \
on doubles; quote case-sensitive identifiers with double quotes; date/time \
literals in ISO 8601). No trailing semicolons inside the SQL string.
- Default LIMIT ≤ 10 unless the goal explicitly asks for more.

Plan composition:
- STRONGLY prefer a single SQL op that JOINs / CTE-composes everything the goal \
needs into one homogeneous result set (one column schema, one set of rows). \
Downstream consumers expect a flat tabular result. Use ``WITH ... AS`` CTEs \
to assemble multi-table goals into one query.
- Multiple ops are allowed only when a single SQL is genuinely impractical \
(e.g. a SQL retrieval plus a follow-up object fetch keyed on a row from the \
SQL). Don't split for stylistic reasons.
"""


@dataclass
class PlanningResult:
    run_id: str
    plan: RetrievalPlan
    transcript: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    steps_taken: int = 0
    final_text: str = ""


class RetrievalPlanner:
    """Wraps the existing agent runner with probe-only tools + schema cards."""

    def __init__(
        self,
        *,
        adapter: LLMAdapter,
        app: Any,
        base_url: str,
        bearer: str | None = None,
        max_steps_default: int = 20,
    ) -> None:
        self._adapter = adapter
        self._app = app
        self._base_url = base_url
        self._bearer = bearer
        self._max_steps_default = max_steps_default

    async def plan(
        self,
        *,
        description: str,
        bundle: SchemaCardBundle,
        max_steps: int | None = None,
        run_id: str | None = None,
    ) -> PlanningResult:
        run_id = run_id or new_run_id()
        system = _SYSTEM_TEMPLATE.format(schema_cards=render_bundle_for_prompt(bundle))
        all_tools = build_tool_catalog(self._app)
        tools = [t for t in all_tools if t.name in PROBE_TOOL_NAMES]
        dispatcher = build_dispatcher_from_app(
            self._app, base_url=self._base_url, bearer=self._bearer
        )

        result = PlanningResult(run_id=run_id, plan=RetrievalPlan(plan=[]))
        final_text_parts: list[str] = []
        async for ev in run_agent(
            adapter=self._adapter,
            tools=tools,
            dispatcher=dispatcher,
            goal=description,
            max_steps=max_steps or self._max_steps_default,
            system=system,
        ):
            if ev.type == "text":
                final_text_parts.append(ev.payload.get("text", ""))
            elif ev.type == "done":
                payload = ev.payload
                result.final_text = payload.get("final_text") or "".join(
                    final_text_parts
                )
                result.tokens_in = int(payload.get("tokens_in", 0))
                result.tokens_out = int(payload.get("tokens_out", 0))
                result.steps_taken = int(payload.get("steps", 0))
                result.transcript = list(payload.get("transcript", []))
            elif ev.type == "error":
                raise PlannerError(ev.payload.get("error") or "agent failed")

        result.plan = parse_retrieval_plan(result.final_text)
        return result


class PlannerError(RuntimeError):
    """Surfaced when the agent fails before emitting a plan."""


_FENCE = "```"


def _extract_fenced_json(text: str) -> str | None:
    """Pull a fenced JSON object (with or without a ``json`` tag) out of ``text``.

    Returns the inner blob if it looks like a JSON object, else None.
    """
    open_idx = text.find(_FENCE)
    if open_idx == -1:
        return None
    body_start = open_idx + len(_FENCE)
    if text[body_start : body_start + 4].lower() == "json":
        body_start += 4
    while body_start < len(text) and text[body_start] in " \t\r\n":
        body_start += 1
    close_idx = text.find(_FENCE, body_start)
    if close_idx == -1:
        return None
    blob = text[body_start:close_idx].strip()
    if blob.startswith("{") and blob.endswith("}"):
        return blob
    return None


def parse_retrieval_plan(final_text: str) -> RetrievalPlan:
    """Extract and validate the JSON plan from the agent's final text."""
    if not final_text:
        raise PlannerError("agent produced empty final_text — no plan to parse")
    blob = _extract_fenced_json(final_text)
    if blob is None:
        start = final_text.find("{")
        end = final_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise PlannerError(
                f"no JSON object found in final_text: {final_text[:200]!r}"
            )
        blob = final_text[start : end + 1]
    try:
        raw = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise PlannerError(f"plan JSON malformed: {exc}") from exc
    try:
        return RetrievalPlan.model_validate(raw)
    except Exception as exc:
        raise PlannerError(f"plan failed schema validation: {exc}") from exc
