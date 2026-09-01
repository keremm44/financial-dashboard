# ST Canonical Roadmap

> **Status:** Canonical roadmap for the Short-Term (ST) trading product.
>
> **Scope:** ST philosophy is frozen. This document defines the canonical implementation roadmap and the boundaries that must remain stable during implementation and later calibration. Detailed LT lifecycle design is intentionally out of scope and remains provisional.

---

## 1. Frozen ST product constitution

### 1.1 Economic purpose of ST

ST is the system's primary earnings engine.

Its purpose is **not** to stay in positions for the shortest possible time. Its purpose is to capture a meaningful short-term move, carry it while it remains productive, and release capital when the same economic move no longer justifies waiting and giveback risk.

The system must avoid both extremes:

- **Overly rigid ST:** exits normal corrections, cuts strong trends early, and creates churn.
- **Overly loose ST:** holds completed swings too long, leaves capital in dead ranges, waits for a full opposite trend, and behaves like hidden LT.

### 1.2 Three canonical ST thesis families

Only three core economic ST thesis families are canonical:

1. **Pullback Continuation**  
   A controlled pullback ends and the existing move produces a new upward leg.

2. **Breakout + New-Area Acceptance**  
   The old range is genuinely left behind and a higher price area becomes accepted.

3. **Failed-Sell Reclaim**  
   A downside attempt fails, the lost area is reclaimed, and the failed sell is unwound upward.

“Strong momentum”, “news”, “good stock”, “oversold” and similar descriptions are context or evidence, not separate thesis families.

### 1.3 Four lifecycle meanings

Every ST trade is interpreted through four economic lifecycle meanings:

- **WORKING**
- **NORMAL CORRECTION**
- **CONSUMED**
- **INVALIDATED**

Their exit outcomes are:

- WORKING / healthy correction → **HOLD**
- CONSUMED → **PROFIT HARVEST**
- INVALIDATED → **PROTECTIVE EXIT**

### 1.4 No fixed take-profit or fixed timeout

The system must not use rules such as:

- “Sell at +7%.”
- “Sell at +12%.”
- “Sell after two days without progress.”

Profit and time still carry economic information, but they are not standalone triggers.

Core rule:

> **Time is not a decision. Time without progress can become economic evidence.**

A mature trade that has already produced a meaningful move may deserve less patience during stagnation than a newly opened trade. This must be evaluated through economic progress, not arbitrary bar/day counts.

### 1.5 ST → LT conversion is prohibited

An ST trade:

> opens as ST → lives as ST → closes as ST.

It cannot become LT because it:

- loses money,
- becomes very profitable,
- enters a range,
- still has a strong higher-timeframe trend.

After the ST trade is closed, an independent LT opportunity may be evaluated separately.

### 1.6 ST product priority

When two genuinely independent qualified trades collide in today's single-position architecture:

- independent ST qualified,
- independent LT qualified,

then **ST has product priority**.

This is a deliberate product decision, not a claim that ST is always economically superior to LT.

If short-term strength is only good timing for an LT thesis rather than an independent ST thesis, ownership remains LT.

---

# STEP 0 — Canonical baseline and validation boundary

## Purpose

Establish one verified starting point before changing trading behavior.

## Preconditions

An accessible repository checkout and the frozen ST specification.

## Verify

- current branch,
- HEAD SHA,
- git status,
- upstream / ahead-behind,
- relevant PR state,
- full test suite,
- architecture audit,
- shell ownership audit,
- gate ownership / duplicate ownership,
- Decision contract,
- lifecycle schema / lifecycle contract,
- replay and checkpoint protections.

Canonical and non-canonical validation paths must also be separated explicitly:

- canonical replay,
- legacy decision stream,
- readiness proxy.

Legacy or proxy output must never be presented as canonical production behavior.

## What changes

Only governance, validation boundaries, and the official gap backlog.

## What does not change

Trading behavior.

## Change class

Governance / validation only.

## Main risk

Building later work on an unverified branch, stale test result, or non-canonical replay path.

## Acceptance criteria

- Branch / HEAD / status are verified from the real checkout.
- Tests and audits are actually run and recorded.
- Canonical path is identified.
- Legacy and readiness-proxy paths are marked audit-only.
- Frozen ST specification is the canonical reference.
- Known gaps are classified as A/B/C/D/E.

