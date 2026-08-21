from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd
import pytest

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.ham_evidence import FamilySnapshot, HamFamilyEvidenceSet
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.raw_indicator_dashboard import RawDataQuality
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.ham_support import (
    HAM_MAX_ABS_DELTA,
    HAM_SUPPORT_TIMEFRAME_WEIGHTS,
    HamSupportAlignment,
    apply_ham_confidence,
    assess_ham_support,
)
from _ui_test_data import make_ui_store


@dataclass(frozen=True, slots=True)
class _CoreDecision:
    direction: Direction
    confidence: float
    action: str = "WAIT"
    status: str = "BLOCKED"
    hard_blockers: tuple[str, ...] = ("CORE_BLOCKER",)
    market_structure: str = "H1_CHOCH"
    support_resistance: str = "AT_SUPPORT"
    risks: tuple[str, ...] = ("VOLATILITY",)


def _with_family_values(
    result,
    *,
    balance: float,
    ready: bool = True,
    coverage: float = 100.0,
):
    family = FamilySnapshot(
        balance=balance,
        activity=abs(balance),
        coverage=coverage,
        ready=ready,
    )
    families = HamFamilyEvidenceSet(
        price=family,
        momentum=family,
        timing=family,
        flow=family,
    )
    replays = []
    for replay in result.timeframe_replays:
        raw = replace(replay.latest.raw, data_quality=RawDataQuality.OK)
        latest = replace(replay.latest, raw=raw, families=families)
        replays.append(replace(replay, latest=latest))
    return replace(result, timeframe_replays=tuple(replays))


def test_support_is_symmetric_bounded_and_neutral_core_is_not_adjusted(tmp_path) -> None:
    make_ui_store(tmp_path)
    replay = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay("THYAO")
    aligned = _with_family_values(replay, balance=100.0)

    upward = assess_ham_support(Direction.UP, aligned)
    downward = assess_ham_support(Direction.DOWN, aligned)
    neutral = assess_ham_support(Direction.NEUTRAL, aligned)

    assert upward.ham_delta == HAM_MAX_ABS_DELTA
    assert downward.ham_delta == -HAM_MAX_ABS_DELTA
    assert upward.ham_delta == -downward.ham_delta
    assert upward.alignment is HamSupportAlignment.AGREES
    assert downward.alignment is HamSupportAlignment.CONFLICTS
    assert "HAM:CONFLICT_IS_SUPPORT_ONLY_NOT_REVERSAL" in downward.reasons
    assert neutral.ham_delta == 0.0
    assert neutral.alignment is HamSupportAlignment.NOT_APPLICABLE


def test_missing_or_unready_dimensions_reduce_capacity_instead_of_being_renormalized(
    tmp_path,
) -> None:
    make_ui_store(tmp_path)
    replay = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay(
        "THYAO", timeframes=("1h",)
    )
    one_timeframe = _with_family_values(replay, balance=100.0)

    assessment = assess_ham_support(Direction.UP, one_timeframe)
    expected = round(
        HAM_MAX_ABS_DELTA
        * HAM_SUPPORT_TIMEFRAME_WEIGHTS["1h"]
        / sum(HAM_SUPPORT_TIMEFRAME_WEIGHTS.values()),
        2,
    )
    assert assessment.ham_delta == expected
    assert 0.0 < assessment.evidence_coverage < 1.0
    assert assessment.timeframe("1d").available is False
    assert assessment.timeframe("1h").available is True

    latest = one_timeframe.replay_for("1h").latest
    warmup_latest = replace(
        latest,
        raw=replace(latest.raw, data_quality=RawDataQuality.WARMUP),
    )
    unavailable = replace(
        one_timeframe,
        timeframe_replays=(
            replace(one_timeframe.replay_for("1h"), latest=warmup_latest),
        ),
    )
    no_support = assess_ham_support(Direction.UP, unavailable)
    assert no_support.ham_delta == 0.0
    assert no_support.alignment is HamSupportAlignment.UNAVAILABLE


