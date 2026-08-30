from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision_audit.structure_reaction_research import (
    BullishReactionEvidence,
    _diagnostic,
    _event_age,
)


def _event(available_at: str):
    return SimpleNamespace(
        event_type="BOS",
        direction=-1,
        validity="VALID",
        relevance="ACTIVE",
        bos_maturity="MATURE",
        ref=SimpleNamespace(
            confirmed_at=pd.Timestamp(available_at),
            available_at=pd.Timestamp(available_at),
        ),
    )


def _reaction(*, confirmed: bool = False, fvg: tuple[str, ...] = (), ob: tuple[str, ...] = ()):
    return BullishReactionEvidence(
        aggregate_state="CONFIRMED" if confirmed else "UNKNOWN",
        confirmation_present=confirmed,
        failure_present=False,
        fvg_confirmed=fvg,
        fvg_developing=(),
        ob_favorable=ob,
    )


def test_structural_event_age_is_measured_from_causal_availability():
    row = _event("2026-07-01 10:00:00+03:00")
    result = _event_age(row, pd.Timestamp("2026-07-02 14:00:00+03:00"))

    assert result is not None
    assert result.age_hours == 28.0
    assert result.age_1h_bars == 28


def test_old_bearish_structure_plus_confirmed_bullish_reaction_is_flagged():
    bearish = _event_age(
        _event("2026-07-01 10:00:00+03:00"),
        pd.Timestamp("2026-07-02 14:00:00+03:00"),
    )

    result = _diagnostic(
        st_direction="SHORT",
        st_thesis="INTACT",
        bearish=bearish,
        bullish=None,
        reaction=_reaction(confirmed=True),
    )

    assert result == "STALE_BEARISH_STRUCTURE_WITH_BULLISH_REACTION_CANDIDATE"


def test_recent_bearish_structure_without_bullish_reaction_is_not_called_stale():
    bearish = _event_age(
        _event("2026-07-02 09:00:00+03:00"),
        pd.Timestamp("2026-07-02 14:00:00+03:00"),
    )

    result = _diagnostic(
        st_direction="SHORT",
        st_thesis="INTACT",
        bearish=bearish,
        bullish=None,
        reaction=_reaction(),
    )

    assert result == "BEARISH_STRUCTURE_STILL_CURRENT_NO_STRONG_BULLISH_REACTION_PROOF"


def test_transitioning_short_with_bullish_fvg_is_distinguished_from_intact_short():
    bearish = _event_age(
        _event("2026-07-02 09:00:00+03:00"),
        pd.Timestamp("2026-07-02 14:00:00+03:00"),
    )

    result = _diagnostic(
        st_direction="SHORT",
        st_thesis="TRANSITIONING",
        bearish=bearish,
        bullish=None,
        reaction=_reaction(fvg=("FVG:1h:confirmed:evidence=2",)),
    )

    assert result == "BEARISH_STRUCTURE_TRANSITIONING_WITH_BULLISH_REACTION"
