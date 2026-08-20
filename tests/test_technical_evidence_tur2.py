from __future__ import annotations

from dataclasses import replace

import pytest

from financial_dashboard.technical_evidence import (
    ConflictKind,
    EvidenceDataQuality,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceGraphError,
    EvidenceRole,
    FreshnessClass,
    NormalizedLevel,
    ProvenanceType,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    build_technical_evidence_bundle,
    independence_group_for,
    link_visible_dependencies,
    validate_dependency_graph,
)


def _item(
    id_: str,
    *,
    source_engine: str = "market_structure",
    evidence_type: str = "TEST",
    timeframe: str = "2h",
    role: EvidenceRole = EvidenceRole.STRUCTURE,
    family: EvidenceFamily = EvidenceFamily.MARKET_STRUCTURE,
    direction: EvidenceDirection = EvidenceDirection.NEUTRAL,
    provenance: ProvenanceType = ProvenanceType.ROOT,
    depends_on: tuple[str, ...] = (),
    known_bar: int | None = 100,
    source_bar: int | None = None,
    timestamp: str | None = "2026-08-20T18:00:00+03:00",
    source_state: str | int | None = None,
    data_quality: EvidenceDataQuality = EvidenceDataQuality.OK,
    level_refs: tuple[str, ...] = (),
    raw_export: dict | None = None,
) -> TechnicalEvidenceItem:
    return TechnicalEvidenceItem(
        id=id_,
        source_engine=source_engine,
        evidence_type=evidence_type,
        timeframe=timeframe,
        role=role,
        family=family,
        direction=direction,
        data_quality=data_quality,
        source_bar=source_bar,
        known_bar=known_bar,
        timestamp=timestamp,
        level_refs=level_refs,
        provenance_type=provenance,
        depends_on=depends_on,
        source_state=source_state,
        raw_export=raw_export or {},
    )


def _packet(
    timeframe: str,
    known_bar: int,
    timestamp: str,
    *items: TechnicalEvidenceItem,
    levels: tuple[NormalizedLevel, ...] = (),
) -> TechnicalEvidencePacket:
    return TechnicalEvidencePacket(
        timeframe=timeframe,
        known_bar=known_bar,
        timestamp=timestamp,
        evidence=tuple(items),
        levels=levels,
    )


def test_mtf_story_links_only_visible_market_structure_and_pattern_timeframes():
    ms = _item("ms-2h", timeframe="2h", timestamp="2026-08-20T16:00:00+03:00")
    pattern = _item(
        "pattern-1h",
        source_engine="pattern_compression",
        evidence_type="PATTERN_COMPRESSION",
        timeframe="1h",
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.PATTERN,
        timestamp="2026-08-20T17:00:00+03:00",
    )
    unrelated = _item(
        "liq-30m",
        source_engine="liquidity",
        evidence_type="LIQUIDITY_EVENT",
        timeframe="30m",
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.LIQUIDITY,
        timestamp="2026-08-20T17:30:00+03:00",
    )
    mtf = _item(
        "mtf",
        source_engine="mtf_story",
        evidence_type="MTF_STORY",
        timeframe="mtf",
        role=EvidenceRole.CONTEXT,
        family=EvidenceFamily.MTF_STORY,
        direction=EvidenceDirection.BULL,
        provenance=ProvenanceType.DERIVED,
        timestamp="2026-08-20T18:00:00+03:00",
        raw_export={
            "timeframe_states": [
                {"timeframe": "2h"},
                {"timeframe": "1h"},
            ]
        },
    )
    packets = (
        _packet("2h", 100, "2026-08-20T16:00:00+03:00", ms),
        _packet("1h", 100, "2026-08-20T17:00:00+03:00", pattern),
        _packet("30m", 100, "2026-08-20T17:30:00+03:00", unrelated),
        _packet("mtf", 100, "2026-08-20T18:00:00+03:00", mtf),
    )

    bundle = build_technical_evidence_bundle(packets)
    linked = bundle.evidence_by_id("mtf")

    assert linked is not None
    assert linked.depends_on == ("ms-2h", "pattern-1h")
    assert bundle.audit.dependency_edges == 2
    assert bundle.lineage_by_id("mtf").root_ids == ("ms-2h", "pattern-1h")
    assert "liq-30m" not in linked.depends_on
    assert mtf.depends_on == ()  # Tur-2 enriches a copy; Tur-1 packet is untouched.


