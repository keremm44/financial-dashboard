from __future__ import annotations

from datetime import datetime
from urllib.error import URLError

import pandas as pd
import pytest

from financial_dashboard.data.eodhd_provider import EODHDConfig, EODHDError, EODHDProvider


TZ = "Europe/Istanbul"


def _payload() -> list[dict[str, object]]:
    return [
        {
            "date": "2026-08-17",
            "open": 300.0,
            "high": 305.0,
            "low": 298.0,
            "close": 304.0,
            "adjusted_close": 152.0,
            "volume": 1_000_000,
        },
        {
            "date": "2026-08-18",
            "open": 304.0,
            "high": 307.0,
            "low": 301.0,
            "close": 303.0,
            "adjusted_close": 151.5,
            "volume": 900_000,
        },
    ]


def test_requires_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(EODHDError, match="API token"):
        EODHDProvider()


def test_daily_request_maps_bist_symbol_and_normalizes_close_time() -> None:
    seen: list[str] = []

    def transport(url: str, timeout: float) -> object:
        seen.append(url)
        assert timeout == 20.0
        return _payload()

    provider = EODHDProvider("secret", transport=transport)
    frame = provider.get_ohlcv(
        "THYAO",
        "1d",
        datetime(2026, 8, 17),
        datetime(2026, 8, 18),
    )

    assert "/eod/THYAO.IS?" in seen[0]
    assert "api_token=secret" in seen[0]
    assert "period=d" in seen[0]
    assert "order=a" in seen[0]
    assert list(frame["close"]) == [304.0, 303.0]
    assert list(frame["source"].unique()) == ["EODHD"]
    assert bool(frame["is_closed"].all())
    assert bool(frame["is_complete"].all())
    assert frame.iloc[0]["timestamp"] == pd.Timestamp("2026-08-17 18:10", tz=TZ)


def test_existing_exchange_suffix_is_not_duplicated() -> None:
    seen: list[str] = []
    provider = EODHDProvider("secret", transport=lambda url, timeout: seen.append(url) or [])
    provider.get_ohlcv("THYAO.IS", "d", datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert "/eod/THYAO.IS?" in seen[0]
    assert "THYAO.IS.IS" not in seen[0]


def test_adjusted_price_mode_scales_all_ohlc_consistently() -> None:
    provider = EODHDProvider(
        "secret",
        config=EODHDConfig(adjust_prices=True),
        transport=lambda url, timeout: _payload()[:1],
    )
    frame = provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 17))
    row = frame.iloc[0]
    assert row["close"] == pytest.approx(152.0)
    assert row["open"] == pytest.approx(150.0)
    assert row["high"] == pytest.approx(152.5)
    assert row["low"] == pytest.approx(149.0)


def test_rejects_non_daily_timeframes() -> None:
    provider = EODHDProvider("secret", transport=lambda url, timeout: [])
    with pytest.raises(ValueError, match="only daily"):
        provider.get_ohlcv("THYAO", "1w", datetime(2026, 1, 1), datetime(2026, 1, 2))


def test_retries_transient_network_failure_without_exposing_token() -> None:
    attempts = 0

    def transport(url: str, timeout: float) -> object:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("temporary")
        return _payload()[:1]

    provider = EODHDProvider(
        "secret",
        config=EODHDConfig(max_attempts=3, retry_backoff_seconds=0.0),
        transport=transport,
        sleep=lambda seconds: None,
    )
    frame = provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert attempts == 3
    assert len(frame) == 1


def test_api_error_payload_is_rejected() -> None:
    provider = EODHDProvider("secret", transport=lambda url, timeout: {"message": "forbidden"})
    with pytest.raises(EODHDError, match="forbidden"):
        provider.get_ohlcv("THYAO", "1d", datetime(2026, 8, 17), datetime(2026, 8, 17))
