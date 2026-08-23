# Cross-Domain Context Contract

Status: **FOUNDATION IMPLEMENTATION IN PROGRESS**

This document freezes the package and authority boundaries before decision/action work. It does not define BUY/SELL logic.

## Architecture chain

```text
Native Domain Facts
        ↓
Thin Domain Projections
        ↓
Lineage / Correlation
        ↓
Qualified Zone Intelligence
        ↓
Cross-Domain Context Snapshot
        ↓
Scoped Permission Envelope
        ↓
Future Action Layer — out of scope
```

## Frozen invariants

1. Native engines remain the sole writers of their native facts.
2. Read projections do not create new native truth.
3. Supporting evidence cannot promote itself into structural authority.
4. Reaction and reversal are separate axes and may coexist with opposing directions.
5. Lower-timeframe evidence cannot create higher-timeframe structural truth.
6. Facts with `available_at > as_of` are ineligible for the snapshot.
7. Candidate is not confirmed.
8. Deduplication never deletes native facts; it only changes independence interpretation.
9. Missing/unavailable evidence is not neutral evidence.
10. Cross-domain weighted voting and raw confirmation counting are prohibited.
11. Derived identifiers must be deterministic and replay-safe.
12. Permission is not action: no BUY/SELL, entry/exit, sizing, SL/TP, or probability authority belongs in this layer.

## P1 fact-reference contract

`FactRef` is the common immutable identity/causality block embedded by later domain projections. It is composition, not a universal base class.

Required semantics:

- `native_id` identifies the native fact and is always required.
- `lineage_id` identifies a **known shared causal origin** and is optional.
- `lineage_id=None` means causal lineage is unknown. It must not be replaced by `native_id` or another fabricated identity.
- `confirmed_at=None` means the fact is still candidate/unconfirmed.
- `available_at` is always required and is later checked against snapshot `as_of`.
- `causal_family` and `source_family` are separate metadata axes; neither is a numerical weight.
- Unknown native data-quality values fail closed rather than silently becoming `VALID` or neutral.

The foundation families are:

```text
CausalFamily:
IMPULSE | STRUCTURAL_LEVEL | PARTICIPATION | REGIME | INDICATOR

SourceFamily:
PRICE_GEOMETRY | PRICE_DERIVED_INDICATOR | VOLUME_SERIES
```

Targeting remains the authority for same-timeframe origin-event deduplication. `context.lineage` only reads the existing `origin_event_id`; it does not reimplement `deduplicate_origin_events`.

Cross-timeframe lineage remains deferred until replay calibration.

## P2 thin projection contract

`context.projections` is a read-only semantic adapter. It must not import native engine implementations, `market_workspace`, UI code, or generic `EngineResult` authority fields.

The P2 projection set is:

- `StructuralFactsProjection` — Market Structure typed export/scope/event facts. Structural authority remains native Market Structure.
- `LiquidityProjection` — objective/draw observations only. Liquidity does not become direction or reversal authority.
- `ReactionEvidenceProjection` — Order Block/FVG as reaction-zone observations; Engulfing is kept separately as confirmation-only evidence.
- `StabilSupportProjection` — daily structural-support lifecycle copied without rewriting its native validity/dynamics/progression.
- `ParticipationProjection` — Volume participation observations plus explicit Structure↔Volume relations. A relation does not create shared lineage automatically.
- `PatternProjection` — pattern-scoped typed export codes only. Generic `EngineResult.direction/score/quality/is_confirmed` is not consumed.
- `VolatilityProjection` — regime/band/Fib context plus early-transition state; early state remains distinct from confirmed export state.
- `HamProjection` — PRICE/MOMENTUM/TIMING/FLOW families remain separate; family counts/quorum are not promoted to authority. FLOW remains `VOLUME_SERIES`-correlated metadata.

Additional P2 rules:

