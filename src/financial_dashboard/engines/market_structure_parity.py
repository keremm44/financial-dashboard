from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .market_structure_engine import MarketStructureEngine

TV_COLUMNS = {
    "external_state": "ARGENT | MS | STATE",
    "internal_state": "ARGENT | MS | INTERNAL_STATE",
    "evidence_score": "ARGENT | MS | EVIDENCE_SCORE",
    "external_protected_low": "ARGENT | MS | PROTECTED_LOW",
    "external_protected_high": "ARGENT | MS | PROTECTED_HIGH",
    "external_weak_low": "ARGENT | MS | WEAK_LOW",
    "external_weak_high": "ARGENT | MS | WEAK_HIGH",
    "internal_protected_low": "ARGENT | MS | INTERNAL_PROTECTED_LOW",
    "internal_protected_high": "ARGENT | MS | INTERNAL_PROTECTED_HIGH",
    "internal_weak_low": "ARGENT | MS | INTERNAL_WEAK_LOW",
    "internal_weak_high": "ARGENT | MS | INTERNAL_WEAK_HIGH",
    "handshake": "ARGENT | MS | HANDSHAKE",
}


@dataclass(frozen=True, slots=True)
class ParityMismatch:
    timestamp: object
    field: str
    tradingview: float | None
    python: float | None
    difference: float | None


@dataclass(frozen=True, slots=True)
class ParityReport:
    compared_rows: int
    compared_values: int
    mismatches: tuple[ParityMismatch, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches


def replay_export(engine: MarketStructureEngine, frame: pd.DataFrame) -> pd.DataFrame:
    engine.reset()
    rows: list[dict[str, object]] = []
    for _, bar in frame.iterrows():
        result = engine.update(bar)
        if result is None or not bool(bar.get("is_closed", True)):
            continue
        export = engine.export_contract
        if export is None:
            continue
        row: dict[str, object] = {"timestamp": bar["timestamp"]}
        for field in TV_COLUMNS:
            row[field] = getattr(export, field)
        rows.append(row)
    return pd.DataFrame(rows)


def normalize_tradingview_export(frame: pd.DataFrame, *, timestamp_column: str = "timestamp") -> pd.DataFrame:
    missing = [name for name in TV_COLUMNS.values() if name not in frame.columns]
    if missing:
        raise ValueError(f"TradingView export missing Market Structure columns: {missing}")
    if timestamp_column not in frame.columns:
        raise ValueError(f"TradingView export missing timestamp column: {timestamp_column}")

    out = pd.DataFrame({"timestamp": pd.to_datetime(frame[timestamp_column], utc=True)})
    for field, title in TV_COLUMNS.items():
        out[field] = pd.to_numeric(frame[title], errors="coerce")
    return out


def compare_parity(
    tradingview: pd.DataFrame,
    python_export: pd.DataFrame,
    *,
    timestamp_column: str = "timestamp",
    price_tolerance: float = 1e-8,
    score_tolerance: float = 0.0,
) -> ParityReport:
    tv = normalize_tradingview_export(tradingview, timestamp_column=timestamp_column)
    py = python_export.copy()
    py["timestamp"] = pd.to_datetime(py["timestamp"], utc=True)

    merged = tv.merge(py, on="timestamp", suffixes=("_tv", "_py"), how="inner")
    mismatches: list[ParityMismatch] = []
    compared_values = 0

    exact_fields = {"external_state", "internal_state", "handshake"}
    score_fields = {"evidence_score"}

    for _, row in merged.iterrows():
        for field in TV_COLUMNS:
            tv_value = row[f"{field}_tv"]
            py_value = row[f"{field}_py"]
            tv_na = pd.isna(tv_value)
            py_na = pd.isna(py_value)
            compared_values += 1
            if tv_na and py_na:
                continue
            if tv_na != py_na:
                mismatches.append(ParityMismatch(row["timestamp"], field, None if tv_na else float(tv_value), None if py_na else float(py_value), None))
                continue

            tolerance = 0.0 if field in exact_fields else score_tolerance if field in score_fields else price_tolerance
            difference = abs(float(tv_value) - float(py_value))
            if difference > tolerance:
                mismatches.append(ParityMismatch(row["timestamp"], field, float(tv_value), float(py_value), difference))

    return ParityReport(len(merged), compared_values, tuple(mismatches))
