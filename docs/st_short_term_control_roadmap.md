# Short-Term Control Architecture Roadmap

## Status

- **Tur 1 — COMPLETE**: Native evidence inventory, transition episode audit, lineage audit, timeline audit, delta/change-point diagnostic.
- **Tur 2 — NEXT**: Shadow `ShortTermControlAssessment`.
- **Tur 3 — PLANNED**: Canonical read-only integration + multi-symbol/multi-regime validation.
- **Tur 4 — CONDITIONAL**: First narrowly-scoped ENTRY policy usage, only if Tur 2–3 validation passes.

This roadmap intentionally keeps EXIT/lifecycle policy mutation out of the first ENTRY rollout.

---

# 1. Frozen architecture principles

These constraints are treated as architectural invariants for the remaining work:

- **Structure = directional authority.**
- **Control = economic control relationship.**
- **Trade thesis = economic mission.**
- **Opportunity = directional room authority.**
- **Timing = entry maturity, not economic authority.**
- **Execution = fresh/non-sticky event.**
- **Qualification = gate-clearance, not a quality/conviction score.**
- **Lifecycle = position policy.**
- **Context/Permission = derived policy/read-model surfaces, not independent evidence.**
- Same lineage must never be counted as multiple confirmations.
- `UNKNOWN` is not positive evidence.
- ST and LT policy boundaries must remain separate.
- No weighted score, vote, points, confidence threshold, or hidden domain tally.
- No hindsight in production.
- Preserve closed-bar / `available_at` causality.
- No sticky execution semantics.
- No global Structure loosening.
- No trade-count optimization target.
- Refactor and trading-policy changes must not be mixed in one commit.

---

# 2. Tur 1 findings — frozen input to the next phases

## 2.1 Canonical conclusion

There is currently **no canonical Short-Term Control state** answering:

> Who currently controls the short-term market, is that control weakening, contested, or genuinely transferring?

The required primitive observables mostly already exist across native domains. The missing part is the **question + composition**, not necessarily a new native engine/domain.

The preferred architecture is therefore:

> Existing native domains → canonical action-free control synthesis.

A new native domain should only be reconsidered if a required primitive market observable is proven to exist nowhere in the native system.

## 2.2 MTF decision

Canonical MTF alignment is not an independent control authority. It is largely a re-expression of Structure across timeframes and therefore carries substantial lineage-duplication risk.

Permitted use:

- direct per-timeframe native Structure facts (`1h`, `2h`, `30m`) as same-family temporal migration context.

Not permitted:

- treating MTF alignment as an additional confirmation/vote;
- reviving legacy score-based MTF story machinery as control authority.

## 2.3 Tur 1 diagnostics

ASELS frozen replay produced symmetric transition diagnostics for UP/DOWN episodes.

Key findings:

1. Transition episodes are economically meaningful; most eventually resolve to target-side structural establishment.
2. `TRANSITION_UP/DOWN` start itself is **not** an entry signal. Some episodes remain adverse for long periods before eventual confirmation.
3. Successful and failed transition episodes can both contain:
   - challenger participation support,
   - Pattern confirmation/retest,
   - lower-timeframe Structure migration,
   - fresh execution events.
4. Therefore control transfer is not an event-presence checklist.
5. The important information is the **sequence and persistence of economic roles**:
   - incumbent continuation quality,
   - failure to extend,
   - challenger initiative,
   - ground gain,
   - acceptance/defense,
   - reclaim/failure,
   - eventual structural convergence.
6. The failed/recovered transition episode proves that `SUPPORTED`, `Pattern confirmed`, or lower-TF flip cannot independently mean `TRANSFER_ESTABLISHED`.
7. Pattern production can recover causal native phase under `DATA_LIMITED`; control synthesis must preserve production-parity semantics rather than blindly inheriting stricter helper visibility.
8. The 2H bridge can show `UNAVAILABLE` while raw 2H native Structure exists as `DATA_LIMITED`; this must remain fail-closed and must not be silently “repaired.”
9. Lineage IDs are unknown for many Structure/Participation/Pattern/SR/Liquidity refs. Unknown lineage must not be promoted to independence.
10. Counting domains or confirmations is therefore architecturally invalid.

