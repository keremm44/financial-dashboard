from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from financial_dashboard.data.yahoo_intraday_provider import (
    YahooFinanceIntradayProvider,
    YahooIntradayError,
)


TZ = "Europe/Istanbul"


def _history() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-08-20 10:00", tz=TZ),
            pd.Timestamp("2026-08-20 10:05", tz=TZ),
        ]
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000, 1200],
        },
        index=index,
    )


class FakeTicker:
    def __init__(self, history: pd.DataFrame | Exception) -> None:
        self._history = history
        self.kwargs = None

    def history(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self._history, Exception):
            raise self._history
        return self._history


def test_maps_bist_symbol_requests_5m_and_marks_closed() -> None:
    ticker = FakeTicker(_history())
    seen: list[str] = []
    provider = YahooFinanceIntradayProvider(
        ticker_factory=lambda symbol: seen.append(symbol) or ticker
    )
    frame = provider.get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:10", tz=TZ).to_pydatetime(),
    )
    assert seen == ["THYAO.IS"]
    assert ticker.kwargs["interval"] == "5m"
    assert ticker.kwargs["auto_adjust"] is False
    assert list(frame["source"].unique()) == ["YAHOO_FALLBACK"]
    assert bool(frame["is_closed"].all())
    assert list(frame["close"]) == [101.0, 102.0]


def test_open_last_bar_is_not_marked_closed() -> None:
    ticker = FakeTicker(_history())
    provider = YahooFinanceIntradayProvider(ticker_factory=lambda symbol: ticker)
    frame = provider.get_ohlcv(
        "THYAO",
        "5m",
        pd.Timestamp("2026-08-20 10:00", tz=TZ).to_pydatetime(),
        pd.Timestamp("2026-08-20 10:07", tz=TZ).to_pydatetime(),
    )
    assert bool(frame.iloc[0]["is_closed"])
    assert not bool(frame.iloc[1]["is_closed"])


def test_empty_history_is_clean_empty_frame() -> None:
    ticker = FakeTicker(pd.DataFrame())
    provider = YahooFinanceIntradayProvider(ticker_factory=lambda symbol: ticker)
    frame = provider.get_ohlcv(
        "THYAO", "5m", datetime(2026, 8, 1), datetime(2026, 8, 2)
    )
    assert frame.empty


def test_provider_error_is_wrapped_cleanly() -> None:
    ticker = FakeTicker(RuntimeError("rate limited\nretry later"))
    provider = YahooFinanceIntradayProvider(ticker_factory=lambda symbol: ticker)
    with pytest.raises(YahooIntradayError, match="Yahoo 5m request failed.*rate limited retry later"):
        provider.get_ohlcv("THYAO", "5m", datetime(2026, 8, 1), datetime(2026, 8, 2))


def test_rejects_non_5m_timeframe() -> None:
    provider = YahooFinanceIntradayProvider(ticker_factory=lambda symbol: FakeTicker(_history()))
    with pytest.raises(ValueError, match="only 5m"):
        provider.get_ohlcv("THYAO", "15m", datetime(2026, 8, 1), datetime(2026, 8, 2))
