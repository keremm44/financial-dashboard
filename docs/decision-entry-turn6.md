# Turn 6 — Arbiter-Owned Entry

Turn 6 is the first layer allowed to emit a fresh BUY. It does not add a new market thesis, new trade direction, or a parallel BUY/SELL interpretation. It consumes the existing Turn 4 scenario and Turn 5 ownership contracts.

## Fixed chain

1. Build LONG_TERM and SHORT_TERM entry scenarios.
2. Apply the strict LONG_TERM-first arbiter.
3. If ownership is unresolved, WAIT.
4. If no scenario exists, NO_TRADE.
5. If the selected scenario is BLOCKED, retain ownership but emit NO_TRADE.
6. If the selected scenario is DEVELOPING/UNAVAILABLE, retain ownership but WAIT.
7. Only a QUALIFIED selected scenario may reach the execution layer.
8. BUY still requires the existing fresh, same-as-of, 30m CONFIRMED execution event.
9. Without a fresh event, a qualified scenario stops at READY.

## Safety invariants

- A BLOCKED or DEVELOPING LONG_TERM scenario cannot be bypassed by a stronger SHORT_TERM setup.
- The entry layer accepts only the arbiter-selected horizon.
- The entry layer never emits SELL or HOLD.
- The entry layer does not infer an execution event from sticky state.
- A supplied execution event is consumed only when the selected scenario is QUALIFIED.
- An execution event that arrives too early is not cached for later bars.
- Horizon mismatches between the arbiter and lower-level assessment fail closed with a programming error.
- Source lineage from the selected scenario and final execution assessment is preserved for audit.

## New audit field

`execution_event_consumed` records whether the current execution event actually reached the qualified selected entry path. This is an auditability/safety field only; it does not add market logic.

## Non-goals

Turn 6 does not add short selling, opposing-position entry, score comparison between horizons, new market thresholds, or additional domain voting. Existing domain/Structure/Scenario semantics remain unchanged.