Tur 1 is considered complete. Further ASELS-only exploratory diagnostics should be avoided unless a specific Tur 2 implementation ambiguity requires them, to reduce overfit risk.

---

# 3. TUR 2 — Shadow `ShortTermControlAssessment`

## Goal

Create a canonical, deterministic, **action-free** Short-Term Control read-model over existing native facts.

Tur 2 must produce **zero production trading-policy change**.

The following must remain behaviorally unchanged:

- Scenario
- Eligibility
- Qualification
- Arbiter
- Timing
- Opportunity
- Execution
- BUY
- SELL
- Lifecycle policy

## 3.1 Contract first

Create a typed immutable assessment before wiring policy.

Conceptual shape:

```text
ShortTermControlAssessment

as_of
horizon = SHORT_TERM

established_side
challenger_side | None

structure_state
structure_transition_target

incumbent_condition
challenger_condition
control_state

evidence_roles[]
source_refs[]
lineage_groups[]
unresolved_lineage_refs[]

data_quality
reasons[]
```

Explicitly forbidden fields:

```text
BUY / SELL / HOLD
READY / QUALIFIED / ELIGIBLE
score / confidence / points / votes
Timing state
Opportunity room
Execution event
Position / PnL / MFE / MAE
future outcome
```

## 3.2 Separate the two sides before composing control

Do not immediately compress everything into one state.

### `IncumbentCondition`

Conceptual vocabulary:

```text
UNKNOWN
DEFENDING
PROGRESSING
WEAKENING
FAILING_TO_EXTEND
LOSING_GROUND
```

Question:

> Is the established side still producing and defending economically meaningful progress?

### `ChallengerCondition`

Conceptual vocabulary:

```text
UNKNOWN
ABSENT
EMERGING
INITIATING
GAINING_GROUND
DEFENDING_GROUND
FAILING
```

Question:

> Is the challenger merely appearing, actually gaining ground, defending it, or failing?

This separation is required because a challenger may be gaining ground while the incumbent is not yet defeated. Failed transition episodes prove this distinction matters.

## 3.3 Shadow `ControlState` vocabulary

Initial conceptual set:

```text
UNKNOWN
CONTROL_HELD
CONTROL_WEAKENING
CONTROL_CONTESTED
TRANSFER_DEVELOPING
TRANSFER_ESTABLISHED
TRANSFER_FAILED
```

Semantics:

- **CONTROL_HELD**: established side still controls; challenger has no accepted effective progress.
- **CONTROL_WEAKENING**: incumbent continuation quality deteriorates but challenger has not yet secured meaningful ground.
- **CONTROL_CONTESTED**: both sides are economically active; no effective transfer yet.
- **TRANSFER_DEVELOPING**: challenger has produced actual progress/acceptance beyond a transient attempt, but transfer is not structurally established.
- **TRANSFER_ESTABLISHED**: converge to target-side Structure authority; first implementation must not claim established control before canonical Structure establishes that side.
- **TRANSFER_FAILED**: challenger transfer path fails and incumbent regains control.

State names are not sacred. Semantics matter more than labels and can be adjusted during shadow validation.

## 3.4 Economic evidence roles

Rules must be expressed in terms of **economic roles**, not “N bullish domains.”

Suggested roles:

| Role | Question |
|---|---|
| `INCUMBENT_PROGRESS` | Is the established side still producing new ground? |
| `INCUMBENT_FAILURE_TO_EXTEND` | Are continuation attempts failing to produce result? |
| `INCUMBENT_LOST_GROUND` | Has previously controlled ground been reclaimed? |
| `CHALLENGER_INITIATIVE` | Has the challenger created real directional initiative? |
| `CHALLENGER_ACCEPTANCE` | Has challenger progress been accepted? |
| `CHALLENGER_DEFENSE` | Is challenger-held ground being defended? |
| `CHALLENGER_FAILURE` | Has challenger break/reaction/retest failed? |
| `CONTROL_MIGRATION` | Are subordinate Structure timeframes migrating toward the challenger? |
| `TRANSFER_INVALIDATION` | Is there a native failure fact invalidating the transfer path? |