---

# STEP 1 — Causal thesis identity shadow

## Purpose

Answer the first foundational question for every executed ST entry:

> **Which economic ST trade is this?**

Current generic scenario identity is not sufficient for the three frozen thesis families.

## Shadow thesis identity

Each executed ST entry should, using only causal evidence available at entry, resolve to at most one of:

- `PULLBACK_CONTINUATION`
- `BREAKOUT_ACCEPTANCE`
- `FAILED_SELL_RECLAIM`
- `UNRESOLVED`

`UNRESOLVED` is intentional and safer than false certainty.

## Conceptual entry contract

For each resolved thesis, identify:

### Thesis family

The economic setup family.

### Economic mission

What move the trade is actually trying to capture.

### Initial defended anchor

The economic area whose loss would contradict the original entry thesis.

### Initial target / economic context

The initial reference needed to understand whether the trade's first economic mission is progressing or materially completed.

## Critical distinction

The **initial defended anchor** freezes at entry and must not be rewritten later.

A separate **active earned defense** may evolve causally only after the trade genuinely gains and accepts new economic territory.

## What changes

Shadow-only semantic classification.

## What does not change

- BUY,
- SELL,
- eligibility,
- arbitration,
- execution,
- current exit behavior.

## Change class

A — architecture / semantic foundation.  
Secondary B meaning exists, but no action gating is allowed in this step.

## Main risk

Forcing ambiguous entries into the wrong thesis family and building all later lifecycle logic on false identity.

## Acceptance criteria

- No future data is used.
- The same causal input always gives the same identity.
- One entry resolves to at most one family.
- Ambiguous entries remain `UNRESOLVED`.
- Classification coverage is reported.
- Cold replay produces the same identity.
- Selected-horizon and action diff remain zero.

---

# STEP 2 — Minimal persistent trade-memory contract

## Purpose

Persist only facts that cannot be reconstructed safely from the current snapshot.

Core rule:

> **Persist causal facts; derive policy conclusions whenever possible.**

This is the main protection against turning ST into a rigid flag-heavy state machine.

## Persistent categories

### Immutable trade identity

- thesis family,
- economic mission,
- initial defended anchor,
- initial target context,
- entry price,
- entry `as_of`.

### Minimal causal history

Only historical trade facts that later evaluation genuinely cannot reconstruct from the current market snapshot.

Examples may include accepted gained-area milestones or completed economic milestones.

## Must not become standalone persistent policy flags

Do **not** persist independent booleans/counters for:

- maturity,
- healthy base,
- continuation failure count,
- CONSUMED.

These should be derived from current evidence plus minimal causal history.

## Replay / checkpoint boundary

Any new persistent trade-memory contract may require:

- lifecycle schema versioning,
- lifecycle contract versioning,
- cold replay for incompatible checkpoints.

Old OPEN checkpoints must not be silently backfilled using hindsight or guessed defaults.

## What changes

Persistence/state contract only.

## What does not change

Trading actions and native domain outputs.

## Change class

A.

## Main risk

Persisting economic interpretations instead of causal facts and making the engine difficult to calibrate safely.

## Acceptance criteria

- Immutable anchors cannot mutate later.
- No future/hindsight backfill.
- No silent migration of incompatible checkpoints.
- Cold / warm / restart replay are equivalent.
- Maturity, healthy base and CONSUMED are not independent persistent flags.
- Current domain snapshots are not copied into trade state.

---

# STEP 3 — Causal trade-relative economic history shadow

## Purpose

Track what the trade has actually experienced since entry, without changing actions.

## Economic history concepts

### Achieved progress

Has the trade genuinely progressed toward its economic mission?

This is not simply raw PnL.

### Gained / accepted area

Has the market merely printed a higher price, or has it genuinely gained and accepted a new price area?

Every new high is not a gained area.

### Mission completion

Has the initial economic mission materially occurred?

Mission completion is a historical factual milestone, not the same thing as CONSUMED.

A trade may complete its first mission and still continue strongly.

### Active earned defense

As genuine new areas become accepted, the trade may causally earn a stronger active defense.

