# Technical Evidence Tur-1 Contract

## Boundary

Technical Evidence (TEL) is a lossless adapter/normalization layer between the deterministic analysis engines and the future Decision Engine.

TEL Tur-1 MUST NOT:

- create BUY/SELL/WAIT decisions;
- classify setup families;
- calculate entry, invalidation, target, reward or R:R;
- reinterpret or recompute upstream indicator math;
- collapse all evidence into one score;
- silently discard stale, weak or conflicting evidence;
- merge numerically close levels implicitly.

## Evidence model

Every normalized evidence item keeps these dimensions separate:

- source engine and source state;
- semantic role;
- evidence family;
- direction;
- strength;
- quality;
- freshness (reserved for Tur-2; Tur-1 leaves it unset);
- canonical data quality plus the original source-quality value;
- source bar and known bar where the upstream export provides them;
- normalized level references;
- provenance type and dependency IDs;
- source-approved raw export payload.

Semantic roles are fixed for this layer:

`CONTEXT / STRUCTURE / LOCATION / TRIGGER / CONFIRMATION / TIMING / RISK`

Evidence family is independent of role. For example HAM Momentum is family `MOMENTUM` with role `CONFIRMATION`; volume participation is family `VOLUME` with role `CONFIRMATION`.

## Lossless-first rule

TEL normalizes without replacing the engine-specific export. `raw_export` remains available to the Decision Engine/audit path.

Where an upstream engine has an intentionally narrow permanent downstream contract, TEL respects that boundary rather than exposing private engine internals:

- STABIL: `STATE / HEALTH / RISK` only.
- Volatility/Bands/Fibonacci: `REGIME / DIRECTION / QUALITY / BAND_STATE / BAND_AGREEMENT / FIB_STATE` only.
- HAM: `MOMENTUM_STATE / MOMENTUM_SCORE / TIMING_STATE / TIMING_SCORE` only.

Order Block and FVG/Engulfing retain their source-approved side/lifecycle exports and remaining-zone geometry.

## Level registry

Price levels/zones are represented as independent `NormalizedLevel` records and referenced from evidence by stable IDs.

The registry does not auto-merge nearby values in Tur-1. A Market Structure level at 100.10 and a Liquidity level at 100.12 remain distinct facts even if a future Decision Engine may treat them as confluence.

A level can represent:

- an exact price (`price`),
- a zone (`lower` / `upper`),
- or both an anchor price and zone.

Source metadata is preserved separately.

## Source bar vs known bar

When an upstream export provides a source/origin bar, TEL preserves it separately from `known_bar`.

TEL never reconstructs an earlier known time from a pivot origin. Missing source-bar information remains `None` rather than being guessed.

## Data quality

TEL keeps two fields:

1. canonical `EvidenceDataQuality` for routing/audit;
2. `source_data_quality` containing the original engine-specific value.

Canonical values:

`OK / WARMUP / INCOMPLETE_BAR / SOURCE_GAP / DATA_LIMITED / DATA_INVALID / UNSUPPORTED_TIMEFRAME / UNKNOWN`

No estimated/fabricated data state exists.

## Open/incomplete-bar safety

`TechnicalEvidenceBuilder` advances only when the supplied `EvidenceContext` is both closed and complete. Open or source-incomplete updates return the last confirmed packet unchanged.

Upstream engines remain authoritative for their own lifecycle freezing; TEL adds this final adapter-layer guard and does not fabricate replacement evidence.

## Provenance in Tur-1

The schema already contains:

- `ROOT`
- `DERIVED`
- `AGGREGATED`
- `CONTEXTUAL`
- `depends_on`

Tur-1 marks MTF Story as derived but does not yet resolve the full dependency graph. Full DAG linking, cycle/future-dependency validation, freshness semantics and conflict summaries are Tur-2 work.

No automatic derived-evidence weighting is applied in TEL.

## Determinism

Evidence IDs and level IDs are stable hashes of source identity, timeframe, bar identity and geometry. Identical confirmed inputs produce identical packets.

Tur-1 tests cover:

- engine adapter contracts;
- official downstream-port boundaries;
- level preservation;
- deterministic IDs;
- no implicit close-level merging;
- canonical/source data-quality preservation;
- open/incomplete packet freeze;
- dangling level-reference rejection.