def test_flow_confidence_is_not_applied_twice(tmp_path) -> None:
    make_ui_store(tmp_path)
    replay = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay(
        "THYAO", timeframes=("1d",)
    )
    timeframe = replay.replay_for("1d")
    not_ready = FamilySnapshot(None, None, 0.0, False)

    def with_flow_confidence(confidence: float):
        flow = FamilySnapshot(40.0, 40.0, 100.0, True, confidence)
        latest = replace(
            timeframe.latest,
            raw=replace(timeframe.latest.raw, data_quality=RawDataQuality.OK),
            families=HamFamilyEvidenceSet(
                price=not_ready,
                momentum=not_ready,
                timing=not_ready,
                flow=flow,
            ),
        )
        return replace(replay, timeframe_replays=(replace(timeframe, latest=latest),))

    limited = assess_ham_support(Direction.UP, with_flow_confidence(0.5))
    adequate = assess_ham_support(Direction.UP, with_flow_confidence(1.0))
    assert limited.ham_delta == adequate.ham_delta
    assert limited.directional_score == adequate.directional_score


def test_post_core_wrapper_changes_only_confidence_and_clamps_boundaries(tmp_path) -> None:
    make_ui_store(tmp_path)
    replay = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay("THYAO")
    aligned = _with_family_values(replay, balance=100.0)
    core = _CoreDecision(direction=Direction.UP, confidence=98.0)

    adjusted = apply_ham_confidence(core, aligned)

    assert adjusted.core is core
    assert adjusted.core == core
    assert adjusted.core.direction is Direction.UP
    assert adjusted.core.action == "WAIT"
    assert adjusted.core.status == "BLOCKED"
    assert adjusted.core.hard_blockers == ("CORE_BLOCKER",)
    assert adjusted.core.market_structure == "H1_CHOCH"
    assert adjusted.core.support_resistance == "AT_SUPPORT"
    assert adjusted.ham_delta == 5.0
    assert adjusted.final_confidence == 100.0
    assert adjusted.applied_delta == 2.0

    conflicting = apply_ham_confidence(
        replace(core, direction=Direction.DOWN, confidence=2.0),
        aligned,
    )
    assert conflicting.ham_delta == -5.0
    assert conflicting.final_confidence == 0.0
    assert conflicting.applied_delta == -2.0


@pytest.mark.parametrize("confidence", [-0.01, 100.01, float("nan")])
def test_post_core_wrapper_rejects_invalid_confidence(tmp_path, confidence) -> None:
    make_ui_store(tmp_path)
    replay = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay(
        "THYAO", timeframes=("1h",)
    )
    with pytest.raises(ValueError, match="core confidence"):
        apply_ham_confidence(
            _CoreDecision(direction=Direction.UP, confidence=confidence),
            replay,
        )


def test_open_preview_changes_source_diagnostic_but_not_ham_delta(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    runner = HamMTFEvidenceReplayRunner(store)
    before_replay = runner.replay("THYAO", timeframes=("30m",))
    before = assess_ham_support(Direction.UP, before_replay)

    previous = store.load("THYAO", "30m").iloc[-1]
    preview = pd.DataFrame(
        [
            {
                "timestamp": previous["timestamp"] + pd.Timedelta(minutes=30),
                "open": float(previous["close"]),
                "high": float(previous["high"]) * 8.0,
                "low": float(previous["low"]) * 0.1,
                "close": float(previous["close"]) * 7.0,
                "volume": float(previous["volume"]) * 20.0,
                "is_closed": False,
                "is_complete": True,
            }
        ]
    )
    store.merge_and_save(preview, symbol="THYAO", timeframe="30m", source="preview")

    after_replay = runner.replay("THYAO", timeframes=("30m",))
    after = assess_ham_support(Direction.UP, after_replay)
    assert after_replay.replay_for("30m").latest == before_replay.replay_for("30m").latest
    assert after.ham_delta == before.ham_delta
    assert after.directional_score == before.directional_score
    assert after.timeframe("30m").source_quality == "DATA_LIMITED"
    assert "HAM:SOURCE_QUALITY:30m:DATA_LIMITED" in after.reasons
