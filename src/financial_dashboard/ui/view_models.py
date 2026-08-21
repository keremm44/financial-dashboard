from __future__ import annotations

from numbers import Integral
from typing import Any, Iterable

import pandas as pd

from financial_dashboard.engines.ham_evidence import HamFamily
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.three_domain_observer import FOUNDATION_OBSERVER_TIMEFRAMES
from financial_dashboard.ham_mtf_replay import (
    HAM_EVIDENCE_TIMEFRAMES,
    HamMTFEvidenceReplay,
)
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.three_domain_replay import ThreeDomainReplayResult

from .runtime import CacheTimeframeStatus


_DIRECTION_LABEL = {
    Direction.UP: "UP",
    Direction.DOWN: "DOWN",
    Direction.NEUTRAL: "NEUTRAL",
}


def _direction(value: Direction | None) -> str:
    return _DIRECTION_LABEL.get(value, "—")


def _frame(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def overview_values(result: ThreeDomainReplayResult) -> dict[str, str]:
    observation = result.observation
    return {
        "MTF pressure": result.pressure.state.value,
        "Recovery evidence": result.pressure.recovery_status.value,
        "Up structure": result.structure.upward.stage.value,
        "Down structure": result.structure.downward.stage.value,
        "Location": result.location.state.value,
        "Combined state": observation.state.value,
        "As of": str(observation.as_of),
    }


def cache_status_frame(
    statuses: Iterable[CacheTimeframeStatus],
) -> pd.DataFrame:
    rows = [
        {
            "Timeframe": status.timeframe,
            "Status": status.display_status,
            "Rows": status.row_count,
            "Confirmed rows": status.confirmed_row_count,
            "Open rows": status.open_row_count,
            "Incomplete rows": status.incomplete_row_count,
            "Earliest timestamp": status.earliest_timestamp,
            "Latest timestamp": status.latest_timestamp,
            "Warnings": " | ".join(status.warnings),
            "Errors": " | ".join(status.errors),
            "Path": str(status.path),
        }
        for status in statuses
    ]
    return _frame(
        rows,
        (
            "Timeframe",
            "Status",
            "Rows",
            "Confirmed rows",
            "Open rows",
            "Incomplete rows",
            "Earliest timestamp",
            "Latest timestamp",
            "Warnings",
            "Errors",
            "Path",
        ),
    )


def structure_history_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    """Expose observed chronology and left-boundary limits without sufficiency claims."""

    rows = [
        {
            "Timeframe": diagnostic.timeframe,
            "Usable closed bars": diagnostic.input_bar_count,
            "Usable first candle": diagnostic.input_start,
            "Usable last candle": diagnostic.input_end,
            "First external event": diagnostic.first_external_event_type,
            "First event at": diagnostic.first_external_event_at,
            "First event maturity": diagnostic.first_external_event_maturity.value,
            "Bars before first event": diagnostic.bars_before_first_external_event,
            "External structure events": diagnostic.external_structure_event_count,
            "External CHoCH": diagnostic.choch_count,
            "Transition BOS": diagnostic.transition_confirmation_bos_count,
            "Continuation BOS": diagnostic.continuation_bos_count,
            "Current uses initial structure": (
                "YES" if diagnostic.current_progression_uses_initial_structure else "NO"
            ),
            "Left-boundary state": diagnostic.state.value,
            "Reasons": " | ".join(diagnostic.reasons),
        }
        for diagnostic in result.structure_history
    ]
    return _frame(
        rows,
        (
            "Timeframe",
            "Usable closed bars",
            "Usable first candle",
            "Usable last candle",
            "First external event",
            "First event at",
            "First event maturity",
            "Bars before first event",
            "External structure events",
            "External CHoCH",
            "Transition BOS",
            "Continuation BOS",
            "Current uses initial structure",
            "Left-boundary state",
            "Reasons",
        ),
    )


def mtf_matrix_frame(
    result: ThreeDomainReplayResult | None,
    statuses: Iterable[CacheTimeframeStatus],
) -> pd.DataFrame:
    status_by_tf = {status.timeframe: status for status in statuses}
    story_by_tf = (
        {state.timeframe: state for state in result.pressure.timeframe_states}
        if result is not None
        else {}
    )
    replay_by_tf = result.structure_location.replays if result is not None else {}
    rows: list[dict[str, Any]] = []

    for timeframe in FOUNDATION_OBSERVER_TIMEFRAMES:
        status = status_by_tf.get(timeframe)
        story = story_by_tf.get(timeframe)
        replay = replay_by_tf.get(timeframe)
        external = None if replay is None else replay.market_structure.external_scope
        internal = None if replay is None else replay.market_structure.internal_scope
        rows.append(
            {
                "Timeframe": timeframe,
                "Data": "MISSING" if status is None else status.display_status,
                "External direction": _direction(
                    None if external is None else external.direction
                ),
                "External state": "—" if external is None else external.state,
                "Internal direction": _direction(
                    None if internal is None else internal.direction
                ),
                "Internal state": "—" if internal is None else internal.state,
                "Pattern direction": _direction(
                    None if story is None else story.pattern_direction
                ),
                "Pattern type": "—" if story is None else story.pattern_type or "—",
                "Pattern state": "—" if story is None else story.pattern_state or "—",
                "Breakout direction": _direction(
                    None if story is None else story.breakout_direction
                ),
                "Structural quality": (
                    None if story is None else story.structural_quality
                ),
                "Pattern quality": None if story is None else story.pattern_quality,
            }
        )
    return _frame(
        rows,
        (
            "Timeframe",
            "Data",
            "External direction",
            "External state",
            "Internal direction",
            "Internal state",
            "Pattern direction",
            "Pattern type",
            "Pattern state",
            "Breakout direction",
            "Structural quality",
            "Pattern quality",
        ),
    )


def structure_events_frame(
    result: ThreeDomainReplayResult,
    *,
    clock: CausalBarClock | None = None,
) -> pd.DataFrame:
    clock = clock or CausalBarClock()
    outcome_availability = {
        outcome.event_uid: outcome.event_available_at
        for outcome in result.structure_location.location_outcomes
    }
    rows: list[dict[str, Any]] = []
    for timeframe in result.timeframes:
        market = result.structure_location.replay_for(timeframe).market_structure
        for event in market.events:
            available_at = outcome_availability.get(event.event_uid)
            if available_at is None and event.confirmed_at is not None:
                available_at = clock.available_at(event.confirmed_at, timeframe)
            rows.append(
                {
                    "Event UID": event.event_uid,
                    "Timeframe": timeframe,
                    "Scope": event.scope,
                    "Event": event.event_type,
                    "BOS maturity": event.bos_maturity.value,
                    "Direction": _direction(event.direction),
                    "Candidate at": event.candidate_at,
                    "Confirmed at": event.confirmed_at,
                    "Causal available at": available_at,
                    "Broken level": event.broken_level,
                    "Origin price": event.origin_price,
                    "Confirmation close": event.confirmation_close,
                    "Quality": event.quality,
                    "Confirmation": event.confirmation_status.value,
                    "Validity": event.validity.value,
                    "Relevance": event.relevance.value,
                    "Outcome": event.outcome.value,
                    "Confirmed by": event.confirmed_by_event_uid,
                    "Failed by": event.failed_by_event_uid,
                    "Evidence": event.evidence_text,
                }
            )
    rows.sort(
        key=lambda row: (
            ""
            if row["Causal available at"] is None
            else pd.Timestamp(row["Causal available at"]).isoformat(),
            row["Event UID"],
        )
    )
    return _frame(
        rows,
        (
            "Event UID",
            "Timeframe",
            "Scope",
            "Event",
            "BOS maturity",
            "Direction",
            "Candidate at",
            "Confirmed at",
            "Causal available at",
            "Broken level",
            "Origin price",
            "Confirmation close",
            "Quality",
            "Confirmation",
            "Validity",
            "Relevance",
            "Outcome",
            "Confirmed by",
            "Failed by",
            "Evidence",
        ),
    )


def zones_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for timeframe in result.timeframes:
        support = result.structure_location.replay_for(timeframe).support_resistance
        for zone in support.zones:
            rows.append(
                {
                    "Zone UID": zone.zone_uid,
                    "Timeframe": timeframe,
                    "Side": zone.side.value,
                    "Kind": zone.kind.value,
                    "Lifecycle": zone.lifecycle.value,
                    "Low": zone.low,
                    "High": zone.high,
                    "Center": zone.center,
                    "Reference ATR": zone.reference_atr,
                    "Quality": zone.quality,
                    "Touches": zone.touches,
                    "Boundary stability": zone.boundary_stability,
                    "Created at": zone.created_at,
                    "Last updated at": zone.last_updated_at,
                    "Last transition at": zone.last_transition_at,
                    "Confluence eligible": zone.is_confluence_eligible,
                    "Active": zone.is_active,
                }
            )
    return _frame(
        rows,
        (
            "Zone UID",
            "Timeframe",
            "Side",
            "Kind",
            "Lifecycle",
            "Low",
            "High",
            "Center",
            "Reference ATR",
            "Quality",
            "Touches",
            "Boundary stability",
            "Created at",
            "Last updated at",
            "Last transition at",
            "Confluence eligible",
            "Active",
        ),
    )


def confluence_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    rows = [
        {
            "Cluster UID": cluster.cluster_uid,
            "Side": cluster.side.value,
            "Timeframes": ", ".join(cluster.timeframes),
            "Member zones": " | ".join(cluster.member_zone_uids),
            "Envelope low": cluster.envelope_low,
            "Envelope high": cluster.envelope_high,
            "Common low": cluster.common_low,
            "Common high": cluster.common_high,
            "Reference price": cluster.reference_price,
            "Geometry": cluster.geometry_score,
            "Quality": cluster.quality_score,
            "Maturity": cluster.maturity_score,
            "Coverage": cluster.timeframe_coverage,
            "Score": cluster.score,
        }
        for cluster in result.location.confluence
    ]
    return _frame(
        rows,
        (
            "Cluster UID",
            "Side",
            "Timeframes",
            "Member zones",
            "Envelope low",
            "Envelope high",
            "Common low",
            "Common high",
            "Reference price",
            "Geometry",
            "Quality",
            "Maturity",
            "Coverage",
            "Score",
        ),
    )


def opposing_conflicts_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    rows = [
        {
            "Conflict UID": conflict.conflict_uid,
            "Kind": conflict.kind.value,
            "Support timeframe": conflict.support_timeframe,
            "Support zone": conflict.support_zone_uid,
            "Resistance timeframe": conflict.resistance_timeframe,
            "Resistance zone": conflict.resistance_zone_uid,
            "Overlap low": conflict.overlap_low,
            "Overlap high": conflict.overlap_high,
            "Gap": conflict.gap,
            "Gap ATR": conflict.gap_atr,
        }
        for conflict in result.location.opposing_conflicts
    ]
    return _frame(
        rows,
        (
            "Conflict UID",
            "Kind",
            "Support timeframe",
            "Support zone",
            "Resistance timeframe",
            "Resistance zone",
            "Overlap low",
            "Overlap high",
            "Gap",
            "Gap ATR",
        ),
    )


def location_outcomes_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    rows = [
        {
            "Outcome UID": outcome.outcome_uid,
            "Event UID": outcome.event_uid,
            "Event": outcome.event_type,
            "Scope": outcome.event_scope,
            "Direction": _direction(outcome.event_direction),
            "Event timeframe": outcome.event_timeframe,
            "Event available at": outcome.event_available_at,
            "Status": outcome.status.value,
            "Causal timeframes": ", ".join(outcome.causal_timeframes),
            "Causal zone count": outcome.causal_zone_count,
            "Link count": len(outcome.links),
        }
        for outcome in result.location.event_outcomes
    ]
    return _frame(
        rows,
        (
            "Outcome UID",
            "Event UID",
            "Event",
            "Scope",
            "Direction",
            "Event timeframe",
            "Event available at",
            "Status",
            "Causal timeframes",
            "Causal zone count",
            "Link count",
        ),
    )


def event_zone_links_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    rows = [
        {
            "Link UID": link.link_uid,
            "Event UID": link.event_uid,
            "Event": link.event_type,
            "Scope": link.event_scope,
            "Direction": _direction(link.event_direction),
            "Event timeframe": link.event_timeframe,
            "Event available at": link.event_available_at,
            "Zone UID": link.zone_uid,
            "Zone timeframe": link.zone_timeframe,
            "Zone side": link.zone_side.value,
            "Zone lifecycle": link.zone_lifecycle.value,
            "Zone available at": link.zone_available_at,
            "Anchor": link.anchor.value,
            "Anchor price": link.anchor_price,
            "Relation": link.relation.value,
            "Meaning": link.meaning.value,
            "Distance": link.distance,
            "Distance ATR": link.distance_atr,
            "Score": link.score,
            "Same timeframe": link.same_timeframe,
        }
        for link in result.structure_location.event_zone_links
    ]
    return _frame(
        rows,
        (
            "Link UID",
            "Event UID",
            "Event",
            "Scope",
            "Direction",
            "Event timeframe",
            "Event available at",
            "Zone UID",
            "Zone timeframe",
            "Zone side",
            "Zone lifecycle",
            "Zone available at",
            "Anchor",
            "Anchor price",
            "Relation",
            "Meaning",
            "Distance",
            "Distance ATR",
            "Score",
            "Same timeframe",
        ),
    )


def observer_facts_frame(result: ThreeDomainReplayResult) -> pd.DataFrame:
    rows = [
        {"Type": "TENSION", "Fact": tension.value}
        for tension in result.observation.tensions
    ]
    rows.extend(
        {"Type": "FACT", "Fact": fact}
        for fact in result.observation.facts
    )
    rows.extend(
        {"Type": "PRESSURE_REASON", "Fact": reason}
        for reason in result.pressure.reasons
    )
    return _frame(rows, ("Type", "Fact"))


def _ham_family_values(snapshot: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for family in HamFamily:
        evidence = snapshot.families.for_family(family)
        prefix = family.value.title()
        values[f"{prefix} balance"] = evidence.balance
        values[f"{prefix} activity"] = evidence.activity
        values[f"{prefix} coverage"] = evidence.coverage
        values[f"{prefix} ready"] = evidence.ready
    return values


def ham_mtf_evidence_frame(
    result: HamMTFEvidenceReplay | None,
    statuses: Iterable[CacheTimeframeStatus],
) -> pd.DataFrame:
    """Latest neutral Ham evidence while keeping all five timeframes visible."""

    status_by_tf = {status.timeframe: status for status in statuses}
    replay_by_tf = (
        {replay.timeframe: replay for replay in result.timeframe_replays}
        if result is not None
        else {}
    )
    rows: list[dict[str, Any]] = []
    for timeframe in HAM_EVIDENCE_TIMEFRAMES:
        status = status_by_tf.get(timeframe)
        replay = replay_by_tf.get(timeframe)
        if replay is None:
            row = {
                "Timeframe": timeframe,
                "Data": "MISSING" if status is None else status.display_status,
                "Source warnings": (
                    "" if status is None else " | ".join(status.warnings)
                ),
                "Source errors": (
                    "Cache status unavailable"
                    if status is None
                    else " | ".join(status.errors)
                ),
                "Profile": "—",
                "Latest confirmed": None,
                "History bars": 0,
                "Warmup bars": 0,
                "Ready bars": 0,
                "Raw quality": "MISSING",
                "Valid indicators": 0,
                "ATR": None,
                "ATR ratio": None,
                "Volume quality": "MISSING",
                "Volume trust": None,
            }
            for family in HamFamily:
                prefix = family.value.title()
                row[f"{prefix} balance"] = None
                row[f"{prefix} activity"] = None
                row[f"{prefix} coverage"] = None
                row[f"{prefix} ready"] = False
            rows.append(row)
            continue

        latest = replay.latest
        row = {
            "Timeframe": timeframe,
            "Data": replay.source_quality.status.value,
            "Source warnings": " | ".join(replay.source_quality.warnings),
            "Source errors": " | ".join(replay.source_quality.errors),
            "Profile": replay.profile.value,
            "Latest confirmed": latest.timestamp,
            "History bars": replay.bar_count,
            "Warmup bars": replay.warmup_bar_count,
            "Ready bars": replay.ready_bar_count,
            "Raw quality": latest.data_quality.value,
            "Valid indicators": latest.raw.valid_evidence_count,
            "ATR": latest.raw.atr,
            "ATR ratio": latest.raw.atr_ratio,
            "Volume quality": latest.raw.volume_quality.name,
            "Volume trust": latest.raw.volume_trust,
        }
        row.update(_ham_family_values(latest))
        rows.append(row)

    columns = (
        "Timeframe",
        "Data",
        "Source warnings",
        "Source errors",
        "Profile",
        "Latest confirmed",
        "History bars",
        "Warmup bars",
        "Ready bars",
        "Raw quality",
        "Valid indicators",
        "ATR",
        "ATR ratio",
        "Volume quality",
        "Volume trust",
        "Price balance",
        "Price activity",
        "Price coverage",
        "Price ready",
        "Momentum balance",
        "Momentum activity",
        "Momentum coverage",
        "Momentum ready",
        "Timing balance",
        "Timing activity",
        "Timing coverage",
        "Timing ready",
        "Flow balance",
        "Flow activity",
        "Flow coverage",
        "Flow ready",
    )
    return _frame(rows, columns)


def ham_indicator_evidence_frame(
    result: HamMTFEvidenceReplay,
    *,
    timeframe: str,
) -> pd.DataFrame:
    """Expose all ten latest Tur-1 components without SYS/decision fields."""

    latest = result.replay_for(timeframe).latest
    rows = [
        {
            "Indicator": name,
            "Value": evidence.value,
            "Valid": evidence.valid,
            "Direction": evidence.direction,
            "Pending direction": evidence.pending_direction,
            "Reason": evidence.reason.name,
            "Consistency": evidence.consistency,
            "Movement strength": evidence.movement_strength,
            "Signed zone": evidence.signed_zone,
            "Evidence": evidence.evidence,
            "Relative evidence": evidence.relative_evidence,
        }
        for name, evidence in latest.raw.indicators.items()
    ]
    return _frame(
        rows,
        (
            "Indicator",
            "Value",
            "Valid",
            "Direction",
            "Pending direction",
            "Reason",
            "Consistency",
            "Movement strength",
            "Signed zone",
            "Evidence",
            "Relative evidence",
        ),
    )


def ham_history_frame(
    result: HamMTFEvidenceReplay,
    *,
    timeframe: str,
    limit: int | None = 100,
) -> pd.DataFrame:
    """Return recent confirmed Ham history by default, or all rows on demand."""

    replay = result.replay_for(timeframe)
    history = replay.history
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, Integral) or limit <= 0:
            raise ValueError("Ham history limit must be a positive integer or None")
        history = history[-int(limit) :]

    rows: list[dict[str, Any]] = []
    for snapshot in history:
        row = {
            "Timestamp": snapshot.timestamp,
            "Raw quality": snapshot.data_quality.value,
            "Valid indicators": snapshot.raw.valid_evidence_count,
            "Up evidence": snapshot.raw.up_evidence_count,
            "Down evidence": snapshot.raw.down_evidence_count,
            "Strong up": snapshot.raw.strong_up_count,
            "Strong down": snapshot.raw.strong_down_count,
            "Net evidence": snapshot.raw.net_evidence_score,
            "ATR": snapshot.raw.atr,
            "ATR ratio": snapshot.raw.atr_ratio,
            "Volume quality": snapshot.raw.volume_quality.name,
            "Volume coverage": snapshot.raw.volume_coverage,
            "Volume variation": snapshot.raw.volume_variation,
            "Volume trust": snapshot.raw.volume_trust,
        }
        row.update(_ham_family_values(snapshot))
        rows.append(row)

    columns = (
        "Timestamp",
        "Raw quality",
        "Valid indicators",
        "Up evidence",
        "Down evidence",
        "Strong up",
        "Strong down",
        "Net evidence",
        "ATR",
        "ATR ratio",
        "Volume quality",
        "Volume coverage",
        "Volume variation",
        "Volume trust",
        "Price balance",
        "Price activity",
        "Price coverage",
        "Price ready",
        "Momentum balance",
        "Momentum activity",
        "Momentum coverage",
        "Momentum ready",
        "Timing balance",
        "Timing activity",
        "Timing coverage",
        "Timing ready",
        "Flow balance",
        "Flow activity",
        "Flow coverage",
        "Flow ready",
    )
    return _frame(rows, columns)