No role count or numeric accumulation is allowed.

## 3.5 Native input ownership

### Structure

Owns:

- established side;
- transition target;
- CHOCH;
- BOS;
- transition-confirmation BOS;
- transition-fail facts;
- internal/external migration.

Structure remains the directional authority. The control model must not rewrite Structure or reinterpret `TRANSITION_*` as an established direction.

### Participation

Prefer native fields over headline classification:

- participation trend/direction;
- break direction;
- break participation/stage;
- effort-result;
- absorption;
- controlled pullback/reaction;
- heavy conflict.

Economically meaningful transitions include:

```text
DEVELOPING -> SUPPORTED
SUPPORTED -> PROTECTED
SUPPORTED -> RECLAIMED
DEVELOPING -> UNSUPPORTED
EFFICIENT -> WEAK_RESULT
```

A `SUPPORTED` break alone must never imply established transfer.

### Pattern 30m

Use production-parity effective-phase semantics.

Potential roles:

- break attempt;
- break confirming;
- break confirmed;
- post-break retest;
- retest held;
- break failed;
- weakening.

Pattern is maturity/economic evidence, not control authority and not an execution event inside this evaluator.

### Support / Resistance

Prefer semantic break/reclaim/role-reversal meaning over raw location churn.

Potential roles:

- break candidate;
- break confirmed;
- failed break;
- reclaim;
- role reversal;
- acceptance outside range;
- return inside range.

### OB / FVG / Reaction

Use native lifecycle facts where possible:

- challenger reaction developing;
- reaction confirmed;
- reaction failed;
- zone consumed;
- defense/reclaim held or failed.

Derived Reaction assessment may support explanation, but must not create duplicate authority over the same native refs.

### MTF

Do not consume canonical MTF as an independent evidence source.

Direct native Structure facts from `1h / 2h / 30m` may provide same-family migration context.

## 3.6 Lineage discipline

The evaluator must distinguish:

```text
semantic role satisfied
```

from:

```text
independent confirmation
```

The second concept must **not** become a counter/score.

Rules:

- unknown lineage is unresolved, not independent;
- Context reversal is not independent from Structure transition;
- MTF alignment is not independent from Structure;
- Permission is not control evidence;
- Timing supportive is not a second confirmation of OB/FVG/Pattern facts;
- same native event lineage must never be counted twice.

## 3.7 Persistence and replay

First shadow implementation should be **stateless per snapshot**:

```text
control(t) = f(causal facts available at t)
```

Avoid persistent control flags unless future evidence proves they are necessary.

Benefits:

- no sticky state;
- no extra checkpoint burden;
- deterministic replay;
- no “it was developing before, so keep it developing” hindsight-like carryover.

Episode identity may be used diagnostically only when derived from native transition event identity.

## 3.8 Tur 2 tests

Required test families:

- same snapshot → same assessment;
- cold replay determinism;
- restart/resume equivalence;
- closed-bar safety;
- `available_at <= as_of` for source refs;
- UP/DOWN side symmetry;
- execution ablation: removing execution overlap must not alter control;
- timing ablation;
- opportunity ablation;
- Context/Permission ablation;
- unknown lineage does not create independent confirmation;
- Structure authority unchanged;
- failed-transition regression: Pattern confirmed + Participation supported must not auto-establish transfer;
- fast successful episode can resolve without Pattern being mandatory;
- native failure/regain semantics can produce failed/regained control state.

## 3.9 Tur 2 exit criteria

Tur 2 is complete only if:

```text
canonical BUY unchanged
canonical SELL unchanged
Scenario unchanged
Eligibility unchanged
Qualification unchanged
Arbitration unchanged
Structure unchanged
```

And:

