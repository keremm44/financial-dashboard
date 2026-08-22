# Semantic Targeting Phase 1 Contract

## Purpose

This phase separates four different analytical questions that were previously mixed inside `TargetCluster`:

1. **Objective** — where price may be drawn.
2. **Path / obstacle** — what reaction evidence sits between price and the objective.
3. **Arrival reaction** — what may react at the objective.
4. **Confirmation** — what confirms a reaction after it becomes causally available.

This is a descriptive market-analysis contract only. It has no BUY/SELL, stop, take-profit, or execution authority.

## Phase-1 semantic policy

### Liquidity

- Active/tested Liquidity is the only default `Objective` source.
- Liquidity remains a heuristic draw candidate, not a guaranteed destination.
- Internal/external scope is preserved as metadata; scope does not create a fixed strength weight.

### Order Block

- Order Block is never an Objective in Phase 1.
- Active/unmitigated Order Block is a `ReactionZone` and may act as a conditional barrier on the path to an Objective.
- The model does not claim that price must revisit an Order Block.
- The model does not encode `institutional_interest=true`; such intent is not directly observable from OHLC data.

### FVG

- FVG is a `ReactionZone` plus `IMBALANCE` semantic role.
- FVG refill as a conditional Objective is schema-supported (`ObjectiveKind.FVG_REFILL`) but disabled in Phase 1.
- No fixed fill threshold or refill probability is assumed before replay validation.

### Engulfing

- Engulfing is `Confirmation` only.
- It cannot create an Objective or ReactionZone.
- A confirmation is attached to an ArrivalContext only when it is spatially relevant to the objective/current arrival area.

### Support / Resistance

- S/R is a `ReactionZone`, `BARRIER`, and structural context.
- S/R is not an Objective.

## Location vs behavior

`TargetSide` (`ABOVE`, `BELOW`, `AT_PRICE`) means geometric location relative to current price.

`BehaviorDirection` (`BULLISH`, `BEARISH`, `NEUTRAL`) means reaction behavior.

These are intentionally separate. A bearish reaction zone can be above or below current price; location must not be used as a proxy for directional behavior.

## Arrival positions

Each ReactionZone is classified relative to an Objective:

- `CURRENT` — price is already inside the reaction zone.
- `AHEAD` — price would encounter the zone before the Objective.
- `AT_OBJECTIVE` — zone spatially overlaps/is within tolerance of the Objective.
- `BEYOND` — zone lies beyond the Objective.
- `UNRELATED` — not part of the current Objective path context.

## Arrival states

Phase 1 uses typed states, not fixed numerical weights:

- `NO_ACTIVE_OBJECTIVE`
- `REACTION_ZONE_ONLY`
- `OBJECTIVE_ONLY`
- `OBJECTIVE_WITH_OBSTACLE`
- `OBJECTIVE_WITH_REACTION`
- `MULTI_DOMAIN_REACTION`
- `CONFLICTING_ARRIVAL`
- `IN_REACTION_ZONE`
- `AT_OBJECTIVE`

No ordinal ranking is implied by these states.

## Provenance and independence

`origin_event_id` is provenance, not a role-merging instruction.

If OB, FVG, and Engulfing share one origin event:

- their semantic manifestations remain visible;
- they are not counted as independent causal origins;
- OB remains Reaction, FVG remains Reaction/Imbalance, Engulfing remains Confirmation.

This preserves information without creating artificial confluence counts.

## Causal contract

Every evidence item keeps:

- `origin_time`
- `confirmed_at`
- `available_at`

Semantic snapshot construction applies `available_at <= as_of` internally even if the caller already provides a causal prefix.

Historical replay therefore runs both:

- legacy `TargetCluster` output;
- semantic `Objective / ReactionZone / Confirmation / ArrivalContext` output.

The two models remain in shadow mode until replay evidence supports migration.

## Deferred decisions

The following are intentionally not hard-coded in Phase 1:

- FVG refill as default Objective.
- Liquidity/FVG/OB fixed weights.
- HTF > LTF strength multipliers.
- FVG fill thresholds such as 50%.
- evidence staleness/decay constants.
- probability labels for obstacle respect or Objective hit rates.
- any trade-action recommendation.

These require causal replay / out-of-sample measurement first.

## Migration path

1. **Phase 1 — current**: legacy TargetCluster remains; semantic shadow model runs in parallel.
2. **Phase 2**: add immutable causal event/provenance store.
3. **Phase 3**: classify MTF relations (`SAME_ORIGIN`, `NESTED`, `FRACTAL_DUPLICATE`, `INDEPENDENT`, `CONFLICTING`) in shadow mode.
4. **Phase 4**: deprecate TargetCluster only after replay proves the semantic outputs are stable and more truthful.
5. **Later**: consider event-graph queries; do not rewrite the whole system before shadow-mode evidence exists.
