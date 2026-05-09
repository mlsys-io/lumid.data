"""Agent skill registry.

Skills are workflow instructions layered into the agent system prompt. They do
not add capabilities by themselves; capabilities still come from deterministic
tools.
"""

from dataclasses import dataclass

from .services.retrieval_tools import retrieval_system_addendum


@dataclass(frozen=True)
class AgentSkill:
    name: str
    description: str
    system_prompt: str
    tool_allowlist: tuple[str, ...] = ()
    required_success_tools: tuple[str, ...] = ()
    required_tools_message: str | None = None
    tool_result_visibility: str = "full"


_SKILLS: dict[str, AgentSkill] = {
    "data_retrieval": AgentSkill(
        name="data_retrieval",
        description=(
            "Retrieve data by collecting schema cards, probing safely, composing "
            "a structured plan, and materializing it with replay."
        ),
        system_prompt=retrieval_system_addendum().strip(),
        tool_allowlist=(
            "get_schema_cards",
            "post_sql_v1_explain",
            "post_sql_v1_count",
            "post_sql_v1_sample",
            "get_storage_v1_list_bucket",
            "get_storage_v1_stat_bucket_path",
            "replay_retrieval_plan",
        ),
        required_success_tools=("replay_retrieval_plan",),
        required_tools_message=(
            "You cannot finish this data retrieval yet. Compose a structured "
            "retrieval plan from the schema cards and metadata probes, then call "
            "replay_retrieval_plan. Do not answer from SQL rows in the model "
            "context."
        ),
        tool_result_visibility="preview_stats",
    )
}


def skill_names() -> list[str]:
    return sorted(_SKILLS)


def render_skill_prompt(names: list[str] | None) -> str:
    if not names:
        return ""
    unknown = sorted(set(names) - set(_SKILLS))
    if unknown:
        raise UnknownSkillError(f"unknown skills: {', '.join(unknown)}")
    sections = []
    for name in names:
        skill = _SKILLS[name]
        sections.append(
            f"## Skill: {skill.name}\n"
            f"{skill.description}\n\n"
            f"{skill.system_prompt}"
        )
    return "\n\nAgent skills:\n\n" + "\n\n".join(sections)


def skill_tool_allowlist(names: list[str] | None) -> set[str] | None:
    if not names:
        return None
    unknown = sorted(set(names) - set(_SKILLS))
    if unknown:
        raise UnknownSkillError(f"unknown skills: {', '.join(unknown)}")
    allowed: set[str] = set()
    for name in names:
        allowed.update(_SKILLS[name].tool_allowlist)
    return allowed


def skill_required_success_tools(names: list[str] | None) -> set[str]:
    if not names:
        return set()
    unknown = sorted(set(names) - set(_SKILLS))
    if unknown:
        raise UnknownSkillError(f"unknown skills: {', '.join(unknown)}")
    required: set[str] = set()
    for name in names:
        required.update(_SKILLS[name].required_success_tools)
    return required


def skill_required_tools_message(names: list[str] | None) -> str | None:
    if not names:
        return None
    unknown = sorted(set(names) - set(_SKILLS))
    if unknown:
        raise UnknownSkillError(f"unknown skills: {', '.join(unknown)}")
    messages: list[str] = []
    for name in names:
        message = _SKILLS[name].required_tools_message
        if message:
            messages.append(message)
    return "\n\n".join(messages) if messages else None


def skill_tool_result_visibility(names: list[str] | None) -> str:
    if not names:
        return "full"
    unknown = sorted(set(names) - set(_SKILLS))
    if unknown:
        raise UnknownSkillError(f"unknown skills: {', '.join(unknown)}")
    if any(_SKILLS[name].tool_result_visibility == "preview_stats" for name in names):
        return "preview_stats"
    return "full"


class UnknownSkillError(ValueError):
    """Raised when a request names a skill that is not registered."""