Frozen rules:

- it cannot move upward without real acceptance,
- it cannot follow every peak,
- it cannot be loosened downward to rescue a deteriorating trade.

### Continuation episode

Avoid crude counters such as:

> “Three failed attempts means harvest.”

Instead, model an economic episode:

- Did a real continuation opportunity form?
- Is the attempt still live?
- Did it produce new acceptance/progress?
- Did it fail and return to the prior area?

## Derived concepts

These remain derived rather than independently persisted:

- maturity,
- current healthy base,
- continuation failure,
- current CONSUMED assessment.

## What changes

Shadow trade-relative economic observations/history.

## What does not change

Canonical actions.

## Change class

A.

## Main risk

Treating PnL as economic progress or double-counting the same market event across multiple domains.

## Acceptance criteria

- Progress is trade-relative and causal.
- Gained area and defended area remain distinct.
- Initial defended anchor never loosens downward.
- Active defense evolves only through genuine acceptance.
- Continuation opportunity is episode-based, not a crude count.
- Healthy base is derived.
- Maturity is derived.
- CONSUMED remains derived.
- Future tail data cannot change prior state.
- Restart equivalence holds.

---

# STEP 4 — ST/LT economic ownership policy

## Purpose

Apply the frozen ST product priority only after the system can distinguish:

- an independent ST thesis,
- short-term timing that merely supports an LT thesis.

## Policy

### Independent ST + independent LT

→ **ST owner**.

### Short-term behavior is only LT entry timing

→ **LT owner**.

## What changes

Arbiter ownership policy only.

## What does not change

- qualification,
- ST exit,
- LT exit,
- execution,
- capital allocation.

## Change class

B.

## Main risk

Applying blanket ST-first and misclassifying LT timing as a separate ST product.

## Acceptance criteria

- Independent qualified ST + LT → ST.
- LT timing-only → LT.
- LT blocked/developing/unknown + independent qualified ST → ST.
- Single economic owner remains enforced.
- Selected-horizon/action diff is explainable and intentional.
- Decision contract boundary is updated if required.

---

# STEP 5 — Thesis-specific protective policy shadow

## Purpose

Measure real thesis invalidation without yet changing canonical SELL behavior.

Protective exit must not wait for a fully established opposite trend, but a single secondary weakness must not trigger SELL either.

## 5.1 Pullback Continuation invalidation

The trade protects the economic ground where the pullback was considered finished and buyer control was regained.

Protective story:

- defended ground is lost,
- price establishes acceptance below,
- reclaim fails,
- sellers produce new downside progress.

A higher timeframe remaining bullish does not rescue the ST thesis.

## 5.2 Breakout + Acceptance invalidation

The trade protects the claim that the old range has genuinely been left behind.

Protective story:

- price returns to the old range,
- the old range becomes the primary trading area again,
- attempts to reclaim the new area fail,
- the breakout becomes a failed excursion instead of accepted expansion.

## 5.3 Failed-Sell Reclaim invalidation

The trade protects the reclaimed area that sellers previously lost.

Protective story:

- reclaimed area is lost again,
- reclaim fails,
- downside acceptance/progress returns.

The original failed sell has effectively become successful selling again.

## Domain roles

- **Structure:** primary control evidence.
- **Reaction:** whether buyers can regain control after selling.
- **Participation:** whether counter-pressure is becoming effective.
- **Pattern:** thesis-specific supporting context.
- **Environment:** shock/regime context only.
- **Stabil:** larger-ground secondary/context.
- **UNKNOWN:** not confirmation.

## What changes

Shadow protective intent only.

## What does not change

Canonical SELL and execution behavior.

## Change class

D — shadow policy.

## Main risk

Either exiting on a single secondary weakness or still waiting for full bearish Structure before recognizing genuine ST failure.

## Acceptance criteria

- Each thesis uses its own invalidation chain.
- One secondary weakness does not create protective intent.
- Full opposite trend is not required.
- UNKNOWN is not evidence.
- General market shock alone does not create exit.
- Early/late protective differences are reported.
- Canonical action remains unchanged.

---

# STEP 6 — Harvest + healthy base + CONSUMED policy shadow

## Purpose

Distinguish:

