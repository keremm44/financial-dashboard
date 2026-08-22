from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines.market_structure_events import MarketStructureEventLedger
from financial_dashboard.engines.market_structure_state import EVENT_BOS, StructureEvent
from financial_dashboard.engines.structure_location import (
    CausalZoneObservation,
    StructureLocationAnchor,
    StructureLocationMeaning,
    StructureLocationOutcomeStatus,
    StructureZoneLinkConfig,
    StructureZoneRelation,
    ZoneConfluenceConfig,
    build_zone_confluence,
    evaluate_structure_event_location,
    link_structure_event_to_zones,
)
from financial_dashboard.engines.support_resistance_zones import (
    SupportResistanceZone,
    ZoneKind,
    ZoneLifecycle,
    ZoneSide,
)


def _zone(
    uid: str,
    *,
    timeframe: str,
    side: ZoneSide,
    symbol: str = "X",
    low: float,
    high: float,
    lifecycle: ZoneLifecycle = ZoneLifecycle.ACTIVE,
    quality: float = 75.0,
    atr: float = 2.0,
    transition_bar: int = 5,
) -> SupportResistanceZone:
    timestamp = pd.Timestamp("2026-01-01T10:00:00Z")
    return SupportResistanceZone(
        zone_uid=f"{symbol}:{timeframe}:{uid}",
        source_range_identity=1,
        kind=ZoneKind.RANGE_BOUNDARY,
        side=side,
        low=low,
        high=high,
        center=(low + high) * 0.5,
        lifecycle=lifecycle,
        range_state="RANGE_ACTIVE",
        quality=quality,
        touches=3,
        boundary_stability=80.0,
        reference_atr=atr,
        origin_bar=0,
        created_bar=2,
        created_at=timestamp,
        last_updated_bar=transition_bar,
        last_updated_at=timestamp,
        last_transition_bar=transition_bar,
        last_transition_at=timestamp,
        symbol=symbol,
        timeframe=timeframe,
    )


def _bullish_bos_event():
    timestamps = pd.date_range("2026-01-01T08:00:00Z", periods=4, freq="h")
    rows = [
        {"timestamp": timestamp, "high": 99.0, "low": 94.0, "close": 96.0}
        for timestamp in timestamps
    ]
    rows[3] = {
        "timestamp": timestamps[3],
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
    }
    native = StructureEvent(
        valid=True,
        identity=1,
        scope="EXTERNAL",
        event_type=EVENT_BOS,
        direction=1,
        event_bar=3,
        broken_swing_identity=10,
        broken_source_bar=1,
        origin_swing_identity=11,
        origin_source_bar=2,
        level=101.0,
        origin_price=95.0,
        quality=80.0,
        evidence_text="BOS_CONFIRMED",
        candidate_bar=2,
    )
    ledger = MarketStructureEventLedger()
    ledger.append(native, rows)
    return ledger.snapshot(current_bar=3)[0].with_namespace(symbol="X", timeframe="1h")


def test_confluence_requires_distinct_timeframes_and_never_mixes_opposing_roles() -> None:
    zones = (
        _zone("D_SUPPORT", timeframe="1d", side=ZoneSide.SUPPORT, low=99.0, high=101.0, atr=4.0, quality=84.0),
        _zone("H4_SUPPORT", timeframe="4h", side=ZoneSide.SUPPORT, low=100.0, high=102.0, atr=2.0, quality=76.0),
        _zone("H1_RESISTANCE", timeframe="1h", side=ZoneSide.RESISTANCE, low=100.0, high=101.0),
        _zone("H4_DUPLICATE", timeframe="4h", side=ZoneSide.SUPPORT, low=100.2, high=101.8),
    )

    clusters = build_zone_confluence(zones)

    support_clusters = [cluster for cluster in clusters if cluster.side is ZoneSide.SUPPORT]
    assert support_clusters
    assert all(cluster.symbol == "X" for cluster in support_clusters)
    assert all(len(set(cluster.timeframes)) == len(cluster.timeframes) for cluster in support_clusters)
    assert all("1h" not in cluster.timeframes for cluster in support_clusters)
    assert not any(cluster.side is ZoneSide.RESISTANCE for cluster in clusters)
    assert all(0.0 <= cluster.score <= 100.0 for cluster in clusters)
    assert all(cluster.common_low is not None for cluster in support_clusters)


def test_confluence_rejects_atr_distant_zones_and_is_input_order_deterministic() -> None:
    near = _zone("A", timeframe="1d", side=ZoneSide.SUPPORT, low=99.0, high=100.0, atr=2.0)
    far = _zone("B", timeframe="4h", side=ZoneSide.SUPPORT, low=102.0, high=103.0, atr=1.0)
    config = ZoneConfluenceConfig(max_gap_atr=0.20)

    assert build_zone_confluence((near, far), config) == ()
    close = _zone("C", timeframe="4h", side=ZoneSide.SUPPORT, low=100.1, high=101.0, atr=1.0)
    assert build_zone_confluence((near, close), config) == build_zone_confluence((close, near), config)


