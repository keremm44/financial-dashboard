from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum

import pytest

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
    normalize_context_data_quality,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _fact(**overrides: object) -> FactRef:
    values: dict[str, object] = {
        "domain": ContextDomain.MARKET_STRUCTURE,
        "fact_type": "BOS",
        "symbol": "ASELS",
        "timeframe": "1h",
        "native_id": "ms:1h:bos:42",
        "native_state": "CONFIRMED",
        "origin_time": NOW - timedelta(hours=2),
        "confirmed_at": NOW - timedelta(hours=1),
        "available_at": NOW,
        "lineage_id": "EVT:STRUCTURAL:1h:42",
        "causal_family": CausalFamily.STRUCTURAL_LEVEL,
        "source_family": SourceFamily.PRICE_GEOMETRY,
        "data_quality": ContextDataQuality.VALID,
    }
    values.update(overrides)
    return FactRef(**values)  # type: ignore[arg-type]


def test_fact_ref_separates_confirmation_from_availability() -> None:
    confirmed = _fact()
    candidate = _fact(confirmed_at=None, lineage_id=None)

    assert confirmed.is_confirmed is True
    assert candidate.is_confirmed is False
    assert confirmed.is_available_at(NOW) is True
    assert confirmed.is_available_at(NOW - timedelta(microseconds=1)) is False


def test_unknown_lineage_is_explicit_none_not_native_identity() -> None:
    fact = _fact(lineage_id=None)
    assert fact.lineage_id is None
    assert fact.native_id == "ms:1h:bos:42"


def test_fact_ref_rejects_empty_required_identity_and_empty_optional_lineage() -> None:
    with pytest.raises(ValueError, match="native_id"):
        _fact(native_id="")
    with pytest.raises(ValueError, match="lineage_id"):
        _fact(lineage_id="   ")


def test_fact_ref_requires_known_origin_and_available_time() -> None:
    with pytest.raises(ValueError, match="origin_time"):
        _fact(origin_time=None)
    with pytest.raises(ValueError, match="available_at"):
        _fact(available_at=None)


def test_fact_ref_has_deterministic_domain_identity_key() -> None:
    fact = _fact()
    assert fact.deterministic_key == (
        "MARKET_STRUCTURE",
        "ASELS",
        "1h",
        "BOS",
        "ms:1h:bos:42",
    )


class ExampleNativeQuality(StrEnum):
    GOOD = "DATA_OK"
    WARM = "WARMUP"
    GAP = "SOURCE_GAP"
    TAIL = "INCOMPLETE_TAIL"
    UNSUPPORTED = "UNSUPPORTED_TIMEFRAME"
    BAD = "DATA_INVALID"


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        (ExampleNativeQuality.GOOD, ContextDataQuality.VALID),
        (ExampleNativeQuality.WARM, ContextDataQuality.WARMING_UP),
        (ExampleNativeQuality.GAP, ContextDataQuality.DATA_LIMITED),
        (ExampleNativeQuality.TAIL, ContextDataQuality.INCOMPLETE),
        (ExampleNativeQuality.UNSUPPORTED, ContextDataQuality.UNSUPPORTED_TIMEFRAME),
        (ExampleNativeQuality.BAD, ContextDataQuality.UNAVAILABLE),
        ("LOW_PARTICIPATION", ContextDataQuality.VALID),
        (ContextDataQuality.VALID, ContextDataQuality.VALID),
    ],
)
def test_native_quality_normalization_is_explicit(native: object, expected: ContextDataQuality) -> None:
    assert normalize_context_data_quality(native) is expected


def test_unknown_native_quality_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported native data-quality"):
        normalize_context_data_quality("MYSTERY_QUALITY")