- Existing TargetEvidence `origin_event_id` is preserved when known; unknown lineage stays `None`.
- FVG/Engulfing unsupported timeframes are reported explicitly instead of being treated as neutral.
- Projection data quality is explicit. Target-evidence projections require a caller-supplied per-timeframe native quality mapping because `TargetEvidenceMTFReplay` does not expose source quality itself.
- Pattern, Volume, Volatility and HAM facts are observations/context, not independent votes.
- Projection contracts are immutable/frozen data only; they do not calculate BUY/SELL, permission, continuation, reaction, or reversal states.

## P3 Zone Intelligence contract

`context.zones` creates a derived **qualified location view**. It does not rewrite S/R, Market Structure, Stabil Support, OB/FVG/Engulfing, or Liquidity native state.

Anchor policy:

- Native S/R zones are preferred geometry anchors and retain their own range-quality fields.
- Protected High/Low may attach as structural significance; when no nearby S/R geometry exists they may remain point anchors.
- Stabil Support may attach as daily support-lifecycle context or remain a standalone support anchor.
- Order Block and FVG are reaction contributors only.
- Engulfing is confirmation-only and never becomes persistent zone geometry.
- Liquidity is an objective overlay only and never contributes to zone quality.

Zone axes remain separate:

```text
Intrinsic S/R Quality
Structural Significance
Stabil Support Context
Reaction Contributors
Interaction Confirmation
Objective Overlay
Freshness
Relevance
Interaction State
Semantic Qualification
MTF Parent/Child Relation
```

`MODERATE/HIGH/VERY_HIGH` is a semantic gate, not a score or probability. The current implementation deliberately avoids numeric cross-domain weighting. Exact semantic calibration remains replay-reviewable.

The public location queries are distinct:

```text
nearest_qualified_support / resistance
strongest_relevant_support / resistance
htf_primary_support / resistance
```

`nearest` is not treated as `strongest`, and MTF overlap is represented as parent/child hierarchy rather than multiple votes.

`context.zone_interaction` derives replay-safe states such as `APPROACHING`, `TESTING`, `DEFENDED`, `WEAKENING`, `BEING_CONSUMED`, `ACCEPTED_THROUGH`, and `RECLAIMED`. Native lifecycle is observed, not duplicated or overwritten.

## Knowledge boundary

The planned decision-time boundary is the reference `target_as_of` used by the workspace. Every projected fact must satisfy:

```text
fact.available_at <= snapshot.as_of
```

Projection creation records `available_at`; eligibility filtering is enforced later by `context.builder`. Zone Intelligence already refuses future projected reaction/liquidity facts and future S/R snapshots. Facts excluded by this rule must eventually be reported through a `KnowledgeBoundary` diagnostic rather than silently treated as neutral.

## Package boundary

```text
src/financial_dashboard/context/
├── __init__.py
├── envelope.py
├── lineage.py
├── projections.py
├── zones.py
├── zone_interaction.py
├── axes.py
├── snapshot.py
├── permissions.py
└── builder.py

src/financial_dashboard/cross_domain_replay.py
```

## Dependency direction

```text
context.envelope
    ↓
context.lineage
    ↓
context.projections
    ↓
context.zones / context.zone_interaction
    ↓
context.axes
    ↓
context.snapshot
    ↓
context.permissions

context.builder orchestrates the above and is called by market_workspace.
```

Rules:

- `context.*` must not import `market_workspace`.
- `context.*` must not import UI modules.
- `context.permissions` must not import native engines.
- Existing targeting deduplication and semantic-targeting roles are reused rather than duplicated.
- Native engine files remain unchanged during the foundation phases.

## Six-step implementation order

1. Contracts + Lineage — complete.
2. All Domain Projections — complete.
3. Zone Intelligence — current step.
4. Cross-Domain Context + Permission.
5. Builder + Workspace shadow integration.
6. Replay + no-lookahead + golden scenarios + final freeze.

## Deferred

- BUY/SELL and future action policy
- position sizing, SL/TP, R:R
- probability/confidence calibration
- sophisticated cross-timeframe lineage
- database/event-store persistence
- Auction/Volume Profile integration
- native Liquidity/OB behavior changes