def test_unlinked_derived_evidence_is_retained_and_audited():
    mtf = _item(
        "mtf",
        source_engine="mtf_story",
        family=EvidenceFamily.MTF_STORY,
        role=EvidenceRole.CONTEXT,
        provenance=ProvenanceType.DERIVED,
        raw_export={"timeframe_states": []},
    )
    bundle = build_technical_evidence_bundle((_packet("2h", 100, mtf.timestamp, mtf),))

    assert bundle.evidence_by_id("mtf") is not None
    assert bundle.audit.unlinked_derived_ids == ("mtf",)
    assert bundle.lineage_by_id("mtf").root_ids == ()


def test_missing_dependency_is_invalid():
    item = _item("derived", provenance=ProvenanceType.DERIVED, depends_on=("missing",))
    report = validate_dependency_graph((item,))
    assert report.unresolved_dependencies == (("derived", "missing"),)
    assert report.valid is False

    with pytest.raises(EvidenceGraphError, match="unresolved"):
        build_technical_evidence_bundle((_packet("2h", 100, item.timestamp, item),))


def test_dependency_cycle_is_invalid():
    a = _item("a", provenance=ProvenanceType.DERIVED, depends_on=("b",))
    b = _item("b", provenance=ProvenanceType.DERIVED, depends_on=("a",))
    report = validate_dependency_graph((a, b))

    assert report.cycles
    assert report.valid is False

    with pytest.raises(EvidenceGraphError, match="cycles"):
        build_technical_evidence_bundle((_packet("2h", 100, a.timestamp, a, b),))


def test_same_timeframe_future_dependency_is_rejected():
    future = _item("future", known_bar=101)
    child = _item("child", provenance=ProvenanceType.DERIVED, depends_on=("future",), known_bar=100)
    report = validate_dependency_graph((child, future))

    assert report.future_dependencies == (("child", "future"),)


def test_cross_timeframe_future_dependency_uses_timestamp_not_bar_index():
    dep = _item(
        "future-1h",
        timeframe="1h",
        known_bar=10,
        timestamp="2026-08-20T19:00:00+03:00",
    )
    child = _item(
        "child-2h",
        timeframe="2h",
        known_bar=500,
        timestamp="2026-08-20T18:00:00+03:00",
        provenance=ProvenanceType.DERIVED,
        depends_on=(dep.id,),
    )
    report = validate_dependency_graph((child, dep))

    assert report.future_dependencies == (("child-2h", "future-1h"),)


def test_cross_timeframe_unverifiable_order_is_audited_not_invented():
    dep = _item("dep", timeframe="1h", timestamp="2026-08-20T17:00:00")
    child = _item(
        "child",
        timeframe="2h",
        timestamp="2026-08-20T18:00:00+03:00",
        provenance=ProvenanceType.DERIVED,
        depends_on=("dep",),
    )
    bundle = build_technical_evidence_bundle(
        (
            _packet("1h", 100, dep.timestamp, dep),
            _packet("2h", 100, child.timestamp, child),
        )
    )

    assert bundle.audit.unverifiable_dependency_order == (("child", "dep"),)


def test_freshness_uses_source_bar_and_persistent_state_extends_horizon():
    active = _item("active", source_bar=90, source_state="ACTIVE", known_bar=100)
    invalid = _item("invalid", source_bar=90, source_state="INVALID", known_bar=100)
    bundle = build_technical_evidence_bundle((_packet("2h", 100, active.timestamp, active, invalid),))

    active_value = bundle.evidence_by_id("active").freshness
    invalid_value = bundle.evidence_by_id("invalid").freshness
    assert active_value is not None and invalid_value is not None
    assert active_value > invalid_value

    records = {record.target_id: record for record in bundle.freshness}
    assert records["active"].anchor == "SOURCE_BAR"
    assert records["active"].age_bars == 10


