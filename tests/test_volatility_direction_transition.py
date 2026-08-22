from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.volatility_bands_fib import VolatilityBandsFibEngine
from financial_dashboard.engines.volatility_bands_fib_engine import VolatilityBandsConfig, VolatilityState
from financial_dashboard.engines.volatility_bands_fib_final import VolatilityBandsFibFinalExport
from financial_dashboard.engines.volatility_direction_transition import (
    EarlyDirectionEvidence,
    EarlyDirectionTransition,
    VolatilityDirectionTransitionEngine,
)


TZ = "Europe/Istanbul"


def _base_frame(n: int = 130) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 100.0 - i * 0.015 + ((i % 4) - 1.5) * 0.015
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=2 * i),
                "open": close + 0.03,
                "high": close + 0.55,
                "low": close - 0.55,
                "close": close,
                "volume": 1000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def _with_up_transition() -> pd.DataFrame:
    frame = _base_frame()
    previous = float(frame.iloc[-1]["close"])
    row = {
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=2),
        "open": previous + 0.05,
        "high": previous + 1.15,
        "low": previous - 0.10,
        "close": previous + 0.85,
        "volume": 1200.0,
        "is_closed": True,
        "is_complete": True,
    }
    return pd.concat([frame, pd.DataFrame([row])], ignore_index=True)


def _with_down_transition() -> pd.DataFrame:
    frame = _base_frame()
    for offset in range(8):
        idx = len(frame) - 8 + offset
        close = 98.4 + offset * 0.08
        frame.loc[idx, ["open", "high", "low", "close"]] = [
            close - 0.03,
            close + 0.55,
            close - 0.55,
            close,
        ]
    previous = float(frame.iloc[-1]["close"])
    row = {
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=2),
        "open": previous - 0.05,
        "high": previous + 0.10,
        "low": previous - 1.15,
        "close": previous - 0.85,
        "volume": 1200.0,
        "is_closed": True,
        "is_complete": True,
    }
    return pd.concat([frame, pd.DataFrame([row])], ignore_index=True)


def _raw(
    state: EarlyDirectionTransition,
    *,
    position: float | None = None,
) -> EarlyDirectionEvidence:
    return EarlyDirectionEvidence(
        state=state,
        raw_state=state,
        evidence_count=5,
        reasons=("synthetic_raw",),
        displacement_atr=.5 if state is EarlyDirectionTransition.EARLY_UP else -.5,
        body_atr=.5,
        bollinger_position=position,
    )


def _none(*, position: float | None = None) -> EarlyDirectionEvidence:
    return EarlyDirectionEvidence(bollinger_position=position)


def _advance(engine: VolatilityDirectionTransitionEngine) -> None:
    engine._rows.append({})


def test_early_up_can_appear_without_rewriting_confirmed_export() -> None:
    frame = _with_up_transition()
    config = VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    wrapper = VolatilityDirectionTransitionEngine(config)
    snapshots = wrapper.replay(frame)

    direct = VolatilityBandsFibEngine(config)
    direct.replay(frame)

    assert snapshots[-1].early.state is EarlyDirectionTransition.EARLY_UP
    assert snapshots[-1].early.episode_started
    assert snapshots[-1].early.evidence_count >= 5
    assert snapshots[-1].confirmed_export == direct.final_export
    assert wrapper.canonical_engine.final_export == direct.final_export


def test_early_down_can_appear_without_rewriting_confirmed_export() -> None:
    frame = _with_down_transition()
    config = VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    wrapper = VolatilityDirectionTransitionEngine(config)
    snapshots = wrapper.replay(frame)

    direct = VolatilityBandsFibEngine(config)
    direct.replay(frame)

    assert snapshots[-1].early.state is EarlyDirectionTransition.EARLY_DOWN
    assert snapshots[-1].early.episode_started
    assert snapshots[-1].early.evidence_count >= 5
    assert snapshots[-1].confirmed_export == direct.final_export


def test_same_direction_raw_evidence_is_not_reemitted_as_new_episode() -> None:
    engine = VolatilityDirectionTransitionEngine()
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]

    first = engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_UP), neutral)
    _advance(engine)
    duplicate = engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_UP), neutral)

    assert first.state is EarlyDirectionTransition.EARLY_UP
    assert first.episode_started
    assert first.episode_id == 1
    assert duplicate.state is EarlyDirectionTransition.NONE
    assert duplicate.raw_state is EarlyDirectionTransition.EARLY_UP
    assert duplicate.episode_id == 1
    assert "same_episode_duplicate" in duplicate.reasons


def test_neutral_opposite_flip_requires_two_consecutive_raw_observations() -> None:
    engine = VolatilityDirectionTransitionEngine()
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_UP), neutral)

    _advance(engine)
    first_down = engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_DOWN), neutral)
    _advance(engine)
    second_down = engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_DOWN), neutral)

    assert first_down.state is EarlyDirectionTransition.NONE
    assert "opposite_rearm_pending" in first_down.reasons
    assert second_down.state is EarlyDirectionTransition.EARLY_DOWN
    assert second_down.episode_started
    assert second_down.episode_id == 2


