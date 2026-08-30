from __future__ import annotations

from dataclasses import replace

from financial_dashboard.context.envelope import ContextDataQuality, normalize_context_data_quality
from financial_dashboard.context.fvg_engulfing_projection import FvgEngulfingLifecycleProjection
from financial_dashboard.context.order_block_behavior_projection import OrderBlockBehaviorProjection


_OB_OBSERVED_INTERACTIONS = frozenset(
    {
        "APPROACHING",
        "ENTERED",
        "DWELLING_INSIDE",
        "EXITING_FAVORABLE",
        "HOLDING_FAVORABLE",
        "REACTION_CONFIRMED",
        "FAILED",
    }
)


def _decision_valid_ref(ref, *, observed: bool):
    """Promote only DATA_LIMITED price-derived facts with native observed behavior.

    Source-domain quality is intentionally left untouched.  The Decision layer may
    consume a causally observed native lifecycle event even when the wider source
    timeframe was tagged DATA_LIMITED, matching the existing Structure decision
    normalization policy.  WARMING_UP/INCOMPLETE/UNAVAILABLE evidence is never
    promoted.
    """

    quality = normalize_context_data_quality(ref.data_quality)
    if quality is ContextDataQuality.DATA_LIMITED and observed:
        return replace(ref, data_quality=ContextDataQuality.VALID)
    return ref


def _ob_has_observed_behavior(item) -> bool:
    state = str(item.state or "").strip().upper()
    interaction = str(item.interaction or "").strip().upper()
    return state == "REACTION_CONFIRMED" or interaction in _OB_OBSERVED_INTERACTIONS


def _fvg_has_observed_behavior(item) -> bool:
    return bool(
        item.reaction_confirmed
        or item.failed_reaction
        or item.first_test_index is not None
    )


def _engulfing_has_observed_behavior(item) -> bool:
    return bool(
        item.continuation_confirmed
        or item.invalid
        or (item.first_test_index is not None and not item.weakened)
    )


def normalize_decision_reaction_projections(
    order_blocks: OrderBlockBehaviorProjection | None,
    fvg_engulfing: FvgEngulfingLifecycleProjection | None,
) -> tuple[OrderBlockBehaviorProjection | None, FvgEngulfingLifecycleProjection | None]:
    """Return Decision-only reaction projections with conservative quality promotion.

    This is a read-model adaptation, not a domain rewrite.  Only native lifecycle
    behavior that has actually been observed is eligible for DATA_LIMITED -> VALID
    promotion; no state, direction, confirmation, or failure flag is manufactured.
    """

    normalized_ob = order_blocks
    if order_blocks is not None:
        rows = tuple(
            replace(
                item,
                ref=_decision_valid_ref(
                    item.ref,
                    observed=_ob_has_observed_behavior(item),
                ),
            )
            for item in order_blocks.observations
        )
        normalized_ob = replace(order_blocks, observations=rows)

    normalized_fvg = fvg_engulfing
    if fvg_engulfing is not None:
        fvg_rows = tuple(
            replace(
                item,
                ref=_decision_valid_ref(
                    item.ref,
                    observed=_fvg_has_observed_behavior(item),
                ),
            )
            for item in fvg_engulfing.fvg
        )
        engulfing_rows = tuple(
            replace(
                item,
                ref=_decision_valid_ref(
                    item.ref,
                    observed=_engulfing_has_observed_behavior(item),
                ),
            )
            for item in fvg_engulfing.engulfing
        )
        normalized_fvg = replace(
            fvg_engulfing,
            fvg=fvg_rows,
            engulfing=engulfing_rows,
        )

    return normalized_ob, normalized_fvg


__all__ = ["normalize_decision_reaction_projections"]
