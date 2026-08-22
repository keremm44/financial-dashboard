from __future__ import annotations

import pandas as pd

from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider
from financial_dashboard.engines.stabil_support_lifecycle import (
    StabilSupportLifecycleEngine,
    SupportValidity,
)
from financial_dashboard.engines.stabil_trend_engine import StabilTrendConfig


TZ = "Europe/Istanbul"


def _oscillating_daily_frame(rows: int = 40) -> pd.DataFrame:
    pattern = (100.0, 104.0, 101.0, 96.0, 99.0, 105.0, 102.0, 95.0)
    records: list[dict[str, object]] = []
    for index in range(rows):
        close = pattern[index % len(pattern)] + index * 0.05
        timestamp = pd.Timestamp("2026-01-01 10:00", tz=TZ) + pd.Timedelta(days=index)
        records.append(
            {
                "timestamp": timestamp,
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 1_000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(records)


def test_support_availability_is_independent_from_legacy_trend_warmup() -> None:
    frame = _oscillating_daily_frame()
    config = StabilTrendConfig(
        daily_pivot_len=1,
        daily_ema_len=200,
        slope_lookback=50,
        acceptance_len=100,
        pullback_lookback=200,
    )

    snapshot = StabilSupportLifecycleEngine(config).analyze(frame)

    assert snapshot.validity is not SupportValidity.NO_SUPPORT
    assert snapshot.support_level is not None
    assert snapshot.support_origin_at is not None
    assert snapshot.support_confirmed_at is not None
    assert snapshot.support_available_at is not None
    assert snapshot.support_origin_at < snapshot.support_confirmed_at <= snapshot.support_available_at


def test_default_tvdatafeed_capacity_covers_100_full_bist_15m_sessions() -> None:
    provider = TvDatafeedProvider(client=object())

    # BIST continuous session is 10:00-18:00: 480 / 15 = 32 bars per full day.
    assert provider.max_bars >= 100 * 32
    assert provider.max_bars == 10_000
