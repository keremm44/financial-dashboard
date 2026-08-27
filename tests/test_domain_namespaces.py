from __future__ import annotations


def test_domain_namespaces_import_without_side_effect_failures() -> None:
    from financial_dashboard.domains import (
        fvg_engulfing,
        ham,
        liquidity,
        order_block,
        pattern,
        stabil,
        structure,
        support_resistance,
        volatility,
        volume,
    )

    namespaces = (
        structure,
        support_resistance,
        pattern,
        liquidity,
        order_block,
        fvg_engulfing,
        volume,
        ham,
        volatility,
        stabil,
    )
    assert all(namespace.__name__.startswith("financial_dashboard.domains.") for namespace in namespaces)
