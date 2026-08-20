from __future__ import annotations

from dataclasses import replace

from .mtf_story_models import MTFStoryResult, MTFStoryState


_IMMEDIATE_ESCALATIONS: set[tuple[MTFStoryState, MTFStoryState]] = {
    (MTFStoryState.REVERSAL_BUILDING, MTFStoryState.REVERSAL_CONFIRMED),
    (MTFStoryState.BREAKOUT_BUILDING, MTFStoryState.BREAKOUT_CONFIRMED),
}


class MTFStoryStateMachine:
    """Small hysteresis layer for confirmed MTF Story results.

    The classifier remains stateless. This layer only prevents a single confirmed
    observation from replacing an established story. Confirmed escalation from
    BUILDING to CONFIRMED is accepted immediately; all other state/key changes need
    two consecutive matching confirmed observations. Unconfirmed/live-preview
    results never mutate stable state.
    """

    def __init__(self, confirmations_required: int = 2) -> None:
        if confirmations_required < 1:
            raise ValueError("confirmations_required must be >= 1")
        self.confirmations_required = confirmations_required
        self.reset()

    def reset(self) -> None:
        self._stable: MTFStoryResult | None = None
        self._pending_key: tuple | None = None
        self._pending_count = 0

    @staticmethod
    def _key(result: MTFStoryResult) -> tuple:
        return (
            result.state,
            result.dominant_direction,
            result.macro_direction,
            result.context_state,
            result.trigger_state,
        )

    @property
    def snapshot(self) -> MTFStoryResult | None:
        return self._stable

    def update(self, candidate: MTFStoryResult) -> MTFStoryResult | None:
        if not candidate.is_confirmed:
            return self._stable

        if self._stable is None:
            self._stable = candidate
            self._pending_key = None
            self._pending_count = 0
            return self._stable

        stable_key = self._key(self._stable)
        candidate_key = self._key(candidate)

        if candidate_key == stable_key:
            # Same semantic state: refresh timestamp/quality/reasons immediately.
            self._stable = candidate
            self._pending_key = None
            self._pending_count = 0
            return self._stable

        if (self._stable.state, candidate.state) in _IMMEDIATE_ESCALATIONS:
            self._stable = candidate
            self._pending_key = None
            self._pending_count = 0
            return self._stable

        if self.confirmations_required == 1:
            self._stable = candidate
            self._pending_key = None
            self._pending_count = 0
            return self._stable

        if candidate_key == self._pending_key:
            self._pending_count += 1
        else:
            self._pending_key = candidate_key
            self._pending_count = 1

        if self._pending_count >= self.confirmations_required:
            self._stable = candidate
            self._pending_key = None
            self._pending_count = 0
            return self._stable

        # Return the immutable stable story, annotated as persistence-held without
        # mutating the original object stored in prior replay history.
        return replace(
            self._stable,
            reasons=self._stable.reasons + (f"PERSISTENCE:HOLD:{candidate.state.value}",),
        )
