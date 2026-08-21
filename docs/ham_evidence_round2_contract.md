# Ham Indicator Dashboard v2.3.7 — Round 2 Support Contract

## Scope

Round 2 completes Ham as deterministic supporting evidence. It still does not own
or produce trade direction, action/status, hard blockers, Market Structure, or
Support/Resistance facts.

The intended boundary is:

```text
authoritative deterministic core result
  + independent Ham MTF evidence
  -> bounded confidence-only adapter
  -> immutable fixed-facts narration payload
  -> optional renderer (not integrated yet)
```

The repository currently has no authoritative global Decision Engine result in the
Streamlit observer. Therefore the UI inspects Ham evidence but does not invent a
core direction/confidence merely to display an adjustment.

## Confidence-only support adapter

Public module: `financial_dashboard.ham_support`

- `assess_ham_support(core_direction, evidence)` returns a typed assessment.
- `apply_ham_confidence(core, evidence)` returns a new confidence envelope.
- The original `core` object is retained by identity and is never mutated.
- The only derived final field is confidence.

The fixed contract is:

```text
ham_delta ∈ [-5, +5]
final_confidence = clamp(core_confidence + ham_delta, 0, 100)
```

For each ready timeframe/family pair:

```text
alignment(tf, family) = core_direction_sign * family_balance(tf, family) / 100
effective_weight(tf, family) = timeframe_weight * family_weight * coverage / 100
```

The weighted alignment sum is divided by the fixed full capacity of all five
timeframes and all four families. Missing, warmup, invalid, or non-ready evidence
therefore contributes zero without allowing the remaining evidence to consume the
complete `±5` budget.

### Fixed timeframe weights

| Timeframe | Weight |
|---|---:|
| `1d` | 1.00 |
| `4h` | 1.00 |
| `2h` | 0.90 |
| `1h` | 0.75 |
| `30m` | 0.60 |

All timeframes remain independent. Higher-timeframe state does not gate lower-
timeframe calculation, storage, or contribution.

### Fixed family weights

| Family | Weight |
|---|---:|
| `PRICE` | 1.35 |
| `MOMENTUM` | 1.35 |
| `TIMING` | 0.35 |
| `FLOW` | 0.80 |

FLOW balance already includes `volume_trust` in the neutral family extractor. The
adapter does not multiply FLOW by confidence or volume trust a second time.

### Symmetry and interpretation

For equal-magnitude evidence, agreement and conflict change confidence by equal
magnitudes with opposite signs. Conflict is explicitly support-only evidence. It
may be consistent with a short-term pullback, but it never declares a trend
reversal and never changes the core direction.

A neutral core direction yields no adjustment. Confidence is validated in
`[0, 100]`; the final value is clamped to that interval.

## Streamlit inspection contract

The **Ham evidence** tab is inspection/debug only and exposes:

- all five foundation timeframes, including explicit missing rows;
- source warnings/errors, profile, confirmed timestamp, history/warmup/readiness;
- ATR, ATR ratio, volume quality/trust;
- PRICE/MOMENTUM/TIMING/FLOW balance, activity, coverage, and readiness;
- all ten latest Tur-1 indicator components for a selected available timeframe;
- the latest 100 confirmed history rows by default;
- the complete confirmed history only when **Tüm geçmiş** is explicitly selected.

The tab does not expose legacy Ham `system_state`, `system_bias`, or family decision
scores. It does not show an action, recommendation, final confidence, or an applied
Ham delta because no authoritative global core result is present in this UI.

## Deterministic narration payload

Public module: `financial_dashboard.ham_narration`

`build_ham_narration_payload(...)` produces immutable fixed facts using schema
`financial-dashboard.ham-narration.v1` and mode `RENDER_FIXED_FACTS_ONLY`.
Canonical JSON and its SHA-256 fingerprint are deterministic across restarts.

The payload checks core direction/confidence consistency and post-adjustment facts.
Its policy expressly forbids a renderer from independently calculating, modifying
facts, inferring action/status, predicting, or recommending. Blocker and risk
collections must already be immutable tuples.

No Groq client, API key handling, provider call, retry path, or free/paid-tier
assumption is integrated in Round 2. A future renderer may verbalize only this
fixed payload; the deterministic fields remain authoritative.

## Guarantees covered by tests

Round 2 tests cover:

- symmetric bounded deltas and confidence clamping;
- core identity/field immutability;
- missing, warmup, and FLOW trust behavior;
- open/incomplete preview safety;
- canonical narration restart determinism and mutation detection;
- independent cached timeframe replay;
- explicit missing rows and source diagnostics;
- all ten latest components;
- recent-100 and explicit all-history UI modes;
- absence of prohibited decision-output columns in Ham view models.
