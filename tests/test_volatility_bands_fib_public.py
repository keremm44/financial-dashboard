import pandas as pd

from financial_dashboard.engines import BandState, VolatilityBandsFibEngine, VolatilityState


def test_public_volatility_engine_uses_exact_quality_facade() -> None:
    assert VolatilityBandsFibEngine.__module__ == "financial_dashboard.engines.volatility_bands_fib"


def test_exact_stress_quality_path_is_finite_and_bounded() -> None:
    engine = VolatilityBandsFibEngine()
    rows = []
    for i in range(130):
        close = 100.0 + i * 0.08 + (0.25 if i % 4 == 0 else 0.0)
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="Europe/Istanbul") + pd.Timedelta(hours=2 * i),
                "open": close - 0.15,
                "high": close + 0.55,
                "low": close - 0.60,
                "close": close,
                "volume": 1000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    engine._rows = rows
    for state in (BandState.UPPER_WEAKENING, BandState.LOWER_WEAKENING, BandState.UPPER_MEAN_REVERSION, BandState.LOWER_MEAN_REVERSION):
        quality = engine._band_quality_exact(state, VolatilityState.BALANCED)
        assert 0.0 <= quality <= 100.0