- assessment is deterministic;
- no score/vote exists;
- no policy mutation exists;
- failed-transition examples do not collapse into the same semantic path as successful transfers;
- state reasons and native refs remain explainable.

---

# 4. TUR 3 — Canonical read-only integration + broad validation

## Goal

Move the validated shadow assessment into the canonical factual flow **without letting it influence actions**.

## 4.1 Placement decision

At Tur 3 start, re-audit the real call chain and decide between:

1. a canonical Decision-layer immutable assessment; or
2. integration under the factual short-term MarketState surface.

Do **not** create a third parallel synthesis hierarchy.

Decision criterion:

- if the object is clearly a reusable market-condition fact → MarketState placement is natural;
- if current schema/replay ownership makes that unsafe → separate Decision read-model is safer.

No placement decision should be made by filename/name alone; follow actual call-chain and persistence boundaries.

## 4.2 Read-only consumers

Allowed read-only consumers:

- Scenario diagnostics;
- ST thesis diagnostics;
- entry audit/backtest reporting;
- lifecycle shadow diagnostics;
- canonical replay reports.

Forbidden in Tur 3:

```text
if control_state == X:
    change BUY/SELL/policy
```

## 4.3 Control state vs ST thesis

Keep explicit separation:

- **Control state** = market condition.
- **Trade thesis** = why a specific trade exists and what it must defend.

Current thesis families can be compared descriptively against control states:

| Thesis family | Expected relation |
|---|---|
| Pullback continuation | Control held / regained |
| Breakout acceptance | Established/accepted control |
| Failed sell reclaim | Failed bearish initiative / regained bullish control |
| Current missing class | Pre-confirmation transfer developing |

Do not auto-map control state to thesis family in Tur 3.

## 4.4 Lifecycle shadow validation

Without changing SELL/protective policy, compare control states with existing lifecycle diagnostics:

- control held vs normal pullback;
- control weakening vs protective watch;
- challenger transfer developing vs protective deterioration;
- transfer failed vs recovery;
- counter-side progress vs current pressure/protective shadows.

Purpose:

Validate that the same market-condition model is reusable for entry and lifecycle **without embedding trade-specific anchor semantics into the control model**.

## 4.5 Multi-symbol / multi-regime validation

ASELS becomes only one sample.

Required regime coverage:

| Regime | Validation question |
|---|---|
| Strong trend | Does `CONTROL_HELD` remain stable? |
| Normal trend pullback | Does model avoid false transfer? |
| Genuine reversal | Does `TRANSFER_DEVELOPING` emerge meaningfully before 1h Structure confirmation? |
| Failed reversal | Does model resolve toward failed/regained control? |
| Range | Does model avoid constant developing-transfer churn? |
| Volatility shock | Does data noise create false certainty? |
| Low participation | Does UNKNOWN/CONTESTED remain honest? |

UP/DOWN symmetry is mandatory.

## 4.6 Ablation study

Ablation is for semantic necessity, **not performance optimization**.

Compare:

```text
Structure only
Structure + Participation
Structure + Pattern
Structure + SR
Structure + OB/FVG
Structure + Participation + Pattern
Full synthesis
```

Measure:

- UNKNOWN rate;
- state resolution;
- contested frequency;
- false `TRANSFER_DEVELOPING`;
- failed-transfer recognition;
- state churn;
- lead time vs Structure confirmation.

Do not optimize trade returns in this stage.

## 4.7 Tur 3 acceptance criteria

### PASS

Proceed to Tur 4 only if the model is:

- economically interpretable;
- multi-symbol stable;
- symmetric UP/DOWN;
- capable of separating at least some failed transfers from real developing transfers;
- capable of showing meaningful pre-confirmation maturity in some genuine reversals;
- not constantly flagging normal pullbacks/ranges as transfers;
- useful in lifecycle shadow comparison;
- deterministic;
- still action-neutral.

### FAIL

Do not touch production policy if:

- every transition becomes `TRANSFER_DEVELOPING`;
- range regimes create constant churn;
- failed transitions are indistinguishable from true transfers;
- state behavior depends on score-like evidence accumulation;
- lineage uncertainty creates false certainty.

