from __future__ import annotations

from financial_dashboard import decision_input
from financial_dashboard.decision import market_state as market_state_module


def _snapshot_for_market_state():
    snapshot = object.__new__(decision_input.DecisionInputSnapshot)
    object.__setattr__(snapshot, "structure", object())
    object.__setattr__(snapshot, "stabil_support", object())
    object.__setattr__(snapshot, "volatility_environment", object())
    object.__setattr__(snapshot, "participation_behavior", object())
    object.__setattr__(snapshot, "_market_state", None)
    return snapshot


def test_decision_input_market_state_is_built_once_and_reused(monkeypatch) -> None:
    snapshot = _snapshot_for_market_state()
    sentinel = object()
    calls = []

    def fake_build(structure, *, stabil=None, volatility=None, participation=None):
        calls.append((structure, stabil, volatility, participation))
        return sentinel

    monkeypatch.setattr(market_state_module, "build_market_state", fake_build)

    first = snapshot.market_state
    second = snapshot.market_state

    assert first is sentinel
    assert second is sentinel
    assert first is second
    assert calls == [
        (
            snapshot.structure,
            snapshot.stabil_support,
            snapshot.volatility_environment,
            snapshot.participation_behavior,
        )
    ]


def test_market_state_cache_does_not_change_snapshot_equality(monkeypatch) -> None:
    left = _snapshot_for_market_state()
    right = _snapshot_for_market_state()
    sentinel = object()

    monkeypatch.setattr(
        market_state_module,
        "build_market_state",
        lambda structure, *, stabil=None, volatility=None, participation=None: sentinel,
    )

    assert left == right
    assert left.market_state is sentinel
    assert left == right
