from __future__ import annotations

import pytest

pytest.importorskip("plotly")  # local dev installs may omit ui extras

import pandas as pd
import plotly.graph_objects as go

from financial_dashboard.targeting.clustering import (
    TargetClusterConfig,
    build_targeting_snapshot,
    deduplicate_origin_events,
)
from financial_dashboard.targeting.models import (
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)
from financial_dashboard.ui.chart_layers.targeting import add_targeting_layers
from financial_dashboard.ui.targeting_view_models import targeting_summary_values


TZ = "Europe/Istanbul"
STAMP = pd.Timestamp("2026-08-20 10:00", tz=TZ)
AS_OF = pd.Timestamp("2026-08-20 12:00", tz=TZ)


def evidence(
    uid: str,
    evidence_type: TargetEvidenceType,
    family: TargetEvidenceFamily,
    roles: tuple[TargetRole, ...],
    low: float,
    high: float,
    *,
    origin_index: int = 10,
) -> TargetEvidence:
    return TargetEvidence(
        uid=uid,
        symbol="TEST",
        timeframe="1h",
        evidence_type=evidence_type,
        family=family,
        roles=roles,
        low=low,
        high=high,
        anchor_price=(low + high) / 2.0,
        origin_index=origin_index,
        origin_time=STAMP,
        confirmed_at=STAMP + pd.Timedelta(hours=1),
        available_at=AS_OF,
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"native:{uid}",
        origin_event_id=f"native:{uid}",
        source_identity=f"source:{uid}",
        formation_atr=1.0,
    )


def test_liquidity_and_sr_same_structural_origin_are_one_independent_origin() -> None:
    liquidity = evidence(
        "liq",
        TargetEvidenceType.LIQUIDITY,
        TargetEvidenceFamily.STRUCTURAL,
        (TargetRole.MAGNET,),
        105.0,
        105.0,
    )
    resistance = evidence(
        "sr",
        TargetEvidenceType.SUPPORT_RESISTANCE,
        TargetEvidenceFamily.STRUCTURAL,
        (TargetRole.SUPPLY, TargetRole.REACTION),
        104.95,
        105.10,
        origin_index=11,
    )
    deduped = deduplicate_origin_events(
        (liquidity, resistance),
        reference_atr=1.0,
        config=TargetClusterConfig(
            origin_bar_tolerance=2,
            origin_price_tolerance_atr=0.25,
            origin_max_span_atr=0.50,
        ),
    )
    assert len({item.origin_event_id for item in deduped}) == 1
    assert len(deduped) == 2


def test_structural_and_impulse_origins_are_not_collapsed_even_when_overlapping() -> None:
    liquidity = evidence(
        "liq",
        TargetEvidenceType.LIQUIDITY,
        TargetEvidenceFamily.STRUCTURAL,
        (TargetRole.MAGNET,),
        105.0,
        105.0,
    )
    fvg = evidence(
        "fvg",
        TargetEvidenceType.FVG,
        TargetEvidenceFamily.IMBALANCE,
        (TargetRole.IMBALANCE,),
        104.95,
        105.10,
    )
    deduped = deduplicate_origin_events((liquidity, fvg), reference_atr=1.0)
    assert len({item.origin_event_id for item in deduped}) == 2


def test_targeting_presentation_exposes_nearest_without_turning_it_into_action() -> None:
    liquidity = evidence(
        "liq",
        TargetEvidenceType.LIQUIDITY,
        TargetEvidenceFamily.STRUCTURAL,
        (TargetRole.MAGNET,),
        105.0,
        105.0,
    )
    fvg = evidence(
        "fvg",
        TargetEvidenceType.FVG,
        TargetEvidenceFamily.IMBALANCE,
        (TargetRole.IMBALANCE,),
        104.9,
        105.2,
    )
    snapshot = build_targeting_snapshot(
        symbol="TEST",
        as_of=AS_OF,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=2.0,
        evidence=(liquidity, fvg),
    )
    summary = targeting_summary_values(snapshot)
    assert "Nearest upside" in summary
    assert "ATR" in summary["Nearest upside"]
    assert "BUY" not in summary["Nearest upside"]
    assert "SELL" not in summary["Nearest upside"]

    figure = go.Figure()
    add_targeting_layers(figure, snapshot, show_nearest=True)
    assert len(figure.layout.shapes) >= 2
