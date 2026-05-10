"""Parser for structured retrieval plans emitted by the data agent."""

import json

from lumid_data.sdk.schemas import RetrievalPlan


class PlannerError(RuntimeError):
    """Raised when a structured retrieval plan cannot be parsed."""


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
