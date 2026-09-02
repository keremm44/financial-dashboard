from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.lifecycle import PositionState
from financial_dashboard.decision.lifecycle_replay import CanonicalLifecycleReplayResult
from financial_dashboard.decision.st_behavior_validation import (
    build_st_behavior_validation_bundle,
    summarize_legacy_behavior,
    validate_st_canonical_behavior,
    validate_st_readiness_proxy_behavior,
)
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.structural import DecisionHorizon


T0 = pd.Timestamp("2026-01-05 10:00")
T1 = pd.Timestamp("2026-01-05 10:30")
T2 = pd.Timestamp("2026-01-05 11:00")
T3 = pd.Timestamp("2026-01-05 11:30")
T4 = pd.Timestamp("2026-01-05 12:00")


def _history(count=0):
    return SimpleNamespace(progress_events=tuple(range(count)))


def _flat(*, closed_movement=None, closed_exit=None):
    return SimpleNamespace(
        position=PositionState.FLAT,
        trade_id=None,
        entry_metadata=None,
        st_economic_history=_history(),
        last_closed_st_movement=closed_movement,
        last_closed_st_exit=closed_exit,
    )


def _open(trade_id="trade:1", *, progress=0, entry_at=T0, entry_price=100.0):
    return SimpleNamespace(
        position=PositionState.OPEN,
        trade_id=trade_id,
        entry_metadata=SimpleNamespace(
            entry_horizon=DecisionHorizon.SHORT_TERM,
            entry_as_of=entry_at,
            entry_price=entry_price,
        ),
        st_economic_history=_history(progress),
        last_closed_st_movement=None,
        last_closed_st_exit=None,
    )


def _entry(action, *, reasons=(), waiting=()):
    return SimpleNamespace(action=action, reasons=tuple(reasons), waiting_for=tuple(waiting))


def _exit(family=None, *, hold_state=None):
    reasons = () if hold_state is None else (f"ST_CANONICAL_ECONOMIC_HOLD:{hold_state}",)
    return SimpleNamespace(economic_exit_family=family, reasons=reasons)


def _row(as_of, price, previous, current, action, *, entry=None, exit_decision=None, proxy=False):
    return SimpleNamespace(
        snapshot=SimpleNamespace(as_of=as_of, current_price=price),
        previous_state=previous,
        current_state=current,
        action=action,
        entry_decision=entry,
        exit_decision=exit_decision,
        execution_proxy_used=proxy,
    )


def _replay(rows, initial, final):
    return CanonicalLifecycleReplayResult(initial_state=initial, final_state=final, rows=tuple(rows))


def _harvest_replay():
    flat0 = _flat()
    open0 = _open(progress=0)
    open1 = _open(progress=1)
    open2 = _open(progress=1)
    closed = SimpleNamespace(family=STExitFamily.PROFIT_HARVEST)
    flat1 = _flat(closed_exit=closed)
    rows = (
        _row(T0, 100.0, flat0, open0, DecisionAction.BUY, entry=_entry(DecisionAction.BUY)),
        _row(
            T1,
            110.0,
            open0,
            open1,
            DecisionAction.HOLD,
            exit_decision=_exit(hold_state="HOLD_PROGRESS"),
        ),
        _row(
            T2,
            112.0,
            open1,
            open2,
            DecisionAction.HOLD,
            exit_decision=_exit(STExitFamily.PROFIT_HARVEST),
        ),
        _row(
            T3,
            108.0,
            open2,
            flat1,
            DecisionAction.SELL,
            exit_decision=_exit(STExitFamily.PROFIT_HARVEST),
        ),
    )
    return _replay(rows, flat0, flat1), rows


