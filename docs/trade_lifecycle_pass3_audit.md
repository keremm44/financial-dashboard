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
6. production BUY must be FLAT -> OPEN and use confirmed fresh entry execution when execution metadata is present;
7. HOLD must remain OPEN -> OPEN;
8. WAIT/READY/NO_TRADE must not leak into OPEN as entry actions;
9. SELL must be OPEN -> FLAT;
10. lifecycle-aware SELL must have `long_exit.stage = EXIT_READY`;
11. production SELL must have a separately CONFIRMED long-exit execution event.

Violations are surfaced in the audit report. They are not silently repaired.

## Audit-only lifecycle readiness baseline

The production execution-event adapter is intentionally not fabricated by Pass 3. To make the new lifecycle measurable before that adapter exists, historical replay exposes an explicit optional mode:

```text
--lifecycle-readiness-proxy
```

This mode does **not** change Structure, Permission, timing, opportunity, conflict, eligibility, position health, or long-exit stage logic. It substitutes only the two execution edges:

```text
FLAT + LONG READY     -> proxy BUY
OPEN + EXIT_READY     -> proxy SELL
```

The events remain marked with `snapshot.lifecycle_readiness_proxy = true` and audit reasons such as:

```text
AUDIT_PROXY_LONG_ENTRY_FROM_READY
AUDIT_PROXY_LONG_EXIT_FROM_EXIT_READY
```

The lifecycle validator recognizes this explicit proxy contract, so lack of a real fresh entry/exit execution event is not mislabeled as a lifecycle violation in this mode. The proxy is never a production decision source and must not be presented as execution-timing validation. It is a **structural lifecycle readiness baseline** used to measure entry readiness, position holding behavior, exit maturity, and structural exit quality before a native fresh execution adapter is available.

The older `--readiness-position-proxy` remains only for historical comparison. It bypasses the dedicated long-exit semantics and therefore is not the preferred lifecycle baseline.

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

## Backtest modes

The historical CLI now reports one of three explicit modes:

```text
CAUSAL_TRADE_LIFECYCLE
LIFECYCLE_READINESS_PROXY
LEGACY_READINESS_POSITION_PROXY
```

`CAUSAL_TRADE_LIFECYCLE` requires real supplied execution events to produce BUY/SELL. `LIFECYCLE_READINESS_PROXY` is the preferred temporary baseline for structural lifecycle measurement. `LEGACY_READINESS_POSITION_PROXY` is retained only for comparison with older audit results.

## Non-goals

Pass 3 does not add:

- a fabricated production execution trigger;
- new exit-arm rules;
- supporting-domain SELL thresholds;
- profit/giveback exit rules;
- stop-loss or take-profit logic;
- short-selling positions;
- portfolio sizing;
- any rule derived from future audit outcomes.

The structural-only long-exit baseline from pass 2 remains unchanged so its quality can be measured before supporting-domain exit evidence is considered.
