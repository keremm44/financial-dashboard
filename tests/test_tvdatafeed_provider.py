from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider


class _Intervals:
    in_5_minute = "5"


class _Client:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = []

    def get_hist(self, **kwargs):
        self.calls.append(kwargs)
        return self.frame.copy()


def _raw(volumes=(100.0, 200.0, 300.0)) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            "2026-08-19 10:00:00",
            "2026-08-19 10:05:00",
            "2026-08-19 10:10:00",
        ],
        name="datetime",
    )
    return pd.DataFrame(
        {
            "symbol": ["BIST:THYAO"] * 3,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": list(volumes),
        },
        index=index,
    )


def test_provider_maps_tvdatafeed_to_canonical_ohlcv() -> None:
    client = _Client(_raw())
    provider = TvDatafeedProvider(client=client, interval_enum=_Intervals, max_bars=123)

    result = provider.get_ohlcv(
        "THYAO",
        "5m",
        datetime.fromisoformat("2026-08-19T10:00:00+03:00"),
        datetime.fromisoformat("2026-08-19T10:20:00+03:00"),
    )

    assert len(result) == 3
    assert result["symbol"].tolist() == ["THYAO"] * 3
    assert result["timeframe"].tolist() == ["5m"] * 3
    assert result["source"].tolist() == ["tvdatafeed"] * 3
    assert str(result["timestamp"].dt.tz) == "Europe/Istanbul"
    assert result["is_closed"].all()
    assert provider.last_volume_status == "VALID"
    assert client.calls[0]["exchange"] == "BIST"
    assert client.calls[0]["interval"] == "5"
    assert client.calls[0]["n_bars"] == 123


def test_provider_filters_requested_window() -> None:
    provider = TvDatafeedProvider(client=_Client(_raw()), interval_enum=_Intervals)

    result = provider.get_ohlcv(
        "THYAO",
        "5m",
        datetime.fromisoformat("2026-08-19T10:05:00+03:00"),
        datetime.fromisoformat("2026-08-19T10:15:00+03:00"),
    )

    assert result["timestamp"].dt.strftime("%H:%M").tolist() == ["10:05", "10:10"]


@pytest.mark.parametrize(
    ("volumes", "expected"),
    [
        ((0.0, 0.0, 0.0), "UNAVAILABLE"),
        ((100.0, 0.0, 200.0), "PARTIAL"),
        ((100.0, 200.0, 300.0), "VALID"),
    ],
)
def test_provider_classifies_volume_availability(volumes, expected) -> None:
    provider = TvDatafeedProvider(client=_Client(_raw(volumes)), interval_enum=_Intervals)

    provider.get_ohlcv(
        "THYAO",
        "5m",
        datetime.fromisoformat("2026-08-19T10:00:00+03:00"),
        datetime.fromisoformat("2026-08-19T10:20:00+03:00"),
    )

    assert provider.last_volume_status == expected


def test_provider_rejects_unknown_timeframe() -> None:
    provider = TvDatafeedProvider(client=_Client(_raw()), interval_enum=_Intervals)
    with pytest.raises(ValueError):
        provider.get_ohlcv(
            "THYAO",
            "7m",
            datetime.fromisoformat("2026-08-19T10:00:00+03:00"),
            datetime.fromisoformat("2026-08-19T10:20:00+03:00"),
        )
