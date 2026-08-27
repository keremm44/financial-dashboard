from __future__ import annotations

from financial_dashboard.decision.domain_runtime_contract import (
    DOMAIN_RUNTIME_CONTRACTS,
    DomainDependency,
    RuntimeOwner,
)


def test_every_decision_domain_has_an_explicit_runtime_contract() -> None:
    assert set(DOMAIN_RUNTIME_CONTRACTS) == {
        "structure",
        "support_resistance",
        "pattern",
        "ham",
        "volume",
        "volatility",
        "liquidity",
        "order_block",
        "fvg_engulfing",
        "stabil",
    }


def test_frozen_historical_outputs_are_never_future_revisable() -> None:
    assert all(
        contract.frozen_outputs_revisable is False
        for contract in DOMAIN_RUNTIME_CONTRACTS.values()
    )


def test_stateful_domains_have_checkpoint_owners() -> None:
    for contract in DOMAIN_RUNTIME_CONTRACTS.values():
        if contract.dependency in {
            DomainDependency.STATEFUL_INCREMENTAL,
            DomainDependency.ROLLING_STATEFUL,
        }:
            assert contract.checkpointed
            assert contract.owner in {RuntimeOwner.NATIVE, RuntimeOwner.SUPPORTING}


def test_supported_timeframes_match_runtime_policy() -> None:
    assert DOMAIN_RUNTIME_CONTRACTS["fvg_engulfing"].timeframes == ("1d", "4h", "2h")
    assert DOMAIN_RUNTIME_CONTRACTS["volatility"].timeframes == ("1d", "4h", "2h")
    assert DOMAIN_RUNTIME_CONTRACTS["stabil"].timeframes == ("1d",)
    for name in {
        "structure",
        "support_resistance",
        "pattern",
        "ham",
        "volume",
        "liquidity",
        "order_block",
    }:
        assert DOMAIN_RUNTIME_CONTRACTS[name].timeframes == ("1d", "4h", "2h", "1h", "30m")


def test_stabil_is_explicitly_causal_prefix_derived_not_fake_incremental() -> None:
    contract = DOMAIN_RUNTIME_CONTRACTS["stabil"]
    assert contract.dependency is DomainDependency.CAUSAL_PREFIX_DERIVED
    assert contract.owner is RuntimeOwner.DERIVED
    assert not contract.checkpointed
    assert contract.current_state_can_evolve
    assert not contract.frozen_outputs_revisable
