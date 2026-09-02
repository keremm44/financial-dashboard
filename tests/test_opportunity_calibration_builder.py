from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pandas as pd

from financial_dashboard.decision.structural import DecisionHorizon


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_opportunity_calibration.py"
_SPEC = spec_from_file_location("opportunity_calibration_builder", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def _sample(index: int, room_atr: float, *, mfe: float, mae: float):
    return _MODULE.OpportunityOutcomeSample(
        as_of=pd.Timestamp("2026-01-01") + pd.Timedelta(hours=index),
        horizon=DecisionHorizon.LONG_TERM,
        room_atr=room_atr,
        future_mfe_atr=mfe,
        future_mae_atr=mae,
        target_reached=mfe >= room_atr,
    )


def test_calibration_boundaries_come_from_current_room_not_future_mfe():
    samples = [
        _sample(i, float(i + 1), mfe=float(100 - i), mae=0.5)
        for i in range(20)
    ]
    calibration, train, validation = _MODULE._calibrate_from_samples(
        samples,
        quantiles=(0.25, 0.50, 0.75),
        train_fraction=0.70,
        min_samples=10,
    )
    train_room = pd.Series([row.room_atr for row in train])
    assert calibration.none_max_atr == float(train_room.quantile(0.25))
    assert calibration.compressed_max_atr == float(train_room.quantile(0.50))
    assert calibration.moderate_max_atr == float(train_room.quantile(0.75))
    assert len(validation) == 6


def test_validation_summary_reports_outcomes_per_room_bucket():
    samples = [
        _sample(0, 0.5, mfe=0.25, mae=0.1),
        _sample(1, 1.5, mfe=2.0, mae=0.2),
        _sample(2, 2.5, mfe=3.0, mae=0.3),
        _sample(3, 4.0, mfe=5.0, mae=0.4),
    ]
    calibration = _MODULE.OpportunityCalibration(1.0, 2.0, 3.0)
    summary = _MODULE._validation_summary(samples, calibration)
    assert summary["NONE"]["count"] == 1
    assert summary["COMPRESSED"]["count"] == 1
    assert summary["MODERATE"]["count"] == 1
    assert summary["AMPLE"]["count"] == 1
    assert summary["AMPLE"]["target_hit_rate"] == 1.0
