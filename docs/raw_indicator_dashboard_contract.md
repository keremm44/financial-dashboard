# Ham Indicator Dashboard v2.3.7 source contract

Authoritative source: `Ham İndikatör Dashboard v2.3.7 FINAL`.

## Tur-1 scope

Tur-1 ports only the raw-indicator/evidence half of the Pine source:

- volume-data quality
- CMF
- OBV + EMA baseline
- CCI
- RSI
- MACD / signal / histogram selection
- Momentum
- ATR + EMA baseline + ATR ratio
- Stochastic %K/%D
- Stochastic RSI %K/%D
- SMI main/signal
- price-context EMA position/order/slope
- XAGTRYG trend profiles
- dynamic thresholds
- stateful trend classification with pending/hysteresis
- raw validity
- ten signed evidence values plus relative-evidence normalization
- raw evidence counts and weighted net-evidence diagnostic

The following are intentionally not implemented in Tur-1:

- PRICE / MOMENTUM / TIMING / FLOW family aggregation
- family coverage/quorum
- decision-quality calculation
- local SYS_* system state
- weakening system lifecycle
- historical labels
- ARGENT HAM Contract v1 four-port export

Those belong to Tur-2.

## Profile rule

Profiles change only the trend engine. Indicator formulas and base periods remain fixed.

| Profile | trend | recent | consistency | dead-zone | hold | spike | dynamic length | step cap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XAG 30m | 6 | 2 | 66 | 40 | 65 | 72 | 24 | 3.0 |
| XAG 1h | 6 | 3 | 66 | 40 | 65 | 72 | 24 | 3.0 |
| XAG 2h | 5 | 2 | 60 | 35 | 65 | 70 | 20 | 3.0 |
| XAG 4h | 5 | 2 | 60 | 35 | 70 | 72 | 20 | 3.0 |
| XAG 1d | 5 | 2 | 60 | 30 | 70 | 75 | 20 | 3.5 |

## Stateful trend contract

Trend output contains:

- direction: -1 / 0 / +1
- reason code
- pending direction
- directional consistency

A dominant one-bar reversal may set visible direction to zero while the previously confirmed direction remains in state memory. The previous direction is only replaced by a confirmed opposite direction or cleared when neither confirmation, pending evidence, nor hold conditions remain.

CMF and OBV are not eligible for the stateful trend engine while volume is not calculable. In those periods their confirmed direction memory is reset rather than allowed to leak across invalid source periods.

## Evidence contract

Evidence strength is:

`direction × state factor × (60% consistency + 40% movement strength) × zone modifier × trust`

For the source v2.3.7 raw ten-evidence layer, each valid raw indicator calls this function with `trust=1.0`. Standard evidence maximum is 1.00. Stochastic and Stochastic RSI are timing evidence and have a raw maximum of 0.65; their relative evidence is normalized back to -1..+1.

CMF and OBV raw evidence is gated by calculable volume but is **not** multiplied by volume trust. Volume trust instead scales their effective contribution weights in the weighted net-evidence diagnostic and, in Tur-2, in the FLOW/family decision layer. This avoids applying the same volume-confidence penalty twice. Missing volume is never fabricated.

## Replay / performance contract

Batch replay computes the causal source arrays and state histories once, then projects confirmed/open/source-gap snapshots in original row order. It must remain equivalent to bar-by-bar incremental execution. Open and source-incomplete rows cannot enter indicator history.

## No-lookahead / data-quality contract

Only closed, source-complete bars advance confirmed engine state. Open bars and bars marked `is_complete=False` return audit status but do not append source history or mutate the confirmed snapshot. Replay and incremental execution must finish with the same confirmed snapshot, and future-tail bars must not alter earlier snapshots.
