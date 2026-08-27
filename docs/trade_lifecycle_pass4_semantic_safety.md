# Trade Lifecycle Pass 4 — Decision Semantic Safety

Status: implemented deterministic corrections before real-data lifecycle calibration.

This pass intentionally changes only rules that are already fixed by the canonical architecture. It does **not** add profit thresholds, ATR thresholds, score voting, supporting-domain exit arms, or hindsight-derived trading rules.

## 1. Horizon-aware permission

The workspace may still expose its generic cross-domain context using a 4H anchor for descriptive UI compatibility. The BUY/SELL decision engine no longer reuses that single generic permission for both horizons.

Decision permission is now recomputed cheaply from the already-frozen projections:

- LT permission anchor: **1D**
- LT subordinate context: 4H / 2H / 1H
- ST permission anchor: **1H**
- ST subordinate timing context: 30m

No native/domain replay is repeated. This is a derived read-model calculation only.

This prevents a generic 4H permission side/state from becoming accidental authority over both the 1D LT thesis and the 1H ST thesis.

## 2. Permission hard-gate correction

Canonical accepted hard gate G4 is `Permission BLOCKED`.

A permission side mismatch is not itself a new hard gate. It can occur in legitimate counter-reaction or reaction-only scope states. Therefore:

- `Permission BLOCKED` remains a hard gate.
- opposite permission side -> WAIT / scope reconciliation.
- unresolved permission side with OPEN/CONDITIONAL gate -> WAIT.

This preserves structural authority and avoids converting normal multi-horizon disagreement into `NO_TRADE` by category error.

## 3. UNRESOLVED context conflict

Canonical architecture explicitly says `UNRESOLVED != HIGH`.

Permission resolution therefore treats:

- HIGH conflict -> BLOCKED
- UNRESOLVED conflict -> WAITING

No unresolved state is promoted to a hard conflict gate merely because evidence is incomplete.

## 4. Direction-aware unsupported break participation

An unsupported break is now interpreted relative to the Structure-owned side.

Example with Structure LONG:

- upside break UNSUPPORTED -> weakens LONG participation path.
- downside break UNSUPPORTED -> does **not** count as contradiction to LONG by itself.

The latter is preserved descriptively as `OPPOSING_BREAK_UNSUPPORTED`; it is not automatically promoted to SUPPORTIVE either.

This removes a sign/direction category error without inventing a new volume score.

## 5. Engulfing reaction relationship

Engulfing remains confirmation-only.

An engulfing observation can affect `ReactionAssessment` only when it:

1. has the same structural side;
2. belongs to an allowed reaction timeframe;
3. belongs to the same timeframe as an already-usable same-side OB/FVG reaction zone; and
4. spatially overlaps that zone.

A same-direction engulfing elsewhere on the chart can no longer confirm or fail an unrelated reaction path.

No ATR proximity threshold was introduced. Exact zone overlap is used as the deterministic v1 relationship contract.

## 6. Audit-friendly per-bar trace

Historical lifecycle events now expose a compact derived `lifecycle_phase` plus the full horizon-aware permission and a normalized decision block.

Representative phases include:

- NO_SETUP
- WATCHING
- WAITING_FOR_TIMING
- READY
- ENTRY_EXECUTED
- HEALTHY
- PROTECTED
- PULLBACK
- COUNTER_REACTION
- PRESSURED
- EXIT_WATCH
- EXIT_READY
- EXIT_EXECUTED

These phases are descriptive audit output; only `FLAT/OPEN` and exit stage remain persistent lifecycle state.

Each event snapshot also carries:

- permission
- structural state
- horizon relation
- durability
- reaction
- participation
- environment
- opportunity
- coverage
- conflict
- timing
- eligibility
- execution
- long-exit state when OPEN
- reasons
- blockers
- waiting_for
- next_conditions

## 7. Backtest CLI output

`decision_backtest.py` supports the lifecycle readiness proxy and can export the full per-bar causal timeline using:

```text
--timeline-json-out <path>
```

This is intended for deterministic debugging and review before any future LLM explanation layer exists.

## 8. Explicitly deferred until real historical baseline

The following remain deliberately unimplemented because they require actual lifecycle audit evidence or a separately accepted native execution-event contract:

- supporting-domain rules that promote `EXIT_WATCH -> EXIT_READY`;
- profit/giveback exits;
- ATR/bar-count exit thresholds;
- fresh production entry execution-event adapter;
- fresh production exit execution-event source beyond the accepted event contract;
- any calibration of opportunity thresholds;
- any rule chosen because it improves one symbol's hindsight P/L.

These must not be guessed before the real causal lifecycle baseline is inspected.
