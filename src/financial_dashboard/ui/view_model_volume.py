from __future__ import annotations

from numbers import Integral
from typing import Iterable

import pandas as pd

from financial_dashboard.volume_mtf_replay import (
    VOLUME_EVIDENCE_TIMEFRAMES,
    VolumeMTFEvidenceReplay,
)

from .runtime import CacheTimeframeStatus
from .view_model_common import frame


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
    status_by_timeframe = {status.timeframe: status for status in statuses}
    contribution_by_timeframe = {
        contribution.timeframe: contribution
        for contribution in result.round2.pressure.contributions
    }
    replay_by_timeframe = {replay.timeframe: replay for replay in result.timeframe_replays}
    rows = []
    for timeframe in VOLUME_EVIDENCE_TIMEFRAMES:
        cache = status_by_timeframe.get(timeframe)
        replay = replay_by_timeframe.get(timeframe)
        contribution = contribution_by_timeframe.get(timeframe)
        if replay is None:
            rows.append({
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
            })
            continue
        latest = replay.latest
        rows.append({
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
            "MTF context factor": None if contribution is None else contribution.signal_factor,
            "Raw volume summed": result.round2.pressure.raw_volume_summed,
        })
    return frame(rows, (
        "Timeframe", "Data", "History bars", "Latest timestamp", "Participation state",
        "Direction", "Readiness", "Replay quality", "RVOL", "Volume z-score",
        "Effort/result", "One-bar shock", "MTF context factor", "Raw volume summed",
    ))


def volume_event_links_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    risk_by_uid = {risk.event_uid: risk for risk in result.round2.risks}
    rows = []
    for assessment in result.round2.event_assessments:
        risk = risk_by_uid.get(assessment.event_uid)
        lower_sources = ", ".join(
            f"{inflow.source_timeframe}:{inflow.state.value}"
            for inflow in assessment.lower_timeframe_inflows
        )
        rows.append({
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
        })
    return frame(rows, (
        "Event UID", "Timeframe", "Scope", "Event type", "Event direction", "Confirmed at",
        "Same-TF relation", "Lower-TF importance", "Lower-TF inflow", "Lower-TF score",
        "Lower-TF sources", "Lower-TF confirms target", "Risk state", "Risk blocked",
        "Release trigger",
    ))


def volume_risk_transitions_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [{
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
    } for risk in result.round2.risks for transition in risk.transitions]
    return frame(rows, (
        "Event UID", "Timeframe", "Scope", "Bar index", "Timestamp", "Available at",
        "Previous risk", "Risk state", "Trigger", "Source state", "Reason",
    ))


def volume_shocks_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [{
        "Shock UID": shock.shock_uid,
        "Timeframe": shock.timeframe,
        "Shock bar": shock.shock_bar,
        "Shock at": shock.shock_at,
        "Direction": _volume_direction(shock.direction),
        "Final stage": shock.final_stage.value,
        "Transitions": " → ".join(transition.stage.value for transition in shock.transitions),
        "Immediate confirmation": shock.immediate_confirmation_allowed,
        "Entry authority": shock.entry_authority,
    } for shock in result.round2.shocks]
    return frame(rows, (
        "Shock UID", "Timeframe", "Shock bar", "Shock at", "Direction", "Final stage",
        "Transitions", "Immediate confirmation", "Entry authority",
    ))


def volume_propagations_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [{
        "Origin timeframe": propagation.origin_timeframe,
        "Origin bar": propagation.origin_bar,
        "Origin at": propagation.origin_at,
        "Volume direction": _volume_direction(propagation.volume_direction),
        "Volume state": propagation.volume_state,
        "Progression state": propagation.state.value,
        "Highest direct timeframe": propagation.highest_direct_timeframe,
        "Direct event count": len(propagation.steps),
        "Direct causal events": " | ".join(
            f"{step.timeframe}:{step.scope}:{step.event_type}:"
            f"{_volume_direction(step.event_direction)}@{step.available_at}"
            for step in propagation.steps
        ) or "—",
        "Higher-TF confirmation invented": propagation.target_confirmation_invented,
    } for propagation in result.round2.structural_propagations]
    return frame(rows, (
        "Origin timeframe", "Origin bar", "Origin at", "Volume direction", "Volume state",
        "Progression state", "Highest direct timeframe", "Direct event count",
        "Direct causal events", "Higher-TF confirmation invented",
    ))


def volume_history_frame(
    result: VolumeMTFEvidenceReplay,
    *,
    timeframe: str,
    limit: int | None = 100,
) -> pd.DataFrame:
    replay = result.replay_for(timeframe)
    history = replay.history
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, Integral) or limit <= 0:
            raise ValueError("Volume history limit must be a positive integer or None")
        history = history[-int(limit):]
    rows = [{
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
    } for snapshot in history]
    return frame(rows, (
        "Bar index", "Timestamp", "Segment", "Readiness", "Data quality",
        "Participation state", "Direction", "RVOL", "Volume z-score", "Volume percentile",
        "Volume regime", "Capital regime", "Pressure 5", "Pressure 10", "Net progress ATR",
        "Directional efficiency", "Effort/result", "Lifecycle stage", "Break stage",
        "Absorption stage", "One-bar shock", "Shock direction", "Confirmed closed bar",
    ))


def volume_diagnostics_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    rows = [{
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
    } for replay in result.timeframe_replays]
    return frame(rows, (
        "Timeframe", "Replay quality", "Source quality", "History bars", "Ready bars",
        "Warmup bars", "Unavailable bars", "Excluded tail bars", "Structure links",
        "Unlinked participation", "Source warnings", "Source errors",
    ))


def volume_deduplication_frame(result: VolumeMTFEvidenceReplay) -> pd.DataFrame:
    dedup = result.round2.deduplication
    return frame([{
        "Source family": dedup.source_family,
        "Registered channels": ", ".join(channel.value for channel in dedup.registered_channels),
        "Active channels": ", ".join(channel.value for channel in dedup.active_channels),
        "Representative": dedup.representative_channel.value,
        "Independent vote cap": dedup.independent_vote_cap,
        "Raw MTF volume summed": dedup.raw_mtf_volume_summed,
        "Policy": dedup.policy,
    }], (
        "Source family", "Registered channels", "Active channels", "Representative",
        "Independent vote cap", "Raw MTF volume summed", "Policy",
    ))


__all__ = [
    "volume_deduplication_frame", "volume_diagnostics_frame", "volume_event_links_frame",
    "volume_history_frame", "volume_mtf_matrix_frame", "volume_propagations_frame",
    "volume_risk_transitions_frame", "volume_shocks_frame",
]