def test_canonical_report_measures_carry_harvest_mfe_giveback_and_dead_capital_without_policy_rules():
    replay, _ = _harvest_replay()

    report = validate_st_canonical_behavior(replay)

    assert report.source == "CANONICAL"
    assert report.production_performance is True
    assert report.proxy_row_count == 0
    assert report.metrics.completed_trade_count == 1
    assert report.metrics.profit_harvest_count == 1
    assert report.metrics.protective_exit_count == 0
    assert report.metrics.strong_continuation_hold_rows == 1
    assert report.metrics.premature_harvest_candidates == 0
    assert report.metrics.mean_harvest_idle_seconds == 30 * 60

    trade = report.trades[0]
    assert trade.entry_price == 100.0
    assert trade.peak_price == 112.0
    assert trade.exit_price == 108.0
    assert trade.mfe_absolute == 12.0
    assert trade.mfe_return == pytest.approx(0.12)
    assert trade.realized_return == pytest.approx(0.08)
    assert trade.giveback_absolute == 4.0
    assert trade.giveback_from_peak_fraction == pytest.approx(4.0 / 12.0)
    assert trade.holding_seconds == 90 * 60


def test_progress_on_same_row_as_harvest_is_reported_as_premature_harvest_candidate():
    flat0 = _flat()
    open0 = _open(progress=0)
    open1 = _open(progress=1)
    flat1 = _flat(closed_exit=SimpleNamespace(family=STExitFamily.PROFIT_HARVEST))
    replay = _replay(
        (
            _row(T0, 100.0, flat0, open0, DecisionAction.BUY, entry=_entry(DecisionAction.BUY)),
            _row(
                T1,
                105.0,
                open0,
                flat1,
                DecisionAction.SELL,
                exit_decision=_exit(STExitFamily.PROFIT_HARVEST),
            ),
        ),
        flat0,
        flat1,
    )
    replay.rows[1].current_state.st_economic_history = open1.st_economic_history

    report = validate_st_canonical_behavior(replay)
    assert report.metrics.premature_harvest_candidates == 1
    assert report.trades[0].premature_harvest_candidate is True


def test_protective_delay_is_measured_from_canonical_terminal_family_to_sell():
    flat0 = _flat()
    open0 = _open()
    flat1 = _flat(closed_exit=SimpleNamespace(family=STExitFamily.PROTECTIVE_EXIT))
    replay = _replay(
        (
            _row(T0, 100.0, flat0, open0, DecisionAction.BUY, entry=_entry(DecisionAction.BUY)),
            _row(
                T1,
                96.0,
                open0,
                flat1,
                DecisionAction.SELL,
                exit_decision=_exit(STExitFamily.PROTECTIVE_EXIT),
            ),
        ),
        flat0,
        flat1,
    )

    report = validate_st_canonical_behavior(replay)
    assert report.metrics.protective_exit_count == 1
    assert report.metrics.mean_protective_delay_seconds == 0.0
    assert report.trades[0].protective_delay_seconds == 0.0


def test_healthy_base_carry_and_next_row_exit_are_paired_as_review_candidate_not_policy_truth():
    flat0 = _flat()
    open0 = _open()
    open1 = _open()
    flat1 = _flat(closed_exit=SimpleNamespace(family=STExitFamily.PROTECTIVE_EXIT))
    replay = _replay(
        (
            _row(T0, 100.0, flat0, open0, DecisionAction.BUY, entry=_entry(DecisionAction.BUY)),
            _row(
                T1,
                102.0,
                open0,
                open1,
                DecisionAction.HOLD,
                exit_decision=_exit(hold_state="HOLD_HEALTHY_BASE"),
            ),
            _row(
                T2,
                98.0,
                open1,
                flat1,
                DecisionAction.SELL,
                exit_decision=_exit(STExitFamily.PROTECTIVE_EXIT),
            ),
        ),
        flat0,
        flat1,
    )

    report = validate_st_canonical_behavior(replay)
    assert report.metrics.healthy_base_hold_rows == 1
    assert report.metrics.exit_after_healthy_correction_candidates == 1
    assert report.trades[0].exit_after_healthy_correction_candidate is True