Revise or remove the model at Tur 2/3 level instead.

---

# 5. TUR 4 — First controlled ENTRY policy usage

## Goal

Only after Tur 2–3 validation passes, allow the control assessment to participate in **one narrowly scoped ST entry scenario**.

EXIT/lifecycle policy changes remain separate.

## 5.1 Structure authority remains frozen

Do not globally loosen:

```text
SHORT Structure -> allow LONG
```

Existing continuation/reclaim paths that require established LONG Structure keep their semantics.

Instead, if product evidence justifies it, create a separate transition-target scenario path.

Conceptual form:

```text
Structure:
    established side = SHORT
    transition target = LONG

Control:
    target-side transfer economically developing

Trade thesis:
    explicit transition/reversal-onset mission exists
```

This is a separate setup class, not a reinterpretation of established Structure direction.

## 5.2 New trade thesis decision

If pre-confirmation transfer is tradable after validation, define an explicit trade mission rather than using the control state itself as a thesis.

A future transition thesis must carry:

```text
side
mission
entry premise
defended anchor
invalidation
target path
setup identity
source refs
```

Possible conceptual family name:

```text
TRANSFER_ONSET
```

Final name should follow existing repo terminology and should not be frozen until Tur 4.

## 5.3 Scenario integration

Do not delete `LONG_ENTRY_REQUIRES_LONG_STRUCTURE` globally.

Prefer a distinct path:

```text
Transition-target LONG scenario

requires:
    ST Structure = SHORT + TRANSITION_UP
    AND control transfer sufficiently developed
    AND valid canonical transition trade thesis
```

Structure therefore continues to report the established side honestly while Scenario explicitly represents a different economic trade mission.

## 5.4 Qualification chain

Control state does not directly produce `QUALIFIED`.

Preferred conceptual flow:

```text
Structure
    ↓
ShortTermControlAssessment
    ↓
Economic transition thesis
    ↓
Scenario Presence
    ↓
Eligibility / viability
    ↓
Opportunity / room
    ↓
Qualification
    ↓
Timing
    ↓
Fresh execution event
    ↓
Action
```

Qualification remains gate-clearance, not conviction.

## 5.5 Timing boundary

Timing continues to answer:

> Given a valid economic setup, is this a suitable moment to enter?

Timing must not answer:

> Is control transferring?

`SUPPORTIVE` Timing is not control evidence.

## 5.6 Opportunity boundary

Opportunity remains directional-room authority.

Example:

```text
TRANSFER_DEVELOPING
+
Opportunity NONE
```

is valid: the market may be transferring control while the trade lacks usable room.

Control evaluator must never consume Opportunity.

## 5.7 Execution boundary

Fresh execution remains necessary downstream where policy requires it, but fresh execution is not economic control evidence.

No execution event may create or upgrade control state.

## 5.8 First rollout scope

The first behavior-changing policy should be narrow and versioned.

Conceptual requirements may include:

```text
active Structure transition toward candidate side
+
control transfer developing under validated semantics
+
canonical transition trade thesis PRESENT
+
no hard eligibility blocker
+
economically usable Opportunity room
+
Timing not adverse/failed
+
fresh candidate-side execution event
```

No numeric domain count/score may replace these typed responsibilities.

## 5.9 Changes forbidden in the same policy commit

Do not mix any of these into the first entry-policy change:

- SELL fix;
- protective exit policy;
- lifecycle redesign;
- QUALIFIED refactor;
- MTF rewrite;
- Structure refactor;
- Opportunity loosening;
- Timing loosening;
- lineage-infrastructure overhaul.

## 5.10 Required decision diff

For the first behavior-changing commit report:

```text
Scenario Presence diff
Eligibility diff
Qualification diff
Arbiter diff
BUY diff
SELL diff
```

For every new BUY report:

```text
timestamp
Structure state
Control state
Trade thesis
Opportunity
Timing
fresh execution event
source refs
old blocker
new scenario path
```

