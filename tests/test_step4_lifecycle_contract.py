import pytest

from financial_dashboard.decision.lifecycle import TradeLifecycleState
from financial_dashboard.decision.lifecycle_persistence import (
    CANONICAL_LIFECYCLE_CONTRACT_VERSION,
    TradeLifecycleCheckpoint,
    deserialize_trade_lifecycle_checkpoint,
    serialize_trade_lifecycle_checkpoint,
)


def test_previous_behavior_contract_cannot_resume_under_step4_policy():
    checkpoint = TradeLifecycleCheckpoint(
        symbol="TEST",
        state=TradeLifecycleState(),
        prefix_count=0,
        last_as_of=None,
        causal_prefix_digest="0" * 64,
        decision_config_digest="1" * 64,
    )
    payload = serialize_trade_lifecycle_checkpoint(checkpoint)
    payload["contract_version"] = CANONICAL_LIFECYCLE_CONTRACT_VERSION - 1

    with pytest.raises(ValueError, match="contract version mismatch"):
        deserialize_trade_lifecycle_checkpoint(payload, expected_symbol="TEST")
