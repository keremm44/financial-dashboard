from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    project_ham,
    project_liquidity,
    project_participation,
    project_pattern,
    project_reaction_evidence,
    project_stabil_support,
    project_structural_facts,
    project_volatility,
)
from financial_dashboard.targeting.models import (
    LiquidityScope,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _available_at(timestamp, timeframe: str):
    delay = timedelta(days=1) if timeframe == "1d" else timedelta(minutes=1)
    return timestamp + delay


def _quality(value="DATA_OK"):
    return SimpleNamespace(status=value)


def _target_evidence(
    uid: str,
    kind: TargetEvidenceType,
    *,
    timeframe: str = "1h",
    origin_event_id: str | None = None,
    roles: tuple[TargetRole, ...] | None = None,
) -> TargetEvidence:
    role_map = {
        TargetEvidenceType.LIQUIDITY: (TargetRole.MAGNET,),
        TargetEvidenceType.ORDER_BLOCK: (TargetRole.SUPPLY, TargetRole.REACTION),
        TargetEvidenceType.FVG: (TargetRole.IMBALANCE,),
        TargetEvidenceType.ENGULFING: (TargetRole.REACTION,),
    }
    family_map = {
        TargetEvidenceType.LIQUIDITY: TargetEvidenceFamily.STRUCTURAL,
        TargetEvidenceType.ORDER_BLOCK: TargetEvidenceFamily.SUPPLY_DEMAND,
        TargetEvidenceType.FVG: TargetEvidenceFamily.IMBALANCE,
        TargetEvidenceType.ENGULFING: TargetEvidenceFamily.REACTION,
    }
    return TargetEvidence(
        uid=uid,
        symbol="ASELS",
        timeframe=timeframe,
        evidence_type=kind,
        family=family_map[kind],
        roles=roles or role_map[kind],
        low=100.0,
        high=101.0,
        anchor_price=100.5,
        origin_index=10,
        origin_time=NOW,
        confirmed_at=NOW + timedelta(minutes=1),
        available_at=NOW + timedelta(minutes=2),
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"native:{uid}",
        origin_event_id=origin_event_id or f"event:{uid}",
        source_identity=f"source:{uid}",
        formation_atr=1.0,
        liquidity_scope=(LiquidityScope.EXTERNAL if kind is TargetEvidenceType.LIQUIDITY else None),
    )


def test_structural_projection_uses_typed_snapshot_and_candidate_failed_is_not_confirmed() -> None:
    confirmed = SimpleNamespace(
        event_uid="ASELS:1h:EXTERNAL:1",
        scope="EXTERNAL",
        event_type="EVENT_BOS",
        direction=1,
        broken_level=100.0,
        origin_price=95.0,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="OBSERVED",
        bos_maturity="CONTINUATION",
        origin_source_at=NOW - timedelta(hours=2),
        broken_source_at=NOW - timedelta(hours=1),
        candidate_at=NOW - timedelta(minutes=10),
        confirmed_at=NOW,
    )
    failed_candidate = SimpleNamespace(
        event_uid="ASELS:1h:EXTERNAL:2",
        scope="EXTERNAL",
        event_type="EVENT_FALSE_BREAK",
        direction=-1,
        broken_level=99.0,
        origin_price=101.0,
        confirmation_status="CANDIDATE_FAILED",
        validity="FAILED",
        relevance="CURRENT",
        outcome="FAILED",
        bos_maturity="NOT_APPLICABLE",
        origin_source_at=NOW - timedelta(hours=2),
        broken_source_at=None,
        candidate_at=NOW - timedelta(minutes=5),
        confirmed_at=NOW + timedelta(minutes=5),
    )
    scope = SimpleNamespace(
        scope="EXTERNAL",
        state="STATE_BULLISH",
        direction=1,
        strong_high_identity=7,
        strong_low_identity=8,
        protected_high_identity=0,
        protected_low_identity=9,
        weak_high_identity=10,
        weak_low_identity=0,
    )
    internal = SimpleNamespace(
        scope="INTERNAL",
        state="STATE_BULLISH",
        direction=1,
        strong_high_identity=1,
        strong_low_identity=2,
        protected_high_identity=0,
        protected_low_identity=3,
        weak_high_identity=4,
        weak_low_identity=0,
    )
    export = SimpleNamespace(
        external_protected_high=None,
        external_protected_low=97.0,
        external_weak_high=105.0,
        external_weak_low=None,
        internal_protected_high=None,
        internal_protected_low=99.0,
        internal_weak_high=103.0,
        internal_weak_low=None,
    )
    snapshot = SimpleNamespace(
        as_of=NOW,
        export=export,
        events=(confirmed, failed_candidate),
        external_scope=scope,
        internal_scope=internal,
    )
    replay = SimpleNamespace(
        symbol="ASELS",
        timeframes=("1h",),
        replays={"1h": SimpleNamespace(input_batch=SimpleNamespace(source_quality=_quality()))},
        structure_for=lambda timeframe: snapshot,
    )

    projection = project_structural_facts(replay, available_at=_available_at)
    row = projection.for_timeframe("1h")

    assert row.external is not None
    assert row.external.protected_low == 97.0
    assert row.external.strong_high_identity == 7
    assert row.events[0].ref.domain is ContextDomain.MARKET_STRUCTURE
    assert row.events[0].ref.confirmed_at == NOW
    assert row.events[1].ref.confirmed_at is None
    assert row.events[1].confirmation_status == "CANDIDATE_FAILED"


def test_liquidity_projection_preserves_objective_role_and_existing_lineage() -> None:
    evidence = _target_evidence(
        "liq",
        TargetEvidenceType.LIQUIDITY,
        origin_event_id="EVT:STRUCTURAL:1h:10",
    )
    replay = SimpleNamespace(symbol="ASELS", timeframes=("1h",), evidence=(evidence,))

    projection = project_liquidity(
        replay,
        data_quality_by_timeframe={"1h": "DATA_OK"},
    )

    item = projection.observations[0]
    assert item.ref.domain is ContextDomain.LIQUIDITY
    assert item.ref.lineage_id == "EVT:STRUCTURAL:1h:10"
    assert item.ref.causal_family is CausalFamily.STRUCTURAL_LEVEL
    assert item.liquidity_scope == "EXTERNAL"
    assert item.roles == ("MAGNET",)


def test_reaction_projection_separates_zones_from_engulfing_confirmation() -> None:
    ob = _target_evidence("ob", TargetEvidenceType.ORDER_BLOCK, origin_event_id="EVT:IMPULSE:1h:10")
    fvg = _target_evidence("fvg", TargetEvidenceType.FVG, origin_event_id="EVT:IMPULSE:1h:10")
    engulf = _target_evidence("eng", TargetEvidenceType.ENGULFING, origin_event_id="EVT:IMPULSE:1h:10")
    ob_replay = SimpleNamespace(symbol="ASELS", timeframes=("1h",), evidence=(ob,))
    fvg_replay = SimpleNamespace(symbol="ASELS", timeframes=("2h", "4h", "1d"), evidence=(fvg, engulf))

    projection = project_reaction_evidence(
        symbol="ASELS",
        order_block_replay=ob_replay,
        fvg_engulfing_replay=fvg_replay,
        data_quality_by_timeframe={"1h": "DATA_OK", "2h": "DATA_OK", "4h": "DATA_OK", "1d": "DATA_OK"},
        requested_timeframes=("1d", "4h", "2h", "1h", "30m"),
    )

    assert {item.evidence_type for item in projection.reaction_zones} == {"ORDER_BLOCK", "FVG"}
    assert tuple(item.evidence_type for item in projection.confirmations) == ("ENGULFING",)
    assert projection.confirmations[0].semantic_role == "CONFIRMATION"
    assert projection.unsupported_fvg_engulfing_timeframes == ("1h", "30m")
    assert len({item.ref.lineage_id for item in (*projection.reaction_zones, *projection.confirmations)}) == 1


def test_stabil_projection_preserves_support_lifecycle_without_reinterpreting_it() -> None:
    event = SimpleNamespace(
        sequence=3,
        event_type="SUPPORT_RECLAIMED",
        event_time=NOW,
        origin_at=NOW - timedelta(days=5),
        confirmed_at=NOW - timedelta(days=3),
        available_at=NOW + timedelta(minutes=1),
        support_level=98.0,
        support_floor=96.5,
        price=99.0,
        bars_above_support=2,
        bars_below_support=1,
        reclaim_count=1,
    )
    snapshot = SimpleNamespace(
        as_of=NOW,
        support_level=98.0,
        support_floor=96.5,
        support_origin_at=NOW - timedelta(days=5),
        support_confirmed_at=NOW - timedelta(days=3),
        support_available_at=NOW - timedelta(days=3) + timedelta(minutes=1),
        validity="ACTIVE",
        dynamics="AT_SUPPORT",
        progression="REBASED_HIGHER",
        distance_pct=1.0,
        distance_atr=0.4,
        bars_above_support=2,
        bars_below_support=1,
        reclaim_count=1,
        events=(event,),
    )
    replay = SimpleNamespace(
        symbol="ASELS",
        timeframe="1d",
        snapshot=snapshot,
        input_batch=SimpleNamespace(source_quality=_quality()),
    )

    projection = project_stabil_support(replay)

    assert projection.support_ref is not None
    assert projection.support_ref.domain is ContextDomain.STABIL_SUPPORT
    assert projection.validity == "ACTIVE"
    assert projection.dynamics == "AT_SUPPORT"
    assert projection.events[0].event_type == "SUPPORT_RECLAIMED"
    assert projection.events[0].ref.confirmed_at == NOW


def test_participation_projection_keeps_structure_link_as_relation_not_lineage() -> None:
    latest = SimpleNamespace(
        data_quality="READY",
        status="READY",
        state="UP_CONFIRMED",
        evidence_direction=1,
        bar_index=20,
        segment_id=2,
        timestamp=NOW,
    )
    link = SimpleNamespace(
        event_uid="ASELS:1h:EXTERNAL:4",
        relation="STRUCTURE_SUPPORTED",
        assessed_at=NOW,
        reasons=("ALIGNED_CONFIRMED",),
    )
    tf = SimpleNamespace(timeframe="1h", latest=latest, event_links=(link,))
    replay = SimpleNamespace(symbol="ASELS", timeframes=("1h",), timeframe_replays=(tf,))

    projection = project_participation(replay, available_at=_available_at)
    item = projection.timeframe_facts[0]

    assert item.ref.domain is ContextDomain.VOLUME
    assert item.ref.lineage_id is None
    assert item.ref.source_family is SourceFamily.VOLUME_SERIES
    assert item.event_links[0].structure_event_uid == "ASELS:1h:EXTERNAL:4"


def test_pattern_projection_reads_export_not_generic_engine_result() -> None:
    export = SimpleNamespace(
        state=5,
        pattern_type=2,
        classic_direction=-1,
        break_state=3,
        break_level=105.0,
        retest_state=1,
        identity=17.0,
    )
    snapshot = SimpleNamespace(timeframe="1h", as_of=NOW, export=export)
    replay = SimpleNamespace(
        symbol="ASELS",
        timeframes=("1h",),
        pattern_snapshots=(snapshot,),
        structure_location=SimpleNamespace(
            replay_for=lambda timeframe: SimpleNamespace(
                input_batch=SimpleNamespace(source_quality=_quality())
            )
        ),
    )

    projection = project_pattern(replay, available_at=_available_at)
    item = projection.timeframe_facts[0]

    assert item.pattern_state_code == 5
    assert item.classic_direction == -1
    assert item.break_level == 105.0
    assert item.ref is not None and item.ref.fact_type == "PATTERN_SNAPSHOT"


def test_volatility_projection_keeps_early_candidate_separate_from_confirmed_export() -> None:
    export = SimpleNamespace(
        data_quality="OK",
        regime=3,
        band_state=4,
        fib_state=6,
        active_swing_direction=1,
        fib_retracement_ratio=0.55,
    )
    latest = SimpleNamespace(
        timestamp=NOW,
        confirmed_export=export,
        early=SimpleNamespace(
            state="EARLY_DOWN",
            episode_id=7,
            episode_started=True,
        ),
    )
    tf_replay = SimpleNamespace(latest=latest)
    replay = SimpleNamespace(
        symbol="ASELS",
        timeframes=("4h",),
        for_timeframe=lambda timeframe: tf_replay,
    )

    projection = project_volatility(replay, available_at=_available_at)
    item = projection.timeframe_facts[0]

    assert item.regime_code == 3
    assert item.early_state == "EARLY_DOWN"
    assert item.ref is not None
    assert item.ref.fact_type == "VOLATILITY_CONTEXT_OBSERVATION"


def test_ham_projection_keeps_families_separate_and_flow_correlated_with_volume() -> None:
    @dataclass(frozen=True)
    class Family:
        balance: float | None
        activity: float | None
        coverage: float
        ready: bool

    families = (
        Family(10.0, 20.0, 100.0, True),
        Family(20.0, 30.0, 100.0, True),
        Family(-5.0, 10.0, 80.0, True),
        Family(30.0, 40.0, 90.0, True),
    )
    latest = SimpleNamespace(
        timestamp=NOW,
        data_quality="OK",
        families=SimpleNamespace(as_tuple=lambda: families),
    )
    tf = SimpleNamespace(timeframe="1h", latest=latest)
    replay = SimpleNamespace(symbol="ASELS", timeframes=("1h",), timeframe_replays=(tf,))

    projection = project_ham(replay, available_at=_available_at)
    family_rows = projection.timeframe_facts[0].families

    assert tuple(item.family for item in family_rows) == ("PRICE", "MOMENTUM", "TIMING", "FLOW")
    assert family_rows[0].ref.source_family is SourceFamily.PRICE_DERIVED_INDICATOR
    assert family_rows[-1].ref.causal_family is CausalFamily.PARTICIPATION
    assert family_rows[-1].ref.source_family is SourceFamily.VOLUME_SERIES
    assert all(item.ref.lineage_id is None for item in family_rows)


def test_projection_quality_does_not_silently_turn_missing_into_neutral() -> None:
    evidence = _target_evidence("liq", TargetEvidenceType.LIQUIDITY)
    replay = SimpleNamespace(symbol="ASELS", timeframes=("1h",), evidence=(evidence,))

    projection = project_liquidity(
        replay,
        data_quality_by_timeframe={"1h": "DATA_LIMITED"},
    )

    assert projection.observations[0].ref.data_quality is ContextDataQuality.DATA_LIMITED
