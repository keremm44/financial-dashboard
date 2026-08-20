from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .provider import MarketDataProvider
from .schema import CANONICAL_COLUMNS, canonicalize_ohlcv


class EODHDError(RuntimeError):
    """Raised when EODHD cannot satisfy the provider contract."""


Transport = Callable[[str, float], object]
Sleep = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class EODHDConfig:
    exchange_code: str = "IS"
    base_url: str = "https://eodhd.com/api"
    timezone: str = "Europe/Istanbul"
    session_close: str = "18:10"
    timeout_seconds: float = 20.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 0.5
    adjust_prices: bool = False


class EODHDProvider(MarketDataProvider):
    """Daily BIST OHLCV provider backed by EODHD.

    The API token is resolved from the explicit ``api_token`` argument first and
    then from ``EODHD_API_KEY``. Only daily bars are exposed deliberately; weekly
    bars are derived locally so downstream behavior remains deterministic.
    """

    source_name = "EODHD"

    def __init__(
        self,
        api_token: str | None = None,
        *,
        config: EODHDConfig | None = None,
        transport: Transport | None = None,
        sleep: Sleep = time.sleep,
    ) -> None:
        token = (api_token or os.getenv("EODHD_API_KEY", "")).strip()
        if not token:
            raise EODHDError("EODHD API token is required via api_token or EODHD_API_KEY")
        self.api_token = token
        self.config = config or EODHDConfig()
        if self.config.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._transport = transport or self._default_transport
        self._sleep = sleep

    @staticmethod
    def _default_transport(url: str, timeout: float) -> object:
        request = Request(url, headers={"User-Agent": "financial-dashboard/0.1"})
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _ticker(self, symbol: str) -> str:
        raw = symbol.strip().upper()
        if not raw:
            raise ValueError("symbol must be non-empty")
        suffix = f".{self.config.exchange_code.upper()}"
        return raw if raw.endswith(suffix) else f"{raw}{suffix}"

    @staticmethod
    def _date(value: datetime) -> str:
        return pd.Timestamp(value).date().isoformat()

    def _build_url(self, symbol: str, start: datetime, end: datetime) -> str:
        query = urlencode(
            {
                "api_token": self.api_token,
                "from": self._date(start),
                "to": self._date(end),
                "period": "d",
                "order": "a",
                "fmt": "json",
            }
        )
        return f"{self.config.base_url.rstrip('/')}/eod/{self._ticker(symbol)}?{query}"

    def _request(self, url: str) -> object:
        last_error: Exception | None = None
        for attempt in range(self.config.max_attempts):
            try:
                return self._transport(url, self.config.timeout_seconds)
            except HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt + 1 >= self.config.max_attempts:
                    raise EODHDError(f"EODHD HTTP error {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 >= self.config.max_attempts:
                    raise EODHDError("EODHD network request failed") from exc
            self._sleep(self.config.retry_backoff_seconds * (2**attempt))
        raise EODHDError("EODHD request failed") from last_error

    def _normalize_payload(self, payload: object, *, symbol: str) -> pd.DataFrame:
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error") or payload.get("errors")
            raise EODHDError(f"EODHD API error: {message or payload}")
        if payload is None or payload == []:
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        if not isinstance(payload, list):
            raise EODHDError("Unexpected EODHD response shape")

        frame = pd.DataFrame(payload)
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise EODHDError(f"EODHD response missing columns: {', '.join(missing)}")

        dates = pd.to_datetime(frame["date"], errors="raise")
        close_hour, close_minute = (int(part) for part in self.config.session_close.split(":", 1))
        timestamps = dates + pd.Timedelta(hours=close_hour, minutes=close_minute)
        timestamps = timestamps.dt.tz_localize(self.config.timezone, ambiguous="raise", nonexistent="raise")

        result = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": pd.to_numeric(frame["open"], errors="raise"),
                "high": pd.to_numeric(frame["high"], errors="raise"),
                "low": pd.to_numeric(frame["low"], errors="raise"),
                "close": pd.to_numeric(frame["close"], errors="raise"),
                "volume": pd.to_numeric(frame["volume"], errors="raise"),
            }
        )

        if self.config.adjust_prices and "adjusted_close" in frame.columns:
            adjusted = pd.to_numeric(frame["adjusted_close"], errors="coerce")
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
            raise ValueError("EODHDProvider exposes only daily bars; derive weekly locally")
        if pd.Timestamp(start) > pd.Timestamp(end):
            raise ValueError("start must be <= end")
        payload = self._request(self._build_url(symbol, start, end))
        return self._normalize_payload(payload, symbol=symbol)
