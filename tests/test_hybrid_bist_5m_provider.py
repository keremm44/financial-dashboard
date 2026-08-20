from __future__ import annotations

from datetime import datetime

import pandas as pd

from financial_dashboard.data.hybrid_bist_5m_provider import HybridBist5mProvider
from financial_dashboard.data.provider import MarketDataProvider


TZ = "Europe/Istanbul"


def _frame(rows: list[tuple[str, float]], *, source: str, closed: bool = True) -> pd.DataFrame:
    out = []
    for ts, close in rows:
        out.append(
            {
                "timestamp": pd.Timestamp(ts, tz=TZ),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000.0,
                "symbol": "THYAO",
                "timeframe": "5m",
                "is_closed": closed,
                "is_complete": True,
                "source": source,
            }
        )
    return pd.DataFrame(out)


class FakeProvider(MarketDataProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        return self.frame.copy()


def test_yahoo_only_fills_missing_tv_timestamp_and_never_overwrites_primary() -> None:
    tv = _frame(
        [("2026-08-20 10:00", 100.0), ("2026-08-20 10:10", 102.0)],
        source="tvdatafeed",
    )
    yahoo = _frame(
        [
            ("2026-08-20 10:00", 999.0),
            ("2026-08-20 10:05", 101.0),
            ("2026-08-20 10:10", 999.0),
        ],
        source="YAHOO_FALLBACK",
    )
    provider = HybridBist5mProvider(FakeProvider(tv), FakeProvider(yahoo))
    merged = provider.get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:15", tz=TZ).to_pydatetime(),
    )

    assert list(merged["timestamp"]) == [
        pd.Timestamp("2026-08-20 10:00", tz=TZ),
        pd.Timestamp("2026-08-20 10:05", tz=TZ),
        pd.Timestamp("2026-08-20 10:10", tz=TZ),
    ]
    assert list(merged["close"]) == [100.0, 101.0, 102.0]
    assert list(merged["source"]) == ["tvdatafeed", "YAHOO_FALLBACK", "tvdatafeed"]
    assert provider.last_gap_report.yahoo_filled == 1
    assert provider.last_gap_report.unresolved_gaps == 0


def test_invalid_yahoo_bar_is_rejected_and_gap_remains() -> None:
    tv = _frame([("2026-08-20 10:00", 100.0)], source="tvdatafeed")
    yahoo = _frame([("2026-08-20 10:05", 101.0)], source="YAHOO_FALLBACK")
    yahoo.loc[0, "high"] = 99.0
    provider = HybridBist5mProvider(FakeProvider(tv), FakeProvider(yahoo))
    merged = provider.get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:10", tz=TZ).to_pydatetime(),
    )
    assert len(merged) == 1
    assert provider.last_gap_report.yahoo_filled == 0
    assert provider.last_gap_report.unresolved_gaps == 1


def test_open_yahoo_bar_cannot_fill_gap() -> None:
    tv = _frame([("2026-08-20 10:00", 100.0)], source="tvdatafeed")
    yahoo = _frame([("2026-08-20 10:05", 101.0)], source="YAHOO_FALLBACK", closed=False)
    provider = HybridBist5mProvider(FakeProvider(tv), FakeProvider(yahoo))
    merged = provider.get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:10", tz=TZ).to_pydatetime(),
    )
    assert len(merged) == 1
    assert provider.last_gap_report.yahoo_filled == 0


def test_future_tail_does_not_rewrite_historical_prefix() -> None:
    tv_a = _frame([("2026-08-20 10:00", 100.0)], source="tvdatafeed")
    yahoo_a = _frame([("2026-08-20 10:05", 101.0)], source="YAHOO_FALLBACK")
    a = HybridBist5mProvider(FakeProvider(tv_a), FakeProvider(yahoo_a)).get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:10", tz=TZ).to_pydatetime(),
    )

    tv_b = _frame(
        [("2026-08-20 10:00", 100.0), ("2026-08-20 10:10", 102.0)],
        source="tvdatafeed",
    )
    yahoo_b = _frame([("2026-08-20 10:05", 101.0)], source="YAHOO_FALLBACK")
    b = HybridBist5mProvider(FakeProvider(tv_b), FakeProvider(yahoo_b)).get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:15", tz=TZ).to_pydatetime(),
    )

    pd.testing.assert_frame_equal(
        a.reset_index(drop=True),
        b[b["timestamp"] <= pd.Timestamp("2026-08-20 10:05", tz=TZ)].reset_index(drop=True),
    )