## 5.11 Behavioral validation

Do not evaluate success by trade count alone.

Measure:

- premature entries;
- false transfer entries;
- MFE / MAE;
- descriptive 3/6/12-bar returns;
- time to Structure confirmation;
- time to transfer failure;
- missed genuine transitions;
- range churn;
- duplicate setup behavior.

These metrics are for validation, not for retrospectively tuning a hidden score to one ASELS history.

## 5.12 Tur 4 acceptance criteria

Required before production acceptance:

- causality preserved;
- deterministic replay;
- Structure authority preserved;
- Timing remains maturity-only;
- Opportunity remains room-only;
- execution remains fresh/non-sticky;
- control remains action-free;
- trade thesis is explicit;
- unknown lineage remains fail-closed;
- entry path is isolated/versioned;
- exit policy unchanged;
- multi-symbol evidence supports the behavior;
- trade-count target was not used as optimization objective.

---

# 6. Commit strategy

Keep architectural refactor and policy changes separate.

| Commit | Scope | Behavior change |
|---|---|---|
| `T2.1` | Control contracts / evidence roles | None |
| `T2.2` | Shadow evaluator | None |
| `T2.3` | Determinism / causality / lineage tests | None |
| `T2.4` | Shadow diagnostics | None |
| `T3.1` | Canonical read-only wiring | None |
| `T3.2` | Entry/lifecycle shadow reports | None |
| `T3.3` | Multi-symbol/regime validation tools | None |
| `T4.1` | Transition thesis contract | Ideally none |
| `T4.2` | New transition scenario / policy v1 | **Yes** |
| `T4.3` | Canonical backtest and decision-diff validation | Validation only |

---

# 7. Mandatory pre-flight before each phase

Before implementing each new phase, re-verify the repository rather than trusting prior summaries:

```text
current branch
HEAD
git status
PR state
latest CI status
relevant tests
canonical call chain
frozen replay identity
```

Re-follow the real production chain:

```text
DecisionInput
→ prepare_horizon_assessment
→ scenario
→ eligibility
→ qualification
→ arbiter
→ entry/execution
→ lifecycle
```

Also re-check canonical backtest wiring through `scripts/buy_sell_backtest.py`.

Never infer ownership or authority from class/file names alone; follow the actual implementation and call chain.

---

# 8. Main failure modes to avoid

## 8.1 Turning Control into a new QUALIFIED

Do not collapse all economic information into one opaque boolean gate.

## 8.2 Turning Control into Structure v2

Control cannot become an alternative directional authority.

## 8.3 Treating Pattern/Participation presence as confirmation

Failed transition episodes already prove that these facts can appear without an eventual successful transfer.

## 8.4 Ignoring lineage debt

Unknown lineage is not an independent vote.

## 8.5 Coupling ENTRY and EXIT too early

The same control read-model may be reusable by lifecycle, but ENTRY and EXIT policy mutations must remain separate experiments/commits.

---

# 9. Compact roadmap

```text
TUR 1 — COMPLETE
Native evidence, lineage, episode sequence and delta behavior understood.

        ↓

TUR 2
Shadow ShortTermControlAssessment
STATELESS / ACTION-FREE / SCORE-FREE

        ↓

TUR 3
Canonical read-only integration
+
entry/lifecycle shadow analysis
+
multi-symbol / multi-regime validation

        ↓

       PASS?
      /     \
    NO       YES
    ↓         ↓
revise/stop  TUR 4

              ↓

TUR 4
Explicit ST transition thesis
+
separate transition scenario path
+
first narrow ENTRY policy usage
+
canonical multi-symbol backtest/diff

              ↓

accept / revise / rollback
```

---

# 10. Immediate next step

Start **Tur 2.1** only after a fresh pre-flight verification of branch/HEAD/status/CI and actual call-chain.

First implementation target:

> Typed, immutable, action-free Short-Term Control contract and evidence-role vocabulary, with no Scenario/Qualification/BUY/SELL/Lifecycle behavior change.
