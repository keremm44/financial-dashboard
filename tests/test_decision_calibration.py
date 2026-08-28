from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_dashboard.decision.calibration import (
    CalibrationSchemaError,
    OpportunityCalibrationRecord,
    load_opportunity_calibration,
    save_opportunity_calibration,
)
from financial_dashboard.decision.opportunity import OpportunityCalibration


def _record(**meta) -> OpportunityCalibrationRecord:
    return OpportunityCalibrationRecord(
        calibration=OpportunityCalibration(0.5, 1.5, 3.0),
        symbol="ASELS",
        sample_size=100,
        version=1,
        meta=meta,
    )


def test_roundtrip(tmp_path: Path):
    target = tmp_path / "calibration" / "opportunity" / "ASELS.json"
    save_opportunity_calibration(target, _record(forward_bars=24, quantiles=[0.25, 0.5, 0.75]))

    loaded = load_opportunity_calibration(target)
    assert loaded.symbol == "ASELS"
    assert loaded.sample_size == 100
    assert loaded.calibration.none_max_atr == 0.5
    assert loaded.calibration.compressed_max_atr == 1.5
    assert loaded.calibration.moderate_max_atr == 3.0
    assert loaded.meta["forward_bars"] == 24
    assert loaded.meta["quantiles"] == [0.25, 0.5, 0.75]


def test_meta_with_non_json_native_objects_is_stringified(tmp_path: Path):
    # Regression for the ASELS crash: meta carried a PersistentCacheIdentity.
    class PersistentCacheIdentity:
        def __str__(self) -> str:
            return "identity=v1;source=test"

    target = tmp_path / "ASELS.json"
    save_opportunity_calibration(
        target,
        _record(source_identity=PersistentCacheIdentity(), nested={"inner": PersistentCacheIdentity()}),
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["source_identity"] == "identity=v1;source=test"
    assert payload["nested"]["inner"] == "identity=v1;source=test"

    loaded = load_opportunity_calibration(target)
    assert loaded.meta["source_identity"] == "identity=v1;source=test"


def test_rejects_wrong_version_kind_and_boundaries(tmp_path: Path):
    target = tmp_path / "ASELS.json"
    save_opportunity_calibration(target, _record())

    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["version"] = 2
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CalibrationSchemaError):
        load_opportunity_calibration(target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["version"] = 1
    payload["kind"] = "something_else"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CalibrationSchemaError):
        load_opportunity_calibration(target)

    payload["kind"] = "opportunity"
    payload["boundaries"] = {"none_max_atr": 3.0, "compressed_max_atr": 1.5, "moderate_max_atr": 0.5}
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CalibrationSchemaError):
        load_opportunity_calibration(target)


def test_missing_file_raises_schema_error(tmp_path: Path):
    with pytest.raises(CalibrationSchemaError):
        load_opportunity_calibration(tmp_path / "missing.json")
