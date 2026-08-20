# FVG + Engulfing final source contract

Authoritative source: `ARGENT_FVG_Engulfing_v0.3.8_FINAL_EXPORT.pine`.

## Production boundary

Production output is limited to FVG and Engulfing zones plus their lifecycles. Internal continuation/rejection/shock calculations are retained only where the source uses them as detector/lifecycle evidence; they are not exposed as standalone production signals. Dashboard, combined quality and story-label layers are excluded.

Supported timeframes are exactly `2h`, `4h`, and `1d`. Unsupported timeframes are never silently coerced.

## Exact source states

FVG: `NONE=1`, `CANDIDATE=2`, `ACTIVE=3`, `FIRST_TEST=4`, `PARTIAL_FILL=5`, `DEEP_TEST=6`, `FULL_FILL=7`, `REACTION=8`, `FAILED_REACTION=9`, `INVALID=10`, `SUPERSEDED=11`.

Engulfing: `NONE=0`, `ACTIVE=1`, `FIRST_TEST=2`, `PARTIAL_RETRACE=3`, `CONTINUATION_CONFIRMED=4`, `WEAKENED=5`, `INVALID=6`, `EXPIRED=7`.

Directions are `NONE=0`, `BULLISH=+1`, `BEARISH=-1`.

## Tur-1 — detector + immutable formation events

Completed:

- Pine-style ATR/RMA and private flow/local-context prerequisites
- sensitivity profiles `Hassas / Dengeli / Seçici`
- bullish/bearish three-bar FVG geometry
- source middle-candle formation ATR
- gap size/gap-ATR, displacement, progress, efficiency and opening-gap defense
- source evidence count and frozen formation quality
- candidate vs active FVG formation gates
- bullish/bearish Engulfing detection, gap/micro-candle/context protection
- frozen swallowed-body zone and Engulfing quality
- immutable formation events
- closed/complete-bar advancement, warmup/source-gap audit separation
- replay == incremental and future-tail invariance

## Tur-2 — lifecycle + takeover + export

Completed:

- FVG candidate promotion and same-bar promotion+first-test preservation
- wick/close fill memory and `FIRST_TEST / PARTIAL_FILL / DEEP_TEST`
- frozen formation-ATR invalidation buffer and profile close-count invalidation
- age invalidation, full fill, reaction, failed reaction
- FVG takeover by candidate replacement / quality / age / meaningful distance
- replaced FVG recorded as `SUPERSEDED`
- Engulfing first test, retrace, continuation confirmation, weakened grace and expiry
- same-direction Engulfing quality takeover
- independent bullish/bearish terminal-event memory
- continuation-candidate candle-family parity for FVG embedded alignment without double counting
- `ARGENT Export Contract v1` represented as four directional side records / 24 source ports
- bearish state/event sign inversion
- FVG TOP/BOTTOM expose only the still-unfilled region
- Engulfing TOP/BOTTOM preserve original swallowed-body region
- QUALITY remains frozen formation quality, never probability
- EVENT exists only on the matching most-recent closed snapshot index
- open/source-incomplete bars cannot overwrite confirmed lifecycle export
- final replay == incremental lifecycle/export parity

## Closed-bar source order

For each confirmed source-complete bar, existing FVG/Engulfing records are updated first. Terminal state/event memory is written before a same-bar new formation is evaluated for takeover. This preserves the Pine lifecycle ordering and prevents a newly detected zone from retroactively participating in the prior record's test/fill logic.

## Data quality

Python audit metadata (`OK`, `WARMUP`, `INCOMPLETE_BAR`, `SOURCE_GAP`, `UNSUPPORTED_TIMEFRAME`) is separate from indicator evidence. Missing bars are never fabricated. Open or incomplete bars do not advance confirmed lifecycle state or overwrite the last confirmed export snapshot.
