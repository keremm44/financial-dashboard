from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.projections import project_stabil_support
from financial_dashboard.engines.stabil_support_behavior import (
    PriceSupportRelation,
    StabilSupportBehaviorSnapshot,
    SupportApproachOrigin,
    SupportInteractionState,
    SupportMotion,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _replay(*, behavior):
    snapshot = SimpleNamespace(
        as_of=NOW,
        support_level=98.0,
        support_floor=96.5,
        support_origin_at=NOW - timedelta(days=5),
        support_confirmed_at=NOW - timedelta(days=3),
        support_available_at=NOW - timedelta(days=3) + timedelta(minutes=1),
        validity="ACTIVE",
        dynamics="ABOVE_SUPPORT",
        progression="REBASED_LOWER",
        distance_pct=1.0,
        distance_atr=0.4,
        bars_above_support=2,
        bars_below_support=0,
        reclaim_count=1,
        events=(),
    )
    return SimpleNamespace(
        symbol="ASELS",
        timeframe="1d",
        snapshot=snapshot,
        behavior=behavior,
        input_batch=SimpleNamespace(
            source_quality=SimpleNamespace(status="DATA_OK")
        ),
    )


def test_stabil_projection_exposes_behavior_without_reinterpreting_lifecycle() -> None:
    behavior = StabilSupportBehaviorSnapshot(
        motion=SupportMotion.FALLING,
        relation=PriceSupportRelation.ABOVE_NEAR,
        interaction=SupportInteractionState.RECLAIM_ATTEMPT,
        approach_origin=SupportApproachOrigin.POST_RECLAIM,
        bars_since_rebase=2,
        cross_count=1,
        last_rebase_step_atr=-0.8,
        distance_delta_atr=0.25,
        reclaim_active=True,
    )

    projection = project_stabil_support(_replay(behavior=behavior))

    assert projection.validity == "ACTIVE"
    assert projection.progression == "REBASED_LOWER"
    assert projection.behavior is not None
    assert projection.behavior.motion == "FALLING"
    assert projection.behavior.relation == "ABOVE_NEAR"
    assert projection.behavior.interaction == "RECLAIM_ATTEMPT"
    assert projection.behavior.approach_origin == "POST_RECLAIM"
    assert projection.behavior.bars_since_rebase == 2
    assert projection.behavior.cross_count == 1
    assert projection.behavior.last_rebase_step_atr == -0.8
    assert projection.behavior.distance_delta_atr == 0.25
    assert projection.behavior.reclaim_active is True


def test_stabil_projection_remains_backward_compatible_when_behavior_is_missing() -> None:
    replay = _replay(behavior=None)
    del replay.__dict__["behavior"]

    projection = project_stabil_support(replay)

    assert projection.support_level == 98.0
    assert projection.behavior is None
