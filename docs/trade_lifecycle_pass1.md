# Trade Lifecycle Pass 1

Status: implementation contract for the first lifecycle pass.

This document supplements `docs/buy_sell_decision_architecture_master.md`. The canonical decision architecture remains authoritative for domain roles, causality, timeframe authority, missing-data policy, and BUY/SELL eligibility semantics.

## Purpose

BUY and SELL are not independent stateless signals. The long-only cash-equity system must maintain one trade lifecycle across bars so a single position cannot be bought repeatedly or sold repeatedly.

Pass 1 adds ownership state and historical replay parity without inventing new exit thresholds or changing domain semantics.

## Persistent state

Only ownership state persists across bars:

```text
PositionState:
  FLAT
  OPEN

ExitStage:
  MONITOR
  EXIT_WATCH      # reserved for dedicated exit assessment
  EXIT_READY      # reserved for dedicated exit assessment
```

`FLAT` carries no open-trade metadata. `OPEN` carries a deterministic trade id, entry timestamp, and exit stage.

Pre-entry labels such as WATCHING, SETUP_FORMING, WAITING_FOR_TIMING, and READY are derived projections, not persistent state. Position-health labels such as HEALTHY, PULLBACK, COUNTER_REACTION, and PRESSURED are also derived projections, not persistent state.

## Pass-1 invariants

1. BUY may execute only while FLAT.
2. SELL may execute only while OPEN.
3. OPEN + another BUY is surfaced as HOLD; it never opens another trade.
4. FLAT + SELL is suppressed; it never closes a nonexistent trade.
5. Execution actions must alternate BUY, SELL, BUY, SELL over time.
6. BUY transitions FLAT -> OPEN/MONITOR.
7. SELL transitions OPEN -> FLAT.
8. While OPEN, entry-path WAIT/READY/NO_TRADE states surface as HOLD because the system is managing an existing trade rather than seeking another entry.
9. Historical replay must fold the same persistent lifecycle contract over a strictly increasing causal snapshot stream.
10. No future bar, hindsight metric, MFE/MAE, profit giveback, or audit-only field may enter lifecycle transition input.

## Important pass-1 limitation

Pass 1 deliberately does **not** define the final exit policy.

The current market-decision stream can still produce a legacy SELL candidate from the existing SHORT-side assessment. The lifecycle fold permits that candidate to close an OPEN trade solely so historical ownership and repeated-action invariants can be exercised end to end.

This is temporary. A later pass must introduce a dedicated long-position exit assessment so:

- a short-term bearish counter-reaction does not automatically mean SELL;
- `market side = SHORT` is not treated as equivalent to `close current LONG`;
- MONITOR -> EXIT_WATCH -> EXIT_READY is driven by typed, causal exit evidence;
- SELL requires a dedicated fresh exit execution event or an explicitly accepted structural hard-exit event.

No exit-arm threshold, bar-count cooldown, profit-protection percentage, or arbitrary ATR cutoff is accepted in pass 1.

## Historical replay contract

The historical decision stream first evaluates each causal `DecisionInputSnapshot` through the existing decision engine and then folds the resulting final market decision through `TradeLifecycleState`.

Conceptually:

```text
state = FLAT

for snapshot in causal_snapshots:
    market_decision = assess(snapshot)
    lifecycle_transition = transition(state, market_decision)
    emit(lifecycle_transition)
    state = lifecycle_transition.current
```

The emitted audit snapshot records:

- previous position state;
- current position state;
- previous/current exit stage;
- trade id;
- entry timestamp;
- requested market action;
- lifecycle-effective action;
- transition reason;
- whether ownership changed.

This makes repeated BUY/SELL suppression directly testable instead of reconstructing position ownership only inside the hindsight audit.

## Required next pass

The next lifecycle pass must add a dedicated exit-assessment contract and cross-horizon position-health interpretation before production Streamlit behavior is considered complete. In particular, LT INTACT + ST COUNTER_REACTION must be representable as HOLD rather than being automatically converted to SELL.
