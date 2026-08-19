from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from .schema import PRICE_COLUMNS, REQUIRED_OHLCV_COLUMNS


class DataQualityStatus(StrEnum):
    OK = "DATA_OK"
    LIMITED = "DATA_LIMITED"
    INVALID = "DATA_INVALID"


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    status: DataQualityStatus
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def can_decide(self) -> bool:
        return self.status is not DataQualityStatus.INVALID


def assess_ohlcv_quality(frame: pd.DataFrame) -> DataQualityReport:
    errors: list[str] = []
    warnings: list[str] = []

    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        return DataQualityReport(
            status=DataQualityStatus.INVALID,
            errors=(f"Missing required columns: {', '.join(missing)}",),
        )

    if frame.empty:
        return DataQualityReport(DataQualityStatus.INVALID, errors=("No market data",))

    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    if timestamps.isna().any():
        errors.append("Invalid timestamp value")
    if timestamps.duplicated().any():
        errors.append("Duplicate timestamps")
    if not timestamps.is_monotonic_increasing:
        errors.append("Timestamps are not monotonic increasing")

    numeric = frame.loc[:, (*PRICE_COLUMNS, "volume")].apply(pd.to_numeric, errors="coerce")
    if numeric.loc[:, PRICE_COLUMNS].isna().any().any():
        errors.append("Missing or non-numeric OHLC value")

    valid_rows = numeric.loc[:, PRICE_COLUMNS].dropna()
    if not valid_rows.empty:
        if (valid_rows["high"] < valid_rows[["open", "close", "low"]].max(axis=1)).any():
            errors.append("High is below another OHLC value")
        if (valid_rows["low"] > valid_rows[["open", "close", "high"]].min(axis=1)).any():
            errors.append("Low is above another OHLC value")

    volume = numeric["volume"]
    if volume.isna().any():
        warnings.append("Volume contains missing/non-numeric values")
    if (volume.dropna() < 0).any():
        errors.append("Negative volume detected")
    if not volume.dropna().empty and (volume.dropna() == 0).all():
        warnings.append("Volume is zero for the entire sample")

    if "is_complete" in frame.columns:
        completeness = frame["is_complete"].fillna(False).astype(bool)
        if not completeness.all():
            warnings.append("One or more candles are incomplete")

    if errors:
        status = DataQualityStatus.INVALID
    elif warnings:
        status = DataQualityStatus.LIMITED
    else:
        status = DataQualityStatus.OK

    return DataQualityReport(status=status, errors=tuple(errors), warnings=tuple(warnings))
