from __future__ import annotations

from financial_dashboard.engines.three_domain_observer import FOUNDATION_OBSERVER_TIMEFRAMES
from financial_dashboard.ui.charts import make_market_figure
from financial_dashboard.ui.runtime import (
    cache_fingerprint,
    discover_cached_symbols,
    inspect_symbol_cache,
    replay_cached_observer,
    runnable_timeframes,
)
from financial_dashboard.ui.view_models import (
    cache_status_frame,
    confluence_frame,
    event_zone_links_frame,
    location_outcomes_frame,
    mtf_matrix_frame,
    observer_facts_frame,
    opposing_conflicts_frame,
    overview_values,
    structure_events_frame,
    structure_history_frame,
    zones_frame,
)
from _ui_test_data import make_ui_store


def test_cache_runtime_preserves_missing_foundation_timeframes_and_replays_available_data(
    tmp_path,
) -> None:
    store = make_ui_store(tmp_path)

    assert discover_cached_symbols(tmp_path) == ("THYAO",)
    complete_fingerprint = cache_fingerprint(tmp_path, symbol="THYAO")
    assert tuple(item[0] for item in complete_fingerprint) == FOUNDATION_OBSERVER_TIMEFRAMES

    complete_statuses = inspect_symbol_cache(tmp_path, symbol="THYAO")
    assert tuple(status.timeframe for status in complete_statuses) == FOUNDATION_OBSERVER_TIMEFRAMES
    assert all(status.exists and status.runnable for status in complete_statuses)
    assert all(status.confirmed_row_count == status.row_count for status in complete_statuses)
    assert all(status.earliest_timestamp is not None for status in complete_statuses)
    assert all(
        status.earliest_timestamp < status.latest_timestamp
        for status in complete_statuses
    )

    store.path_for("THYAO", "2h").unlink()
    statuses = inspect_symbol_cache(tmp_path, symbol="THYAO")
    assert statuses[2].timeframe == "2h"
    assert statuses[2].display_status == "MISSING"
    assert runnable_timeframes(statuses) == ("1d", "4h", "1h", "30m")
    assert cache_fingerprint(tmp_path, symbol="THYAO") != complete_fingerprint

    result = replay_cached_observer(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
        pattern_profile="Dengeli",
    )

    assert result.timeframes == ("1d", "4h", "1h", "30m")
    assert result.observation.symbol == "THYAO"
    matrix = mtf_matrix_frame(result, statuses)
    assert tuple(matrix["Timeframe"]) == FOUNDATION_OBSERVER_TIMEFRAMES
    missing = matrix.loc[matrix["Timeframe"] == "2h"].iloc[0]
    assert missing["Data"] == "MISSING"
    assert missing["External state"] == "—"


def test_view_models_and_plotly_chart_are_pure_contract_adapters(tmp_path) -> None:
    make_ui_store(tmp_path)
    statuses = inspect_symbol_cache(tmp_path, symbol="THYAO")
    result = replay_cached_observer(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
    )

    assert overview_values(result)["Combined state"] == "DOMAINS_REPORTED"
    cache_status = cache_status_frame(statuses)
    assert len(cache_status) == 5
    assert "Earliest timestamp" in cache_status.columns
    assert len(mtf_matrix_frame(result, statuses)) == 5
    history = structure_history_frame(result)
    assert len(history) == 5
    assert set(history["Usable closed bars"]) == {160}
    assert set(history["Left-boundary state"]) <= {
        "NO_EXTERNAL_STRUCTURE",
        "LEFT_BOUNDARY_ACTIVE",
        "INITIAL_STRUCTURE_NOT_CURRENT",
        "POST_INITIAL_PROGRESSION",
        "NO_INITIALIZATION_RECORD",
    }
    assert "Bars before first event" in history.columns
    structure_events = structure_events_frame(result)
    assert not structure_events.empty
    assert "BOS maturity" in structure_events.columns
    assert set(structure_events["BOS maturity"]) <= {
        "NOT_APPLICABLE",
        "INITIAL_STRUCTURE",
        "TRANSITION_CONFIRMATION",
        "CONTINUATION",
    }
    assert tuple(zones_frame(result).columns)[0] == "Zone UID"
    assert tuple(confluence_frame(result).columns)[0] == "Cluster UID"
    assert tuple(opposing_conflicts_frame(result).columns)[0] == "Conflict UID"
    assert len(location_outcomes_frame(result)) == len(
        result.structure_location.location_outcomes
    )
    assert len(event_zone_links_frame(result)) == len(
        result.structure_location.event_zone_links
    )
    assert not observer_facts_frame(result).empty

    before = result
    figure = make_market_figure(
        result,
        timeframe="1h",
        zone_timeframes=result.timeframes,
        bar_limit=100,
    )
    assert figure.data[0].type == "candlestick"
    assert figure.layout.xaxis.rangeslider.visible is False
    assert result is before
