from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator

import pandas as pd
import yfinance as yf

from .provider import MarketDataProvider
from .schema import CANONICAL_COLUMNS, canonicalize_ohlcv


class YahooFinanceError(RuntimeError):
    """Raised when Yahoo Finance cannot satisfy the daily OHLCV contract."""


TickerFactory = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class YahooFinanceConfig:
    exchange_suffix: str = ".IS"
    timezone: str = "Europe/Istanbul"
    session_close: str = "18:10"
    timeout_seconds: float = 20.0
    adjust_prices: bool = False
    suppress_library_logs: bool = True


class YahooFinanceDailyProvider(MarketDataProvider):
    """Daily BIST OHLCV provider backed by Yahoo Finance via yfinance.

    Symbols such as ``THYAO`` are mapped to ``THYAO.IS``. Only daily bars are
    exposed intentionally; weekly context is derived locally from cached daily bars.
    """

    source_name = "YAHOO_FINANCE"

    def __init__(
        self,
        *,
        config: YahooFinanceConfig | None = None,
        ticker_factory: TickerFactory | None = None,
    ) -> None:
        self.config = config or YahooFinanceConfig()
        self._ticker_factory = ticker_factory or yf.Ticker

    def _ticker(self, symbol: str) -> str:
        raw = symbol.strip().upper()
        if not raw:
            raise ValueError("symbol must be non-empty")
        suffix = self.config.exchange_suffix.upper()
        return raw if raw.endswith(suffix) else f"{raw}{suffix}"

    @staticmethod
    def _date(value: datetime) -> str:
        return pd.Timestamp(value).date().isoformat()

    @contextmanager
    def _quiet_yfinance(self) -> Iterator[None]:
        logger = logging.getLogger("yfinance")
        old_level = logger.level
        old_disabled = logger.disabled
        if self.config.suppress_library_logs:
            logger.setLevel(logging.CRITICAL)
        try:
            yield
        finally:
            logger.setLevel(old_level)
            logger.disabled = old_disabled

    @staticmethod
    def _clean_error(exc: Exception) -> str:
        text = " ".join(str(exc).split())
        if not text:
            text = exc.__class__.__name__
        return text[:240]

    def _fetch_history(self, ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
        # yfinance treats `end` as exclusive. Add one calendar day so the requested
        # final trading date is eligible without introducing future bars downstream.
        end_exclusive = pd.Timestamp(end).date() + pd.Timedelta(days=1)
        try:
            with self._quiet_yfinance():
                history = self._ticker_factory(ticker).history(
                    start=self._date(start),
                    end=pd.Timestamp(end_exclusive).date().isoformat(),
                    interval="1d",
                    auto_adjust=False,
                    actions=False,
                    timeout=self.config.timeout_seconds,
                    raise_errors=True,
                )
        except Exception as exc:
            raise YahooFinanceError(
                f"Yahoo Finance request failed for {ticker}: {self._clean_error(exc)}"
            ) from exc
        if history is None or history.empty:
            raise YahooFinanceError(
                f"Yahoo Finance returned no daily data for {ticker} in the requested range"
            )
        return history.copy()

    def _normalize(self, frame: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise YahooFinanceError(
                f"Yahoo Finance response missing columns: {', '.join(missing)}"
            )

        dates = pd.to_datetime(frame.index, errors="raise")
        if getattr(dates, "tz", None) is not None:
            dates = dates.tz_convert(self.config.timezone).tz_localize(None)
        dates = pd.DatetimeIndex(dates).normalize()
        close_hour, close_minute = (int(part) for part in self.config.session_close.split(":", 1))
        timestamps = (
            dates
            + pd.Timedelta(hours=close_hour, minutes=close_minute)
        ).tz_localize(self.config.timezone, ambiguous="raise", nonexistent="raise")

        result = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": pd.to_numeric(frame["Open"], errors="raise").to_numpy(),
                "high": pd.to_numeric(frame["High"], errors="raise").to_numpy(),
                "low": pd.to_numeric(frame["Low"], errors="raise").to_numpy(),
                "close": pd.to_numeric(frame["Close"], errors="raise").to_numpy(),
                "volume": pd.to_numeric(frame["Volume"], errors="raise").to_numpy(),
            }
        )

        if self.config.adjust_prices and "Adj Close" in frame.columns:
            adjusted = pd.to_numeric(frame["Adj Close"], errors="coerce").reset_index(drop=True)
            raw_close = result["close"].replace(0.0, pd.NA)
            factor = (adjusted / raw_close).fillna(1.0)
            for column in ("open", "high", "low", "close"):
                result[column] = result[column] * factor

        result["symbol"] = symbol.strip().upper()
        result["timeframe"] = "1d"
        result["is_closed"] = True
        result["is_complete"] = True
        result["source"] = self.source_name
        return canonicalize_ohlcv(result, symbol=symbol, timeframe="1d", source=self.source_name)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        normalized_tf = timeframe.strip().lower()
        if normalized_tf not in {"1d", "d"}:
            raise ValueError("YahooFinanceDailyProvider exposes only daily bars; derive weekly locally")
        if pd.Timestamp(start) > pd.Timestamp(end):
            raise ValueError("start must be <= end")
        ticker = self._ticker(symbol)
        return self._normalize(self._fetch_history(ticker, start, end), symbol=symbol)
