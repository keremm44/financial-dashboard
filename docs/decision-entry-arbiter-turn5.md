# Entry Scenario Arbiter — Turn 5

Turn 5 adds a non-action horizon ownership layer above the Turn 4 Scenario Engine.

## Canonical priority

```text
LONG_TERM > SHORT_TERM
```

The arbiter implements the hierarchy as a semantic rule, not a score comparison:

1. If the LONG_TERM scenario is `PRESENT`, LONG_TERM owns the decision.
2. SHORT_TERM is considered only when LONG_TERM is explicitly `ABSENT`.
3. LONG_TERM `UNKNOWN` never means absence, so SHORT_TERM cannot be selected while LT presence is unresolved.
4. If LONG_TERM is absent and SHORT_TERM is present, SHORT_TERM owns the decision.
5. At most one horizon can be selected.

A blocked or developing LONG_TERM scenario is still present and therefore retains ownership. This prevents a locally stronger/cleaner SHORT_TERM setup from bypassing the higher-priority opportunity class.

## Non-action boundary

Arbitration does not emit `READY`, `BUY`, `SELL`, `HOLD`, or lifecycle transitions. It only identifies the scenario owner that Turn 6 may evaluate for entry execution.

The result exposes `is_actionable_signal = False` explicitly.