def test_confluence_and_links_never_cross_symbol_boundaries() -> None:
    x_daily = _zone(
        "X_DAILY",
        timeframe="1d",
        side=ZoneSide.RESISTANCE,
        low=100.0,
        high=102.0,
    )
    y_h4 = _zone(
        "Y_H4",
        symbol="Y",
        timeframe="4h",
        side=ZoneSide.RESISTANCE,
        low=100.0,
        high=102.0,
    )
    assert build_zone_confluence((x_daily, y_h4)) == ()

    event = _bullish_bos_event()
    outcome = evaluate_structure_event_location(
        event,
        (
            CausalZoneObservation(
                symbol="Y",
                timeframe="4h",
                bar_index=5,
                observed_at=pd.Timestamp("2026-01-01T07:00:00Z"),
                available_at=pd.Timestamp("2026-01-01T11:00:00Z"),
                zones=(y_h4,),
            ),
        ),
        event_available_at=pd.Timestamp("2026-01-01T12:00:00Z"),
    )
    assert outcome.status is StructureLocationOutcomeStatus.COMPUTED_NO_CAUSAL_ZONE_MATCH
    assert outcome.causal_timeframes == ()
    assert outcome.causal_zone_count == 0


def test_structure_links_use_broken_and_origin_anchors_from_only_available_zones() -> None:
    event = _bullish_bos_event()
    event_available_at = pd.Timestamp("2026-01-01T12:00:00Z")
    resistance = _zone(
        "RESISTANCE",
        timeframe="4h",
        side=ZoneSide.RESISTANCE,
        low=100.0,
        high=102.0,
    )
    support = _zone(
        "SUPPORT",
        timeframe="1d",
        side=ZoneSide.SUPPORT,
        low=94.5,
        high=95.5,
        atr=4.0,
    )
    observations = (
        CausalZoneObservation(
            symbol="X",
            timeframe="4h",
            bar_index=5,
            observed_at=pd.Timestamp("2026-01-01T07:00:00Z"),
            available_at=pd.Timestamp("2026-01-01T11:00:00Z"),
            zones=(resistance,),
        ),
        CausalZoneObservation(
            symbol="X",
            timeframe="1d",
            bar_index=5,
            observed_at=pd.Timestamp("2025-12-31T08:00:00Z"),
            available_at=pd.Timestamp("2026-01-01T08:00:00Z"),
            zones=(support,),
        ),
        CausalZoneObservation(
            symbol="X",
            timeframe="2h",
            bar_index=5,
            observed_at=pd.Timestamp("2026-01-01T11:00:00Z"),
            available_at=pd.Timestamp("2026-01-01T13:00:00Z"),
            zones=(
                _zone("FUTURE", timeframe="2h", side=ZoneSide.RESISTANCE, low=100.0, high=102.0),
            ),
        ),
    )

    links = link_structure_event_to_zones(
        event,
        observations,
        event_available_at=event_available_at,
    )

    by_timeframe = {link.zone_timeframe: link for link in links}
    assert set(by_timeframe) == {"4h", "1d"}
    assert by_timeframe["4h"].anchor is StructureLocationAnchor.BROKEN_LEVEL
    assert by_timeframe["4h"].relation is StructureZoneRelation.INSIDE_ZONE
    assert by_timeframe["4h"].meaning is StructureLocationMeaning.BREAKS_RESISTANCE
    assert by_timeframe["1d"].anchor is StructureLocationAnchor.ORIGIN_PRICE
    assert by_timeframe["1d"].meaning is StructureLocationMeaning.ORIGINATES_AT_SUPPORT
    assert all(link.zone_available_at <= link.event_available_at for link in links)


def test_as_of_matching_uses_latest_available_observation_per_timeframe() -> None:
    event = _bullish_bos_event()
    event_available_at = pd.Timestamp("2026-01-01T12:00:00Z")
    earlier = CausalZoneObservation(
        symbol="X",
        timeframe="4h",
        bar_index=5,
        observed_at=pd.Timestamp("2026-01-01T07:00:00Z"),
        available_at=pd.Timestamp("2026-01-01T11:00:00Z"),
        zones=(
            _zone(
                "EARLIER_MATCH",
                timeframe="4h",
                side=ZoneSide.RESISTANCE,
                low=100.0,
                high=102.0,
            ),
        ),
    )
    latest = CausalZoneObservation(
        symbol="X",
        timeframe="4h",
        bar_index=6,
        observed_at=pd.Timestamp("2026-01-01T07:30:00Z"),
        available_at=pd.Timestamp("2026-01-01T11:30:00Z"),
        zones=(
            _zone(
                "LATEST_DISTANT",
                timeframe="4h",
                side=ZoneSide.RESISTANCE,
                low=120.0,
                high=122.0,
                transition_bar=6,
            ),
        ),
    )

    outcome = evaluate_structure_event_location(
        event,
        (earlier, latest),
        event_available_at=event_available_at,
    )

    assert outcome.status is StructureLocationOutcomeStatus.COMPUTED_NO_CAUSAL_ZONE_MATCH
    assert outcome.causal_zone_count == 1
    assert outcome.links == ()