> a valid but economically consumed ST move

from:

> a mature trade that is genuinely building another continuation/base.

Healthy base and harvest are one policy problem, not separate independent engines.

## 6.1 Final CONSUMED standard

A trade is CONSUMED only when the whole economic story is present:

1. Initial mission has materially completed.
2. Thesis is still valid.
3. New progress is no longer being produced.
4. Real continuation opportunities have occurred but failed to produce new area/acceptance.
5. There is no concrete healthy base / new-expansion preparation that still justifies carrying the current trade.

No single item is enough.

The following alone are insufficient:

- target touch,
- time in range,
- sideways price,
- one Participation weakness.

## 6.2 Healthy base

A healthy base is not simply:

> “Price has not fallen yet.”

It requires a coherent positive preparation story such as:

- gained area is protected,
- downside attempts fail to produce meaningful downside progress,
- buyer Reaction remains alive,
- Participation is controlled/absorptive rather than distributional,
- an understandable new risk boundary develops,
- Pattern behavior genuinely supports another expansion.

Frozen rule:

> **Healthy base may suspend harvest, but it never resets trade maturity.**

If the base later stops producing evidence, the trade does not become “new” again.

## What changes

Shadow HOLD/HARVEST interpretation.

## What does not change

Canonical actions.

## Change class

D — shadow policy.

## Main risk

Harvesting on one weak signal, or using “maybe it breaks out” as an excuse for endless HOLD.

## Acceptance criteria

- Strong continuation does not become CONSUMED.
- A real healthy base produces HOLD shadow intent.
- Merely-not-broken range does not automatically qualify as healthy base.
- Healthy base does not reset maturity.
- Invalidation always outranks harvest.
- No single indicator/event creates harvest.
- Canonical action remains unchanged.

---

# STEP 7 — Exit intent / reason lifecycle contract

## Purpose

Once the economic exit decision is final, make that intent restart-safe and durable.

Important distinction:

> **Before the policy decision, CONSUMED is derived.**

Once policy commits to PROFIT HARVEST or PROTECTIVE EXIT, that terminal economic intent becomes lifecycle state.

## Persistent exit families

- `PROFIT_HARVEST`
- `PROTECTIVE_EXIT`

## Escalation

Allowed:

> HARVEST → PROTECTIVE

because the thesis may invalidate while harvest execution is pending.

Not allowed:

> PROTECTIVE → HARVEST

because an invalidated thesis must not be downgraded inside the same trade.

## Why exit reason is not analytics-only

Exit reason determines:

- urgency,
- restart behavior,
- harvest→protective escalation,
- reliable closed-trade analytics.

Analytics must not infer the reason later from PnL.

## What changes

Lifecycle intent/reason contract.

## What does not change

Actual exit execution timing.

## Change class

A.

## Main risk

Persisting CONSUMED too early as a market-state flag or losing terminal intent on restart.

## Acceptance criteria

- Pre-decision CONSUMED remains derived.
- Final harvest intent cannot revert to HOLD.
- Harvest can escalate to protective.
- Protective cannot downgrade to harvest.
- Restart preserves intent.
- Closed-trade reason is not inferred after the fact.
- Schema/contract versioning is explicit.

---

# STEP 8 — Canonical ST exit policy activation

## Purpose

Activate the shadow-validated economic decision hierarchy on the canonical open-position path.

## Canonical precedence

### 1. Thesis-specific invalidation?

Yes → **PROTECTIVE EXIT**.

### 2. Real progress still being produced?

Yes → **HOLD**.

### 3. Normal correction + successful regain of control?

Yes → **HOLD**.

### 4. No progress but a real healthy base exists?

Yes → **HOLD**.  
Trade maturity is preserved.

### 5. Full CONSUMED story present?

Yes → **PROFIT HARVEST**.

### 6. Evidence insufficient?

→ **HOLD under uncertainty**.

This does **not** mean the trade is healthy. It only means the current evidence is insufficient for an irreversible exit decision.

## What changes

Canonical ST economic exit decision.

## What does not change

- execution urgency,
- native domains,
- LT exit policy.

## Change class

D.

## Main risk

Mixing economic exit policy with execution behavior in the same change.

