# Decision Scenario Engine — Turn 4

Turn 4 adds a non-action scenario layer between causal market interpretation and later horizon arbitration.

## Contract

- Structure remains the only directional authority.
- The current product is long-only: a canonical SHORT structural thesis is preserved analytically but does not become a long-entry scenario.
- `ScenarioPresence` and `ScenarioStage` are separate concepts.
  - `PRESENT + BLOCKED` means an observed scenario exists but a hard gate prevents fresh entry.
  - `ABSENT` means the horizon truly has no current long-entry opportunity (for example canonical SHORT structure or `Opportunity.NONE`).
  - `UNKNOWN` is never treated as `ABSENT`.
- Missing target/opportunity evidence does not mean a clear path.
- An uncalibrated opportunity may still be observed when causal target room/path evidence exists; it remains developing until calibration/evidence resolves.
- A transitioning LONG thesis may retain scenario presence but cannot be qualified for a fresh continuation entry.
- A defended active target-path node forces scenario reassessment/development and does not unlock downstream nodes.
- The layer emits no `READY`, `BUY`, `SELL`, `HOLD`, or lifecycle transition.

## Why presence is separate from stage

Turn 5 needs to know whether a higher-priority LONG_TERM scenario exists even when that scenario is temporarily blocked. Erasing a blocked LT scenario would allow a lower-priority SHORT_TERM setup to bypass the hierarchy. The scenario layer therefore preserves ownership while separately exposing blockers and waiting conditions.
