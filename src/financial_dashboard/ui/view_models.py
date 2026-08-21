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
from financial_dashboard.volume_mtf_replay import (
    VOLUME_EVIDENCE_TIMEFRAMES,
    VolumeMTFEvidenceReplay,
)

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


def _volume_direction(value: int) -> str:
    if value > 0:
        return "UP"
    if value < 0:
        return "DOWN"
    return "NEUTRAL"


def volume_mtf_matrix_frame(
    result: VolumeMTFEvidenceReplay,
    statuses: Iterable[CacheTimeframeStatus],
) -> pd.DataFrame:
    """Project latest independent Volume facts; never aggregate raw MTF volume."""

    status_by_timeframe = {status.timeframe: status for status in statuses}
    contribution_by_timeframe = {
        contribution.timeframe: contribution
        for contribution in result.round2.pressure.contributions
    }
    replay_by_timeframe = {
        replay.timeframe: replay for replay in result.timeframe_replays
    }
    rows: list[dict[str, Any]] = []
    for timeframe in VOLUME_EVIDENCE_TIMEFRAMES:
        cache = status_by_timeframe.get(timeframe)
        replay = replay_by_timeframe.get(timeframe)
        contribution = contribution_by_timeframe.get(timeframe)
        if replay is None:
            rows.append(
                {
                    "Timeframe": timeframe,
                    "Data": "MISSING" if cache is None else cache.display_status,
                    "History bars": 0,
                    "Latest timestamp": None,
                    "Participation state": "—",
                    "Direction": "—",
                    "Readiness": "—",
                    "Replay quality": "—",
                    "RVOL": None,
                    "Volume z-score": None,
                    "Effort/result": "—",
                    "One-bar shock": False,
                    "MTF context factor": None,
                    "Raw volume summed": False,
                }
            )
            continue
        latest = replay.latest
        rows.append(
            {
                "Timeframe": timeframe,
                "Data": "READY" if cache is None else cache.display_status,
                "History bars": replay.bar_count,
                "Latest timestamp": latest.timestamp,
                "Participation state": latest.state,
                "Direction": _volume_direction(latest.evidence_direction),
                "Readiness": latest.status.value,
                "Replay quality": replay.replay_data_quality.value,
                "RVOL": latest.metrics.rvol,
                "Volume z-score": latest.metrics.volume_z_score,
                "Effort/result": latest.metrics.effort_result_class.value,
                "One-bar shock": latest.audit_export.one_bar_shock,
                "MTF context factor": (
                    None if contribution is None else contribution.signal_factor
                ),
                "Raw volume summed": result.round2.pressure.raw_volume_summed,
            }
        )
    return _frame(
        rows,
        (
            "Timeframe",
            "Data",
            "History bars",
            "Latest timestamp",
            "Participation state",
            "Direction",
            "Readiness",
            "Replay quality",
            "RVOL",
            "Volume z-score",
            "Effort/result",
            "One-bar shock",
            "MTF context factor",
            "Raw volume summed",
        ),
    )


def volume_event_links_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    risk_by_uid = {risk.event_uid: risk for risk in result.round2.risks}
    rows = []
    for assessment in result.round2.event_assessments:
        risk = risk_by_uid.get(assessment.event_uid)
        lower_sources = ", ".join(
            f"{inflow.source_timeframe}:{inflow.state.value}"
            for inflow in assessment.lower_timeframe_inflows
        )
        rows.append(
            {
                "Event UID": assessment.event_uid,
                "Timeframe": assessment.timeframe,
                "Scope": assessment.scope,
                "Event type": assessment.event_type,
                "Event direction": _volume_direction(assessment.event_direction),
                "Confirmed at": assessment.confirmed_at,
                "Same-TF relation": assessment.same_timeframe_relation.value,
                "Lower-TF importance": assessment.lower_timeframe_importance.value,
                "Lower-TF inflow": assessment.lower_timeframe_state.value,
                "Lower-TF score": assessment.lower_timeframe_score,
                "Lower-TF sources": lower_sources or "—",
                "Lower-TF confirms target": assessment.lower_timeframe_can_confirm,
                "Risk state": "—" if risk is None else risk.state.value,
                "Risk blocked": False if risk is None else risk.is_blocked,
                "Release trigger": "—" if risk is None else risk.release_trigger.value,
            }
        )
    return _frame(
        rows,
        (
            "Event UID",
            "Timeframe",
            "Scope",
            "Event type",
            "Event direction",
            "Confirmed at",
            "Same-TF relation",
            "Lower-TF importance",
            "Lower-TF inflow",
            "Lower-TF score",
            "Lower-TF sources",
            "Lower-TF confirms target",
            "Risk state",
            "Risk blocked",
            "Release trigger",
        ),
    )