def test_nonconsecutive_opposite_raw_does_not_rearm_episode() -> None:
    engine = VolatilityDirectionTransitionEngine()
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_UP), neutral)

    _advance(engine)
    engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_DOWN), neutral)
    _advance(engine)
    engine._apply_episode_lifecycle(_none(position=.7), neutral)
    _advance(engine)
    second_attempt = engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_DOWN), neutral)

    assert second_attempt.state is EarlyDirectionTransition.NONE
    assert "opposite_rearm_pending" in second_attempt.reasons


def test_idle_expiry_blocks_same_direction_until_semantic_reset() -> None:
    engine = VolatilityDirectionTransitionEngine(
        VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    )
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    first = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )
    assert first.episode_id == 1

    last = None
    for _ in range(4):
        _advance(engine)
        last = engine._apply_episode_lifecycle(_none(position=.8), neutral)

    assert last is not None
    assert "episode_expired_idle" in last.reasons
    assert "semantic_rearm_required" in last.reasons
    assert engine._episode_direction is EarlyDirectionTransition.NONE
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP

    _advance(engine)
    blocked = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )
    assert blocked.state is EarlyDirectionTransition.NONE
    assert "same_direction_rearm_not_observed" in blocked.reasons
    assert engine._episode_id == 1


def test_basis_return_requires_two_completed_observations_before_same_direction_rearm() -> None:
    engine = VolatilityDirectionTransitionEngine(
        VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    )
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )
    for _ in range(4):
        _advance(engine)
        engine._apply_episode_lifecycle(_none(position=.8), neutral)

    _advance(engine)
    first_reset = engine._apply_episode_lifecycle(_none(position=.40), neutral)
    assert "semantic_rearm_lower_basis_acceptance" not in first_reset.reasons
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP

    _advance(engine)
    second_reset = engine._apply_episode_lifecycle(_none(position=.39), neutral)
    assert "semantic_rearm_lower_basis_acceptance" in second_reset.reasons
    assert engine._rearm_block_direction is EarlyDirectionTransition.NONE

    _advance(engine)
    rearmed = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.75), neutral
    )
    assert rearmed.state is EarlyDirectionTransition.EARLY_UP
    assert rearmed.episode_started
    assert rearmed.episode_id == 2


def test_single_basis_reset_bar_does_not_survive_interruption() -> None:
    engine = VolatilityDirectionTransitionEngine(
        VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    )
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )
    for _ in range(4):
        _advance(engine)
        engine._apply_episode_lifecycle(_none(position=.8), neutral)

    _advance(engine)
    engine._apply_episode_lifecycle(_none(position=.40), neutral)
    _advance(engine)
    engine._apply_episode_lifecycle(_none(position=.55), neutral)
    _advance(engine)
    second_attempt = engine._apply_episode_lifecycle(_none(position=.39), neutral)

    assert "semantic_rearm_lower_basis_acceptance" not in second_attempt.reasons
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP


def test_opposite_evidence_requires_persistence_to_clear_same_direction_block() -> None:
    engine = VolatilityDirectionTransitionEngine(
        VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    )
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )
    for _ in range(4):
        _advance(engine)
        engine._apply_episode_lifecycle(_none(position=.8), neutral)

    _advance(engine)
    first_opposite = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_DOWN, position=.3), neutral
    )
    assert first_opposite.state is EarlyDirectionTransition.EARLY_DOWN
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP

    _advance(engine)
    second_opposite = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_DOWN, position=.3), neutral
    )
    assert "semantic_rearm_opposite_evidence" in second_opposite.reasons
    assert engine._rearm_block_direction is EarlyDirectionTransition.NONE


def test_canonical_neutralization_alone_does_not_rearm_after_graduation() -> None:
    engine = VolatilityDirectionTransitionEngine()
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )

    _advance(engine)
    up_candidate = VolatilityBandsFibFinalExport(regime=int(VolatilityState.UP_CANDIDATE))
    graduated = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), up_candidate
    )
    assert "same_direction_already_candidate_or_confirmed" in graduated.reasons
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP

    _advance(engine)
    neutralized = engine._apply_episode_lifecycle(_none(position=.8), neutral)
    assert "semantic_rearm" not in " ".join(neutralized.reasons)
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP

    _advance(engine)
    blocked = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP, position=.8), neutral
    )
    assert blocked.state is EarlyDirectionTransition.NONE
    assert "same_direction_rearm_not_observed" in blocked.reasons
    assert engine._episode_id == 1


