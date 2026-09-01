from __future__ import annotations

from hashlib import sha256

from financial_dashboard.decision.engine import (
    DECISION_CONTRACT_VERSION,
    DecisionEngineConfig,
    _permission_policy,
    _timeframe_policy,
)
from financial_dashboard.decision.horizon_profile import (
    LONG_TERM_EVALUATION_PROFILE,
    SHORT_TERM_EVALUATION_PROFILE,
    horizon_evaluation_profile,
)
from financial_dashboard.decision.lifecycle_persistence import decision_config_digest
from financial_dashboard.decision.structural import DecisionHorizon


def test_horizon_profiles_are_the_single_typed_decision_role_contract() -> None:
    lt = horizon_evaluation_profile(DecisionHorizon.LONG_TERM)
    st = horizon_evaluation_profile(DecisionHorizon.SHORT_TERM)

    assert lt is LONG_TERM_EVALUATION_PROFILE
    assert st is SHORT_TERM_EVALUATION_PROFILE

    assert lt.structural_authority_timeframe == "1d"
    assert lt.secondary_structural_timeframe == "4h"
    assert lt.permission_anchor_timeframe == "1d"
    assert lt.permission_context_timeframes == ("4h", "2h", "1h")
    assert lt.reaction_timeframes == ("1d", "4h", "2h", "1h")
    assert lt.participation_timeframe == "4h"
    assert lt.environment_timeframe == "4h"
    assert lt.timing_timeframe == "1h"
    assert lt.execution_timeframe == "30m"

    assert st.structural_authority_timeframe == "1h"
    assert st.secondary_structural_timeframe is None
    assert st.permission_anchor_timeframe == "1h"
    assert st.permission_context_timeframes == ("30m",)
    assert st.reaction_timeframes == ("4h", "2h", "1h", "30m")
    assert st.participation_timeframe == "1h"
    assert st.environment_timeframe == "4h"
    assert st.timing_timeframe == "30m"
    assert st.execution_timeframe == "30m"


def test_existing_engine_helpers_delegate_to_the_typed_profiles() -> None:
    assert _timeframe_policy(DecisionHorizon.LONG_TERM) == (
        LONG_TERM_EVALUATION_PROFILE.reaction_timeframes,
        LONG_TERM_EVALUATION_PROFILE.participation_timeframe,
        LONG_TERM_EVALUATION_PROFILE.environment_timeframe,
        LONG_TERM_EVALUATION_PROFILE.timing_timeframe,
    )
    assert _timeframe_policy(DecisionHorizon.SHORT_TERM) == (
        SHORT_TERM_EVALUATION_PROFILE.reaction_timeframes,
        SHORT_TERM_EVALUATION_PROFILE.participation_timeframe,
        SHORT_TERM_EVALUATION_PROFILE.environment_timeframe,
        SHORT_TERM_EVALUATION_PROFILE.timing_timeframe,
    )
    assert _permission_policy(DecisionHorizon.LONG_TERM) == (
        LONG_TERM_EVALUATION_PROFILE.permission_anchor_timeframe,
        LONG_TERM_EVALUATION_PROFILE.permission_context_timeframes,
    )
    assert _permission_policy(DecisionHorizon.SHORT_TERM) == (
        SHORT_TERM_EVALUATION_PROFILE.permission_anchor_timeframe,
        SHORT_TERM_EVALUATION_PROFILE.permission_context_timeframes,
    )


def test_decision_contract_version_is_part_of_checkpoint_config_digest() -> None:
    config = DecisionEngineConfig()
    assert config.decision_contract_version == DECISION_CONTRACT_VERSION

    current_digest = decision_config_digest(config)
    legacy_repr = repr(config).replace(
        f", decision_contract_version={DECISION_CONTRACT_VERSION}",
        "",
    )
    legacy_digest = sha256(legacy_repr.encode("utf-8")).hexdigest()

    assert current_digest != legacy_digest


def test_turn5b_arbiter_policy_semantics_are_contract_version_three() -> None:
    assert DECISION_CONTRACT_VERSION == 3
    assert DecisionEngineConfig().decision_contract_version == 3


def test_execution_timeframe_still_matches_both_horizon_profiles() -> None:
    config = DecisionEngineConfig()
    assert config.execution_timeframe == LONG_TERM_EVALUATION_PROFILE.execution_timeframe
    assert config.execution_timeframe == SHORT_TERM_EVALUATION_PROFILE.execution_timeframe
