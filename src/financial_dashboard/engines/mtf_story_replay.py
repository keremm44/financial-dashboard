from __future__ import annotations

from collections.abc import Iterable

from .mtf_story_models import MTFStoryResult
from .mtf_story_state_machine import MTFStoryStateMachine


def replay_story_results(
    candidates: Iterable[MTFStoryResult],
    *,
    confirmations_required: int = 2,
) -> list[MTFStoryResult]:
    """Replay candidate story results through the same persistence state machine.

    Only confirmed candidates can advance stable state. Output is deterministic and
    prefix-safe: later candidates cannot mutate earlier returned immutable results.
    """

    machine = MTFStoryStateMachine(confirmations_required=confirmations_required)
    output: list[MTFStoryResult] = []
    for candidate in candidates:
        result = machine.update(candidate)
        if result is not None:
            output.append(result)
    return output