## Acceptance criteria

- Thesis-specific invalidation is active.
- Protective precedence is active.
- Real healthy base remains HOLD.
- CONSUMED becomes HARVEST.
- UNKNOWN remains non-evidence.
- No domain vote counting.
- Controlled action/stage diff contains only intended D-class changes.
- Causal lineage remains intact.

---

# STEP 9 — Harvest / protective execution urgency

## Purpose

Separate **why we exit** from **how urgently the exit must be executed**.

## 9.1 PROFIT HARVEST

The thesis is still valid.

Therefore limited execution patience for reasonable exit quality may be acceptable.

But harvest may not become:

- waiting for a new upside target,
- unlimited delay,
- an excuse to revive a terminal trade.

If invalidation develops while harvest is pending:

> HARVEST → PROTECTIVE urgency.

## 9.2 PROTECTIVE EXIT

The purpose is no longer price optimization.

The purpose is:

> **terminate exposure to an invalidated thesis.**

Policy must not:

- demand new market confirmation,
- wait for “one more reclaim”,
- allow Timing to become a veto/confirmation gate.

This does not prescribe broker order type; live order/fill behavior remains a separate later concern.

## What changes

Execution lifecycle urgency only.

## What does not change

Economic exit classification.

## Change class

C.

## Main risk

Recreating the old late-exit problem by adding a fresh confirmation gate after protective intent already exists.

## Acceptance criteria

- Protective exit does not wait for new policy confirmation.
- Timing is not a veto owner.
- Harvest has bounded exit-quality patience.
- Harvest can escalate to protective.
- Restart does not change event-consumption outcome.
- Canonical lifecycle contract boundary is updated if necessary.

---

# STEP 10A — Setup / movement continuity shadow

## Purpose

Distinguish after exit:

> the same old economic movement

from:

> a genuinely new setup.

Fresh execution event is **not** the same thing as new economic setup.

## New setup economic requirements

A genuinely new setup requires a new combination of:

- new information,
- new risk boundary,
- new economic move.

## No cooldown solution

Rules such as:

> “Do not trade this stock for seven days.”

are prohibited as the primary novelty model.

Time can neither guarantee novelty nor prove that the movement is still the same.

## What changes

Shadow movement/setup continuity only.

## What does not change

Re-entry actions.

## Change class

A.

## Main risk

Equating event freshness with economic novelty or permanently locking legitimate future setups.

## Acceptance criteria

- Execution event identity and economic movement identity are distinct.
- Same movement can remain identifiable after exit.
- New base/risk/information can be evaluated causally.
- No time cooldown.
- Closed-trade continuity survives restart.
- Actions remain unchanged.

---

# STEP 10B — Re-entry novelty policy

## Purpose

Prevent churn after correct exits while still allowing genuinely new ST opportunities.

## Re-entry rule

Re-entry requires a genuinely new economic trade:

- new information,
- new risk boundary,
- new economic move.

## Examples

### Profit harvest → price continues without new structure

Likely same old movement → do not chase.

### Profit harvest → new base + new acceptance + new risk boundary

Potential new ST trade.

### Protective exit → immediate rebound only

Not enough for a new trade.

### Protective exit → prior invalidation is genuinely reversed + new acceptance + new risk

Potential new trade.

## What changes

Re-entry eligibility.

## What does not change

First-entry thesis rules and exit policy.

## Change class

B.

## Main risk

Either repeatedly retaking the same movement or over-locking real new setups.

## Acceptance criteria

- Immediate old-movement retake is controlled.
- Genuine new-base opportunities remain tradable.
- No cooldown.
- Churn and missed-new-setup are measured together.
- Exit policy remains unchanged.

This is not a blocker for the first thesis-aware ST exit, but it **is mandatory before final ST freeze**.

---

# STEP 11 — Canonical behavior validation

## Purpose

Measure whether the implemented ST product actually behaves like the frozen product philosophy.

Total PnL alone is insufficient.

## Canonical-only validation

Validation must use:

- canonical arbiter,
- canonical lifecycle,
- canonical exit.

Legacy decision stream must be reported separately.

Readiness proxy must never be treated as production execution performance.

## Required behavioral measures

### Strong continuation carrying