def test_missing_freshness_anchor_stays_unknown_instead_of_using_snapshot_age():
    item = _item("no-anchor", source_bar=None)
    bundle = build_technical_evidence_bundle((_packet("2h", 100, item.timestamp, item),))
    enriched = bundle.evidence_by_id("no-anchor")
    record = next(record for record in bundle.freshness if record.target_id == "no-anchor")

    assert enriched.freshness is None
    assert record.classification is FreshnessClass.UNKNOWN
    assert "no-anchor" in bundle.semantic.structure.unknown_freshness_ids


def test_stale_evidence_is_preserved_not_filtered():
    old = _item("old", source_bar=0, known_bar=1000, source_state="INVALID")
    bundle = build_technical_evidence_bundle((_packet("2h", 1000, old.timestamp, old),))
    enriched = bundle.evidence_by_id("old")

    assert enriched is not None
    assert enriched.freshness is not None and enriched.freshness < 0.25
    assert "old" in bundle.semantic.structure.stale_ids


def test_level_without_source_bar_inherits_referencing_evidence_freshness():
    level = NormalizedLevel(
        id="level",
        source_engine="order_block",
        level_type="BULL_OB",
        timeframe="2h",
        lower=99.0,
        upper=100.0,
        known_bar=100,
        timestamp="2026-08-20T18:00:00+03:00",
    )
    item = _item(
        "ob",
        source_engine="order_block",
        evidence_type="ORDER_BLOCK_BULL",
        role=EvidenceRole.LOCATION,
        family=EvidenceFamily.ORDER_BLOCK,
        source_bar=90,
        level_refs=(level.id,),
    )
    bundle = build_technical_evidence_bundle(
        (_packet("2h", 100, item.timestamp, item, levels=(level,)),)
    )

    assert bundle.level_by_id("level").freshness == bundle.evidence_by_id("ob").freshness
    level_record = next(record for record in bundle.freshness if record.target_id == "level")
    assert level_record.anchor == "REFERENCING_EVIDENCE"


def test_same_independence_group_opposition_is_not_labeled_conflict():
    bull = _item(
        "bull-fvg",
        source_engine="fvg_engulfing",
        evidence_type="BULL_FVG",
        role=EvidenceRole.LOCATION,
        family=EvidenceFamily.FVG,
        direction=EvidenceDirection.BULL,
    )
    bear = replace(bull, id="bear-fvg", evidence_type="BEAR_FVG", direction=EvidenceDirection.BEAR)
    bundle = build_technical_evidence_bundle((_packet("2h", 100, bull.timestamp, bull, bear),))

    assert independence_group_for(bull) == independence_group_for(bear)
    assert bundle.conflicts == ()
    assert any(group == "FVG_ENGULFING_CORE" for group, _ in bundle.audit.multi_item_independence_groups)


def test_independent_opposition_is_recorded_without_decision_severity():
    structure = _item("structure", direction=EvidenceDirection.BULL)
    timing = _item(
        "timing",
        source_engine="ham_dashboard",
        evidence_type="HAM_TIMING",
        role=EvidenceRole.TIMING,
        family=EvidenceFamily.TIMING,
        direction=EvidenceDirection.BEAR,
        provenance=ProvenanceType.AGGREGATED,
    )
    bundle = build_technical_evidence_bundle((_packet("2h", 100, structure.timestamp, structure, timing),))

    assert len(bundle.conflicts) == 1
    conflict = bundle.conflicts[0]
    assert conflict.kind is ConflictKind.CROSS_ROLE_OPPOSITION
    assert conflict.independent is True
    assert conflict.shared_lineage == ()


def test_derived_opposition_is_not_independent_double_vote():
    root = _item("root", direction=EvidenceDirection.BULL)
    derived = _item(
        "derived",
        source_engine="mtf_story",
        role=EvidenceRole.CONTEXT,
        family=EvidenceFamily.MTF_STORY,
        direction=EvidenceDirection.BEAR,
        provenance=ProvenanceType.DERIVED,
        depends_on=("root",),
    )
    bundle = build_technical_evidence_bundle((_packet("2h", 100, root.timestamp, root, derived),))

    conflict = bundle.conflicts[0]
    assert conflict.kind is ConflictKind.DERIVED_OPPOSITION
    assert conflict.independent is False
    assert conflict.shared_lineage == ("root",)
    assert bundle.lineage_by_id("derived").overlaps_with == ("root",)


