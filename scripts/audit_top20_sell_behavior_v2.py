from __future__ import annotations

import pandas as pd

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.engine import _decision_structure_projection, _execution_channel_quality
from financial_dashboard.decision.st_bearish_reversal import (
    assess_st_bearish_reversal,
    refine_short_term_exit_with_bearish_reversal,
)
from financial_dashboard.decision.stabil_authority import assess_stabil_authority
from financial_dashboard.decision.structural import build_horizon_structural_snapshot
from financial_dashboard.decision.trade_exit import assess_long_exit_execution, exit_click_event
from financial_dashboard.decision.exit import _short_term_position_exit, refine_short_term_exit_with_stabil

from scripts import audit_top20_sell_behavior as base


def _hypothetical_st_exit_row(snapshot, *, exit_event, real_action: str) -> base.ExitRow:
    """Mirror the production ST exit path, including the early bearish reversal layer."""

    structural_snapshot = build_horizon_structural_snapshot(
        _decision_structure_projection(snapshot.structure)
    )
    structural = _short_term_position_exit(structural_snapshot)
    reversal = assess_st_bearish_reversal(
        snapshot,
        structural_snapshot.short_term,
    )
    structural = refine_short_term_exit_with_bearish_reversal(structural, reversal)
    stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))
    structural = refine_short_term_exit_with_stabil(
        structural,
        structural_snapshot.short_term,
        stabil,
    )
    click = exit_click_event(exit_event)
    channel_available = _execution_channel_quality(snapshot, "1h") is ContextDataQuality.VALID
    armed = base._token(structural.stage) == "EXIT_READY"
    execution = assess_long_exit_execution(
        structural,
        as_of=snapshot.as_of,
        event=click if armed else None,
        execution_timeframe="1h",
        channel_available=channel_available,
    )
    action = "SELL" if base._token(execution.state) == "CONFIRMED" else "HOLD"
    st = structural_snapshot.short_term
    lt = structural_snapshot.long_term
    return base.ExitRow(
        as_of=pd.Timestamp(snapshot.as_of),
        price=float(snapshot.current_price),
        stage=base._token(structural.stage),
        health=base._token(structural.position_health),
        execution=base._token(execution.state),
        hypothetical_action=action,
        structural_reasons=tuple(structural.reasons),
        waiting_for=tuple(dict.fromkeys((*structural.waiting_for, *execution.waiting_for))),
        st_structure=f"{base._token(st.direction)}/{base._token(st.thesis_state)}",
        lt_structure=f"{base._token(lt.direction)}/{base._token(lt.thesis_state)}",
        stabil=base._token(stabil.state),
        real_action=real_action,
        exit_event=exit_event is not None,
    )


if __name__ == "__main__":
    base._hypothetical_st_exit_row = _hypothetical_st_exit_row
    base.main()
