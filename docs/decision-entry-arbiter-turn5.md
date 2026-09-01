# Entry Scenario Arbiter — Turn 5

Turn 5B keeps LT and ST technical evaluation independent, then resolves the one horizon that may reach entry finalization.

## Canonical policy

The arbiter is **qualification-first with a LONG_TERM tie-break**. It is not a score comparison and it is not blanket ST-first.

1. If LONG_TERM is `PRESENT + QUALIFIED`, LONG_TERM is selected.
2. Otherwise, if SHORT_TERM is `PRESENT + QUALIFIED`, SHORT_TERM is selected even when LONG_TERM is `PRESENT` but non-qualified or LONG_TERM presence is `UNKNOWN`.
3. If neither horizon is qualified and LONG_TERM is `PRESENT`, LONG_TERM retains deterministic ownership of its blocked/developing non-action state.
4. If LONG_TERM is `UNKNOWN` and there is no qualified SHORT_TERM setup, ownership remains unresolved.
5. If LONG_TERM is `ABSENT`, a present SHORT_TERM scenario owns its own state as before.
6. At most one horizon is selected.

### Matrix

| LONG_TERM | SHORT_TERM | Selection |
| --- | --- | --- |
| QUALIFIED | QUALIFIED | LONG_TERM |
| QUALIFIED | non-qualified / unknown / absent | LONG_TERM |
| PRESENT non-qualified | QUALIFIED | SHORT_TERM |
| UNKNOWN | QUALIFIED | SHORT_TERM |
| PRESENT non-qualified | PRESENT non-qualified | LONG_TERM |
| UNKNOWN | PRESENT non-qualified / UNKNOWN / ABSENT | unresolved LT ownership |
| ABSENT | PRESENT | SHORT_TERM |
| ABSENT | UNKNOWN | unresolved ST ownership |
| ABSENT | ABSENT | none |

This removes the old rule where mere LT presence or unresolved LT presence could veto an independently qualified ST setup. It deliberately does **not** make ST win when both horizons are technically qualified.

## Boundaries

Arbitration still emits no `READY`, `BUY`, `SELL`, `HOLD`, execution lifecycle, exit policy, position sizing, or capital-allocation rule. It only selects the horizon whose already-prepared technical assessment may proceed to the existing Entry layer.

Because this changes trading semantics, the Decision contract version is bumped from 2 to 3. A checkpoint created under the previous Decision semantics cannot silently resume under this policy and must cold replay.
