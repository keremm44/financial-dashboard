from __future__ import annotations

import os

import pandas as pd
import pytest

import financial_dashboard.market_workspace as workspace_module
from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.engines.three_domain_observer import FOUNDATION_OBSERVER_TIMEFRAMES
from financial_dashboard.ham_mtf_replay import HAM_EVIDENCE_TIMEFRAMES
from financial_dashboard.market_workspace import (
    CacheSnapshotChangedError,
    MarketAnalysisWorkspaceRunner,
    WorkspaceDomainStatus,
    replay_market_workspace_from_cache,
)
from financial_dashboard.mtf_replay import FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.ui.workspace_view_models import workspace_domain_status_frame
from financial_dashboard.volume_mtf_replay import VOLUME_EVIDENCE_TIMEFRAMES
from _ui_test_data import make_ui_store


def test_analysis_timeframes_and_symbol_identity_are_canonical() -> None:
    assert ANALYSIS_TIMEFRAMES == FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES
    assert ANALYSIS_TIMEFRAMES == FOUNDATION_OBSERVER_TIMEFRAMES
    assert ANALYSIS_TIMEFRAMES == HAM_EVIDENCE_TIMEFRAMES
    assert ANALYSIS_TIMEFRAMES == VOLUME_EVIDENCE_TIMEFRAMES
    assert normalize_symbol("  asels ") == "ASELS"
    with pytest.raises(ValueError, match="symbol must not be empty"):
        normalize_symbol("   ")


def test_default_causal_clock_uses_left_labels_intraday_and_close_labels_daily() -> None:
    clock = CausalBarClock()
    intraday_start = pd.Timestamp("2026-08-21T10:00:00+03:00")
    daily_close = pd.Timestamp("2026-08-21T18:10:00+03:00")

    assert clock.available_at(intraday_start, "1h") == pd.Timestamp(
        "2026-08-21T11:00:00+03:00"
    )
    assert clock.available_at(daily_close, "1d") == daily_close

    explicit = CausalBarClock(
        durations=(("1d", pd.Timedelta(hours=8)),),
        close_labelled_timeframes=("1d",),
    )
    assert explicit.available_at(intraday_start, "1D") == pd.Timestamp(
        "2026-08-21T18:00:00+03:00"
    )


def test_workspace_runs_foundation_once_and_exposes_isolated_domain_health(tmp_path) -> None:
    make_ui_store(tmp_path)

    workspace = replay_market_workspace_from_cache(
        tmp_path,
        symbol=" thyao ",
        timeframes=ANALYSIS_TIMEFRAMES,
        pattern_profile="Dengeli",
    )

    assert workspace.symbol == "THYAO"
    assert workspace.timeframes == ANALYSIS_TIMEFRAMES
    assert workspace.observer.symbol == "THYAO"
    assert workspace.observer.structure_location.symbol == "THYAO"
    assert workspace.ham.status is WorkspaceDomainStatus.READY
    assert workspace.volume.status is WorkspaceDomainStatus.READY
    assert workspace.liquidity.status is WorkspaceDomainStatus.READY
    assert workspace.order_block.status is WorkspaceDomainStatus.READY
    assert workspace.fvg_engulfing.status is WorkspaceDomainStatus.READY
    assert workspace.targeting.status is WorkspaceDomainStatus.READY
    assert workspace.ham_result is not None
    assert workspace.volume_result is not None
    assert workspace.liquidity_result is not None
    assert workspace.order_block_result is not None
    assert workspace.fvg_engulfing_result is not None
    assert workspace.targeting_result is not None
    assert workspace.volume_result.symbol == workspace.symbol
    assert tuple(row[0] for row in workspace.fingerprint) == ANALYSIS_TIMEFRAMES

    health = workspace_domain_status_frame(workspace)
    assert tuple(health["Domain"]) == (
        "Observer foundation",
        "Ham evidence",
        "Volume Participation",
        "Liquidity",
        "Order Block",
        "FVG / Engulfing",
        "Targeting",
    )
    assert set(health["Status"]) == {"READY"}


def test_workspace_loads_each_timeframe_once_and_reuses_prepared_batches(
    tmp_path,
    monkeypatch,
) -> None:
    store = make_ui_store(tmp_path)
    real_load = store.load
    loads: list[tuple[str, str]] = []

    def counted_load(symbol: str, timeframe: str):
        loads.append((symbol, timeframe))
        return real_load(symbol, timeframe)

    monkeypatch.setattr(store, "load", counted_load)
    workspace = MarketAnalysisWorkspaceRunner(store).run(
        symbol="THYAO",
        timeframes=ANALYSIS_TIMEFRAMES,
    )

    assert loads == [("THYAO", timeframe) for timeframe in ANALYSIS_TIMEFRAMES]
    for timeframe in ANALYSIS_TIMEFRAMES:
        observer_batch = workspace.observer.structure_location.replay_for(
            timeframe
        ).input_batch
        ham_batch = workspace.ham_result.replay_for(timeframe).input_batch
        volume_batch = workspace.volume_result.replay_for(timeframe).input_batch
        assert observer_batch is ham_batch
        assert observer_batch is volume_batch


def test_workspace_keeps_optional_domain_failure_from_hiding_other_domains(
    tmp_path,
    monkeypatch,
) -> None:
    store = make_ui_store(tmp_path)

    def fail_ham(
        self,
        symbol,
        *,
        timeframes=HAM_EVIDENCE_TIMEFRAMES,
        input_snapshot=None,
    ):
        raise RuntimeError("synthetic Ham failure")

    monkeypatch.setattr(
        workspace_module.HamMTFEvidenceReplayRunner,
        "replay",
        fail_ham,
    )

    workspace = MarketAnalysisWorkspaceRunner(store).run(
        symbol="THYAO",
        timeframes=ANALYSIS_TIMEFRAMES,
    )

    assert workspace.observer is not None
    assert workspace.ham.status is WorkspaceDomainStatus.ERROR
    assert workspace.ham.error_type == "RuntimeError"
    assert workspace.ham_result is None
    assert workspace.volume.status is WorkspaceDomainStatus.READY
    assert workspace.liquidity.status is WorkspaceDomainStatus.READY
    assert workspace.order_block.status is WorkspaceDomainStatus.READY
    assert workspace.fvg_engulfing.status is WorkspaceDomainStatus.READY
    assert workspace.targeting.status is WorkspaceDomainStatus.READY
    assert workspace.volume_result is not None


def test_workspace_rejects_cache_mutation_during_one_replay(
    tmp_path,
    monkeypatch,
) -> None:
    store = make_ui_store(tmp_path)
    real_replay = workspace_module.HamMTFEvidenceReplayRunner.replay
    target = store.path_for("THYAO", "30m")

    def replay_and_touch(
        self,
        symbol,
        *,
        timeframes=HAM_EVIDENCE_TIMEFRAMES,
        input_snapshot=None,
    ):
        result = real_replay(
            self,
            symbol,
            timeframes=timeframes,
            input_snapshot=input_snapshot,
        )
        stat = target.stat()
        os.utime(
            target,
            ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
        )
        return result

    monkeypatch.setattr(
        workspace_module.HamMTFEvidenceReplayRunner,
        "replay",
        replay_and_touch,
    )

    with pytest.raises(
        CacheSnapshotChangedError,
        match="cache files changed",
    ):
        MarketAnalysisWorkspaceRunner(store).run(
            symbol="THYAO",
            timeframes=ANALYSIS_TIMEFRAMES,
        )
