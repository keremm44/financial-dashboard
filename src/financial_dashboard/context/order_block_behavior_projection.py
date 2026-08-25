from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .envelope import ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


@dataclass(frozen=True, slots=True)
class OrderBlockBehaviorObservation:
    timeframe: str
    ref: FactRef
    identity: str
    bullish: bool
    top: float
    bottom: float
    state: str
    interaction: str
    active: bool
    age_bars: int
    bars_since_confirmation: int | None
    mitigation_count: int
    visit_count: int
    deepest_fill_ratio: float
    distance_atr: float | None
    total_inside_bars: int
    inside_close_bars: int
    current_visit_bars: int
    close_inside: bool
    range_intersects: bool
    first_entry_index: int | None
    last_entry_index: int | None
    favorable_exit_index: int | None
    bars_held_favorable: int
    max_favorable_move_atr: float
    terminal_reason: str | None


@dataclass(frozen=True, slots=True)
class OrderBlockBehaviorProjection:
    symbol: str
    timeframes: tuple[str, ...]
    observations: tuple[OrderBlockBehaviorObservation, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in self.observations)

    def for_timeframe(self, timeframe: str) -> tuple[OrderBlockBehaviorObservation, ...]:
        normalized = timeframe.strip().lower()
        return tuple(item for item in self.observations if item.timeframe == normalized)

    def available_at(self, as_of: Any) -> "OrderBlockBehaviorProjection":
        return replace(
            self,
            observations=tuple(
                item for item in self.observations if item.ref.is_available_at(as_of)
            ),
        )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _lineage_id(timeframe: str, identity: str) -> str:
    # Tracker identity is OB:<source_index>:<side>; target evidence uses
    # OB:<timeframe>:<source_index>:<side>. This is a known shared native origin.
    prefix = "OB:"
    suffix = identity[len(prefix) :] if identity.startswith(prefix) else identity
    return f"OB:{timeframe}:{suffix}"


def project_order_block_behavior(
    replay: Any | None,
    *,
    data_quality_by_timeframe: Mapping[str, Any],
) -> OrderBlockBehaviorProjection | None:
    if replay is None:
        return None

    behavior_by_timeframe = getattr(replay, "order_block_behavior", None) or {}
    rows: list[OrderBlockBehaviorObservation] = []
    for timeframe, behavior_rows in behavior_by_timeframe.items():
        replay_snapshot = replay.snapshots.get(timeframe)
        if replay_snapshot is None:
            continue
        quality = normalize_context_data_quality(data_quality_by_timeframe[timeframe])
        for item in behavior_rows:
            state = _enum_value(item.state)
            interaction = _enum_value(item.interaction)
            native_state = f"{state}:{interaction}"
            causal_family, source_family = families_for(
                ContextDomain.ORDER_BLOCK,
                fact_type="ORDER_BLOCK_BEHAVIOR",
            )
            lineage_id = _lineage_id(timeframe, str(item.identity))
            ref = FactRef(
                domain=ContextDomain.ORDER_BLOCK,
                fact_type="ORDER_BLOCK_BEHAVIOR",
                symbol=replay.symbol,
                timeframe=timeframe,
                native_id=(
                    f"OB_BEHAVIOR:{timeframe}:{item.identity}:{replay_snapshot.as_of}"
                ),
                native_state=native_state,
                origin_time=replay_snapshot.as_of,
                confirmed_at=replay_snapshot.as_of,
                available_at=replay_snapshot.available_at,
                lineage_id=lineage_id,
                causal_family=causal_family,
                source_family=source_family,
                data_quality=quality,
            )
            rows.append(
                OrderBlockBehaviorObservation(
                    timeframe=timeframe,
                    ref=ref,
                    identity=str(item.identity),
                    bullish=bool(item.bullish),
                    top=float(item.top),
                    bottom=float(item.bottom),
                    state=state,
                    interaction=interaction,
                    active=bool(item.active),
                    age_bars=int(item.age_bars),
                    bars_since_confirmation=item.bars_since_confirmation,
                    mitigation_count=int(item.mitigation_count),
                    visit_count=int(item.visit_count),
                    deepest_fill_ratio=float(item.deepest_fill_ratio),
                    distance_atr=item.distance_atr,
                    total_inside_bars=int(item.total_inside_bars),
                    inside_close_bars=int(item.inside_close_bars),
                    current_visit_bars=int(item.current_visit_bars),
                    close_inside=bool(item.close_inside),
                    range_intersects=bool(item.range_intersects),
                    first_entry_index=item.first_entry_index,
                    last_entry_index=item.last_entry_index,
                    favorable_exit_index=item.favorable_exit_index,
                    bars_held_favorable=int(item.bars_held_favorable),
                    max_favorable_move_atr=float(item.max_favorable_move_atr),
                    terminal_reason=item.terminal_reason,
                )
            )

    return OrderBlockBehaviorProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        observations=tuple(sorted(rows, key=lambda item: item.ref.deterministic_key)),
    )


__all__ = [
    "OrderBlockBehaviorObservation",
    "OrderBlockBehaviorProjection",
    "project_order_block_behavior",
]
