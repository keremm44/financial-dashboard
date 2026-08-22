from __future__ import annotations

import pandas as pd


def wilder_atr(frame: pd.DataFrame, length: int = 14, *, minimum: float = 1e-12) -> float:
    if length < 2:
        raise ValueError("ATR length must be >= 2")
    tr_values: list[float] = []
    prev_close: float | None = None
    atr: float | None = None
    for row in frame.itertuples(index=False):
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        tr = high - low
        if prev_close is not None:
            tr = max(tr, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(float(tr))
        if len(tr_values) == length:
            atr = sum(tr_values[-length:]) / length
        elif len(tr_values) > length:
            assert atr is not None
            atr = (atr * (length - 1) + tr) / length
        prev_close = close
    if not tr_values:
        raise ValueError("ATR requires at least one closed bar")
    return max(float(atr if atr is not None else tr_values[-1]), minimum)


def interval_gap(low_a: float, high_a: float, low_b: float, high_b: float) -> float:
    if high_a < low_b:
        return low_b - high_a
    if high_b < low_a:
        return low_a - high_b
    return 0.0


__all__ = ["interval_gap", "wilder_atr"]
