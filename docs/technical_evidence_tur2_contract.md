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

A public bundle has one snapshot per timeframe. Multiple engine packets for the same timeframe may be coalesced only when their `known_bar` and timestamp are identical. Historical and current snapshots for one timeframe cannot coexist as current evidence.

Within each packet, supplied member `known_bar` and timestamp metadata must match the containing snapshot. `source_bar` may legitimately be older because it represents causal origin/event time.

Cross-timeframe causal order is checked by timestamp when possible. Same-timeframe dependencies use `known_bar` first. If causal order cannot be verified, the edge is retained but reported in audit metadata; future edges, missing edges and cycles are invalid.

An explicit `as_of_timestamp` is fail-closed: if a packet timestamp cannot be compared safely (for example timezone-naive versus timezone-aware), the bundle is rejected rather than silently bypassing the future-data guard.

## MTF Story provenance

MTF Story remains `DERIVED`.

When its public `timeframe_states` identify timeframes that are also present in the bundle, Tur-2 links the MTF Story item to visible Market Structure and Pattern evidence for those timeframes. These dependencies are lineage, not weights.

If the underlying packets are absent, the MTF Story item is retained as derived and reported as `unlinked_derived`; it is never silently promoted to a root fact.

MTF Story consumes only Market Structure and Pattern normalized inputs. Any lineage representation remains conservative; the Story never becomes an independent vote merely because a visible upstream detail is absent.

## Independence groups

Independence groups prevent a flat vote count across multiple ports from the same engine. Examples:

- primary Market Structure state and its structured external/internal event ports share one group;
- Support/Resistance range geometry and confirmed-break trigger share one group;
- all Volatility/Bands/Fibonacci ports share one independence group;
- all FVG/Engulfing lifecycle ports share one group;
- all Order Block sides share one group;
- HAM Momentum and HAM Timing are separate groups because their permanent downstream contracts have separate roles.

An independence group is correlation metadata only. Tur-2 does not assign a weight to it.

## Structured event handoff

Decision-relevant event facts are structured where the upstream contract can support them:

- Market Structure exposes structured external/internal event ports for BOS, CHoCH, SWEEP, FALSE_BREAK and TRANSITION_FAIL instead of requiring string parsing.
- Liquidity exposes SWEEP/RECLAIM and separate structured CONSUME semantics.
- Support/Resistance exposes a separate trigger only for `RANGE_BREAK_CONFIRMED`, anchored by the public `break_confirmed_index`; the range boundary itself does not inherit that event bar as formation origin.
- FVG/Engulfing terminal/current event ports remain separate from active location zones.

## Freshness

Freshness is metadata, not a filter or vote multiplier.

Tur-2 only calculates numeric freshness when a causal `source_bar` is available. It does not guess an event age from the current snapshot bar. The role supplies a recency horizon and lifecycle state may lengthen or shorten that horizon.

Evidence without a safe age anchor remains `freshness=None / UNKNOWN` and is preserved.

Persistent and terminal lifecycle states may change the horizon, but no evidence is removed because of age.

An unanchored normalized level may inherit freshness only when the public policy can prove a single same-source causal anchor. Otherwise level freshness remains unknown.

## Conflicts

Conflicts are mechanical records of opposite non-neutral directions across different independence groups.

Tur-2 distinguishes:

- same-role opposition;
- cross-role opposition;
- opposition involving derived/shared-lineage evidence.

Opposite states inside the same engine independence group are not automatically labeled a conflict. For example simultaneous bull and bear FVG zones may coexist as location facts, and internal/external Market Structure event facts do not become independent votes merely because their directions differ.

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
- evidence or levels beyond an explicit as-of bar/timestamp;
- mixed snapshots inside one timeframe packet or bundle.

The original Tur-1 packet is not mutated when Tur-2 adds dependency/freshness metadata.