def test_freshly_broken_zone_remains_linkable_on_transition_bar_but_old_terminal_zone_does_not() -> None:
    event = _bullish_bos_event()
    event_available_at = pd.Timestamp("2026-01-01T12:00:00Z")
    fresh = _zone(
        "FRESH",
        timeframe="1h",
        side=ZoneSide.RESISTANCE,
        low=100.0,
        high=102.0,
        lifecycle=ZoneLifecycle.BROKEN,
        transition_bar=5,
    )
    old = _zone(
        "OLD",
        timeframe="4h",
        side=ZoneSide.RESISTANCE,
        low=100.0,
        high=102.0,
        lifecycle=ZoneLifecycle.BROKEN,
        transition_bar=2,
    )
    links = link_structure_event_to_zones(
        event,
        (
            CausalZoneObservation(
                symbol="X",
                timeframe="1h",
                bar_index=5,
                observed_at=pd.Timestamp("2026-01-01T11:00:00Z"),
                available_at=event_available_at,
                zones=(fresh,),
            ),
            CausalZoneObservation(
                symbol="X",
                timeframe="4h",
                bar_index=5,
                observed_at=pd.Timestamp("2026-01-01T08:00:00Z"),
                available_at=event_available_at,
                zones=(old,),
            ),
        ),
        event_available_at=event_available_at,
    )

    assert len(links) == 1
    assert links[0].zone_uid == fresh.zone_uid
    assert links[0].meaning is StructureLocationMeaning.ZONE_BREAK_CONFIRMED


def test_no_spatial_match_is_an_explicit_computed_outcome_not_a_missing_result() -> None:
    event = _bullish_bos_event()
    event_available_at = pd.Timestamp("2026-01-01T12:00:00Z")
    outcome = evaluate_structure_event_location(
        event,
        (
            CausalZoneObservation(
                symbol="X",
                timeframe="4h",
                bar_index=5,
                observed_at=pd.Timestamp("2026-01-01T07:00:00Z"),
                available_at=pd.Timestamp("2026-01-01T11:00:00Z"),
                zones=(
                    _zone(
                        "DISTANT",
                        timeframe="4h",
                        side=ZoneSide.RESISTANCE,
                        low=120.0,
                        high=122.0,
                    ),
                ),
            ),
        ),
        event_available_at=event_available_at,
    )

    assert outcome.status is StructureLocationOutcomeStatus.COMPUTED_NO_CAUSAL_ZONE_MATCH
    assert not outcome.has_link
    assert outcome.links == ()
    assert outcome.causal_zone_count == 1
    assert outcome.causal_timeframes == ("4h",)


def test_causal_location_is_prefix_invariant_when_future_zone_state_is_appended() -> None:
    event = _bullish_bos_event()
    event_available_at = pd.Timestamp("2026-01-01T12:00:00Z")
    current = CausalZoneObservation(
        symbol="X",
        timeframe="4h",
        bar_index=5,
        observed_at=pd.Timestamp("2026-01-01T07:00:00Z"),
        available_at=pd.Timestamp("2026-01-01T11:00:00Z"),
        zones=(
            _zone(
                "RESISTANCE",
                timeframe="4h",
                side=ZoneSide.RESISTANCE,
                low=100.0,
                high=102.0,
            ),
        ),
    )
    future = CausalZoneObservation(
        symbol="X",
        timeframe="4h",
        bar_index=6,
        observed_at=pd.Timestamp("2026-01-01T11:00:00Z"),
        available_at=pd.Timestamp("2026-01-01T15:00:00Z"),
        zones=(
            _zone(
                "FUTURE_REPLACEMENT",
                timeframe="4h",
                side=ZoneSide.RESISTANCE,
                low=130.0,
                high=132.0,
                transition_bar=6,
            ),
        ),
    )

    prefix = evaluate_structure_event_location(
        event,
        (current,),
        event_available_at=event_available_at,
    )
    extended = evaluate_structure_event_location(
        event,
        (current, future),
        event_available_at=event_available_at,
    )

    assert extended == prefix


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ZoneConfluenceConfig(max_gap_atr=-0.01),
        lambda: ZoneConfluenceConfig(max_cluster_span_atr=0.0),
        lambda: ZoneConfluenceConfig(minimum_timeframes=1),
        lambda: ZoneConfluenceConfig(timeframe_weights=(("1h", 1.0), ("1H", 0.5))),
        lambda: StructureZoneLinkConfig(max_distance_atr=-0.01),
    ),
)
def test_geometry_configs_reject_ambiguous_or_invalid_normalization(factory) -> None:
    with pytest.raises(ValueError):
        factory()
