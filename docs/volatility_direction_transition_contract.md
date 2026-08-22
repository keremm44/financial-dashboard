# Volatility / Bands / Fibonacci — Direction Transition Contract

## Purpose

The existing Volatility/Bands/Fibonacci engine deliberately confirms some facts slowly. That delay remains correct for right-side pivot confirmation, structural-break confirmation, active-swing/Fibonacci geometry, reclaim and invalidation.

The narrow problem in this round is different: a directional volatility transition can become observable before those slower facts are allowed to change state.

This round therefore adds an independent **early direction-transition evidence track** without weakening any existing confirmed threshold.

## Two clocks

1. **Early direction transition** — fast, descriptive and reversible.
2. **Confirmed Volatility / Structure / Fibonacci state** — slower and deliberately conservative.

Allowed early states:

- `NONE`
- `EARLY_UP`
- `EARLY_DOWN`

An early state is evidence only. It is not BOS, CHoCH, trend reversal, Fibonacci break/reclaim/invalidation, or trading authority.

## Confirmed logic that remains unchanged

This round does not change:

- `VolatilityState` thresholds;
- candidate/confirmed persistence;
- right-side pivot confirmation;
- meaningful swing acceptance;
- structural-break buffers;
- structural-break confirmation bars;
- Fibonacci active-swing identity;
- Fibonacci reclaim/invalidation rules;
- closed + complete bar gating;
- no-lookahead behavior.

## Early evidence

Early direction transition uses only the current completed bar and already-known history. It may use:

- one-bar displacement normalized by prior ATR;
- candle body normalized by prior ATR;
- close location in the candle;
- current close versus previous close;
- current close versus Bollinger basis;
- Bollinger position change;
- ATR slope;
- normalized Bollinger-width slope.

The current strong directional bar is mandatory. Context evidence must additionally agree. A single weak opposite-colour candle is insufficient.

If the canonical engine classifies the bar as `ONE_BAR_SHOCK`, the early transition track remains `NONE`; shock classification is not silently converted into a direction change.

## Authority boundary

Examples:

`EARLY_UP + confirmed down regime`

means upward transition evidence has appeared while the confirmed regime is still down.

It does not mean the confirmed down regime has reversed.

The early track cannot create or rewrite:

- Market Structure facts;
- structural-break facts;
- Fibonacci facts;
- targets;
- probabilities;
- BUY / SELL / entry / stop / take-profit.

## Supported timeframes

Round 1 preserves the existing Volatility/Bands/Fib contract:

- `2h`
- `4h`
- `1d`

No new 1h/30m presets are introduced without separate replay calibration.

## Round split

### Round 1 — engine closure

- early transition model;
- canonical-engine wrapper;
- immutable descriptive snapshot;
- replay and no-lookahead tests;
- open/incomplete-bar freeze tests;
- regression proof that confirmed/Fibonacci exports are unchanged.

### Round 2 — system integration

- shared-input MTF replay (`2h`, `4h`, `1d`);
- independent workspace domain;
- typed view models / inspection surface;
- lag diagnostics: early → candidate → confirmed → structural break → Fib;
- real BIST replay validation.
