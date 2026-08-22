# Volatility / Bands / Fibonacci — Direction Transition Contract

## Purpose

This round does **not** remove confirmation delays globally. Some delay is intentional and necessary, especially for confirmed structure breaks, active swing/Fibonacci geometry, reclaim/invalidation and any state that claims a level has actually been broken or held.

The problem being addressed is narrower:

> Directional volatility change can become observable before the slower confirmed break/Fibonacci machinery is allowed to change state.

The engine therefore keeps two separate clocks:

1. **Early direction-transition evidence** — descriptive, fast, reversible.
2. **Confirmed structure/Fibonacci state** — slower, causal and deliberately conservative.

Early evidence must never rewrite confirmed break, swing or Fibonacci facts.

## What stays conservative

The following existing confirmation behavior stays intact in this round:

- right-side pivot confirmation;
- meaningful swing acceptance;
- structural break buffer;
- structural break confirmation bars;
- Fibonacci active-swing geometry;
- Fibonacci reclaim/invalidation confirmation;
- closed + complete candle gating;
- no-lookahead replay behavior.

A Fibonacci level is not considered broken merely because early directional pressure changed.

## What may react earlier

The volatility/band family may expose an early direction-transition observation before a confirmed structure break.

Permitted early states:

- `NONE`
- `EARLY_UP`
- `EARLY_DOWN`

These are evidence states, not trend-reversal claims.

`EARLY_UP` means the latest completed bar shows a coherent upward directional transition in volatility/band behavior before the slower confirmed state has caught up.

`EARLY_DOWN` is the symmetric case.

## Evidence family

Early transition evidence must use only information available on the current completed bar and already-known history. It may use:

- one-bar price displacement normalized by prior ATR;
- candle body normalized by prior ATR;
- close location inside the candle;
- direction of the latest close versus the previous close;
- Bollinger basis side;
- Bollinger position change;
- ATR slope;
- normalized band-width slope;
- current/previous confirmed volatility candidate state;
- current band retreat / weakening context.

It must not use a future pivot, future break confirmation or future Fibonacci outcome.

## Separation from confirmed direction

The existing volatility states remain authoritative for confirmed regime state:

- `UP_CANDIDATE`
- `UP_CONFIRMED`
- `DOWN_CANDIDATE`
- `DOWN_CONFIRMED`

The early-transition track is additional evidence. It does not silently relabel a confirmed state.

Examples:

```text
EARLY_UP + DOWN_CONFIRMED
```

means:

> Upward transition evidence has appeared, but the confirmed volatility regime is still down.

It does **not** mean the down regime has already reversed.

Likewise:

```text
EARLY_DOWN + Fibonacci NORMAL_RETRACEMENT
```

does not invalidate the active Fibonacci swing. The Fibonacci state changes only under its existing causal confirmation rules.

## Anti-noise rule

A single weak opposite-colour candle is insufficient.

Early direction evidence requires a coherent combination of displacement, candle location/body and volatility/band transition evidence. A one-bar shock remains a shock unless the existing shock logic permits normal directional classification.

No early state may be emitted from an open or incomplete bar.

## Replay requirement

Before changing confirmed thresholds, replay must measure separately:

- first early directional-transition bar;
- first existing candidate bar;
- first existing confirmed volatility bar;
- first confirmed structural-break bar;
- first Fibonacci state transition tied to the new swing;

For each event, record lag in bars.

The objective is not `zero lag`. The objective is:

> earlier descriptive awareness without weakening the confirmation standard of facts that require confirmation.

## Authority rule

Early direction transition can say:

- upward/downward pressure is emerging;
- volatility expansion is turning upward/downward;
- an opposite-direction transition is appearing before confirmation.

It cannot say:

- BOS/CHoCH occurred;
- main trend reversed;
- Fibonacci was broken/reclaimed/invalidated before its own confirmation;
- BUY/SELL/entry/stop/take-profit.