def volume_risk_transitions_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [
        {
            "Event UID": risk.event_uid,
            "Timeframe": risk.timeframe,
            "Scope": risk.scope,
            "Bar index": transition.bar_index,
            "Timestamp": transition.timestamp,
            "Available at": transition.available_at,
            "Previous risk": transition.previous_state.value,
            "Risk state": transition.state.value,
            "Trigger": transition.trigger.value,
            "Source state": transition.source_state,
            "Reason": transition.reason,
        }
        for risk in result.round2.risks
        for transition in risk.transitions
    ]
    return _frame(
        rows,
        (
            "Event UID",
            "Timeframe",
            "Scope",
            "Bar index",
            "Timestamp",
            "Available at",
            "Previous risk",
            "Risk state",
            "Trigger",
            "Source state",
            "Reason",
        ),
    )


def volume_shocks_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [
        {
            "Shock UID": shock.shock_uid,
            "Timeframe": shock.timeframe,
            "Shock bar": shock.shock_bar,
            "Shock at": shock.shock_at,
            "Direction": _volume_direction(shock.direction),
            "Final stage": shock.final_stage.value,
            "Transitions": " → ".join(
                transition.stage.value for transition in shock.transitions
            ),
            "Immediate confirmation": shock.immediate_confirmation_allowed,
            "Entry authority": shock.entry_authority,
        }
        for shock in result.round2.shocks
    ]
    return _frame(
        rows,
        (
            "Shock UID",
            "Timeframe",
            "Shock bar",
            "Shock at",
            "Direction",
            "Final stage",
            "Transitions",
            "Immediate confirmation",
            "Entry authority",
        ),
    )


def volume_propagations_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [
        {
            "Origin timeframe": propagation.origin_timeframe,
            "Origin bar": propagation.origin_bar,
            "Origin at": propagation.origin_at,
            "Volume direction": _volume_direction(propagation.volume_direction),
            "Volume state": propagation.volume_state,
            "Progression state": propagation.state.value,
            "Highest direct timeframe": propagation.highest_direct_timeframe,
            "Direct event count": len(propagation.steps),
            "Direct causal events": " | ".join(
                (
                    f"{step.timeframe}:{step.scope}:{step.event_type}:"
                    f"{_volume_direction(step.event_direction)}@{step.available_at}"
                )
                for step in propagation.steps
            ) or "—",
            "Higher-TF confirmation invented": propagation.target_confirmation_invented,
        }
        for propagation in result.round2.structural_propagations
    ]
    return _frame(
        rows,
        (
            "Origin timeframe",
            "Origin bar",
            "Origin at",
            "Volume direction",
            "Volume state",
            "Progression state",
            "Highest direct timeframe",
            "Direct event count",
            "Direct causal events",
            "Higher-TF confirmation invented",
        ),
    )