def test_same_direction_candidate_closes_and_blocks_early_episode() -> None:
    engine = VolatilityDirectionTransitionEngine()
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_UP), neutral)

    _advance(engine)
    up_candidate = VolatilityBandsFibFinalExport(regime=int(VolatilityState.UP_CANDIDATE))
    suppressed = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP), up_candidate
    )

    assert suppressed.state is EarlyDirectionTransition.NONE
    assert "same_direction_already_candidate_or_confirmed" in suppressed.reasons
    assert engine._episode_direction is EarlyDirectionTransition.NONE
    assert engine._rearm_block_direction is EarlyDirectionTransition.EARLY_UP


def test_reversal_away_from_confirmed_episode_direction_can_emit_immediately() -> None:
    engine = VolatilityDirectionTransitionEngine()
    neutral = VolatilityBandsFibFinalExport(regime=int(VolatilityState.BALANCED))
    engine._rows = [{}]
    engine._apply_episode_lifecycle(_raw(EarlyDirectionTransition.EARLY_UP), neutral)

    _advance(engine)
    confirmed_up = VolatilityBandsFibFinalExport(regime=int(VolatilityState.UP_CONFIRMED))
    reversal = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_DOWN), confirmed_up
    )

    assert reversal.state is EarlyDirectionTransition.EARLY_DOWN
    assert reversal.episode_started
    assert reversal.episode_id == 2


def test_same_direction_candidate_or_confirmed_is_not_called_early() -> None:
    engine = VolatilityDirectionTransitionEngine()
    engine._rows = [{}]
    up_candidate = VolatilityBandsFibFinalExport(regime=int(VolatilityState.UP_CANDIDATE))

    suppressed = engine._apply_episode_lifecycle(
        _raw(EarlyDirectionTransition.EARLY_UP), up_candidate
    )

    assert suppressed.state is EarlyDirectionTransition.NONE
    assert suppressed.raw_state is EarlyDirectionTransition.EARLY_UP
    assert "same_direction_already_candidate_or_confirmed" in suppressed.reasons


def test_weak_opposite_candle_is_not_promoted_to_early_transition() -> None:
    frame = _base_frame()
    previous = float(frame.iloc[-1]["close"])
    weak = {
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=2),
        "open": previous,
        "high": previous + 0.20,
        "low": previous - 0.20,
        "close": previous + 0.05,
        "volume": 1000.0,
        "is_closed": True,
        "is_complete": True,
    }
    frame = pd.concat([frame, pd.DataFrame([weak])], ignore_index=True)
    latest = VolatilityDirectionTransitionEngine().replay(frame)[-1]
    assert latest.early.state is EarlyDirectionTransition.NONE


def test_canonical_one_bar_shock_suppresses_early_direction_relabel() -> None:
    engine = VolatilityDirectionTransitionEngine()
    frame = _with_up_transition()
    engine._rows = frame.to_dict("records")
    shock = EngineResult(
        engine="VolatilityBandsFib",
        state="COHERENCE_CONFLICT",
        timestamp=frame.iloc[-1]["timestamp"],
        direction=Direction.UP,
        reasons=("vol=VOL_ONE_BAR_SHOCK",),
    )
    early = engine._early_evidence(shock)
    assert early.state is EarlyDirectionTransition.NONE
    assert "canonical_one_bar_shock" in early.reasons


def test_open_and_incomplete_bars_freeze_both_clocks() -> None:
    engine = VolatilityDirectionTransitionEngine()
    frame = _with_up_transition()
    engine.replay(frame)
    before = engine.snapshot()
    before_count = len(engine._rows)

    open_bar = frame.iloc[-1].to_dict()
    open_bar["timestamp"] = pd.Timestamp("2026-09-30", tz=TZ)
    open_bar["close"] = 9999.0
    open_bar["high"] = 10000.0
    open_bar["is_closed"] = False
    assert engine.update(open_bar) == before
    assert len(engine._rows) == before_count

    incomplete = frame.iloc[-1].to_dict()
    incomplete["timestamp"] = pd.Timestamp("2026-10-01", tz=TZ)
    incomplete["close"] = 1.0
    incomplete["is_complete"] = False
    assert engine.update(incomplete) == before
    assert len(engine._rows) == before_count


def test_future_tail_cannot_rewrite_prefix_transition_history() -> None:
    frame = _with_up_transition()
    tail_rows = []
    last = frame.iloc[-1]
    for i in range(12):
        close = float(last["close"]) + (i + 1) * 0.20
        tail_rows.append(
            {
                "timestamp": last["timestamp"] + pd.Timedelta(hours=2 * (i + 1)),
                "open": close - 0.08,
                "high": close + 0.50,
                "low": close - 0.45,
                "close": close,
                "volume": 1200.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    full = pd.concat([frame, pd.DataFrame(tail_rows)], ignore_index=True)

    prefix_replay = VolatilityDirectionTransitionEngine().replay(frame)
    full_replay = VolatilityDirectionTransitionEngine().replay(full)
    assert full_replay[: len(prefix_replay)] == prefix_replay


def test_existing_timeframe_contract_is_preserved() -> None:
    for timeframe in ("2h", "4h", "1d"):
        VolatilityDirectionTransitionEngine(VolatilityBandsConfig(timeframe=timeframe))
