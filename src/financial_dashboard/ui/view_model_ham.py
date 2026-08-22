from __future__ import annotations

from numbers import Integral
from typing import Any, Iterable

import pandas as pd

from financial_dashboard.engines.ham_evidence import HamFamily
from financial_dashboard.ham_mtf_replay import HAM_EVIDENCE_TIMEFRAMES, HamMTFEvidenceReplay

from .runtime import CacheTimeframeStatus
from .view_model_common import frame


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
    status_by_tf = {status.timeframe: status for status in statuses}
    replay_by_tf = (
        {replay.timeframe: replay for replay in result.timeframe_replays}
        if result is not None else {}
    )
    rows = []
    for timeframe in HAM_EVIDENCE_TIMEFRAMES:
        status = status_by_tf.get(timeframe)
        replay = replay_by_tf.get(timeframe)
        if replay is None:
            row = {
                "Timeframe": timeframe,
                "Data": "MISSING" if status is None else status.display_status,
                "Source warnings": "" if status is None else " | ".join(status.warnings),
                "Source errors": (
                    "Cache status unavailable" if status is None else " | ".join(status.errors)
                ),
                "Profile": "—", "Latest confirmed": None, "History bars": 0,
                "Warmup bars": 0, "Ready bars": 0, "Raw quality": "MISSING",
                "Valid indicators": 0, "ATR": None, "ATR ratio": None,
                "Volume quality": "MISSING", "Volume trust": None,
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
        "Timeframe", "Data", "Source warnings", "Source errors", "Profile",
        "Latest confirmed", "History bars", "Warmup bars", "Ready bars", "Raw quality",
        "Valid indicators", "ATR", "ATR ratio", "Volume quality", "Volume trust",
        "Price balance", "Price activity", "Price coverage", "Price ready",
        "Momentum balance", "Momentum activity", "Momentum coverage", "Momentum ready",
        "Timing balance", "Timing activity", "Timing coverage", "Timing ready",
        "Flow balance", "Flow activity", "Flow coverage", "Flow ready",
    )
    return frame(rows, columns)


def ham_indicator_evidence_frame(
    result: HamMTFEvidenceReplay,
    *,
    timeframe: str,
) -> pd.DataFrame:
    latest = result.replay_for(timeframe).latest
    rows = [{
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
    } for name, evidence in latest.raw.indicators.items()]
    return frame(rows, (
        "Indicator", "Value", "Valid", "Direction", "Pending direction", "Reason",
        "Consistency", "Movement strength", "Signed zone", "Evidence", "Relative evidence",
    ))


def ham_history_frame(
    result: HamMTFEvidenceReplay,
    *,
    timeframe: str,
    limit: int | None = 100,
) -> pd.DataFrame:
    replay = result.replay_for(timeframe)
    history = replay.history
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, Integral) or limit <= 0:
            raise ValueError("Ham history limit must be a positive integer or None")
        history = history[-int(limit):]
    rows = []
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
        "Timestamp", "Raw quality", "Valid indicators", "Up evidence", "Down evidence",
        "Strong up", "Strong down", "Net evidence", "ATR", "ATR ratio", "Volume quality",
        "Volume coverage", "Volume variation", "Volume trust", "Price balance",
        "Price activity", "Price coverage", "Price ready", "Momentum balance",
        "Momentum activity", "Momentum coverage", "Momentum ready", "Timing balance",
        "Timing activity", "Timing coverage", "Timing ready", "Flow balance",
        "Flow activity", "Flow coverage", "Flow ready",
    )
    return frame(rows, columns)


__all__ = ["ham_history_frame", "ham_indicator_evidence_frame", "ham_mtf_evidence_frame"]
