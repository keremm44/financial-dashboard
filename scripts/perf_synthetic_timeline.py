from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from financial_dashboard.analysis_config import BAR_DURATIONS
from financial_dashboard.data.parquet_store import ParquetOHLCVStore

# Roughly proportional to a real ASELS-like cache (bar counts, not file sizes).
_BASE_BARS = {
    "1d": 2100,
    "4h": 4200,
    "2h": 6500,
    "1h": 10400,
    "30m": 17000,
}


def _synthetic_frame(bars: int, timeframe: str, *, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    duration = pd.Timedelta(BAR_DURATIONS[timeframe])
    end = pd.Timestamp("2026-08-20").floor("D")
    start = end - duration * bars
    timestamps = pd.date_range(start=start, periods=bars, freq=duration)

    # Regime-switching drift keeps structure market-like (sustained trends with
    # pullbacks) instead of a pure random walk, which can produce degenerate
    # break/pivot sequences that real OHLCV does not exhibit.
    drift = np.zeros(bars)
    position = 0
    while position < bars:
        segment = int(rng.integers(80, 320))
        drift[position : position + segment] = float(
            rng.choice([-0.0012, -0.0004, 0.0002, 0.0012])
        )
        position += segment
    steps = rng.normal(loc=drift, scale=0.0035, size=bars)
    close = 90.0 * np.exp(np.cumsum(steps))
    open_ = np.empty(bars)
    open_[0] = 90.0
    open_[1:] = close[:-1]
    wick = np.abs(rng.normal(loc=0.003, scale=0.002, size=bars))
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    volume = rng.lognormal(mean=13.0, sigma=0.4, size=bars)

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate synthetic OHLCV for all analysis timeframes and run the "
            "decision timeline builder with live domain timing, so per-domain "
            "replay cost can be measured without touching a real cache."
        )
    )
    parser.add_argument("--scale", type=float, default=1.0, help="bar-count multiplier")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=None, help="cache root (default: temp dir)")
    parser.add_argument("--keep", action="store_true", help="keep the synthetic cache afterwards")
    args = parser.parse_args()

    if args.out is not None:
        root = args.out
        root.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        tmp = tempfile.mkdtemp(prefix="fd-perf-")
        root = Path(tmp)
        cleanup = not args.keep

    store = ParquetOHLCVStore(root)
    counts: dict[str, int] = {}
    for index, (timeframe, base) in enumerate(_BASE_BARS.items()):
        bars = int(base * args.scale)
        frame = _synthetic_frame(bars, timeframe, seed=args.seed * 100 + index)
        store.merge_and_save(frame, symbol="PERF", timeframe=timeframe, source="synthetic")
        counts[timeframe] = bars

    print(f"SYNTHETIC_CACHE\t{root}")
    print(f"SCALE\t{args.scale}")
    for timeframe, bars in counts.items():
        print(f"BARS\t{timeframe}\t{bars}")

    command = [
        sys.executable,
        "scripts/build_decision_timeline_cache.py",
        str(root),
        "PERF",
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=1700
    )
    print(completed.stdout, end="")
    if completed.returncode != 0:
        print(completed.stderr[-4000:], end="", file=sys.stderr)
        raise SystemExit(completed.returncode)

    if cleanup:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
