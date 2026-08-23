from __future__ import annotations

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.market_workspace import analysis_snapshots_share_all_timeframes
from financial_dashboard.runtime_profile import profile_market_workspace_from_cache
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.targeting.causal_inputs import clip_analysis_inputs_at_cutoff
from financial_dashboard.ui import runtime as ui_runtime
from _ui_test_data import make_ui_store


FAST_APP_PATH = Path(__file__).parents[1] / "src" / "financial_dashboard" / "ui" / "fast_app.py"


def test_causal_clip_reuses_timeframe_snapshot_when_every_bar_is_available(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    inputs = load_analysis_inputs(store, symbol="THYAO", timeframes=ANALYSIS_TIMEFRAMES)
    clock = CausalBarClock()
    cutoff = max(
        clock.available_at(
            inputs.for_timeframe(timeframe).input_batch.frame.iloc[-1]["timestamp"],
            timeframe,
        )
        for timeframe in ANALYSIS_TIMEFRAMES
    )

    clipped = clip_analysis_inputs_at_cutoff(inputs, cutoff=cutoff, clock=clock)

    for timeframe in ANALYSIS_TIMEFRAMES:
        assert clipped.for_timeframe(timeframe) is inputs.for_timeframe(timeframe)
        assert (
            clipped.for_timeframe(timeframe).input_batch
            is inputs.for_timeframe(timeframe).input_batch
        )
    assert analysis_snapshots_share_all_timeframes(
        clipped,
        inputs,
        timeframes=ANALYSIS_TIMEFRAMES,
    )


def test_structure_reuse_gate_fails_closed_when_any_timeframe_is_clipped(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    inputs = load_analysis_inputs(store, symbol="THYAO", timeframes=ANALYSIS_TIMEFRAMES)
    clock = CausalBarClock()
    cutoff = pd.Timestamp("2026-01-03T00:00:00Z")

    clipped = clip_analysis_inputs_at_cutoff(inputs, cutoff=cutoff, clock=clock)

    assert any(
        clipped.for_timeframe(timeframe) is not inputs.for_timeframe(timeframe)
        for timeframe in ANALYSIS_TIMEFRAMES
    )
    assert not analysis_snapshots_share_all_timeframes(
        clipped,
        inputs,
        timeframes=ANALYSIS_TIMEFRAMES,
    )


def test_runtime_profiler_reports_real_workspace_stages(tmp_path) -> None:
    make_ui_store(tmp_path)
    profile = profile_market_workspace_from_cache(
        tmp_path,
        symbol="THYAO",
        timeframes=ANALYSIS_TIMEFRAMES,
        pattern_profile="Dengeli",
    )

    assert profile.total_seconds > 0
    assert profile.workspace.symbol == "THYAO"
    stages = {item.stage: item for item in profile.stages}
    assert stages["input_load"].calls == 1
    assert stages["observer"].calls == 1
    assert stages["ham"].calls == 1
    assert stages["volume"].calls == 1
    assert stages["structure_location"].calls >= 1
    assert stages["cross_domain"].calls == 1


def test_fast_streamlit_default_does_not_build_full_workspace(
    tmp_path, monkeypatch
) -> None:
    make_ui_store(tmp_path)
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    def forbidden_full_workspace(*args, **kwargs):
        raise AssertionError("fast-start default must not build the full workspace")

    monkeypatch.setattr(ui_runtime, "replay_cached_workspace", forbidden_full_workspace)
    app = AppTest.from_file(str(FAST_APP_PATH), default_timeout=120).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["Financial Dashboard"]
    assert any("FAST FOUNDATION" in caption.value for caption in app.caption)
    assert any("hızlı başlangıç" in caption.value.lower() for caption in app.caption)