Does the system continue to carry genuinely productive trades?

### Premature harvest

Does it cut healthy continuation too early?

### Mature-range idle capital

Does it keep capital trapped in already-consumed moves?

### Protective delay

How long after thesis invalidation does risk remain open?

### Giveback

How much previously achieved progress is returned before exit?

This is analytics, not a mechanical trailing-stop objective.

### Correction false exit

Does normal correction produce unnecessary exits?

### Churn

Does the same economic movement get repeatedly traded?

### New-setup re-entry

Does the novelty policy accidentally block genuinely new opportunities?

### Holding duration

Is holding time economically appropriate for the thesis and lifecycle stage?

### Flat-capital duration

How long does capital remain flat after exits?

### Exit-family distribution

How many trades close via:

- PROFIT HARVEST,
- PROTECTIVE EXIT?

### Peak / MFE

Analytics only. Never policy state.

## What changes

Validation and reporting only.

## What does not change

Trading policy.

## Change class

Validation.

## Main risk

Optimizing total PnL while ignoring early-exit, late-exit, churn, and dead-capital behavior.

## Acceptance criteria

- Cold / warm / restart replay match.
- Legacy results are separate.
- Early and late exit risks are measured together.
- PnL is not the sole success criterion.
- Churn and missed-new-setup are measured together.
- Strong continuation is not systematically cut.

---

# STEP 12 — Small calibration

## Purpose

Adjust ST sensitivity without reopening the frozen product philosophy.

The calibration question is:

> Is ST a little too strict or a little too loose?

not:

> What is ST supposed to be?

## Calibratable later

- correction tolerance,
- reclaim confidence,
- acceptance confidence,
- gained-area acceptance sensitivity,
- progress significance,
- maturity sensitivity,
- continuation-opportunity quality,
- progress-failure sensitivity,
- healthy-base confidence,
- counter-pressure severity,
- protective control-loss sensitivity,
- harvest execution-quality patience.

## Never calibratable

The following are frozen product/lifecycle principles and must not become tuning parameters:

- the three thesis families,
- ST→LT prohibition,
- thesis identity cannot change based on PnL,
- initial defended anchor cannot be loosened downward,
- invalidation outranks harvest,
- UNKNOWN is not evidence,
- HOLD/HARVEST/PROTECTIVE distinction,
- harvest/protective urgency difference,
- CONSUMED requires a combined story,
- final harvest intent is irreversible inside the same trade,
- healthy base does not reset maturity,
- domain vote engines are prohibited,
- independent ST/LT collision gives ST product priority,
- causal/replay/checkpoint fail-close guarantees.

## Calibration principle

Do not search for historical-optimal rules such as:

> “+8.4% was the best take-profit in this sample.”

Every calibration change must evaluate both sides:

- Did premature exit increase?
- Did late exit / dead capital improve?

## Change class

Depends on the tuned sensitivity: B, D or C.  
Different behavior classes should not be mixed casually in one calibration change.

## Main risk

Overfitting one period or silently changing persistent-state semantics under a new configuration.

## Acceptance criteria

- No single-trade/single-regime optimization.
- Early/late exit measured together.
- Thesis identity and exit-family semantics stay unchanged.
- Config digest changes require appropriate replay behavior.
- Persistent state is not silently reused under incompatible calibration semantics.

---

# STEP 13 — ST implementation freeze

## Purpose

Freeze the implemented ST product as a production candidate after behavior is validated across different market regimes.

This does not mean “the first backtest ran successfully.”

## Freeze acceptance

### Identity

- Three thesis families operate distinctly.
- `UNRESOLVED` behaves safely.
- No hidden ST→LT conversion.

### Lifecycle

- Minimal persistent facts only.
- Deterministic restart.
- Checkpoint compatibility boundaries are explicit.
- All state remains causal.

### Exit

- HOLD / HARVEST / PROTECTIVE are real distinct outcomes.
- Protective exit does not systematically wait for full opposite trend.
- Harvest releases consumed moves.
- Healthy base protects genuine continuation.

### Ownership

- Independent ST/LT collision follows ST product priority.
- LT timing-only is not misclassified as independent ST.

### Re-entry

- Same-movement churn is controlled.
- Genuine new setups are not systematically blocked.

