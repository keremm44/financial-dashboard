import pandas as pd

from financial_dashboard.data.quality import DataQualityStatus, assess_ohlcv_quality


def _valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-08-19 10:00:00", periods=3, freq="1min", tz="Europe/Istanbul"),
            "open": [100, 101, 102],
            "high": [102, 103, 104],
            "low": [99, 100, 101],
            "close": [101, 102, 103],
            "volume": [10, 20, 30],
            "is_complete": [True, True, True],
        }
    )


def test_quality_accepts_valid_data() -> None:
    report = assess_ohlcv_quality(_valid_frame())
    assert report.status == DataQualityStatus.OK
    assert report.can_decide is True


def test_quality_rejects_duplicate_timestamp() -> None:
    frame = _valid_frame()
    frame.loc[1, "timestamp"] = frame.loc[0, "timestamp"]
    report = assess_ohlcv_quality(frame)
    assert report.status == DataQualityStatus.INVALID
    assert "Duplicate timestamps" in report.errors


def test_quality_rejects_invalid_high() -> None:
    frame = _valid_frame()
    frame.loc[0, "high"] = 98
    report = assess_ohlcv_quality(frame)
    assert report.status == DataQualityStatus.INVALID
    assert "High is below another OHLC value" in report.errors


def test_quality_marks_zero_volume_limited() -> None:
    frame = _valid_frame()
    frame["volume"] = 0
    report = assess_ohlcv_quality(frame)
    assert report.status == DataQualityStatus.LIMITED
    assert report.can_decide is True
