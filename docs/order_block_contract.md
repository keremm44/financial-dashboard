# Order Block final source contract

Primary lifecycle source: `ORDER BLOCK - Fill Debug v0.4.23 TIME FIX EXPORT`.

Export source: `ARGENT_Order_Block_v0.4.22_FINAL_EXPORT.pine` / the supplied v0.4.21→v0.4.22 export diff.

## Source provenance

The supplied v0.4.23 TIME FIX file is the authoritative lifecycle/math source. Its stated v0.4.23 change is drawing old OBs from `source time` instead of `bar_index`; that is a visual-coordinate fix and does not change OB decision math.

Although the v0.4.23 indicator title contains `EXPORT`, the supplied file physically ends after the lifecycle candidate-creation block and contains no export section. Therefore Python does not invent an export algorithm. The persistent `ARGENT Export Contract v1` is taken exactly from the supplied v0.4.22 FINAL EXPORT source/diff and applied on top of the unchanged v0.4.23 lifecycle.

## Lifecycle math

The Python engine ports only:

- consecutive opposite-color A/B pair detection
- A wick preservation vs B source replacement
- source-local imbalance search on the 3rd, 4th, or 5th candle
- full source-candle high/low OB zone
- pre-confirm fill accumulation from `source + 2`
- full gap-through => 100% used
- fill cancellation threshold
- candidate expiry when its own imbalance window ends
- same source + same direction deduplication
- closed/complete-bar state advancement only in Python

## Export Contract v1

Bullish and bearish OBs are selected independently.

Only a record that is 3-evidence confirmed, not fully used, has positive zone height, and remains below the fill-cancellation threshold is export-eligible.

For each eligible record:

- `STATE`: `+1` bullish, `-1` bearish; `None` when no eligible OB exists
- `TOP/BOTTOM`: the active remaining zone, never the already-used portion
- `FILL`: `0..1`
- `SOURCE_BAR`: selected source candle index

Selection is by shortest price distance to the active remaining zone. If distances are equal within `minimum_tick`, the newer `source_index` wins. Visual distance filtering does not participate in export selection.

Open or source-incomplete bars do not advance lifecycle or overwrite the last confirmed export snapshot.

## Python audit metadata

Audit metadata is deliberately separate from Pine math:

- `OK`: closed, source-complete bar processed
- `INCOMPLETE_BAR`: open/unconfirmed bar; state/export frozen
- `SOURCE_GAP`: source/derived bar marked incomplete; state/export frozen

No missing bar is fabricated and audit status is not treated as OB evidence.

## Explicitly out of scope

The supplied Pine explicitly excludes:

- BOS / CHoCH
- trend filter
- pivot / swing confirmation
- liquidity
- buy/sell signals
- MTF
- time-based OB deletion
