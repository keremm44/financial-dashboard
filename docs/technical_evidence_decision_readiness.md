# Technical Evidence — Decision Readiness Audit

This note freezes the final boundary between Technical Evidence (TEL) and the future deterministic Decision Engine.

## TEL authority

TEL is an adapter, normalization, provenance and audit layer. It has no trading authority.

TEL may expose facts, source-approved scores/quality, levels, freshness metadata, lineage and mechanical conflicts. It must not choose a setup, action, entry, invalidation, target, position state or reward/risk outcome.

## Permanent downstream boundaries

The following narrow contracts remain intentional:

- Stabil Trend: `STATE / HEALTH / RISK` only. Internal direction is not promoted downstream.
- Volatility/Bands/Fibonacci: `REGIME / DIRECTION / QUALITY / BAND_STATE / BAND_AGREEMENT / FIB_STATE` only.
- Ham Dashboard: `MOMENTUM_STATE / MOMENTUM_SCORE / TIMING_STATE / TIMING_SCORE` only.
- Order Block: source-approved bull/bear lifecycle zones and fill/source fields.
- FVG/Engulfing: source-approved bull/bear lifecycle zones, quality, fill/retrace and event ports. Private candle-story evidence is not promoted.

MTF Story `confidence` is a heuristic coherence value, not a calibrated probability. TEL preserves it only in the source payload and never converts it to probability or duplicates `quality` into an invented `strength`.

## Pattern separation

Pattern geometry and trigger lifecycle are separate facts:

- `PATTERN_GEOMETRY` is structural evidence.
- `PATTERN_BREAK` exists only when signed `BREAK_STATE` is non-zero.
- `PATTERN_RETEST` exists only when the source retest lifecycle is present.

`BREAK_STATE` is source-defined and signed: positive lifecycle codes are bullish and negative lifecycle codes are bearish. Classic pattern direction is not treated as proof that a break occurred.

## Liquidity separation

Sweep/reclaim and consume have different semantics:

- SWEEP / RECLAIM are reversal-style trigger events using the source event direction.
- CONSUME is a structured continuation fact: consumed BSL -> up continuation; consumed SSL -> down continuation.
- CONSUME does not rewrite the Liquidity engine's own neutral/reversal decision state and is emitted only on the bar where consumption occurs.

The Decision Engine must not recover CONSUME by parsing strings.

## Source bar discipline

`source_bar` means causal origin/event bar, never merely a known/confirmation index.

Therefore:

- Order Block source bars remain source bars because the public contract supplies them.
- Liquidity SWEEP/RECLAIM and CONSUME are anchored to the current closed bar because those exports are current-bar event ports.
- FVG/Engulfing `EVENT` evidence is anchored to the current closed bar because the source facade exports an event only when its event index equals the current export index.
- Active FVG/Engulfing zone origin remains unknown because the permanent export does not expose formation index.
- Support/Resistance break candidate/confirmed indices remain metadata and are not relabelled as source bars.
- Volume Participation pivot known indices remain metadata and are not relabelled as source bars.

If causal origin cannot be proven, freshness remains `UNKNOWN` rather than guessed.

## Snapshot integrity

A `TechnicalEvidencePacket` represents exactly one timeframe snapshot.

It rejects:

- evidence from another timeframe;
- levels from another timeframe;
- duplicate evidence/level IDs;
- dangling level references;
- evidence or levels known after the packet's `known_bar`.

Same-timeframe engine packets may be coalesced into a Tur-2 bundle only when they have the same known bar and timestamp. Historical and current snapshots for the same timeframe cannot coexist as current evidence.

Source result timestamps and merged packet timestamps must match their `EvidenceContext`; a missing timestamp is not silently treated as equal to a timestamped snapshot.

## Freshness

Freshness is metadata only. It is never a filter or automatic weight.

Numeric freshness requires a defensible causal source/event bar. Current-bar event ports can therefore have freshness `1.0` at their event snapshot. Persistent zones without a public origin remain `UNKNOWN` unless a valid same-source anchor exists.

Stale evidence is retained.

## Provenance and independence

Derived or aggregated evidence is retained but not silently promoted to an independent root vote.

MTF Story links to visible Market Structure/Pattern dependencies when those public timeframe states are present. Multi-port engines share independence groups so their ports cannot be interpreted as flat independent votes. Ham Momentum and Ham Timing intentionally remain separate independence groups because they are separate permanent downstream contracts.

TEL itself applies no weighting or depth discount.

## Evidence IDs versus lifecycle identity

TEL evidence and level IDs identify facts within an as-of snapshot. They are not a promise of stable identity across future snapshots because `known_bar`, timestamp and mutable remaining-zone geometry can legitimately change.

The future Decision Engine must create its own persistent `setup_id` / lifecycle identity when a setup candidate is opened. It must not use TEL snapshot IDs as position-state identity.

Where a source provides stable identity metadata (for example Liquidity identity or Order Block source bar), the Decision Engine may retain it as supporting provenance for that setup.

## Decision Engine handoff

The Decision Engine receives:

- detailed evidence;
- normalized levels/zones;
- provenance/lineage;
- independence groups;
- freshness availability;
- mechanical conflicts;
- semantic role indexes;
- data-quality/audit information.

It alone will be responsible for:

- setup-family selection;
- hard blockers and soft qualifiers;
- entry/invalidation/target selection;
- setup quality;
- setup expiry;
- action/state transitions;
- persistent setup/position identity.

No AI/LLM belongs in that authority path. Any later LLM integration may only render an already-final deterministic `DecisionResult` into readable language.
