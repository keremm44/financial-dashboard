# Ham Indicator Dashboard v2.3.7 — Tur-2 contract

Authoritative source: `Ham İndikatör Dashboard v2.3.7 FINAL`.

Tur-2 consumes only causal Tur-1 snapshots. It does not recalculate OHLCV indicators.

The exact family aggregation now lives in the neutral Round 1 extractor
`build_ham_family_evidence`; Tur-2 delegates to it for source-parity audit. This does
not make Tur-2 `system_state`, `system_bias`, or family decision score part of the
normal Ham evidence contract. See `ham_evidence_round1_contract.md`.

## Decision families

Four normalized families are produced on a common -100..+100 scale:

- PRICE: price context
- MOMENTUM: equal 50/50 roles between impulse (MACD + Momentum) and oscillator state (RSI + CCI + SMI)
- TIMING: Stochastic + Stochastic RSI, normalized from their 0.65 raw evidence capacity
- FLOW: CMF + OBV, with volume trust applied to family confidence / effective decision weight

Decision weights are source-faithful:

| Family | Weight |
|---|---:|
| PRICE | 1.35 |
| MOMENTUM | 1.35 |
| FLOW | 0.80 |
| TIMING | 0.35 |

The combined family decision score is not a probability.

## Family thresholds

Default common family thresholds:

- weak: 15
- healthy: 35
- strong: 60
- minimum family coverage: 75%

Threshold ordering is hardened so weak < healthy < strong even if user configuration is inverted.

## Quality

Decision quality is built from:

- 40% family decision strength
- 20% family agreement
- 20% PRICE+MOMENTUM core confirmation
- 10% effective family coverage
- 10% weighted indicator directional consistency

Penalties are limited to:

- strong opposite core evidence: -15
- pending evidence count greater than confirmed evidence count: -10

ATR volatility and timing mismatch are intentionally not applied as a second quality penalty. They remain separate risk flags.

## Conflict / reaction / quorum

Conflict can be produced by:

- healthy PRICE vs MOMENTUM opposition
- strong core split with insufficient net score
- balanced two-up / two-down family split

Counter-trend REACTION requires more than TIMING alone. It requires:

- healthy timing in the reaction direction
- weak opposite price context
- second-family support from MOMENTUM or FLOW
- minimum signed family score
- minimum quality
- no strong opposite core family

Strong / healthy / developing / pressure states follow the source quorum rules. Strong and healthy states require PRICE + MOMENTUM plus secondary TIMING/FLOW support.

## Weakening lifecycle

Weakening is only eligible when:

- the previous system state was directionally bullish/bearish
- the current base state has not confirmed the opposite direction
- conflict is absent
- valid family count is unchanged
- effective family decision weight changed by no more than 10%
- signed family score remains on the previous side of zero
- score drop exceeds the configured weakening threshold

This prevents data-coverage changes from masquerading as market weakening.

## Atomic snapshot contract

Only closed, source-complete bars advance Tur-1 and Tur-2 state. Open bars and source gaps return audit data quality but do not mutate the confirmed decision snapshot.

Replay must equal incremental execution, and future-tail bars cannot alter historical decision snapshots.

## ARGENT HAM Contract v1

Only four closed-snapshot ports are public:

- `MOMENTUM_STATE`
- `MOMENTUM_SCORE`
- `TIMING_STATE`
- `TIMING_SCORE`

Direction state semantics:

- +2 / -2 = healthy or strong directional family
- +1 / -1 = weak directional family
- 0 = ready but neutral/conflicting
- unavailable = family not ready or decision chart blocked

PRICE, FLOW, ATR and the combined system state are intentionally excluded from this cross-engine contract to avoid double counting in downstream global decision layers.