def test_reentry_churn_and_new_setup_are_measured_together():
    prior = SimpleNamespace(trade_id="old")
    flat0 = _flat(closed_movement=prior)
    flat1 = _flat(closed_movement=prior)
    flat2 = _flat(closed_movement=prior)
    open_new = _open(trade_id="trade:new", entry_at=T2, entry_price=105.0)
    replay = _replay(
        (
            _row(
                T0,
                101.0,
                flat0,
                flat1,
                DecisionAction.WAIT,
                entry=_entry(
                    DecisionAction.WAIT,
                    reasons=("ST_REENTRY_SAME_ECONOMIC_MOVEMENT",),
                    waiting=("ST_REENTRY_NOVELTY_TO_ESTABLISH",),
                ),
            ),
            _row(
                T1,
                103.0,
                flat1,
                flat2,
                DecisionAction.READY,
                entry=_entry(
                    DecisionAction.READY,
                    reasons=("ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED",),
                    waiting=("FRESH_EXECUTION_EVENT",),
                ),
            ),
            _row(
                T2,
                105.0,
                flat2,
                open_new,
                DecisionAction.BUY,
                entry=_entry(
                    DecisionAction.BUY,
                    reasons=("ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED",),
                ),
            ),
        ),
        flat0,
        open_new,
    )

    metrics = validate_st_canonical_behavior(replay).metrics
    assert metrics.same_movement_blocks == 1
    assert metrics.novel_setups_released == 2
    assert metrics.novel_setups_waiting_execution == 1
    assert metrics.novel_setups_executed == 1
    assert metrics.novelty_policy_contradictions == 0
    assert metrics.st_reentries_without_novelty == 0


def test_st_reentry_buy_without_novel_reason_is_visible_as_churn_regression():
    prior = SimpleNamespace(trade_id="old")
    flat0 = _flat(closed_movement=prior)
    open1 = _open(trade_id="trade:bad")
    replay = _replay(
        (
            _row(
                T1,
                101.0,
                flat0,
                open1,
                DecisionAction.BUY,
                entry=_entry(DecisionAction.BUY, reasons=("FRESH_EXECUTION_EVENT",)),
            ),
        ),
        flat0,
        open1,
    )

    assert validate_st_canonical_behavior(replay).metrics.st_reentries_without_novelty == 1


def test_cold_and_restart_segmented_validation_are_identical():
    replay, rows = _harvest_replay()
    prefix = _replay(rows[:2], replay.initial_state, rows[1].current_state)
    resumed = _replay(rows[2:], rows[1].current_state, replay.final_state)

    cold = validate_st_canonical_behavior(replay)
    segmented = validate_st_canonical_behavior((prefix, resumed))

    assert segmented == cold


def test_noncontiguous_restart_segments_fail_closed():
    replay, rows = _harvest_replay()
    prefix = _replay(rows[:2], replay.initial_state, rows[1].current_state)
    wrong_initial = _open(trade_id="other")
    resumed = _replay((), wrong_initial, wrong_initial)

    with pytest.raises(ValueError, match="contiguous lifecycle chain"):
        validate_st_canonical_behavior((prefix, resumed))


def test_readiness_proxy_is_rejected_from_production_report_and_kept_separate():
    flat0 = _flat()
    open0 = _open()
    row = _row(
        T0,
        100.0,
        flat0,
        open0,
        DecisionAction.BUY,
        entry=_entry(DecisionAction.BUY),
        proxy=True,
    )
    replay = _replay((row,), flat0, open0)

    with pytest.raises(ValueError, match="cannot consume readiness proxy"):
        validate_st_canonical_behavior(replay)

    proxy = validate_st_readiness_proxy_behavior(replay)
    assert proxy.source == "CANONICAL_READINESS_PROXY"
    assert proxy.production_performance is False
    assert proxy.proxy_row_count == 1


def test_legacy_summary_is_explicitly_separate_and_never_substitutes_for_canonical_metrics():
    canonical, _ = _harvest_replay()
    legacy_events = (
        SimpleNamespace(action=SimpleNamespace(value="BUY")),
        SimpleNamespace(action=SimpleNamespace(value="SELL")),
        SimpleNamespace(action=SimpleNamespace(value="SELL")),
    )

    summary = summarize_legacy_behavior(legacy_events)
    bundle = build_st_behavior_validation_bundle(canonical, legacy_events=legacy_events)

    assert summary.source == "LEGACY"
    assert summary.event_count == 3
    assert dict(summary.action_counts) == {"BUY": 1, "SELL": 2}
    assert bundle.canonical.source == "CANONICAL"
    assert bundle.legacy == summary
    assert bundle.readiness_proxy is None
