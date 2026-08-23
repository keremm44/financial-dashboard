from __future__ import annotations

import pytest

from financial_dashboard.context.axes import evaluate_context_axes
from financial_dashboard.context.snapshot import (
    build_context_snapshot,
    eligible_fact_refs,
    evaluate_knowledge_boundary,
)
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import QualifiedZoneSide

from _context_step4_test_data import ref, reaction_zone, structural_projection, zone_snapshot


def test_knowledge_boundary_excludes_future_facts_instead_of_neutralizing_them() -> None:
    known = ref("KNOWN", available_at=10)
    future = ref("FUTURE", available_at=11)
    candidate = ref("CANDIDATE", available_at=10, confirmed_at=None)

    boundary = evaluate_knowledge_boundary(
        (known, future, candidate),
        as_of=10,
        unsupported_contexts=("FVG:1h",),
    )

    assert boundary.eligible_fact_ids == ("CANDIDATE", "KNOWN")
    assert boundary.excluded_future_fact_ids == ("FUTURE",)
    assert boundary.unconfirmed_fact_ids == ("CANDIDATE",)
    assert boundary.unsupported_contexts == ("FVG:1h",)
    assert boundary.has_future_leakage_candidates is True


def test_eligible_fact_refs_enforces_available_at_only() -> None:
    known = ref("KNOWN", available_at=10)
    future = ref("FUTURE", available_at=11)
    assert eligible_fact_refs((future, known), as_of=10) == (known,)


def test_context_snapshot_carries_single_as_of_and_eligible_sources() -> None:
    structural = structural_projection(anchor_direction=-1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.DEFENDED,
    )
    zones = zone_snapshot(support)
    axes = evaluate_context_axes(
        structural=structural,
        zones=zones,
        anchor_timeframe="4h",
    )
    known = ref("KNOWN", available_at=10)
    future = ref("FUTURE", available_at=12)

    snapshot = build_context_snapshot(
        symbol="ASELS",
        as_of=10,
        anchor_timeframe="4h",
        axes=axes,
        zones=zones,
        all_fact_refs=(known, future),
    )

    assert snapshot.as_of == 10
    assert snapshot.source_refs == (known,)
    assert snapshot.knowledge_boundary.excluded_future_fact_ids == ("FUTURE",)
    assert snapshot.anchor_timeframe == "4h"


def test_context_snapshot_rejects_mismatched_as_of() -> None:
    structural = structural_projection(anchor_direction=-1)
    zones = zone_snapshot()
    axes = evaluate_context_axes(
        structural=structural,
        zones=zones,
        anchor_timeframe="4h",
    )
    with pytest.raises(ValueError, match="zone snapshot as_of"):
        build_context_snapshot(
            symbol="ASELS",
            as_of=11,
            anchor_timeframe="4h",
            axes=axes,
            zones=zones,
            all_fact_refs=(),
        )
