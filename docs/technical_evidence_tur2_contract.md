# Technical Evidence Tur-2 Contract

## Boundary

Tur-2 enriches confirmed Tur-1 packets with relationships and audit metadata. It still does **not** make a trading decision.

It may:

- link visible derivation dependencies;
- validate the dependency DAG;
- expose independence/correlation groups;
- annotate causal freshness when a source-bar anchor exists;
- report mechanical directional conflicts;
- build semantic role summaries;
- expose an audit trail for future Decision Engine use.

It must not:

- emit BUY/SELL/WAIT or position states;
- select a setup family;
- invent missing evidence, levels, timestamps or event ages;
- apply evidence weights;
- convert derived evidence into an independent vote;
- collapse the packet into one score;
- delete stale or conflicting evidence.

## Multi-timeframe bundle

Tur-1 packets remain timeframe-local. Tur-2 combines them into a `TechnicalEvidenceBundle` so cross-timeframe provenance can be represented without pretending bar indices are comparable across timeframes.

Cross-timeframe causal order is checked by timestamp when possible. Same-timeframe dependencies use `known_bar` first. If causal order cannot be verified, the edge is retained but reported in audit metadata; future edges, missing edges and cycles are invalid.

## MTF Story provenance

MTF Story remains `DERIVED`.

When its public `timeframe_states` identify timeframes that are also present in the bundle, Tur-2 links the MTF Story item to visible Market Structure and Pattern evidence for those timeframes. These dependencies are lineage, not weights.

If the underlying packets are absent, the MTF Story item is retained as derived and reported as `unlinked_derived`; it is never silently promoted to a root fact.

## Independence groups

Independence groups prevent a flat vote count across multiple ports from the same engine. Examples:

- all Volatility/Bands/Fibonacci ports share one independence group;
- all FVG/Engulfing lifecycle ports share one group;
- all Order Block sides share one group;
- HAM Momentum and HAM Timing are separate groups because their permanent downstream contracts have separate roles.

An independence group is correlation metadata only. Tur-2 does not assign a weight to it.

## Freshness

Freshness is metadata, not a filter or vote multiplier.

Tur-2 only calculates numeric freshness when a causal `source_bar` is available. It does not guess an event age from the current snapshot bar. The role supplies a recency horizon and lifecycle state may lengthen or shorten that horizon.

Evidence without a safe age anchor remains `freshness=None / UNKNOWN` and is preserved.

Persistent and terminal lifecycle states may change the horizon, but no evidence is removed because of age.

## Conflicts

Conflicts are mechanical records of opposite non-neutral directions across different independence groups.

Tur-2 distinguishes:

- same-role opposition;
- cross-role opposition;
- opposition involving derived/shared-lineage evidence.

Opposite states inside the same engine independence group are not automatically labeled a conflict. For example simultaneous bull and bear FVG zones may coexist as location facts.

A conflict has no severity, blocker or decision consequence in TEL.

## Semantic summary

The fixed semantic roles remain:

`CONTEXT / STRUCTURE / LOCATION / TRIGGER / CONFIRMATION / TIMING / RISK`

Each role summary only indexes evidence IDs by provenance, direction, freshness availability, data quality and conflict membership. It does not emit a dominant direction or score.

## Audit

`EvidenceAudit` exposes:

- evidence/level counts;
- provenance counts;
- dependency edge count;
- independence-group count;
- derived items with no visible dependency;
- multi-item independence groups;
- overlapping lineage pairs;
- dependency edges whose cross-timeframe order could not be verified.

## No-lookahead

The bundle rejects:

- missing dependency IDs;
- dependency cycles;
- same-timeframe dependencies whose dependency `known_bar` is later than the child;
- cross-timeframe dependencies whose dependency timestamp is later than the child;
- evidence or levels beyond an explicit as-of bar/timestamp.

The original Tur-1 packet is not mutated when Tur-2 adds dependency/freshness metadata.
