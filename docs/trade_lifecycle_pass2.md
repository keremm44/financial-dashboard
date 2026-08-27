# Trade Lifecycle Pass 2 — Dedicated Long Exit Contract

Status: implementation contract for the second lifecycle pass.

This document supplements `docs/buy_sell_decision_architecture_master.md` and `docs/trade_lifecycle_pass1.md`. The canonical master remains authoritative for causality, domain authority, timeframe roles, missing-data treatment, and calibration philosophy.

## Purpose

Pass 1 established persistent `FLAT -> OPEN -> FLAT` ownership and prevented repeated BUY/SELL execution. Its explicit temporary limitation was that an existing bearish market-decision SELL candidate could still close the open long.

Pass 2 removes that category error.

A bearish market assessment and a long-position exit are now separate semantic paths:

```text
Market assessment: SHORT
!=
Long-position exit: SELL
```

An OPEN long is managed by a dedicated structural exit assessment. A SELL can execute only after that exit path reaches `EXIT_READY` and a separate fresh exit execution event is confirmed.

## Persistent lifecycle

```text
PositionState:
  FLAT
  OPEN

OPEN ExitStage:
  MONITOR
  EXIT_WATCH
  EXIT_READY
```

Actions remain:

```text
FLAT: NO_TRADE | WAIT | READY | BUY
OPEN: HOLD | SELL
```

`EXIT_READY` is an exit stage, not an execution action. The position remains OPEN/HOLD until a fresh exit event is confirmed.

## Pass-2 structural exit rules

Pass 2 intentionally uses Structure only for exit maturity. Supporting-domain deterioration is not yet allowed to arm SELL.

This keeps the first exit policy threshold-free and prevents curve-fitting while the historical lifecycle is being validated.

Accepted mapping:

```text
LT LONG / INTACT + ST ALIGNED
  -> MONITOR / HEALTHY / HOLD

LT LONG / INTACT + ST COUNTER_REACTION
  -> MONITOR / PROTECTED / HOLD

LT LONG / INTACT + ST PULLBACK
  -> MONITOR / PROTECTED / HOLD

LT LONG / TRANSITIONING toward SHORT
  -> EXIT_WATCH / PRESSURED / HOLD

LT authority unavailable or unresolved
  -> EXIT_WATCH / UNKNOWN / HOLD
  -> never forced SELL from missing evidence

LT LONG / INTACT + unreconciled structural conflict
  -> EXIT_WATCH / PRESSURED / HOLD

LT thesis INVALIDATED
  -> EXIT_READY / PRESSURED

Canonical LT SHORT / INTACT established against the open long
  -> EXIT_READY / PRESSURED
```

The important invariant is:

```text
LT LONG / INTACT + ST bearish counter-reaction
never becomes SELL merely because the short-term market side is bearish.
```

## Exit execution contract

`EXIT_READY` is necessary but not sufficient for SELL.

A normal exit requires a separate fresh causal event:

- side = SHORT, meaning exit timing against the open long rather than permission to open a short position;
- timeframe = 30m in v1;
- `observed_at == decision as_of`;
- `available_at <= decision as_of`;
- all source refs available at the decision timestamp;
- state = CONFIRMED.

A stale event, wrong-side event, wrong-timeframe event, or future-unavailable event fails closed.

If the exit path is not `EXIT_READY`, even a fresh SHORT-side execution event is `NOT_ARMED` and cannot sell the position.

Pass 2 does not yet add a separate native exit-event detector. Historical/runtime callers may supply a separately typed execution-event channel to the lifecycle orchestration; it is not taken from the market composer's SELL action.

## Historical replay semantics

Historical replay now separates entry and exit event maps:

```text
entry execution events -> market decision / BUY path
exit execution events  -> OPEN long exit path only
```

The normal historical action policy is long-only. The old `(LONG, SHORT)` action policy is retained only inside the explicitly legacy readiness-position proxy so old diagnostic behavior remains comparable while it is being phased out.

Conceptually:

```text
state = FLAT

for assessment in causal_stream:
    if state == FLAT:
        lifecycle <- entry market decision
    else:
        exit_assessment <- assess LT/ST structure for open long
        exit_execution  <- validate dedicated fresh exit event
        lifecycle       <- HOLD or SELL
```

The historical event snapshot records:

- position state and exit stage;
- requested market action and effective lifecycle action;
- derived position health;
- dedicated long-exit reasons;
- dedicated exit waiting conditions;
- exit execution state;
- lifecycle transition reason.

## Explicit non-goals in pass 2

Pass 2 does not introduce:

- stop-loss percentages;
- profit targets;
- ATR exit cutoffs;
- bar-count cooldowns;
- giveback thresholds;
- volume-vote exits;
- pattern-vote exits;
- multi-domain exit scores;
- short-selling positions;
- position sizing or portfolio risk logic.

Supporting-domain deterioration such as durability, reaction, participation, conflict, and volatility remains visible in the decision snapshot but does not yet arm `EXIT_READY`. Those rules must be justified by lifecycle historical audit before adoption.

## Required next validation

The next validation stage should measure the structural-only exit baseline before adding supporting-domain exit evidence:

- repeated BUY count must remain zero while OPEN;
- FLAT SELL count must remain zero as an execution;
- LT-intact/ST-counter-reaction bars must remain HOLD;
- EXIT_WATCH -> EXIT_READY churn;
- time from structural exit readiness to confirmed SELL;
- entry/exit local-extreme miss;
- MFE/MAE and profit giveback;
- censored OPEN trades at replay end;
- early/late exits segmented by the structural exit reason.

Only after those metrics are visible should supporting-domain exit-arm rules be considered.
