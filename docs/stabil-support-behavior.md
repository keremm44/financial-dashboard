# Stabil Support Behavior Model

## Purpose

This document explains the reasoning behind the Stabil support behavior layer added on top of the existing Stabil support lifecycle.

The goal is **not** to turn Stabil into a BUY/SELL engine. The goal is to make Stabil describe the relationship between price and structural support in a way that later decision layers can understand without re-deriving market structure from raw values.

The central design principle is:

> Stabil should answer **“What is happening between price and structural support?”**, not **“Should we buy or sell?”**

This distinction is important for every future domain extension in the project.

---

## Why the old lifecycle was not enough

The existing Stabil lifecycle already captured important factual events such as:

- current support level,
- support floor,
- support breach,
- close below support,
- reclaim,
- progression / rebasing,
- distance from support,
- bars above / below support.

Those facts are necessary, but they are not sufficient to describe actual market behavior.

For example, these two situations are very different:

1. Price moves above support while the support itself is still moving downward.
2. Price moves above support after the support has stopped falling, flattened, and price remains above it.

A simple `price > support` check would treat both as bullish. That is incorrect for our intended interpretation.

The behavior layer was added to preserve this distinction.

---

## Core idea: separate price position from support behavior

The most important design decision is that **price location** and **support motion** are separate dimensions.

### Support motion

The support itself can be classified as:

- `RISING`
- `FALLING`
- `FLAT`
- `FLAT_AFTER_RISE`
- `FLAT_AFTER_FALL`
- `UNAVAILABLE`

This captures whether structural support is actively stepping upward, actively stepping downward, or has stopped changing after a prior move.

A flattening support line is meaningful because it often marks a change in structural behavior even before price produces an obvious directional move.

### Price/support relation

Price is classified relative to support as:

- `AT_SUPPORT`
- `ABOVE_NEAR`
- `ABOVE_FAR`
- `BELOW_NEAR`
- `BELOW_FAR`
- `UNAVAILABLE`

Distance is normalized with ATR so the same logic can work across different instruments and volatility regimes.

This keeps “where price is” independent from “what support itself is doing.”

---

## Why this separation matters

The following cases motivated the model.

### 1. Healthy upward progression

Typical behavior:

- support rebases higher step by step,
- price remains above support,
- price may periodically approach support,
- reactions from support preserve the upward structure.

This should not be treated the same as simply “price is above support.”

The model can describe this as a combination such as:

- support motion: `RISING`
- relation: `ABOVE_FAR` or `ABOVE_NEAR`
- interaction: `SUPPORTED_ADVANCE`, `APPROACHING_SUPPORT`, or `HOLDING_ABOVE`

A move back toward support is not automatically bearish. It may simply be a structural retest.

### 2. Price below support while support is still falling

Typical behavior:

- price loses support,
- price remains below it,
- the support itself continues stepping lower,
- price may expand further away from support.

This is structurally different from a temporary breach around a flat level.

When persistence is present and the support remains falling, the behavior can become:

- `BREAKDOWN_ACCEPTED`
- or, when price is already far below a falling support, `DOWNSIDE_CONTINUATION`

This captures the idea that the support is not merely being violated; the structural reference itself is moving with the decline.

### 3. Price reclaims support, but support is still falling

This was one of the most important cases from the visual review.

Typical behavior:

- price was below support,
- price crosses back above support,
- but support itself is still structurally falling.

This must **not** be treated as a confirmed bullish recovery.

The model calls this:

- `RECLAIM_ATTEMPT`

This is intentionally weaker than recovery confirmation.

The reason is simple: price has changed side, but the structural support has not yet stabilized.

### 4. Recovery after support flattening

A more meaningful recovery looks like:

- price was below support,
- the support stops falling,
- support becomes flat or `FLAT_AFTER_FALL`,
- price reclaims it,
- price remains above it for a persistence window.

This can be classified as:

- `RECOVERY_CONFIRMED`

The key point is that confirmation comes from a combination of **price reclaim + persistence + support stabilization**, not from a single cross.

### 5. Failed recovery

Another important case:

- price moves from below to above support,
- then quickly falls back below,
- the reclaim cannot establish persistence.

This is classified separately as:

- `RECOVERY_FAILED`

That distinction matters because a failed reclaim carries different information from a first-time breakdown attempt.

### 6. Range around old support

A long-standing support that remains flat while price repeatedly crosses around it should not be forced into bullish or bearish continuation logic.

The model can classify this as:

- `RANGE_AROUND_SUPPORT`

This requires:

- support to remain flat for a meaningful period,
- repeated crossings,
- price to remain near the support area.

This captures the idea that the structural level has become a center of balance rather than an active directional guide.

---

## Interaction states

The behavior layer currently exposes the following higher-level interaction states:

- `HOLDING_ABOVE`
- `SUPPORTED_ADVANCE`
- `APPROACHING_SUPPORT`
- `TESTING_SUPPORT`
- `BREAKDOWN_ATTEMPT`
- `BREAKDOWN_ACCEPTED`
- `DOWNSIDE_CONTINUATION`
- `RECLAIM_ATTEMPT`
- `RECOVERY_CONFIRMED`
- `RECOVERY_FAILED`
- `RANGE_AROUND_SUPPORT`
- `UNAVAILABLE`

