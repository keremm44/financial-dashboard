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


class YahooIntradayError(RuntimeError):
    """Raised when Yahoo Finance cannot satisfy the intraday OHLCV contract."""


TickerFactory = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class YahooIntradayConfig:
    exchange_suffix: str = ".IS"
    timezone: str = "Europe/Istanbul"
    timeout_seconds: float = 20.0
    suppress_library_logs: bool = True


class YahooFinanceIntradayProvider(MarketDataProvider):
    """BIST 5m fallback provider backed by Yahoo Finance via yfinance.

    This provider is intentionally narrow: it exposes only 5m bars and is designed
    to sit behind TradingView in HybridBist5mProvider. It never decides which Yahoo
    bars are accepted; the hybrid layer owns gap detection and primary-source priority.
    """

    source_name = "YAHOO_FALLBACK"

    def __init__(
        self,
        *,
        config: YahooIntradayConfig | None = None,
        ticker_factory: TickerFactory | None = None,
    ) -> None:
        self.config = config or YahooIntradayConfig()
        self._ticker_factory = ticker_factory or yf.Ticker

    def _ticker(self, symbol: str) -> str:
        raw = symbol.strip().upper()
        if not raw:
            raise ValueError("symbol must be non-empty")
        suffix = self.config.exchange_suffix.upper()
        return raw if raw.endswith(suffix) else f"{raw}{suffix}"

    @contextmanager
    def _quiet_yfinance(self) -> Iterator[None]:
        logger = logging.getLogger("yfinance")
        old_level = logger.level
        if self.config.suppress_library_logs:
            logger.setLevel(logging.CRITICAL)
        try:
            yield
        finally:
            logger.setLevel(old_level)

    @staticmethod
    def _clean_error(exc: Exception) -> str:
        text = " ".join(str(exc).split()) or exc.__class__.__name__
        return text[:240]

    def _fetch_history(self, ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            with self._quiet_yfinance():
                history = self._ticker_factory(ticker).history(
                    start=start,
                    end=pd.Timestamp(end) + pd.Timedelta(minutes=5),
                    interval="5m",
                    auto_adjust=False,
                    actions=False,
                    timeout=self.config.timeout_seconds,
                    raise_errors=True,
                )
        except Exception as exc:
            raise YahooIntradayError(
                f"Yahoo 5m request failed for {ticker}: {self._clean_error(exc)}"
            ) from exc
        if history is None or history.empty:
            return pd.DataFrame()
        return history.copy()

    def _normalize(self, frame: pd.DataFrame, *, symbol: str, end: datetime) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise YahooIntradayError(
                f"Yahoo 5m response missing columns: {', '.join(missing)}"
            )

        timestamps = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
        if timestamps.tz is None:
            timestamps = timestamps.tz_localize(self.config.timezone)
        else:
            timestamps = timestamps.tz_convert(self.config.timezone)

        end_ts = pd.Timestamp(end)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize(self.config.timezone)
        else:
            end_ts = end_ts.tz_convert(self.config.timezone)

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
        result["symbol"] = symbol.strip().upper()
        result["timeframe"] = "5m"
        result["is_closed"] = result["timestamp"] + pd.Timedelta(minutes=5) <= end_ts
        result["is_complete"] = True
        result["source"] = self.source_name
        return canonicalize_ohlcv(
            result,
            symbol=symbol,
            timeframe="5m",
            source=self.source_name,
            default_is_closed=False,
        )

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        if timeframe.strip().lower() != "5m":
            raise ValueError("YahooFinanceIntradayProvider exposes only 5m bars")
        if pd.Timestamp(start) > pd.Timestamp(end):
            raise ValueError("start must be <= end")
        ticker = self._ticker(symbol)
        return self._normalize(self._fetch_history(ticker, start, end), symbol=symbol, end=end)
