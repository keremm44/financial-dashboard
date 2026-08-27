from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .opportunity import OpportunityCalibration

OPPORTUNITY_CALIBRATION_SCHEMA_VERSION = 1
OPPORTUNITY_CALIBRATION_KIND = "opportunity"

_BOUNDARY_KEYS = ("none_max_atr", "compressed_max_atr", "moderate_max_atr")


class CalibrationSchemaError(ValueError):
    """Raised when a calibration file does not match the expected schema."""


@dataclass(frozen=True, slots=True)
class OpportunityCalibrationRecord:
    """Versioned, deterministic wrapper around one calibrated threshold set."""

    calibration: OpportunityCalibration
    symbol: str
    sample_size: int
    version: int
    meta: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise ValueError("calibration symbol must be non-empty")
        if self.sample_size < 0:
            raise ValueError("calibration sample_size must be >= 0")


def save_opportunity_calibration(
    path: Path | str,
    record: OpportunityCalibrationRecord,
) -> None:
    """Atomically persist one calibration record as deterministic JSON."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": record.version,
        "kind": OPPORTUNITY_CALIBRATION_KIND,
        "symbol": record.symbol,
        "boundaries": {
            "none_max_atr": record.calibration.none_max_atr,
            "compressed_max_atr": record.calibration.compressed_max_atr,
            "moderate_max_atr": record.calibration.moderate_max_atr,
        },
        "sample_size": record.sample_size,
        **dict(record.meta),
    }
    tmp_path = target.with_name(f"{target.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, target)


def load_opportunity_calibration(path: Path | str) -> OpportunityCalibrationRecord:
    """Load and validate one opportunity calibration JSON file."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CalibrationSchemaError(f"calibration file not found: {source}") from error
    except json.JSONDecodeError as error:
        raise CalibrationSchemaError(f"calibration file is not valid JSON: {source}") from error

    if not isinstance(payload, dict):
        raise CalibrationSchemaError(f"calibration payload must be an object: {source}")

    version = payload.get("version")
    if version != OPPORTUNITY_CALIBRATION_SCHEMA_VERSION:
        raise CalibrationSchemaError(
            f"unsupported calibration version {version!r}; expected "
            f"{OPPORTUNITY_CALIBRATION_SCHEMA_VERSION}: {source}"
        )
    kind = payload.get("kind")
    if kind != OPPORTUNITY_CALIBRATION_KIND:
        raise CalibrationSchemaError(
            f"calibration kind {kind!r} does not match {OPPORTUNITY_CALIBRATION_KIND!r}: {source}"
        )

    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise CalibrationSchemaError(f"calibration symbol must be a non-empty string: {source}")

    boundaries = payload.get("boundaries")
    if not isinstance(boundaries, dict):
        raise CalibrationSchemaError(f"calibration boundaries must be an object: {source}")
    values: list[float] = []
    for key in _BOUNDARY_KEYS:
        raw = boundaries.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CalibrationSchemaError(
                f"calibration boundary {key!r} must be numeric: {source}"
            )
        values.append(float(raw))

    try:
        calibration = OpportunityCalibration(*values)
    except ValueError as error:
        raise CalibrationSchemaError(f"invalid calibration boundaries: {error}") from error

    sample_size = payload.get("sample_size")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 0:
        raise CalibrationSchemaError(f"calibration sample_size must be a non-negative int: {source}")

    known_keys = {
        "version",
        "kind",
        "symbol",
        "boundaries",
        "sample_size",
    }
    meta = {key: value for key, value in payload.items() if key not in known_keys}

    return OpportunityCalibrationRecord(
        calibration=calibration,
        symbol=symbol,
        sample_size=sample_size,
        version=int(version),
        meta=meta,
    )


__all__ = [
    "CalibrationSchemaError",
    "OpportunityCalibrationRecord",
    "OPPORTUNITY_CALIBRATION_KIND",
    "OPPORTUNITY_CALIBRATION_SCHEMA_VERSION",
    "load_opportunity_calibration",
    "save_opportunity_calibration",
]
