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

## P4 Cross-Domain Context axes

`context.axes` is a set of small deterministic evaluators. It is not a mega decision score.

The public axes are independent fields:

```text
STRUCTURAL_THESIS
CONTINUATION_CONTEXT
REACTION_CONTEXT + REACTION_DIRECTION
REVERSAL_CONTEXT + REVERSAL_DIRECTION
OBJECTIVE_CONTEXT
PARTICIPATION_CONTEXT
VOLATILITY_CONTEXT
PATTERN_READINESS
MTF_CONTEXT
HAM_READINESS
CONFLICT_STATE
```

Authority rules:

- `STRUCTURAL_THESIS` is derived only from the explicitly supplied anchor-timeframe canonical Market Structure projection.
- Anchor timeframe is mandatory; lower-timeframe structure cannot silently become higher-timeframe truth.
- `CONTINUATION_CONTEXT=ALIGNED` requires an aligned canonical external BOS on the anchor timeframe. Volume, Pattern, HAM, FVG or Zone context cannot manufacture continuation authority.
- `REACTION_CONTEXT` is derived from qualified-zone interaction with reaction contributors. A counter-trend reaction may coexist with an unchanged main structural thesis.
- `REVERSAL_CONTEXT=CANDIDATE` requires anchor-timeframe structural transition/CHoCH semantics. `STRUCTURALLY_CONFIRMED` requires canonical structural follow-through; supporting domains do not upgrade reversal themselves.
- Liquidity remains objective direction only.
- Volume is participation context only.
- Pattern is pattern-scoped readiness only.
- Volatility is regime/readiness context only.
- HAM readiness reports availability/coverage state and is not an independent vote.
- MTF context is hierarchical relation to the explicit anchor; it is not a timeframe vote.
- `CONFLICT_STATE` preserves contradictory context rather than forcing one global direction.

Examples that are intentionally valid:

```text
STRUCTURAL_THESIS = DOWN
REACTION_CONTEXT = ACTIVE
REACTION_DIRECTION = UP
REVERSAL_CONTEXT = NOT_PRESENT
```

and:

```text
STRUCTURAL_THESIS = DOWN
MTF_CONTEXT = COUNTER_REACTION
```

Neither combination rewrites the 4H/HTF structural authority.

## P4 Context snapshot and knowledge boundary

`CrossDomainContextSnapshot` is immutable and has exactly one `as_of` and one explicit `anchor_timeframe`.

`KnowledgeBoundary` records:

- eligible facts at `as_of`,
- future facts excluded because `available_at > as_of`,
- unconfirmed facts,
- unsupported context/TF diagnostics.

Future facts are excluded and reported; they are not converted into neutral evidence.

`source_refs` may contain only facts satisfying:

```text
fact.available_at <= snapshot.as_of
```

Candidate/unconfirmed status remains visible and is not promoted to confirmed authority by the snapshot.

## P4 Scoped Permission Envelope

`PermissionEnvelope` has three separate semantics:

```text
permission_scope
permitted_side
gate_state
```

Current scopes:

```text
NONE
REACTION_ONLY
CONTINUATION_ONLY
STRUCTURAL_TRANSITION
```

Current gate states:

```text
BLOCKED
WAITING
CONDITIONAL
OPEN
```

The resolver is rule/gate based, not count based. Canonical structural blockers are evaluated first. Supporting domains may block or delay a scope but cannot manufacture structural authority.

Examples:

```text
scope = REACTION_ONLY
permitted_side = LONG
gate_state = CONDITIONAL
```

or:

```text
scope = CONTINUATION_ONLY
permitted_side = SHORT
gate_state = OPEN
```

These outputs are **not BUY/SELL**. They contain no entry, exit, stop, target, position-size, or probability policy. Even `OPEN` means only that the cross-domain permission gate is open; a future Action Layer must still perform timing/action policy.

`REDUCED_SIZE_ONLY` and other sizing semantics are intentionally absent.

## P5 Builder and workspace shadow integration

`context.builder` is the orchestration boundary for the completed P1-P4 contracts. It does not run or own native engine calculations. It accepts already-produced replay/domain outputs, projects them, applies one knowledge boundary, derives Zone Intelligence and context axes, then resolves scoped permission.

The runtime path is:

```text
existing workspace/domain replays
        ↓
target/reference decision boundary (`target_as_of`)
        ↓
target-bounded Structure/SR replay
        ↓
context.builder
        ↓
thin projections
        ↓
future-fact filtering (`available_at <= as_of`)
        ↓
lineage + Zone Intelligence + context axes
        ↓
CrossDomainContextSnapshot
        ↓
PermissionEnvelope
        ↓
MarketAnalysisWorkspace.cross_domain  [shadow/read-only]
```

P5 rules:

- The workspace's existing reference `target_as_of` is the single cross-domain decision boundary.
- Canonical Structure/SR consumed by the builder comes from the already-created target-bounded replay, so structural scope is not evaluated with bars beyond `target_as_of`.
- Other existing domain results are reused rather than rerun. If a projection contains a fact whose `available_at > target_as_of`, the builder removes that fact from axis/zone eligibility and retains it only as a `KnowledgeBoundary` exclusion diagnostic.
- Pattern, Volume, Stabil Support, Volatility and HAM are therefore allowed to be produced by their existing workspace paths without being granted future knowledge.
- Optional domain failure does not silently become neutral. The failed domain is omitted from semantic evaluation and an explicit `*_ERROR` unsupported-context diagnostic is attached.
- FVG/Engulfing unsupported timeframes remain explicit diagnostics.
- `market_workspace.py` only coordinates the builder call and stores `WorkspaceDomainResult`; cross-domain business logic remains inside `context.*`.
- `context.builder` must not import `market_workspace`, UI modules, or native engine implementations.
- The integration is additive/shadow-only. Existing observer, targeting, semantic-targeting, UI, and native-domain outputs are not rewritten by cross-domain context or permission.
- `MarketAnalysisWorkspace.cross_domain` failure is isolated from existing native/targeting outputs.
- The cross-domain result still ends at `PermissionEnvelope`; no BUY/SELL or Future Action Layer is introduced in P5.

Anchor selection in the current workspace integration is explicit and deterministic: prefer `4h`, then `2h`, then the reference timeframe if the higher structural anchors are not requested. This is selection of the canonical context anchor, not an MTF vote.

## Knowledge boundary

The decision-time boundary is the reference `target_as_of` used by the workspace. Every eligible projected fact must satisfy:

```text
fact.available_at <= snapshot.as_of
```

Projection creation records `available_at`; `context.builder` now enforces the common workspace decision boundary before Zone Intelligence/context construction. `KnowledgeBoundary` records facts excluded for future availability rather than treating them as neutral.

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
- `context.permissions` and `context.builder` must not import native engines.
- Existing targeting deduplication and semantic-targeting roles are reused rather than duplicated.
- Native engine files remain unchanged during the foundation phases.

## Six-step implementation order

1. Contracts + Lineage — complete.
2. All Domain Projections — complete.
3. Zone Intelligence — complete.
4. Cross-Domain Context + Permission — complete.
5. Builder + Workspace shadow integration — complete pending final CI validation.
6. Replay + no-lookahead + golden scenarios + final freeze — next.

## Deferred

- BUY/SELL and future action policy
- position sizing, SL/TP, R:R
- probability/confidence calibration
- sophisticated cross-timeframe lineage
- database/event-store persistence
- Auction/Volume Profile integration
- native Liquidity/OB behavior changes