### Validation

- Strong trends are not systematically cut early.
- Mature dead ranges are not systematically held too long.
- Protective exits are not systematically late.
- Normal corrections are not systematically exited.

### Replay

- Deterministic replay.
- Checkpoint contracts current.
- Exit reason reliable.
- Legacy/proxy results remain separate from canonical results.

## Change class

Release / governance.

---

# Canonical state ownership

This section is intentionally explicit because state ownership is one of the largest risks of future over-complexity.

| Information | Canonical ownership |
| --- | --- |
| Thesis family | Immutable trade identity |
| Economic mission | Immutable entry mission |
| Initial defended anchor | Immutable entry anchor |
| Active defended area | Causal minimal history |
| Initial target context | Immutable entry reference |
| Entry price | Persistent trade fact |
| Entry `as_of` | Persistent trade fact |
| Progress | Current snapshot + minimal history |
| Gained area | Minimal history only for accepted areas |
| Mission completion | Persistent factual milestone |
| Maturity | Derived |
| Continuation opportunity | Derived current episode |
| Completed episode outcome | Minimal causal history when economically necessary |
| Continuation failure | Derived |
| Healthy base | Derived |
| CONSUMED | Derived until final exit decision |
| Final harvest/protective intent | Persistent lifecycle state |
| Peak / MFE | Analytics only |
| Exit reason | Lifecycle contract + analytics |

Core rule:

> **Store the minimum causal history needed to understand the trade; do not persist every policy conclusion as state.**

---

# Canonical domain ownership

Native market domains remain factual sources. They must not become independent BUY/SELL voters.

## Structure

Primary control evidence for protective exit.

Not a profit-harvest engine.

## Reaction

Answers whether buyers can genuinely regain control after selling.

Important for normal-correction vs control-loss interpretation.

## Participation

Answers whether market effort still produces progress and whether counter-pressure is becoming effective.

Important for progress efficiency, continuation quality, and supporting protective evidence.

## Pattern

Supports base / breakout lifecycle interpretation.

Especially important for breakout thesis and healthy-base evaluation.

## Target / room

Supports interpretation of initial mission progress.

Target touch is never automatic exit.

## Environment / volatility

Shock / regime context only.

Never standalone HOLD/SELL authority.

## Stabil

Larger-ground secondary/context information.

Not the primary ST exit authority.

## Timing

Not the owner of economic exit classification.

Especially for PROTECTIVE EXIT, Timing must not become a veto or new confirmation gate.

---

# UNKNOWN and conflicting evidence

Frozen rule:

> **UNKNOWN is not market evidence.**

UNKNOWN is not:

- bullish evidence,
- bearish evidence,
- harvest evidence,
- protective evidence.

But an UNKNOWN secondary domain also must not cancel otherwise sufficient direct thesis invalidation.

## Canonical precedence

1. Sufficient thesis-specific invalidation → **PROTECTIVE EXIT**.
2. No invalidation + real progress / real healthy base → **HOLD**.
3. Neither above + full CONSUMED story → **PROFIT HARVEST**.
4. Evidence insufficient → **HOLD under uncertainty**.

HOLD-under-uncertainty means only that evidence is insufficient for irreversible exit. It does not certify health.

Operational data outage is a separate lifecycle/risk problem and must not be converted into fake bullish/bearish trading evidence.

---

# Change-class boundaries

## A — Architecture / state / persistence

Examples:

- thesis identity,
- minimal trade history,
- lifecycle schema,
- exit reason persistence,
- setup continuity.

## B — Trading policy

Examples:

- ST/LT ownership,
- re-entry novelty.

## C — Execution lifecycle

Examples:

- harvest/protective execution urgency.

## D — Exit policy

Examples:

- thesis-specific invalidation,
- CONSUMED/harvest,
- HOLD/HARVEST/PROTECTIVE hierarchy.

## E — Capital allocation

Outside this roadmap.

### Boundary rule

Do not collapse A/B/C/D behavior changes into one large change.

Especially avoid mixing:

- persistence refactor + exit behavior,
- exit behavior + execution urgency,
- setup continuity + re-entry eligibility,
- trading policy + capital allocation.

---

