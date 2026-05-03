"""Planning stage: routing policy table.

Maps ``(modality, cadence, descriptor)`` to a ``Route`` plus the target
table / volume / topic name. Phase 1 only emits ``Route="delta"`` for
batched structured / text sources; other branches raise (Phase 2+).

This is intentionally a small, deterministic function — not an LLM.
LLM-driven planning slots in here as another branch when a descriptor
opts in.
"""

from ..schemas.descriptors import Modality, Route, SourceDescriptor


class RoutingDecision:
    __slots__ = ("route", "target_table", "target_volume", "target_topic")

    def __init__(
        self,
        route: Route,
        target_table: str | None = None,
        target_volume: str | None = None,
        target_topic: str | None = None,
    ) -> None:
        self.route = route
        self.target_table = target_table
        self.target_volume = target_volume
        self.target_topic = target_topic


def decide(descriptor: SourceDescriptor, modality: Modality) -> RoutingDecision:
    if descriptor.policy.override_route is not None:
        return _build(descriptor, modality, descriptor.policy.override_route)
    if descriptor.cadence == "batch" and modality in {"structured", "text"}:
        return _build(descriptor, modality, "delta")
    if descriptor.cadence == "batch" and modality in {
        "image",
        "audio",
        "video",
        "blob",
    }:
        return _build(descriptor, modality, "uc_volume_with_manifest")
    if descriptor.cadence == "batch" and modality == "timeseries":
        return _build(descriptor, modality, "timescale")
    if descriptor.cadence == "batch" and descriptor.embed_with:
        return _build(descriptor, modality, "milvus_embed")
    if descriptor.cadence == "stream":
        return _build(descriptor, modality, "rw_stream")
    raise NotImplementedError(
        f"No Phase 1 route for cadence={descriptor.cadence!r} modality={modality!r}"
    )


def _build(
    descriptor: SourceDescriptor, modality: Modality, route: Route
) -> RoutingDecision:
    namespace = _namespace(descriptor)
    table_name = _table_name(descriptor, modality)
    if route == "delta":
        return RoutingDecision(route=route, target_table=f"{namespace}.{table_name}")
    if route == "rw_stream":
        topic = f"{namespace}__{table_name}"
        return RoutingDecision(
            route=route,
            target_topic=topic,
            target_table=f"{namespace}.{table_name}",
        )
    if route == "uc_volume_with_manifest":
        return RoutingDecision(
            route=route,
            target_volume=f"{namespace}.{table_name}.objects",
            target_table=f"{namespace}.{table_name}",
        )
    if route == "milvus_embed":
        return RoutingDecision(
            route=route,
            target_table=f"{namespace}.{table_name}",
            target_volume=f"{namespace}.{table_name}.objects",
        )
    if route == "timescale":
        return RoutingDecision(route=route, target_table=f"{namespace}.{table_name}")
    if route == "dlq":
        return RoutingDecision(route=route, target_table=f"{namespace}._dlq")
    raise NotImplementedError(f"Unhandled route {route!r}")


def _namespace(descriptor: SourceDescriptor) -> str:
    parts = ["lumid_data", descriptor.tenant]
    if descriptor.app:
        parts.append(descriptor.app)
    return ".".join(parts)


def _table_name(descriptor: SourceDescriptor, modality: Modality) -> str:
    base = descriptor.name.lower().replace("-", "_").replace(" ", "_")
    return base
