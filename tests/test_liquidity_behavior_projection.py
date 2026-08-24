from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.builder import _filter_liquidity
from financial_dashboard.context.envelope import ContextDomain
from financial_dashboard.context.projections import project_liquidity
from financial_dashboard.engines.liquidity_behavior import (
    LiquidityBehaviorSnapshot,
    LiquidityLandscapeState,
    LiquidityPoolBehaviorSnapshot,
    LiquidityPoolMaturity,
    LiquidityPriceRelation,
    LiquidityRemovalState,
)
from financial_dashboard.engines.liquidity_models import LiquiditySide
from financial_dashboard.targeting.models import (
    LiquidityScope,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _evidence() -> TargetEvidence:
    return TargetEvidence(
        uid="TE-liq",
        symbol="ASELS",
        timeframe="1h",
        evidence_type=TargetEvidenceType.LIQUIDITY,
        family=TargetEvidenceFamily.STRUCTURAL,
        roles=(TargetRole.MAGNET,),
        low=105.0,
        high=105.0,
        anchor_price=105.0,
        origin_index=10,
        origin_time=NOW - timedelta(hours=5),
        confirmed_at=NOW - timedelta(hours=3),
        available_at=NOW - timedelta(hours=2),
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id="LIQ:1h:LQ-1",
        origin_event_id="LIQ:1h:LQ-1",
        source_identity="LQ-1",
        formation_atr=None,
        source_quality=None,
        liquidity_scope=LiquidityScope.EXTERNAL,
    )


def _behavior() -> LiquidityBehaviorSnapshot:
    return LiquidityBehaviorSnapshot(
        as_of=NOW,
        landscape=LiquidityLandscapeState.ONE_SIDED_OBJECTIVE,
        pools=(
            LiquidityPoolBehaviorSnapshot(
                identity="LQ-1",
                side=LiquiditySide.BSL,
                level=105.0,
                maturity=LiquidityPoolMaturity.MATURE,
                relation=LiquidityPriceRelation.APPROACHING,
                removal=LiquidityRemovalState.UNTOUCHED,
                age_bars=8,
                bars_since_touch=2,
                touch_count=3,
                distance_atr=0.5,
                distance_delta_atr=-0.2,
            ),
        ),
    )


def _replay(*, with_behavior: bool = True):
    return SimpleNamespace(
        symbol="ASELS",
        timeframes=("1h",),
        evidence=(_evidence(),),
        liquidity_behavior={"1h": _behavior()} if with_behavior else None,
        snapshots={
            "1h": SimpleNamespace(available_at=NOW + timedelta(minutes=1))
        },
    )


def test_liquidity_behavior_is_projected_as_separate_causal_fact() -> None:
    projection = project_liquidity(
        _replay(),
        data_quality_by_timeframe={"1h": "DATA_OK"},
    )

    assert len(projection.observations) == 1
    assert len(projection.behavior_observations) == 1
    row = projection.behavior_observations[0]
    assert row.ref.domain is ContextDomain.LIQUIDITY
    assert row.ref.fact_type == "POOL_BEHAVIOR"
    assert row.ref.available_at == NOW + timedelta(minutes=1)
    assert row.pool_identity == "LQ-1"
    assert row.maturity == "MATURE"
    assert row.relation == "APPROACHING"
    assert row.removal == "UNTOUCHED"
    assert row.ref.lineage_id is None


def test_liquidity_behavior_is_filtered_by_its_own_availability() -> None:
    projection = project_liquidity(
        _replay(),
        data_quality_by_timeframe={"1h": "DATA_OK"},
    )

    before_behavior_available = _filter_liquidity(projection, NOW)
    assert before_behavior_available is not None
    assert len(before_behavior_available.observations) == 1
    assert before_behavior_available.behavior_observations == ()

    after_behavior_available = _filter_liquidity(
        projection,
        NOW + timedelta(minutes=1),
    )
    assert after_behavior_available is not None
    assert len(after_behavior_available.behavior_observations) == 1


def test_liquidity_projection_remains_backward_compatible_without_behavior() -> None:
    replay = _replay(with_behavior=False)

    projection = project_liquidity(
        replay,
        data_quality_by_timeframe={"1h": "DATA_OK"},
    )

    assert len(projection.observations) == 1
    assert projection.behavior_observations == ()
