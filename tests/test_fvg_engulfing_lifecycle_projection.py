from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDomain
from financial_dashboard.context.fvg_engulfing_projection import project_fvg_engulfing_lifecycle
from financial_dashboard.target_evidence_replay import EngulfingLifecycleSnapshot, FvgLifecycleSnapshot


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _replay(*, available_at):
    fvg = FvgLifecycleSnapshot(
        identity="FVG:4h:10:1",
        direction=1,
        state="REACTION",
        lower_boundary=100.0,
        upper_boundary=102.0,
        quality=82.0,
        gap_atr=0.8,
        formation_atr=2.5,
        formation_index=10,
        formation_time=NOW - timedelta(days=2),
        first_test_index=14,
        wick_fill_ratio=0.40,
        close_fill_ratio=0.25,
        maximum_fill_ratio=0.40,
        reaction_evidence_count=2,
        reaction_confirmed=True,
        failed_reaction=False,
        full_fill=False,
        invalid=False,
        invalid_reason="YOK",
        invalid_close_count=0,
    )
    engulf = EngulfingLifecycleSnapshot(
        identity="ENG:4h:12:-1",
        direction=-1,
        state="WEAKENED",
        lower_boundary=104.0,
        upper_boundary=106.0,
        quality=71.0,
        body_atr=1.2,
        formation_index=12,
        formation_time=NOW - timedelta(days=1),
        first_test_index=15,
        maximum_retrace_ratio=0.55,
        continuation_evidence_count=1,
        continuation_confirmed=False,
        weakened=True,
        weakened_index=16,
        invalid=False,
        completion_reason="YOK",
    )
    return SimpleNamespace(
        symbol="ASELS",
        timeframes=("4h",),
        snapshots={
            "4h": SimpleNamespace(as_of=NOW, available_at=available_at),
        },
        fvg_lifecycle={"4h": (fvg,)},
        engulfing_lifecycle={"4h": (engulf,)},
    )


def test_projection_preserves_native_fvg_and_engulfing_lifecycle_dimensions() -> None:
    projection = project_fvg_engulfing_lifecycle(
        _replay(available_at=NOW + timedelta(minutes=1)),
        data_quality_by_timeframe={"4h": "DATA_OK"},
    )

    assert projection is not None
    assert len(projection.fvg) == 1
    assert len(projection.engulfing) == 1

    fvg = projection.fvg[0]
    assert fvg.ref.domain is ContextDomain.FVG
    assert fvg.ref.fact_type == "FVG_LIFECYCLE"
    assert fvg.ref.lineage_id == "FVG:4h:10:1"
    assert fvg.state == "REACTION"
    assert fvg.maximum_fill_ratio == 0.40
    assert fvg.reaction_confirmed is True
    assert fvg.failed_reaction is False

    engulf = projection.engulfing[0]
    assert engulf.ref.domain is ContextDomain.ENGULFING
    assert engulf.ref.fact_type == "ENGULFING_LIFECYCLE"
    assert engulf.ref.lineage_id == "ENG:4h:12:-1"
    assert engulf.state == "WEAKENED"
    assert engulf.maximum_retrace_ratio == 0.55
    assert engulf.weakened is True
    assert engulf.continuation_confirmed is False


def test_lifecycle_observation_is_not_available_before_its_closed_bar_boundary() -> None:
    available_at = NOW + timedelta(minutes=1)
    projection = project_fvg_engulfing_lifecycle(
        _replay(available_at=available_at),
        data_quality_by_timeframe={"4h": "DATA_OK"},
    )

    assert projection is not None
    before = projection.available_at(NOW)
    after = projection.available_at(available_at)
    assert before.fvg == ()
    assert before.engulfing == ()
    assert len(after.fvg) == 1
    assert len(after.engulfing) == 1


def test_fvg_and_engulfing_remain_distinct_semantic_families() -> None:
    projection = project_fvg_engulfing_lifecycle(
        _replay(available_at=NOW),
        data_quality_by_timeframe={"4h": "DATA_OK"},
    )

    assert projection is not None
    assert projection.fvg[0].identity.startswith("FVG:")
    assert projection.engulfing[0].identity.startswith("ENG:")
    assert projection.fvg[0].ref.native_state == "REACTION"
    assert projection.engulfing[0].ref.native_state == "WEAKENED"
