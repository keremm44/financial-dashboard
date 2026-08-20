# FVG + Engulfing source contract

Authoritative source: `ARGENT_FVG_Engulfing_v0.3.8_FINAL_EXPORT.pine`.

## Production boundary

The source explicitly limits production scope to FVG and Engulfing zones plus their lifecycles. General candle-direction/story logic, continuation/rejection/compression/shock outputs, dashboard, combined quality, and story-label layers are not part of this engine contract.

The engine supports only:

- 2h
- 4h
- 1d

No unsupported timeframe is silently coerced.

## Exact source states

### FVG

- NONE = 1
- CANDIDATE = 2
- ACTIVE = 3
- FIRST_TEST = 4
- PARTIAL_FILL = 5
- DEEP_TEST = 6
- FULL_FILL = 7
- REACTION = 8
- FAILED_REACTION = 9
- INVALID = 10
- SUPERSEDED = 11

Direction: NONE=0, BULLISH=+1, BEARISH=-1.

### Engulfing

- NONE = 0
- ACTIVE = 1
- FIRST_TEST = 2
- PARTIAL_RETRACE = 3
- CONTINUATION_CONFIRMED = 4
- WEAKENED = 5
- INVALID = 6
- EXPIRED = 7

Direction: NONE=0, BULLISH=+1, BEARISH=-1.

## Two-tour implementation plan

### Tur-1 — detector + formation foundation

- source-faithful shared OHLC/ATR/context metrics used by FVG/Engulfing only
- sensitivity profiles: Hassas / Dengeli / Seçici
- supported-timeframe guard
- bullish/bearish FVG candidate and formation geometry
- FVG formation quality and frozen formation references
- bullish/bearish Engulfing detection and swallowed-body zone geometry
- Engulfing formation quality and frozen formation references
- closed/complete-bar advancement only
- warmup/source-gap audit separated from indicator evidence
- focused detector parity tests
- replay == incremental and future-tail invariance for formation events

### Tur-2 — lifecycle + takeover + export

- bullish/bearish FVG lifecycle through test/fill/reaction/failure/invalidation/superseded
- FVG takeover rules and frozen references
- bullish/bearish Engulfing lifecycle through test/retrace/continuation/weakened/invalid/expired
- directional terminal-event memory so same-bar bull/bear terminal events cannot overwrite each other
- source `ARGENT Export Contract v1`
- 24 hidden export ports
- FVG TOP/BOTTOM = still-unfilled active region
- Engulfing TOP/BOTTOM = original swallowed-body zone
- FILL / RETRACE / QUALITY / STATE / EVENT ports
- open/source-incomplete bars freeze the confirmed snapshot
- final lifecycle replay parity and full-suite CI

## Export contract facts

The v0.3.8 export adapter adds no detector math. FVG and Engulfing exports are independent of visual fallback/recent boxes. Formation QUALITY is a frozen heuristic quality value, not probability. Directional EVENT ports are visible only for the most recent closed snapshot bar.

## Data-quality boundary

Python may expose audit metadata (`OK`, `WARMUP`, `INCOMPLETE_BAR`, `SOURCE_GAP`, `UNSUPPORTED_TIMEFRAME`) but these statuses are not indicator evidence and must not alter source math. Missing bars are never fabricated.
