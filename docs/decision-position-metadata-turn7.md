# Turn 7 — Immutable Position Entry Metadata

Turn 7 preserves the origin of an executed Turn 6 BUY. It does not add market logic, a new trade direction, position promotion, targets, stops, profit thresholds, or additional BUY/SELL rules.

## Why metadata exists

Once a position is OPEN, later bars may change market state. The exit layer must not reconstruct the original entry horizon or scenario from those later facts. Turn 7 therefore freezes only facts that were known at the actual entry timestamp.

## Frozen entry facts

`PositionEntryMetadata` stores:

- symbol
- selected entry horizon (`LONG_TERM` or `SHORT_TERM` horizon ownership already defined by Turn 5)
- scenario kind
- entry timestamp
- entry price from the frozen decision snapshot
- active target identity at entry, when present
- 30m execution timeframe
- execution event observed timestamp
- execution reason
- deterministic entry source lineage

These fields are audit/ownership facts. They are not independent evidence and must not be fed back as new market votes.

## Creation contract

Metadata can be created only from an actually executed Turn 6 BUY where:

- one horizon is selected by the arbiter
- selected scenario is PRESENT and QUALIFIED
- execution assessment is CONFIRMED
- `execution_event_consumed` is true
- the raw execution event is supplied again as provenance
- the event is LONG, 30m, fresh at the snapshot `as_of`, and not future-unavailable

A READY/WAIT/NO_TRADE result cannot create position metadata.

## Lifecycle invariants

- FLAT cannot carry position entry metadata.
- Opening BUY freezes metadata into the OPEN lifecycle state.
- Exit-stage transitions preserve the exact metadata object/value.
- Repeated BUY while OPEN cannot replace or promote the original entry horizon metadata.
- No later market state may backfill missing legacy metadata.
- Confirmed SELL/close clears metadata together with position ownership.
- Legacy OPEN states without Turn 7 metadata remain readable until the replay/persistence migration in Turn 9.

## Explicit non-goals

Turn 7 does not introduce short selling, reverse positions, side switching, horizon promotion, dynamic position reclassification, profit targets, stop-loss rules, ATR exits, or new decision thresholds. `LONG_TERM` and `SHORT_TERM` remain existing entry-horizon ownership classes only.
