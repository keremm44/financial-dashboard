# Stabil Daily Structural Support Lifecycle Contract

## Purpose

This contract narrows the future Stabil role to one observable question:

> How is price behaving relative to the existing daily structural-support stepline over time?

It is descriptive only. It has no BUY/SELL/WAIT, entry, stop, take-profit, target, probability, or main-trend-reversal authority.

## Source preservation

Phase 1 intentionally preserves the **same daily support source already used by the existing Stabil implementation**:

- confirmed Stabil daily pivot lows;
- pivot `origin_time` and right-side `known_time`;
- the existing structural floor:
  `support_floor = support_level - atr_at_origin * support_atr_tolerance`.

The lifecycle does **not** replace this observed stepline with Market Structure or Support/Resistance output in this phase. Doing that would change the line whose behavior is being studied and would invalidate visual/replay comparability.

Market Structure remains authoritative for BOS/CHoCH/HH-HL and may later be composed with Stabil evidence, but it does not silently redefine the Stabil support line.

## Causal timestamps

Every support keeps three distinct timestamps:

- `support_origin_at`: pivot bar where the low physically occurred;
- `support_confirmed_at`: right-side pivot confirmation time;
- `support_available_at`: first bar where the current Stabil support contract can actually expose that pivot.

`support_available_at` is the lifecycle knowledge boundary. A pivot must never be projected backwards from confirmation/availability into historical bars.

Only closed + complete daily bars are processed.

## Two-track state

### Support validity

- `NO_SUPPORT`: no usable daily support is causally available yet.
- `ACTIVE`: close is at/above the support line.
- `BREACHED`: close is below the green support line but not below the structural floor.
- `BELOW_FLOOR`: close is below the existing structural floor.

`BELOW_FLOOR` is not made permanently terminal by an arbitrary bar-count rule. The old support may still be reclaimed. A later confirmed lower support is the causal evidence that rebases the reference lower.

### Price-to-support dynamics

- `UNAVAILABLE`: no usable support.
- `AT_SUPPORT`: the completed candle range touches the support line and closes at/above it.
- `EXPANDING`: price-to-support distance increased versus the prior completed bar for the **same support identity**.
- `CONTRACTING`: price-to-support distance decreased versus the prior completed bar for the same support identity.
- `FLAT`: distance did not materially change beyond price precision.
- `BELOW_SUPPORT`: close is below the support line.

No `%5`, `%10`, `%20`, 7-bar, or 8-bar threshold defines these states.

Distance direction uses the change in raw price distance, normalized by current ATR for diagnostics. A support rebase resets the comparison baseline so a change in the support level itself cannot masquerade as price expansion/contraction.

## Factual diagnostics

The snapshot keeps:

- `support_level`
- `support_floor`
- `distance_pct`
- `distance_atr`
- `distance_delta_atr`
- `bars_since_support`
- `bars_above_support`
- `bars_below_support`
- `reclaim_count`
- `intrabar_below_support`
- `close_below_support`
- `close_below_floor`
- support provenance timestamps
- support progression

`distance_pct` is primarily human-readable/replay data. `distance_atr` is regime-normalized diagnostics. Neither implies a price target.

`bars_below_support` is factual. The manual 7–8 bar observation remains a replay hypothesis and is not hard-coded.

## Support progression

Progression is structural step movement, not moving-average direction:

- `INITIAL`
- `SAME`
- `REBASED_HIGHER`
- `REBASED_LOWER`

A higher/lower rebase is emitted only when a newly available confirmed Stabil pivot changes the stepline. The new level is never written back to bars before its availability.

## Immutable event ledger

Events are append-only in causal replay order:

- `SUPPORT_CONFIRMED`
- `SUPPORT_TESTED`
- `SUPPORT_HELD`
- `SUPPORT_BREACHED`
- `SUPPORT_FLOOR_BROKEN`
- `SUPPORT_RECLAIMED`
- `SUPPORT_LOST`
- `SUPPORT_REBASED_HIGHER`
- `SUPPORT_REBASED_LOWER`

`SUPPORT_LOST` is intentionally conservative in this first contract: it is emitted when an old support was already below/broken and a **new confirmed lower support** becomes available. It is not triggered by an arbitrary number of bars below support.

Every event stores origin/confirmation/availability provenance, support geometry, price, ATR distances, bar counters, reclaim count, and progression.

## What is intentionally outside this core

The new lifecycle does not calculate:

- Volume Participation, selling pressure, shock or absorption;
- BOS/CHoCH/HH-HL;
- Weekly main-trend state;
- H4 recovery/displacement;
- gap narrative;
- Liquidity, Order Block, FVG, Engulfing;
- health/risk confidence scores;
- main trend reversal;
- execution actions.

The existing legacy Stabil Trend engine remains untouched during the first migration round so source-parity tests and historical compatibility are not destroyed.

## Replay hypotheses, not rules

The following remain research questions:

- reclaim rate versus `bars_below_support`, including the manual 7–8 bar observation;
- MFE/MAE after support hold/reclaim;
- distribution of maximum distance from support in percent and ATR;
- retest frequency and time-to-retest;
- behavior after higher/lower support rebase;
- whether a persistence rule is ever justified;
- whether distance-direction noise requires a calibrated filter.

Any threshold introduced later must be justified by causal replay and checked out-of-sample.

## Migration plan

### Round 1

- add the independent daily support lifecycle core;
- preserve the current Stabil stepline source exactly;
- add causal provenance and append-only event ledger;
- add factual distance/bar diagnostics;
- regression-test breach/reclaim/rebase/no-lookahead/open-tail behavior;
- leave legacy Stabil untouched.

### Round 2

- add a daily lifecycle replay/workspace domain;
- expose typed view-model/UI state;
- add replay diagnostics for `bars_below`, reclaim, retest and rebase;
- compose, but do not merge authority with, Market Structure and Volume;
- keep legacy Stabil available only for compatibility/audit until the new contract is validated.

## Authority rule

`support held + price expanding` is **not** `main trend reversed`.

Stabil emits only its own support-lifecycle facts. A later composition layer may read independent Market Structure, Volume and other evidence, but no single Stabil state can manufacture another domain's authority.
