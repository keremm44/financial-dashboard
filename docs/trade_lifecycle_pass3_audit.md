# Trade Lifecycle Pass 3 — Audit Contract

Status: implementation contract for lifecycle-aware historical validation.

This document supplements `docs/buy_sell_decision_architecture_master.md`, `docs/trade_lifecycle_pass1.md`, and the pass-2 dedicated long-exit contract.

## Purpose

Pass 3 moves the historical audit from signal reconstruction toward lifecycle validation and measurement.

The decision/lifecycle layer owns trade state. The audit must not repair repeated BUY/SELL events or invent position ownership after the fact. It grades the already-frozen causal lifecycle using hindsight bars only after each decision has been produced.

## Completed-trade versus censored-trade semantics

A completed trade is exactly:

```text
FLAT -> BUY -> OPEN ... -> SELL -> FLAT
```

A BUY that remains OPEN at the end of the historical sample is **right-censored**. It is not:

- an unmatched BUY;
- a losing trade;
- a completed trade;
- evidence that the exit engine failed.

The censored record reports only sample-bounded hindsight facts such as unrealized return, MFE, MAE and bars open through the final available audit bar.

Repeated BUY while already open and SELL while flat remain invariant violations/unmatched execution events. A valid lifecycle stream must drive both counts to zero.

## Lifecycle validation

When `DecisionEvent.snapshot.trade_lifecycle` metadata is present, the audit validates rather than reconstructs it.

The validator checks:

1. lifecycle metadata is not partially present across the stream;
2. emitted action equals the lifecycle-effective action stored in the snapshot;
3. previous/current ownership states are continuous;
4. FLAT cannot carry open-trade metadata;
5. OPEN must carry a valid exit stage and stable trade id;
6. BUY must be FLAT -> OPEN and, when execution metadata is present, entry execution must be CONFIRMED;
7. HOLD must remain OPEN -> OPEN;
8. WAIT/READY/NO_TRADE must not leak into OPEN as entry actions;
9. SELL must be OPEN -> FLAT;
10. lifecycle-aware SELL must have `long_exit.stage = EXIT_READY`;
11. lifecycle-aware SELL must have a separately CONFIRMED long-exit execution event.

Violations are surfaced in the audit report. They are not silently repaired.

## Lifecycle stability metrics

Pass 3 adds measurements that do not alter decision semantics:

- lifecycle metadata event count;
- completed lifecycle cycles;
- censored open trades;
- HOLD bars;
- PROTECTED HOLD bars;
- PRESSURED HOLD bars;
- EXIT_WATCH episode count and average duration;
- EXIT_READY episode count and average duration;
- EXIT_READY -> SELL delay;
- EXIT_WATCH -> MONITOR reversions;
- EXIT_READY -> EXIT_WATCH reversions.

These are diagnostic/audit measurements only. No threshold from these metrics is fed back into production decisions.

## Existing hindsight quality metrics remain separate

Completed trades continue to be graded using:

- return;
- MFE / MAE;
- favorable move capture;
- entry local-low miss;
- post-entry additional downside;
- exit local-high miss;
- post-exit missed upside;
- profit giveback;
- early/late entry and exit bar offsets.

These metrics remain hindsight-only.

## Non-goals

Pass 3 does not add:

- new exit-arm rules;
- supporting-domain SELL thresholds;
- profit/giveback exit rules;
- stop-loss or take-profit logic;
- short-selling positions;
- portfolio sizing;
- any rule derived from future audit outcomes.

The structural-only long-exit baseline from pass 2 remains unchanged so its quality can be measured before supporting-domain exit evidence is considered.
