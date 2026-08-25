from __future__ import annotations

import pytest

pytest.importorskip("plotly")  # local dev installs may omit ui extras

import pandas as pd

from financial_dashboard.engines.three_domain_observer import FOUNDATION_OBSERVER_TIMEFRAMES
from financial_dashboard.ui.charts import make_market_figure
from financial_dashboard.ui.runtime import (
    cache_fingerprint,
    discover_cached_symbols,
    inspect_symbol_cache,
    replay_cached_ham,
    replay_cached_observer,
    replay_cached_volume,
    runnable_timeframes,
)
from financial_dashboard.ui.view_models import (
    cache_status_frame,
    confluence_frame,
    event_zone_links_frame,
    ham_history_frame,
    ham_indicator_evidence_frame,
    ham_mtf_evidence_frame,
    location_outcomes_frame,
    mtf_matrix_frame,
    observer_facts_frame,
    opposing_conflicts_frame,
    overview_values,
    structure_events_frame,
    structure_history_frame,
    volume_deduplication_frame,
    volume_diagnostics_frame,
    volume_event_links_frame,
    volume_history_frame,
    volume_mtf_matrix_frame,
    volume_propagations_frame,
    volume_risk_transitions_frame,
    volume_shocks_frame,
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

    ham = replay_cached_ham(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
    )
    assert ham.timeframes == ("1d", "4h", "1h", "30m")
    ham_matrix = ham_mtf_evidence_frame(ham, statuses)
    assert tuple(ham_matrix["Timeframe"]) == FOUNDATION_OBSERVER_TIMEFRAMES
    ham_missing = ham_matrix.loc[ham_matrix["Timeframe"] == "2h"].iloc[0]
    assert ham_missing["Data"] == "MISSING"
    assert ham_missing["History bars"] == 0
    assert ham_missing["Source errors"] == "Cache file is missing"
    assert pd.isna(ham_missing["Price balance"])

    volume = replay_cached_volume(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
        structure_replay=result.structure_location,
    )
    assert volume.timeframes == ("1d", "4h", "1h", "30m")
    volume_matrix = volume_mtf_matrix_frame(volume, statuses)
    assert tuple(volume_matrix["Timeframe"]) == FOUNDATION_OBSERVER_TIMEFRAMES
    volume_missing = volume_matrix.loc[volume_matrix["Timeframe"] == "2h"].iloc[0]
    assert volume_missing["Data"] == "MISSING"
    assert volume_missing["History bars"] == 0
    assert not volume_missing["Raw volume summed"]


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


def test_replay_cached_ham_keeps_timeframes_isolated(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    statuses = inspect_symbol_cache(tmp_path, symbol="THYAO")
    baseline = replay_cached_ham(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
    )

    changed = store.load("THYAO", "30m")
    replacement_close = pd.Series(
        [300.0 - index * 0.4 for index in range(len(changed))],
        index=changed.index,
    )
    replacement_open = replacement_close.shift(1, fill_value=replacement_close.iloc[0])
    changed["open"] = replacement_open
    changed["close"] = replacement_close
    changed["high"] = pd.concat((replacement_open, replacement_close), axis=1).max(axis=1) + 0.5
    changed["low"] = pd.concat((replacement_open, replacement_close), axis=1).min(axis=1) - 0.5
    store.merge_and_save(
        changed,
        symbol="THYAO",
        timeframe="30m",
        source="ui-test-isolation",
    )

    replayed = replay_cached_ham(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(
            inspect_symbol_cache(tmp_path, symbol="THYAO")
        ),
    )
    for timeframe in ("1d", "4h", "2h", "1h"):
        assert replayed.replay_for(timeframe).history == baseline.replay_for(timeframe).history
    assert replayed.replay_for("30m").history != baseline.replay_for("30m").history


def test_ham_ui_adapters_expose_recent_and_all_confirmed_history_without_decisions(
    tmp_path,
) -> None:
    make_ui_store(tmp_path)
    statuses = inspect_symbol_cache(tmp_path, symbol="THYAO")
    ham = replay_cached_ham(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
    )

    matrix = ham_mtf_evidence_frame(ham, statuses)
    assert tuple(matrix["Timeframe"]) == FOUNDATION_OBSERVER_TIMEFRAMES
    assert set(matrix["History bars"]) == {160}
    assert set(matrix["Profile"]) == {"1d", "4h", "2h", "1h", "30m"}
    assert "Source warnings" in matrix.columns
    assert "Source errors" in matrix.columns
    assert "System state" not in matrix.columns
    assert "System bias" not in matrix.columns
    assert "Family decision score" not in matrix.columns

    detail = ham_indicator_evidence_frame(ham, timeframe="1h")
    assert len(detail) == 10
    assert set(detail["Indicator"]) == {
        "PRICE_CONTEXT",
        "MACD",
        "MOMENTUM",
        "RSI",
        "CCI",
        "SMI",
        "CMF",
        "OBV",
        "STOCHASTIC",
        "STOCH_RSI",
    }
    assert "Reason" in detail.columns
    assert "Pending direction" in detail.columns

    recent = ham_history_frame(ham, timeframe="1h")
    complete = ham_history_frame(ham, timeframe="1h", limit=None)
    assert len(recent) == 100
    assert len(complete) == 160
    assert recent.iloc[0]["Timestamp"] == complete.iloc[-100]["Timestamp"]
    assert recent.iloc[-1]["Timestamp"] == complete.iloc[-1]["Timestamp"]
    assert "Flow ready" in complete.columns
    assert "Volume trust" in complete.columns
    for invalid_limit in (0, -1, True, 1.5):
        with pytest.raises(
            ValueError,
            match="positive integer or None",
        ):
            ham_history_frame(ham, timeframe="1h", limit=invalid_limit)

    prohibited_decision_columns = {
        "action",
        "status",
        "system state",
        "system bias",
        "family decision score",
        "core confidence",
        "final confidence",
        "recommendation",
        "buy",
        "sell",
        "al",
        "sat",
    }
    for frame in (matrix, detail, recent, complete):
        assert prohibited_decision_columns.isdisjoint(
            {str(column).strip().lower() for column in frame.columns}
        )


def test_volume_ui_adapters_expose_mtf_links_history_risks_and_diagnostics_without_actions(
    tmp_path,
) -> None:
    make_ui_store(tmp_path)
    statuses = inspect_symbol_cache(tmp_path, symbol="THYAO")
    volume = replay_cached_volume(
        tmp_path,
        symbol="THYAO",
        timeframes=runnable_timeframes(statuses),
    )

    matrix = volume_mtf_matrix_frame(volume, statuses)
    assert tuple(matrix["Timeframe"]) == FOUNDATION_OBSERVER_TIMEFRAMES
    assert set(matrix["History bars"]) == {160}
    assert not matrix["Raw volume summed"].any()
    assert "Action" not in matrix.columns
    assert "Recommendation" not in matrix.columns

    links = volume_event_links_frame(volume)
    assert len(links) == len(volume.round2.event_assessments)
    assert not links["Lower-TF confirms target"].any()
    assert set(links["Same-TF relation"])
    assert "Risk blocked" in links.columns

    recent = volume_history_frame(volume, timeframe="1h")
    complete = volume_history_frame(volume, timeframe="1h", limit=None)
    assert len(recent) == 100
    assert len(complete) == 160
    assert recent.iloc[0]["Timestamp"] == complete.iloc[-100]["Timestamp"]
    assert recent.iloc[-1]["Timestamp"] == complete.iloc[-1]["Timestamp"]
    assert complete["Confirmed closed bar"].all()
    for invalid_limit in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer or None"):
            volume_history_frame(volume, timeframe="1h", limit=invalid_limit)

    risk_transitions = volume_risk_transitions_frame(volume)
    shocks = volume_shocks_frame(volume)
    propagations = volume_propagations_frame(volume)
    diagnostics = volume_diagnostics_frame(volume)
    dedup = volume_deduplication_frame(volume)
    assert tuple(diagnostics["Timeframe"]) == volume.timeframes
    assert dedup.iloc[0]["Independent vote cap"] == 1
    assert not dedup.iloc[0]["Raw MTF volume summed"]
    assert tuple(risk_transitions.columns)[0] == "Event UID"
    assert tuple(shocks.columns)[0] == "Shock UID"
    assert tuple(propagations.columns)[0] == "Origin timeframe"

    prohibited = {
        "action",
        "recommendation",
        "entry",
        "buy",
        "sell",
        "al",
        "sat",
        "final confidence",
    }
    for frame in (
        matrix,
        links,
        recent,
        complete,
        risk_transitions,
        shocks,
        propagations,
        diagnostics,
        dedup,
    ):
        assert prohibited.isdisjoint(
            {str(column).strip().lower() for column in frame.columns}
        )
