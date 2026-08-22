# Ham Indicator Dashboard v2.3.7 — Round 1 Evidence Contract

## Scope

Round 1 exposes Ham as deterministic, action-free measurement evidence. It does not
produce an action, trade direction, blocker, Market Structure state, S/R state, or
confidence adjustment.

The public evidence path is:

```text
closed + complete OHLCV
  -> RawIndicatorDashboardEngine (Tur-1)
  -> exact neutral PRICE/MOMENTUM/TIMING/FLOW extraction
  -> immutable per-bar evidence history
```

The source Tur-2 decision engine remains in the repository for parity and internal
audit. Its `_families` method delegates to the same neutral extractor, so family
math is not duplicated. Tur-2 `system_state`, `system_bias`, and
`family_decision_score` are not fields of the Round 1 public evidence snapshot.

## Public modules

- `financial_dashboard.engines.ham_evidence`
  - `HamEvidenceEngine`
  - `HamEvidenceSnapshot`
  - `HamFamilyEvidenceSet`
  - `FamilySnapshot` / `HamFamilyEvidence`
  - `build_ham_family_evidence`
- `financial_dashboard.ham_mtf_replay`
  - `HamMTFEvidenceReplayRunner`
  - `HamMTFEvidenceReplay`
  - `HamTimeframeEvidenceReplay`
  - `replay_ham_evidence_from_cache`

## Tur-1 indicator evidence

Every confirmed snapshot preserves all ten source components:

1. `PRICE_CONTEXT`
2. `MACD`
3. `MOMENTUM`
4. `RSI`
5. `CCI`
6. `SMI`
7. `CMF`
8. `OBV`
9. `STOCHASTIC`
10. `STOCH_RSI`

Each `IndicatorEvidence` retains source value, validity, confirmed direction,
pending direction, `TrendReason`, consistency, movement strength, signed zone,
absolute evidence, and relative evidence. The raw snapshot also retains ATR,
ATR ratio, valid/up/down/strong counts, volume coverage/variation/calculability,
volume reliability/trust, and explicit raw data quality.

## Neutral families

The extractor preserves the v2.3.7 family math exactly:

- **PRICE**: normalized `PRICE_CONTEXT`.
- **MOMENTUM**: equal 50/50 role blend of:
  - impulse: `MACD`, `MOMENTUM`;
  - oscillators: `RSI`, `CCI`, `SMI`.
- **TIMING**: weighted `STOCHASTIC` + `STOCH_RSI`, normalized by the source
  timing evidence capacity of `0.65`.
- **FLOW**: weighted `CMF` + `OBV`, scaled by source `volume_trust` exactly once.

Family balance/activity are descriptive values in `[-100, +100]` and `[0, 100]`.
Coverage and readiness are explicit. FLOW additionally exposes confidence equal to
source volume trust. A non-ready family remains present; it is not silently neutral.

## MTF profile mapping

Each timeframe is replayed by a fresh engine with its matching source profile:

| Timeframe | `TrendProfile` |
|---|---|
| `1d` | `XAG_1D` |
| `4h` | `XAG_4H` |
| `2h` | `XAG_2H` |
| `1h` | `XAG_1H` |
| `30m` | `XAG_30M` |

Default MTF order is `1d`, `4h`, `2h`, `1h`, `30m`. Timeframes are independent:
no higher-timeframe result can gate lower-timeframe calculation or retention.

## Full-cache and history rules

- The Parquet file for each timeframe is loaded in full.
- `prepare_engine_input` validates source quality, sorts timestamps, and excludes
  every row that is not both closed and complete.
- Every usable row receives one immutable `HamEvidenceSnapshot` in the timeframe
  history, including warmup rows.
- `latest` is the last confirmed snapshot; it is not a preview.
- Histories are held as compact tuples inside the replay result. Round 1 does not
  create one persistence file per bar or per snapshot.
- An open/incomplete row may produce a transient quality response when passed to
  `HamEvidenceEngine` directly, but it never advances confirmed snapshot/history.
- Source quality and indicator warmup are separate contracts. A 43-bar daily cache,
  for example, remains fully retained while the 50-bar volume window reports
  `WAITING` and FLOW remains not ready.

## Causal guarantees

The Round 1 tests require and verify:

- prefix/no-lookahead parity;
- batch replay and one-bar incremental parity;
- restart determinism;
- timeframe state isolation;
- full-cache history retention;
- missing and limited volume behavior;
- open/incomplete preview safety;
- exact family parity with the unchanged Tur-2 source logic.

## Round 2 status

Round 1 intentionally excluded the bounded support adapter, confidence adjustment,
Streamlit Ham views, and narration boundary. Those deterministic layers are now
implemented under the separate
[`ham_evidence_round2_contract.md`](ham_evidence_round2_contract.md) contract.
Groq/provider integration remains intentionally absent.
