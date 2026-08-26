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
  EXIT_WATCH
  EXIT_READY
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

## Pass-1 limitation and pass-2 replacement

Pass 1 originally allowed the existing bearish market-decision SELL candidate to close an OPEN trade solely so repeated-action ownership invariants could be exercised end to end.

That temporary behavior has now been superseded by `docs/trade_lifecycle_pass2.md`.

The active contract is now:

- a bearish market assessment does not close an OPEN long by itself;
- LT INTACT + ST COUNTER_REACTION is HOLD/protected, not SELL;
- MONITOR -> EXIT_WATCH -> EXIT_READY is produced by the dedicated long-exit assessment;
- SELL requires EXIT_READY plus a separate fresh causal exit execution event.

No exit-arm threshold, bar-count cooldown, profit-protection percentage, or arbitrary ATR cutoff is introduced by either pass.

## Historical replay contract

Historical decision replay evaluates each causal `DecisionInputSnapshot` through the existing market decision engine and then folds the result through `TradeLifecycleState`.

Conceptually:

```text
state = FLAT

for snapshot in causal_snapshots:
    market_decision = assess(snapshot)
    lifecycle_transition = transition(state, market_decision, dedicated_exit_path)
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

Pass 2 additionally records dedicated long-exit health, reasons, waiting conditions, and exit execution state.
