# Cross-Domain Context Contract

Status: **DESIGN / FOUNDATION ONLY**

This document freezes the package boundary before implementation. It does not define BUY/SELL logic.

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

## Knowledge boundary

The planned decision-time boundary is the reference `target_as_of` used by the workspace. Every projected fact must satisfy:

```text
fact.available_at <= snapshot.as_of
```

Facts excluded by this rule must be reported through a `KnowledgeBoundary` diagnostic rather than silently treated as neutral.

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

## Planned implementation order

1. P0 — invariants and `as_of` freeze
2. P1 — FactRef / data-quality / lineage foundations
3. P2 — structural, liquidity, reaction projections
4. P3 — Zone Intelligence
5. P4 — core context axes and snapshot
6. P5 — Volume, Pattern, Volatility supporting projections
7. P6 — PermissionEnvelope
8. P7 — builder + workspace shadow integration
9. P8 — replay, no-lookahead, golden scenarios
10. P9 — HAM integration
11. P10 — diagnostics, docs, final architecture freeze

## Deferred

- BUY/SELL and future action policy
- position sizing, SL/TP, R:R
- probability/confidence calibration
- sophisticated cross-timeframe lineage
- database/event-store persistence
- Auction/Volume Profile integration
- native Liquidity/OB behavior changes

