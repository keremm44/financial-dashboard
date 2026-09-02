# ST implementation freeze — Step 13

This document is the release/governance boundary for the canonical short-term product defined by `ST_CANONICAL_ROADMAP.md`.

## What freeze means

Step 13 does **not** mean that unit tests passed or that one backtest completed successfully. The ST implementation may be called a production candidate only after its canonical behavior has been reviewed across distinct real market regimes.

The freeze gate is implemented in `financial_dashboard.decision.st_implementation_freeze` and fails closed when empirical evidence is absent.

## Frozen mechanical contract

The Step-13 gate pins the current canonical implementation contract:

- lifecycle state schema: **6**
- canonical lifecycle behavior contract: **9**
- resolved ST thesis families:
  - `PULLBACK_CONTINUATION`
  - `BREAKOUT_ACCEPTANCE`
  - `FAILED_SELL_RECLAIM`
- safe unresolved identity: `UNRESOLVED`
- default Step-12 exit calibration:
  - healthy-base buyer reaction confidence = `DEVELOPING_OR_CONFIRMED`

Changing one of these after freeze is not a calibration tweak. The freeze assessment will block until the change is explicitly reviewed and the freeze contract is deliberately revised.

## Empirical evidence required

A production-candidate assessment requires all of the following:

1. At least two distinct real historical market-regime slices.
2. Each slice must come from a canonical Step-11 behavior report with `production_performance=True`.
3. Every slice must contain at least one completed ST trade.
4. Regime identities and historical periods must be distinct; duplicate labels or duplicate periods cannot satisfy cross-regime coverage.
5. Readiness-proxy rows cannot be used as production evidence.
6. Legacy decision streams cannot be used as production evidence.
7. A release review must explicitly accept all roadmap behavior axes together:
   - strong trends are not systematically cut early;
   - mature dead ranges are not systematically held too long;
   - protective exits are not systematically late;
   - normal corrections are not systematically exited;
   - same-movement churn is controlled;
   - genuine new setups are not systematically blocked.

The review does not introduce fixed profit, time, bar-count, cooldown, or PnL thresholds. Those would change the frozen product philosophy and require their own roadmap decision.

## Canonical historical replay

The existing production backtest entry point is:

```bash
python scripts/decision_backtest.py storage/cache SYMBOL --horizon st
```

Historical cache data must be available under the supplied cache root. The canonical path uses closed bars, causal `available_at` semantics, the canonical lifecycle replay, and separates hindsight audit from information available to the decision at that timestamp.

Do **not** use `--legacy-decision-stream` as Step-13 evidence.

Do **not** use `--canonical-readiness-proxy` as production performance evidence. It remains audit-only.

Step-11 `build_st_canonical_behavior_report(...)` output is the evidence object consumed by the freeze gate. Each real regime run must be tagged with a deliberate regime identity and its actual historical start/end period before `STRegimeValidationEvidence` is constructed.

## Current repository status

The repository does not commit historical OHLCV cache data; `storage/` contains only repository placeholders. Therefore CI can verify the freeze contract and fail-closed governance behavior, but it cannot by itself satisfy cross-regime empirical acceptance.

Until real canonical multi-regime reports are supplied and reviewed, the correct Step-13 assessment is:

```text
VALIDATION_REQUIRED
```

It must not be represented as `PRODUCTION_CANDIDATE` merely because pytest, architecture audit, or shell ownership audit are green.

## What remains frozen while validation is pending

No new ST trading behavior should be added under Step 13. In particular:

- no fixed-percent take profit;
- no day/bar timeout;
- no cooldown as a substitute for setup novelty;
- no hidden ST-to-LT conversion;
- no domain-level independent SELL engines;
- no relaxation of protective precedence;
- no use of UNKNOWN as positive evidence;
- no checkpoint migration that guesses causal trade state;
- no mixing of readiness proxy or legacy streams with canonical production results.

A future behavior change must return to the roadmap with its explicit change class rather than being smuggled through the freeze/release layer.
