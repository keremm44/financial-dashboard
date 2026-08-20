from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from financial_dashboard.data.yahoo_finance_provider import (
    YahooFinanceConfig,
    YahooFinanceDailyProvider,
    YahooFinanceError,
)


TZ = "Europe/Istanbul"


def _history() -> pd.DataFrame:
    index = pd.DatetimeIndex(["2026-08-17", "2026-08-18"], tz=TZ)
    return pd.DataFrame(
        {
            "Open": [300.0, 304.0],
            "High": [305.0, 307.0],
            "Low": [298.0, 301.0],
            "Close": [304.0, 303.0],
            "Adj Close": [152.0, 151.5],
            "Volume": [1_000_000, 900_000],
        },
        index=index,
    )


class FakeTicker:
    def __init__(self, frame: pd.DataFrame, calls: list[dict[str, object]]) -> None:
        self.frame = frame
        self.calls = calls

    def history(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(kwargs)
        return self.frame.copy()


def test_maps_bist_symbol_and_normalizes_daily_close_time() -> None:
    symbols: list[str] = []
    calls: list[dict[str, object]] = []

    def factory(symbol: str) -> FakeTicker:
        symbols.append(symbol)
        return FakeTicker(_history(), calls)

    provider = YahooFinanceDailyProvider(ticker_factory=factory)
    frame = provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 18))

    assert symbols == ["THYAO.IS"]
    assert calls[0]["interval"] == "1d"
    assert calls[0]["auto_adjust"] is False
    assert calls[0]["actions"] is False
    assert calls[0]["raise_errors"] is True
    assert calls[0]["end"] == "2026-08-19"
    assert list(frame["close"]) == [304.0, 303.0]
    assert list(frame["source"].unique()) == ["YAHOO_FINANCE"]
    assert bool(frame["is_closed"].all())
    assert bool(frame["is_complete"].all())
    assert frame.iloc[0]["timestamp"] == pd.Timestamp("2026-08-17 18:10", tz=TZ)


def test_existing_is_suffix_is_not_duplicated() -> None:
    symbols: list[str] = []
    provider = YahooFinanceDailyProvider(
        ticker_factory=lambda symbol: symbols.append(symbol) or FakeTicker(_history(), [])
    )
    provider.get_ohlcv("THYAO.IS", "d", datetime(2026, 8, 17), datetime(2026, 8, 18))
    assert symbols == ["THYAO.IS"]


def test_adjusted_mode_scales_all_ohlc_consistently() -> None:
    provider = YahooFinanceDailyProvider(
        config=YahooFinanceConfig(adjust_prices=True),
        ticker_factory=lambda symbol: FakeTicker(_history().iloc[:1], []),
    )
    row = provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 17)).iloc[0]
    assert row["close"] == pytest.approx(152.0)
    assert row["open"] == pytest.approx(150.0)
    assert row["high"] == pytest.approx(152.5)
    assert row["low"] == pytest.approx(149.0)


def test_empty_history_returns_clean_provider_error() -> None:
    provider = YahooFinanceDailyProvider(
        ticker_factory=lambda symbol: FakeTicker(pd.DataFrame(), [])
    )
    with pytest.raises(YahooFinanceError, match="returned no daily data for THYAO.IS"):
        provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 18))


def test_library_exception_is_wrapped_without_traceback_contract() -> None:
    class BrokenTicker:
        def history(self, **kwargs: object) -> pd.DataFrame:
            raise RuntimeError("HTTP 429 too many requests")

    provider = YahooFinanceDailyProvider(ticker_factory=lambda symbol: BrokenTicker())
    with pytest.raises(
        YahooFinanceError,
        match=r"Yahoo Finance request failed for THYAO\.IS: HTTP 429 too many requests",
    ):
        provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 18))


def test_rejects_non_daily_timeframes() -> None:
    provider = YahooFinanceDailyProvider(ticker_factory=lambda symbol: FakeTicker(_history(), []))
    with pytest.raises(ValueError, match="only daily"):
        provider.get_ohlcv("THYAO", "1w", datetime(2026, 8, 17), datetime(2026, 8, 18))
