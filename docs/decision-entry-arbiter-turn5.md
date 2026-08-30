# Entry Scenario Arbiter — Current Authority Contract

The arbiter is a non-action horizon-ownership layer above the Scenario Engine.
It does not emit BUY/SELL or soften any setup, conflict, targeting, timing, or execution gate.

## Current priority

Trade-horizon ownership is intentionally different from context authority:

```text
QUALIFIED SHORT_TERM trade setup -> SHORT_TERM trade horizon
LONG_TERM -> thesis/context/risk authority remains visible
```

Rules:

1. A `PRESENT + QUALIFIED` SHORT_TERM scenario owns the trade horizon, including when LONG_TERM is also `PRESENT + QUALIFIED`.
2. LONG_TERM remains attached as context/risk evidence; selecting SHORT_TERM does not erase LT information.
3. If SHORT_TERM is only `DEVELOPING`, it does not displace a present LONG_TERM scenario.
4. If LONG_TERM is `UNKNOWN` because structural/data authority is unresolved, a merely developing SHORT_TERM scenario still waits.
5. A `PRESENT + QUALIFIED` SHORT_TERM scenario may stand on its own when LT is unresolved, preserving the existing independent-ST exception.
6. At most one trade horizon is selected.

This distinction prevents an otherwise valid 1H-owned, 3-9 trading-day setup from being labelled LONG_TERM solely because a longer-horizon thesis is also present.

## Scenario existence versus economics

Targeting/Opportunity describes path economics; it does not define whether a structurally valid long scenario exists.

Therefore an observed `OpportunityState.NONE` means:

```text
scenario = PRESENT
stage = DEVELOPING (or BLOCKED if another hard blocker exists)
waiting_for += MORE_DIRECTIONAL_ROOM
```

It no longer means `ScenarioPresence.ABSENT`.

`UNKNOWN` opportunity remains distinct: when directional opportunity has not actually been observed, scenario presence can remain unresolved.

## Non-action boundary

Arbitration still exposes `is_actionable_signal = False`.
Entry execution remains downstream and still requires the normal eligibility/setup/execution path.
