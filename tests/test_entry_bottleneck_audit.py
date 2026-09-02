from types import SimpleNamespace

from financial_dashboard.decision.entry_bottleneck_audit import (
    EntryBottleneckFamily,
    attribute_entry_bottlenecks,
    diagnostic_episode_key,
)
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage


def _scenario(*, waiting=(), blockers=(), stage=ScenarioStage.DEVELOPING, target="T1"):
    return SimpleNamespace(
        presence=ScenarioPresence.PRESENT,
        stage=stage,
        waiting_for=tuple(waiting),
        blockers=tuple(blockers),
        kind=ScenarioKind.SHORT_TERM_STANDALONE,
        active_target_identity=target,
    )


def test_attribute_entry_bottlenecks_reports_overlap_without_policy_change() -> None:
    result = attribute_entry_bottlenecks(
        _scenario(
            waiting=(
                "SETUP_TRIGGER",
                "PERMISSION_SCOPE_SIDE_TO_RECONCILE",
                "MORE_DIRECTIONAL_ROOM",
            )
        )
    )

    assert result.families == (
        EntryBottleneckFamily.OPPORTUNITY,
        EntryBottleneckFamily.PERMISSION,
        EntryBottleneckFamily.TIMING,
    )
    assert result.label == "OPPORTUNITY+PERMISSION+TIMING"
    assert not result.is_single_family


def test_attribute_entry_bottlenecks_identifies_timing_only() -> None:
    result = attribute_entry_bottlenecks(
        _scenario(waiting=("SETUP_TRIGGER_CONFIRMATION",))
    )

    assert result.families == (EntryBottleneckFamily.TIMING,)
    assert result.label == "TIMING"
    assert result.is_single_family


def test_attribution_uses_canonical_gate_registry_ownership() -> None:
    structural_context = attribute_entry_bottlenecks(
        _scenario(waiting=("CONTEXT_CONFLICT_TO_RECONCILE",))
    )
    structure = attribute_entry_bottlenecks(
        _scenario(waiting=("LOWER_HORIZON_COUNTER_MOVE_TO_RESOLVE",))
    )

    assert structural_context.families == (EntryBottleneckFamily.STRUCTURE,)
    assert structure.families == (EntryBottleneckFamily.STRUCTURE,)


def test_qualified_scenario_has_no_diagnostic_bottleneck() -> None:
    result = attribute_entry_bottlenecks(
        _scenario(stage=ScenarioStage.QUALIFIED, waiting=("SETUP_TRIGGER",))
    )

    assert result.families == ()
    assert result.tokens == ()
    assert result.label == "NONE"


def test_non_present_scenario_is_outside_present_funnel() -> None:
    scenario = _scenario()
    scenario.presence = ScenarioPresence.ABSENT

    result = attribute_entry_bottlenecks(scenario)

    assert result.families == ()
    assert result.tokens == ()


def test_episode_key_is_target_context_proxy_only() -> None:
    scenario = _scenario(target="target-42")

    assert diagnostic_episode_key(scenario) == (
        ScenarioKind.SHORT_TERM_STANDALONE.value,
        "target-42",
    )
    assert diagnostic_episode_key(_scenario(target=None)) is None
