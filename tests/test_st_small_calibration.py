from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.lifecycle_persistence import decision_config_digest
from financial_dashboard.decision.lifecycle_replay import _compose_open_exit
from financial_dashboard.decision.st_calibration import (
    STExitCalibration,
    STHealthyBaseReactionConfidence,
    compare_st_calibration_reports,
)
from financial_dashboard.decision.st_harvest import _buyer_reaction_at_defense


NOW = pd.Timestamp("2026-01-05 14:00")


def _ref(native_id: str) -> FactRef:
    return FactRef(
        domain=ContextDomain.FVG,
        fact_type="TEST_FACT",
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state="TEST",
        origin_time=NOW,
        confirmed_at=NOW,
        available_at=NOW,
        lineage_id=native_id,
        causal_family=CausalFamily.IMPULSE,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _snapshot(*, confirmed: bool):
    item = SimpleNamespace(
        ref=_ref("reaction"),
        identity="base:reaction",
        direction=1,
        lower_boundary=110.0,
        upper_boundary=111.0,
        failed_reaction=False,
        full_fill=False,
        invalid=False,
        reaction_confirmed=confirmed,
        first_test_index=20,
    )
    return SimpleNamespace(
        as_of=NOW,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=SimpleNamespace(fvg=(item,)),
    )


def _reaction(snapshot, confidence):
    return _buyer_reaction_at_defense(
        snapshot,
        timeframe="1h",
        defense_low=110.0,
        defense_high=111.0,
        confidence=confidence,
    )


def test_default_calibration_preserves_developing_or_confirmed_healthy_base_reaction():
    config = DecisionEngineConfig()

    assert config.st_exit_calibration == STExitCalibration()
    assert config.st_exit_calibration.healthy_base_reaction_confidence is (
        STHealthyBaseReactionConfidence.DEVELOPING_OR_CONFIRMED
    )
    alive, evidence, _ = _reaction(
        _snapshot(confirmed=False),
        config.st_exit_calibration.healthy_base_reaction_confidence,
    )
    assert alive is True
    assert any("DEVELOPING" in item for item in evidence)


def test_confirmed_only_candidate_rejects_developing_reaction_but_accepts_confirmation():
    confidence = STHealthyBaseReactionConfidence.CONFIRMED_ONLY

    developing, developing_evidence, _ = _reaction(_snapshot(confirmed=False), confidence)
    confirmed, confirmed_evidence, _ = _reaction(_snapshot(confirmed=True), confidence)

    assert developing is False
    assert any("DEVELOPING" in item for item in developing_evidence)
    assert confirmed is True
    assert any("CONFIRMED" in item for item in confirmed_evidence)


def test_calibration_change_changes_checkpoint_decision_config_digest():
    baseline = DecisionEngineConfig()
    candidate = DecisionEngineConfig(
        st_exit_calibration=STExitCalibration(
            healthy_base_reaction_confidence=STHealthyBaseReactionConfidence.CONFIRMED_ONLY
        )
    )

    assert decision_config_digest(baseline) != decision_config_digest(candidate)


def test_explicit_calibration_is_forwarded_to_open_exit_composition():
    candidate = DecisionEngineConfig(
        st_exit_calibration=STExitCalibration(
            healthy_base_reaction_confidence=STHealthyBaseReactionConfidence.CONFIRMED_ONLY
        )
    )
    sentinel = object()

    class _ConfigAwareSnapshot:
        def __init__(self):
            self.seen_config = None

        def position_exit_decision(self, state, *, config=None, execution_event=None):
            self.seen_config = config
            return sentinel

    snapshot = _ConfigAwareSnapshot()
    result = _compose_open_exit(
        snapshot,
        SimpleNamespace(),
        config=candidate,
        execution_event=None,
    )

    assert result is sentinel
    assert snapshot.seen_config is candidate


def _report(**overrides):
    defaults = dict(
        premature_harvest_candidates=1,
        strong_continuation_hold_rows=3,
        healthy_base_hold_rows=2,
        mean_harvest_idle_seconds=1800.0,
        mean_protective_delay_seconds=0.0,
        same_movement_blocks=4,
        novel_setups_executed=2,
    )
    defaults.update(overrides)
    return SimpleNamespace(
        source="CANONICAL",
        production_performance=True,
        metrics=SimpleNamespace(**defaults),
    )


def test_calibration_comparison_pairs_early_and_late_behavior_metrics():
    baseline = _report()
    candidate = _report(
        premature_harvest_candidates=2,
        strong_continuation_hold_rows=2,
        healthy_base_hold_rows=1,
        mean_harvest_idle_seconds=900.0,
        mean_protective_delay_seconds=60.0,
    )

    comparison = compare_st_calibration_reports(baseline, candidate)

    assert comparison.premature_harvest_delta == 1
    assert comparison.strong_continuation_hold_delta == -1
    assert comparison.healthy_base_hold_delta == -1
    assert comparison.harvest_idle_seconds_delta == -900.0
    assert comparison.protective_delay_seconds_delta == 60.0


def test_calibration_comparison_rejects_proxy_or_legacy_as_production_performance():
    proxy = SimpleNamespace(
        source="CANONICAL_READINESS_PROXY",
        production_performance=False,
        metrics=_report().metrics,
    )

    with pytest.raises(ValueError, match="canonical validation reports"):
        compare_st_calibration_reports(_report(), proxy)
