from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision.calibration import (
    OpportunityCalibrationRecord,
    save_opportunity_calibration,
)
from financial_dashboard.decision.opportunity import OpportunityCalibration


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "buy_sell_backtest.py"
_SPEC = spec_from_file_location("buy_sell_backtest_helpers", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _decision(timestamp, action):
    return SimpleNamespace(
        timestamp=pd.Timestamp(timestamp),
        action=SimpleNamespace(value=action),
    )


def test_execution_pnl_defaults_to_next_open_and_never_uses_signal_close():
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([
                "2026-01-05 10:00",
                "2026-01-05 10:30",
                "2026-01-05 11:00",
                "2026-01-05 11:30",
            ]),
            "open": [100.0, 101.0, 104.0, 105.0],
            "close": [100.5, 103.0, 104.5, 106.0],
        }
    )
    report = _MODULE.simulate_execution_pnl(
        (
            _decision("2026-01-05 10:00", "BUY"),
            _decision("2026-01-05 11:00", "SELL"),
        ),
        bars,
    )
    assert report.closed_trades == 1
    trade = report.trades[0]
    assert trade.entry_fill_at == pd.Timestamp("2026-01-05 10:30")
    assert trade.entry_fill == 101.0
    assert trade.exit_fill_at == pd.Timestamp("2026-01-05 11:30")
    assert trade.exit_fill == 105.0


def test_execution_costs_reduce_net_return():
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([
                "2026-01-05 10:00",
                "2026-01-05 10:30",
                "2026-01-05 11:00",
                "2026-01-05 11:30",
            ]),
            "open": [100.0, 100.0, 110.0, 110.0],
            "close": [100.0, 100.0, 110.0, 110.0],
        }
    )
    decisions = (
        _decision("2026-01-05 10:00", "BUY"),
        _decision("2026-01-05 11:00", "SELL"),
    )
    free = _MODULE.simulate_execution_pnl(decisions, bars)
    costly = _MODULE.simulate_execution_pnl(
        decisions,
        bars,
        spread_bps=10.0,
        slippage_bps=5.0,
        commission_bps=2.0,
    )
    assert costly.trades[0].net_return_pct < free.trades[0].net_return_pct


def test_auto_calibration_loads_symbol_file(tmp_path):
    path = tmp_path / "calibration" / "opportunity" / "ASELS.json"
    expected = OpportunityCalibration(0.5, 1.0, 2.0)
    save_opportunity_calibration(
        path,
        OpportunityCalibrationRecord(
            calibration=expected,
            symbol="ASELS",
            sample_size=100,
            version=1,
            meta={"method": "test"},
        ),
    )
    args = SimpleNamespace(
        opportunity_none_max_atr=None,
        opportunity_compressed_max_atr=None,
        opportunity_moderate_max_atr=None,
        opportunity_calibration=None,
        auto_calibration=True,
    )
    loaded, label = _MODULE._calibration(args, cache_root=tmp_path, symbol="ASELS")
    assert loaded == expected
    assert label == str(path)
