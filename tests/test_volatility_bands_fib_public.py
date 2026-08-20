from financial_dashboard.engines import VolatilityBandsFibEngine


def test_public_volatility_engine_uses_exact_quality_facade() -> None:
    assert VolatilityBandsFibEngine.__module__ == "financial_dashboard.engines.volatility_bands_fib"
