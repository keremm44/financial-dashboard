# Stabil Trend final source contract

Authoritative source: `ARGENT_Stabil_Yukselis_v0.4.4_FINAL_EXPORT.pine`.

## Preservation strategy

The original `agent/stabil-trend` implementation was based on an older main commit. To avoid reverting newer engines, the four Stabil implementation files and their focused tests are restored unchanged on top of current main. Shared package exports are merged additively; newer FVG/Engulfing, Order Block, Volatility/Bands/Fib and data-layer code are not replaced.

## Lifecycle scope

- Weekly structural trend / EMA / acceptance context
- Daily structural support and frozen pullback lifecycle
- H4 displacement / recovery / failure lifecycle
- pivot origin vs known-time separation
- closed/complete timeframe filtering and as-of prefix safety
- final MAIN_* resolver and reason priority
- stabilized weekly, daily, H4, overall health and risk score snapshots

## ARGENT Export Contract v1

Only three downstream ports belong to STABIL:

- `ARGENT | STABIL | STATE`
  - 1 Stable Uptrend
  - 2 Healthy Uptrend
  - 3 Controlled Correction
  - 4 Recovery Starting
  - 5 Overextended
  - 6 Uptrend Weakening
  - 7 Not Stable Uptrend
  - unavailable when core W/D data or main state is unsafe/pending
- `ARGENT | STABIL | HEALTH`
  - stabilized `overallTrendScore`, 0..100
  - heuristic trend-health score, not probability
- `ARGENT | STABIL | RISK`
  - stabilized `riskPressureScore`, 0..100
  - heuristic risk-pressure score, not probability

Daily support, support-break floor and H4 event levels remain internal. STABIL is a trend-health domain and must not become a location engine.

## Data/no-lookahead contract

- open/incomplete bars cannot mutate confirmed W/D/H4 snapshots
- pivots become usable only at known-time after right-side confirmation
- pullback origin/reference ATR and H4 event references freeze when their lifecycle begins
- repeated analysis of the same closed source snapshots cannot apply stabilization twice
- future tails after an as-of cutoff cannot alter prior output