These states are descriptive market states, not trade decisions.

---

## Important interpretation rules

### Rule 1: `price > support` is not bullish by itself

A reclaim above a still-falling support is intentionally weaker than a reclaim above a stabilized support.

### Rule 2: `price < support` is not bearish by itself

A brief move below an old flat support is different from persistent price acceptance below a support that is itself moving lower.

### Rule 3: support direction has independent informational value

Support can continue falling even after price moves above it.

Support can flatten before price produces a strong reversal.

Support can rise while price temporarily approaches it.

The model must preserve all of these cases.

### Rule 4: persistence matters

Crossing a level is not enough.

The model distinguishes between:

- attempt,
- acceptance,
- confirmed recovery,
- failed recovery.

This avoids treating one-bar noise as a structural state change.

### Rule 5: near/far matters

A price only slightly below support is structurally different from a price that has expanded far below a falling support.

Likewise, a price approaching rising support during an uptrend is different from price collapsing through that support.

---

## Causal / closed-bar requirement

The behavior layer must remain causal.

It must only use information available in the supplied historical prefix and must not depend on future bars.

The project intentionally uses closed-bar logic for this layer.

The historical replay verifies that:

- each point is rebuilt from the prefix available at that moment,
- future data cannot change an earlier behavior classification,
- the latest historical behavior matches the direct replay result.

This constraint must be preserved in any future enhancement.

---

## Relationship to the existing lifecycle

The behavior layer does **not** replace the canonical Stabil lifecycle.

The lifecycle remains the factual source of truth for:

- support identity,
- breach state,
- reclaim facts,
- structural support level,
- canonical lifecycle transitions.

The behavior layer is intentionally additive.

Its job is to turn those facts plus causal price observations into a richer descriptive state.

A regression test ensures the new behavior layer does not alter the canonical lifecycle snapshot.

---

## Relationship to BUY/SELL

Stabil behavior must not directly produce BUY or SELL decisions.

The intended flow is:

```text
Stabil lifecycle
    -> Stabil behavior state
        -> cross-domain context
            -> future thesis / readiness / BUY-SELL layer
```

Examples:

- `APPROACHING_SUPPORT` may tell the future decision layer that a reaction area is becoming relevant.
- `RECOVERY_CONFIRMED` may strengthen a bullish thesis.
- `DOWNSIDE_CONTINUATION` may strengthen a bearish thesis.
- `RANGE_AROUND_SUPPORT` may increase ambiguity or reduce directional confidence.

But none of these states are standalone trade commands.

---

## Relationship to other domains

This work should be used as a **design reference**, not copied mechanically.

The lesson is not “every domain needs the same enum names.”

The lesson is:

> A domain should expose meaningful market behavior, not just raw detector outputs.

For example, future reviews of other domains should ask questions like:

- Is the object active, stale, consumed, weakening, strengthening, or invalidated?
- Is price approaching it, reacting from it, accepting through it, rejecting from it, or oscillating around it?
- Is the object structurally persistent or only transient?
- Is the current state continuation-like, exhaustion-like, recovery-like, or range-like?
- Is absence of evidence different from contradictory evidence?
- Can the domain describe behavior without pretending to be BUY/SELL authority?

The same philosophy should be considered for:

- Order Blocks,
- FVG / Engulfing,
- Liquidity,
- Pattern / Compression,
- Volume,
- Volatility,
- Structure / Location,
- S/R,
- HAM and other evidence layers.

However, each domain must preserve its own market meaning. Order Block, FVG, Liquidity, and Engulfing must not be collapsed into one generic “zone” concept.

---

## What this model deliberately does not do

The Stabil behavior model does not:

- emit BUY/SELL,
- calculate position size,
- set stop-loss,
- set take-profit,
- merge OB/FVG/Liquidity semantics,
- use MTF majority voting,
- declare a move valid merely because one condition is true,
- replace the canonical structural support engine.

---

## Current implementation files

Primary implementation:

- `src/financial_dashboard/engines/stabil_support_behavior.py`

Replay integration:

- `src/financial_dashboard/stabil_support_replay.py`

Tests:

- `tests/test_stabil_support_behavior.py`

The tests cover, among other cases:

- falling support becoming flat after a lower rebase,
- downside continuation below falling support,
- reclaim above still-falling support remaining unconfirmed,
- confirmed recovery after support flattening and persistence,
- failed reclaim,
- range around an old flat support,
- approach to support from above,
- supported advance with rising support,
- direct replay vs historical replay parity,
- canonical lifecycle regression safety.

---

## Summary

The Stabil enhancement is best understood as a move from **raw structural facts** to **causal market-behavior description**.

The system now treats these as separate questions:

1. Where is price relative to support?
2. What is the support itself doing?
3. Is the relationship persistent or temporary?
4. Is the market testing, accepting, reclaiming, failing, continuing, or ranging around the structure?

That is the pattern future domain work should study.

The objective is not to add more indicators. The objective is to make each existing domain describe market behavior with enough precision that a later decision layer can reason from clean, domain-specific states.