def test_source_gap_evidence_does_not_create_directional_conflict():
    up = _item("up", direction=EvidenceDirection.BULL)
    down_gap = _item(
        "down-gap",
        source_engine="liquidity",
        family=EvidenceFamily.LIQUIDITY,
        role=EvidenceRole.TRIGGER,
        direction=EvidenceDirection.BEAR,
        data_quality=EvidenceDataQuality.SOURCE_GAP,
    )
    bundle = build_technical_evidence_bundle((_packet("2h", 100, up.timestamp, up, down_gap),))

    assert bundle.conflicts == ()
    assert "down-gap" in bundle.semantic.trigger.limited_quality_ids


def test_semantic_summary_indexes_roles_without_creating_dominant_score():
    bull = _item("bull", direction=EvidenceDirection.BULL)
    bear = _item(
        "bear",
        source_engine="liquidity",
        family=EvidenceFamily.LIQUIDITY,
        role=EvidenceRole.TRIGGER,
        direction=EvidenceDirection.BEAR,
    )
    bundle = build_technical_evidence_bundle((_packet("2h", 100, bull.timestamp, bull, bear),))

    assert bundle.semantic.structure.bullish_ids == ("bull",)
    assert bundle.semantic.trigger.bearish_ids == ("bear",)
    assert not hasattr(bundle.semantic.structure, "score")
    assert not hasattr(bundle.semantic.structure, "dominant_direction")


def test_ham_momentum_and_timing_are_separate_independence_groups():
    momentum = _item(
        "momentum",
        source_engine="ham_dashboard",
        evidence_type="HAM_MOMENTUM",
        role=EvidenceRole.CONFIRMATION,
        family=EvidenceFamily.MOMENTUM,
        provenance=ProvenanceType.AGGREGATED,
    )
    timing = replace(
        momentum,
        id="timing",
        evidence_type="HAM_TIMING",
        role=EvidenceRole.TIMING,
        family=EvidenceFamily.TIMING,
    )

    assert independence_group_for(momentum) == "HAM_MOMENTUM"
    assert independence_group_for(timing) == "HAM_TIMING"


def test_bundle_is_deterministic_independent_of_packet_order():
    a = _item("a", timeframe="2h")
    b = _item(
        "b",
        source_engine="liquidity",
        family=EvidenceFamily.LIQUIDITY,
        role=EvidenceRole.TRIGGER,
        timeframe="1h",
        timestamp="2026-08-20T17:00:00+03:00",
    )
    pa = _packet("2h", 100, a.timestamp, a)
    pb = _packet("1h", 100, b.timestamp, b)

    assert build_technical_evidence_bundle((pa, pb)) == build_technical_evidence_bundle((pb, pa))


def test_explicit_as_of_timestamp_rejects_future_tail():
    future = _item("future", timestamp="2026-08-20T19:00:00+03:00")
    packet = _packet("2h", 100, future.timestamp, future)

    with pytest.raises(EvidenceGraphError, match="as-of timestamp"):
        build_technical_evidence_bundle((packet,), as_of_timestamp="2026-08-20T18:00:00+03:00")


def test_explicit_as_of_bar_rejects_future_tail():
    future = _item("future", known_bar=101)
    packet = _packet("2h", 101, future.timestamp, future)

    with pytest.raises(EvidenceGraphError, match="as-of bar"):
        build_technical_evidence_bundle((packet,), as_of_known_bars={"2h": 100})


def test_duplicate_identical_packet_is_deduped_but_conflicting_duplicate_is_rejected():
    item = _item("same", direction=EvidenceDirection.BULL)
    packet = _packet("2h", 100, item.timestamp, item)
    bundle = build_technical_evidence_bundle((packet, packet))
    assert bundle.audit.evidence_count == 1

    conflicting = replace(item, direction=EvidenceDirection.BEAR)
    bad = _packet("2h", 100, item.timestamp, conflicting)
    with pytest.raises(ValueError, match="conflicting duplicate evidence"):
        build_technical_evidence_bundle((packet, bad))