# Frozen / do not reopen

The following are canonical ST product principles and should not be reopened during ordinary implementation or calibration:

- ST is the primary product/earnings engine.
- Three thesis families only.
- HOLD / PROFIT HARVEST / PROTECTIVE EXIT distinction.
- ST→LT conversion prohibited.
- Independent ST/LT collision → ST product priority.
- No fixed percentage TP.
- No fixed bar/day timeout.
- CONSUMED requires combined economic evidence.
- Healthy base does not reset maturity.
- Invalidation outranks harvest.
- UNKNOWN is not evidence.
- Protective exit does not wait for fresh policy confirmation after sufficient invalidation.
- Native domains do not become SELL voters.
- Fresh execution event is not setup novelty.
- Causal / available-at / closed-bar / deterministic-replay fail-close guarantees remain central.

---

# Calibratable later

The following may be tuned after canonical validation, without changing product meaning:

- correction tolerance,
- acceptance/reclaim confidence,
- gained-area acceptance sensitivity,
- economic-progress significance,
- maturity sensitivity,
- continuation-opportunity quality,
- progress-failure sensitivity,
- healthy-base confidence,
- counter-pressure severity,
- protective control-loss sensitivity,
- harvest execution-quality patience.

Calibration must never change thesis identity, lower the initial defense, turn UNKNOWN into evidence, or reintroduce confirmation delay after protective invalidation.

---

# Later non-blocking work

Important, but not blockers for the first thesis-aware ST exit implementation:

- live broker/order/partial-fill lifecycle,
- detailed LT lifecycle,
- capital allocation,
- alternative-opportunity ranking,
- MFE/MAE analytics,
- operational data-outage production policy.

Setup novelty / re-entry continuity is non-blocking for the first ST exit activation, but it **is mandatory before final ST freeze**.

---

# Do not touch

- Do not add harvest/fatigue semantics into native Structure.
- Do not give Stabil direct SELL authority.
- Do not mutate ST horizon identity while OPEN.
- Do not turn domains into vote engines.
- Do not copy current domain snapshots into trade state.
- Do not loosen the initial defended anchor downward.
- Do not merge gained area and defended area into one concept.
- Do not use future full-history hindsight backfill.
- Do not silently migrate incompatible old checkpoints.
- Do not solve setup novelty with a crude cooldown.
- Do not put capital allocation inside arbiter/exit policy.
- Do not reintroduce fresh market-confirmation requirements after protective invalidation.
- Do not report legacy/readiness-proxy output as canonical backtest performance.
- Do not calibrate from one trade or one historical period.

---

# Real first phase

Implementation begins with:

> **STEP 1 — Causal Thesis Identity Shadow**

Before persistence expansion, exit changes, or ST-priority behavior changes, answer one real question:

> **Can the existing causal entry evidence reliably classify executed ST entries into exactly one of the three frozen thesis families, while leaving ambiguous entries UNRESOLVED?**

If this is not reliable, all later ST lifecycle and exit logic would be built on the wrong economic identity.

---

# Canonical path summary

> **0. Verify canonical baseline**  
> → **1. Learn the trade's economic identity in shadow**  
> → **2. Persist only minimal causal history**  
> → **3. Shadow trade-relative economic progress/history**  
> → **4. Correct ST/LT ownership using real economic identity**  
> → **5. Shadow thesis-specific protective behavior**  
> → **6. Shadow harvest / healthy-base / CONSUMED behavior**  
> → **7. Persist terminal exit intent/reason**  
> → **8. Activate canonical ST exit policy**  
> → **9. Separate harvest/protective execution urgency**  
> → **10. Solve setup continuity and re-entry novelty**  
> → **11. Validate canonical behavior across replay/backtests**  
> → **12. Apply only small calibration changes**  
> → **13. Freeze the ST implementation**

---

## Final design principle

The roadmap is deliberately designed so that future tuning can make ST slightly stricter or looser **without redefining what ST is**.

The system should store a small set of causal trade facts, derive economic interpretations from those facts plus current market evidence, and preserve the frozen product boundaries above.

A poor backtest result should first trigger investigation of sensitivity, classification coverage, or implementation correctness — not immediate rewriting of the ST philosophy.