def volume_history_frame(
    result: VolumeMTFEvidenceReplay,
    *,
    timeframe: str,
    limit: int | None = 100,
) -> pd.DataFrame:
    """Return last 100 confirmed Volume bars by default, or all retained history."""

    replay = result.replay_for(timeframe)
    history = replay.history
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, Integral) or limit <= 0:
            raise ValueError("Volume history limit must be a positive integer or None")
        history = history[-int(limit) :]
    rows = [
        {
            "Bar index": snapshot.bar_index,
            "Timestamp": snapshot.timestamp,
            "Segment": snapshot.segment_id,
            "Readiness": snapshot.status.value,
            "Data quality": snapshot.data_quality.value,
            "Participation state": snapshot.state,
            "Direction": _volume_direction(snapshot.evidence_direction),
            "RVOL": snapshot.metrics.rvol,
            "Volume z-score": snapshot.metrics.volume_z_score,
            "Volume percentile": snapshot.metrics.volume_percent_rank,
            "Volume regime": snapshot.metrics.volume_regime,
            "Capital regime": snapshot.metrics.capital_regime,
            "Pressure 5": snapshot.metrics.directional_value_pressure_5,
            "Pressure 10": snapshot.metrics.directional_value_pressure_10,
            "Net progress ATR": snapshot.metrics.net_progress_atr,
            "Directional efficiency": snapshot.metrics.directional_efficiency,
            "Effort/result": snapshot.metrics.effort_result_class.value,
            "Lifecycle stage": snapshot.audit_export.participation_stage,
            "Break stage": snapshot.audit_export.break_stage,
            "Absorption stage": snapshot.audit_export.absorption_stage,
            "One-bar shock": snapshot.audit_export.one_bar_shock,
            "Shock direction": _volume_direction(snapshot.audit_export.shock_direction),
            "Confirmed closed bar": snapshot.is_confirmed,
        }
        for snapshot in history
    ]
    return _frame(
        rows,
        (
            "Bar index",
            "Timestamp",
            "Segment",
            "Readiness",
            "Data quality",
            "Participation state",
            "Direction",
            "RVOL",
            "Volume z-score",
            "Volume percentile",
            "Volume regime",
            "Capital regime",
            "Pressure 5",
            "Pressure 10",
            "Net progress ATR",
            "Directional efficiency",
            "Effort/result",
            "Lifecycle stage",
            "Break stage",
            "Absorption stage",
            "One-bar shock",
            "Shock direction",
            "Confirmed closed bar",
        ),
    )


def volume_diagnostics_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [
        {
            "Timeframe": replay.timeframe,
            "Replay quality": replay.replay_data_quality.value,
            "Source quality": replay.source_quality.status.value,
            "History bars": replay.bar_count,
            "Ready bars": replay.ready_bar_count,
            "Warmup bars": replay.warmup_bar_count,
            "Unavailable bars": replay.unavailable_bar_count,
            "Excluded tail bars": replay.excluded_tail_bar_count,
            "Structure links": len(replay.event_links),
            "Unlinked participation": len(replay.participation_without_structure),
            "Source warnings": " | ".join(replay.source_quality.warnings),
            "Source errors": " | ".join(replay.source_quality.errors),
        }
        for replay in result.timeframe_replays
    ]
    return _frame(
        rows,
        (
            "Timeframe",
            "Replay quality",
            "Source quality",
            "History bars",
            "Ready bars",
            "Warmup bars",
            "Unavailable bars",
            "Excluded tail bars",
            "Structure links",
            "Unlinked participation",
            "Source warnings",
            "Source errors",
        ),
    )


def volume_deduplication_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    dedup = result.round2.deduplication
    return _frame(
        [
            {
                "Source family": dedup.source_family,
                "Registered channels": ", ".join(
                    channel.value for channel in dedup.registered_channels
                ),
                "Active channels": ", ".join(
                    channel.value for channel in dedup.active_channels
                ),
                "Representative": dedup.representative_channel.value,
                "Independent vote cap": dedup.independent_vote_cap,
                "Raw MTF volume summed": dedup.raw_mtf_volume_summed,
                "Policy": dedup.policy,
            }
        ],
        (
            "Source family",
            "Registered channels",
            "Active channels",
            "Representative",
            "Independent vote cap",
            "Raw MTF volume summed",
            "Policy",
        ),
    )
