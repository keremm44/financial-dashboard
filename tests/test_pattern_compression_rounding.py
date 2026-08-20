from financial_dashboard.engines.pattern_compression_specialized import inclined_pennant_max_duration


def test_inclined_pennant_duration_uses_pine_rounding() -> None:
    assert inclined_pennant_max_duration(30) == 26
    assert inclined_pennant_max_duration(40) == 34
    assert inclined_pennant_max_duration(50) == 43
