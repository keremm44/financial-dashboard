# Auction / Estimated Volume Profile Contract

## Purpose

The current Auction engine is built from OHLCV bars, not exchange tick-by-tick price-at-volume data. Therefore its POC, VAH, VAL, HVN and LVN outputs must be treated as **estimated auction/profile evidence**, not as an exact exchange volume profile.

## Data truth

For each candle the engine only knows:

- open
- high
- low
- close
- total candle volume

It does **not** know the exact distribution of that volume at every traded price inside the candle.

The current profile builder distributes bar volume across the candle's high-low range according to geometric overlap with price bins. This preserves total volume accounting but does not reconstruct the true exchange histogram.

Therefore:

`OHLCV_ESTIMATED_PROFILE != TRUE_PRICE_AT_VOLUME_PROFILE`

## Authority boundary

The Auction domain may describe:

- estimated value-area geometry
- estimated POC
- estimated HVN/LVN nodes
- estimated value migration
- balance / imbalance context
- acceptance / rejection relative to its own estimated profile
- proximity to estimated auction zones

The Auction domain must not claim:

- exact institutional traded-volume concentration
- exact exchange POC/HVN/LVN
- order-flow imbalance
- bid/ask delta
- footprint facts
- Market Structure facts
- liquidity-pool creation
- trade entry/exit authority

## Provenance

Every public Auction snapshot/export should expose enough provenance to distinguish the source model:

- profile_source = `OHLCV_ESTIMATED`
- source_timeframe
- bars_used
- source_volume
- allocated_volume
- allocation_error_pct
- profile_resolution_bins
- data_quality

If true price-at-volume data is introduced later, a different source classification must be used, e.g. `TRUE_PRICE_AT_VOLUME`, without silently changing the semantics of historical OHLCV-estimated outputs.

## Quality model

Profile quality must not be treated as probability.

Quality should reflect only observable implementation/data facts such as:

- sufficient history
- valid/non-zero volume coverage
- allocation conservation
- usable price range
- stable node separation
- source completeness

The engine must fail closed or downgrade quality when source bars are incomplete/gapped.

## Estimated node semantics

POC/HVN/LVN remain useful as an approximation, but public language must identify them as estimated profile levels.

Examples:

- `estimated_poc`
- `estimated_hvn`
- `estimated_lvn`
- `estimated_value_area`

UI text may use concise names, but a visible provenance/quality note must make clear that the profile is OHLCV-derived.

## Lifecycle / reaction semantics

Existing concepts remain valid within this bounded authority:

- acceptance
- rejection
- migration
- balance / imbalance

However these are reactions to the **estimated profile geometry**. They cannot become Market Structure or true order-flow facts.

## Replay requirements

Auction replay must be deterministic and prefix-safe.

For every historical point:

- only data available at `as_of` may be used
- future bars cannot rewrite prior outputs
- incomplete/open bars cannot advance confirmed analytics
- source provenance must remain stable

## Real-data validation plan

For BIST symbols with the new 15m production base, replay should record:

- estimated POC / VAH / VAL
- POC migration in ATR
- value-area migration in ATR
- reaction around VAH/VAL
- estimated HVN/LVN retests
- subsequent excursion / rejection / acceptance
- profile stability across 15m -> 30m/1h/2h/4h/1d derived frames

The goal is not to prove that estimated levels equal exchange profile levels. The goal is to determine whether the OHLCV-derived approximation has useful and stable behavioral value.

## Future true-profile path

If tick-by-tick, trade-print, footprint, or price-at-volume data becomes available later:

1. add a separate true-profile adapter/source;
2. preserve `OHLCV_ESTIMATED` compatibility;
3. compare estimated vs true POC/VAH/VAL/HVN/LVN in replay;
4. measure level error and behavioral differences;
5. only then decide whether true-profile data should replace or coexist with estimated profile evidence.

## Round plan

### A1 — Authority and provenance

- expose `OHLCV_ESTIMATED` source provenance
- rename/clarify public semantics where needed
- add data-quality fields
- add tests that prevent exact-profile claims

### A2 — Replay and diagnostics

- causal historical replay
- level/migration ledger
- acceptance/rejection diagnostics
- prefix/no-lookahead tests

### A3 — Real BIST validation

- multi-symbol replay on the 15m-base cache
- compare estimated-profile behavior across timeframes
- collect retest/acceptance/rejection statistics
- do not introduce fixed trading thresholds from one symbol

### A4 — Workspace/UI integration

- independent Auction workspace domain
- typed view-model
- Streamlit inspection surface
- visible estimated-profile provenance
- no trade authority

## Non-goals

This round will not:

- simulate tick-level truth that is not present in the data
- invent bid/ask delta
- convert Auction into Structure/Liquidity authority
- assign probabilities
- generate BUY/SELL/SL/TP outputs
