from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines.auction_engine import AuctionConfig, _reaction, build_profile


TZ = "Europe/Istanbul"


def _bar(i: int, *, low: float, high: float, close: float, volume: float = 1000.0) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=i),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _reference_rows(n: int = 24) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        center = 100.0 + ((i % 3) - 1) * 0.05
        rows.append(_bar(i, low=center - 0.80, high=center + 0.80, close=center))
    return rows


def _rows_for_dual_test(close_selector: str) -> tuple[list[dict], float, float]:
    config = AuctionConfig(timeframe="1h")
    reference = _reference_rows()
    ref_profile = build_profile(reference, config)
    assert ref_profile.valid
    assert ref_profile.vah_price is not None
    assert ref_profile.val_price is not None

    vah = ref_profile.vah_price
    val = ref_profile.val_price
    midpoint = (vah + val) * 0.5
    if close_selector == "upper":
        close = vah - (vah - val) * 0.10
    elif close_selector == "lower":
        close = val + (vah - val) * 0.10
    elif close_selector == "equal":
        close = midpoint
    else:
        raise AssertionError(close_selector)

    # First evidence bar stays inside value. The final bar touches both frozen
    # boundaries without making an excursion beyond either boundary, so the
    # state must be decided only by the Pine dual-test distance tie-break.
    evidence_1 = _bar(len(reference), low=midpoint - 0.05, high=midpoint + 0.05, close=midpoint)
    evidence_2 = _bar(len(reference) + 1, low=val, high=vah, close=close)
    return [*reference, evidence_1, evidence_2], vah, val


def test_dual_boundary_test_chooses_upper_when_close_is_nearer_vah():
    rows, vah, _ = _rows_for_dual_test("upper")
    reaction = _reaction(rows, AuctionConfig(timeframe="1h"), atr=1.0)
    assert reaction.state == "TEST_UP"
    assert reaction.reference_level == pytest.approx(vah)
    assert reaction.direction == 0


def test_dual_boundary_test_chooses_lower_when_close_is_nearer_val():
    rows, _, val = _rows_for_dual_test("lower")
    reaction = _reaction(rows, AuctionConfig(timeframe="1h"), atr=1.0)
    assert reaction.state == "TEST_DOWN"
    assert reaction.reference_level == pytest.approx(val)
    assert reaction.direction == 0


def test_dual_boundary_exact_distance_tie_prefers_upper_like_pine():
    rows, vah, _ = _rows_for_dual_test("equal")
    reaction = _reaction(rows, AuctionConfig(timeframe="1h"), atr=1.0)
    assert reaction.state == "TEST_UP"
    assert reaction.reference_level == pytest.approx(vah)
