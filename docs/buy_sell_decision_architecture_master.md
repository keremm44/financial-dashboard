# BUY/SELL Decision Architecture — Canonical Master Reference

> **Status:** Accepted architecture / implementation reference
>
> **Purpose:** This document is the canonical planning reference for the future BUY/SELL decision layer. Before modifying the decision engine, read this document together with the relevant native domain contracts. It consolidates the accepted parts of the external architecture reviews and mathematical proposals, while explicitly excluding recommendations that were rejected during review.
>
> **Scope:** Market decision semantics, multi-timeframe authority, supporting-domain roles, mathematical composition, readiness/action states, causal lineage, missing-data policy, calibration philosophy, and historical decision audit.
>
> **Out of scope for v1:** position sizing, leverage, portfolio allocation, stop-loss placement, take-profit placement, portfolio risk optimization.

---

## 1. Core Design Goal

The BUY/SELL layer must not behave like a conventional indicator stack. It must be a deterministic, causal, explainable multi-timeframe decision system built on top of existing native engines.

The target pipeline is:

```text
Native Domain Facts
    ↓
Domain Behavior / Lifecycle
    ↓
Lineage + Qualified Zones
    ↓
Cross-Domain Context
    ↓
Permission Envelope
    ↓
Market Decision Layer
    ↓
Long-Term Assessment
    ↓
Short-Term Assessment
    ↓
Timing / Readiness
    ↓
Opportunity / Room
    ↓
Conflict + Data Quality
    ↓
Final Eligibility
    ↓
WAIT / READY / BUY / SELL / NO_TRADE / HOLD
```

The system must be able to explain states such as:

- “Long-term bullish thesis remains intact, but short-term bearish counter-reaction is active.”
- “Direction is favorable, but entry is still too early.”
- “Direction and timing are favorable, but upside opportunity is compressed.”
- “The current OB/FVG reaction failed, but Structure is still bullish.”
- “The prior bullish thesis was invalidated, but a new bearish thesis is not yet established.”
- “1H short-term direction remains valid, but 30m trigger data is unavailable, so execution readiness is incomplete.”

The architecture must preserve those distinctions rather than averaging them away.

---

# PART I — NON-NEGOTIABLE ARCHITECTURAL RULES

## 2. Causal Safety

Only **closed and complete bars** may advance causal state.

Every fact used by the decision engine must obey its causal metadata:

- symbol
- timeframe
- native/source identity
- origin time
- confirmation time
- available-at time
- lineage id
- causal family
- source family
- data quality

A fact whose `available_at` is later than the decision timestamp must not be used.

Historical evaluation may look into the future only in a separate hindsight-audit stage after the causal decision has already been frozen.

---

## 3. Structure Owns Direction and Thesis

**Market Structure is the only domain allowed to establish or change structural directional thesis.**

Supporting domains may influence:

- durability
- reaction quality
- participation quality
- environment
- timing
- opportunity
- conflict
- confidence/evidence coverage
- permission/readiness

They must never directly set:

- LT direction
- ST direction
- LT thesis lifecycle
- ST thesis lifecycle

Example:

```text
Structure: LONG / INTACT
Stabil: BROKEN
OB: FAILED
Volume: WEAK

Valid result:
LT direction: LONG
LT thesis: INTACT
Durability: FRACTURED/BROKEN
Reaction quality: FAILED
Participation: WEAK
Readiness: NOT_READY

Invalid result:
Supporting domains average together and flip thesis to SHORT.
```

This must be enforced by module/function boundaries, not only by documentation. Structural state builders should not accept supporting-domain values as inputs capable of modifying thesis state.

---

## 4. Thesis State Is Structural; Weakness Lives Elsewhere

Accepted thesis states:

```text
INTACT
TRANSITIONING
INVALIDATED
UNRESOLVED
```

A generic `WEAKENING` state is intentionally excluded from structural thesis because it can blur two different concepts:

1. Structure itself is transitioning.
2. Structure remains intact while supporting evidence deteriorates.

Supporting weakness belongs in dimensions such as:

```text
durability = SOFTENING / FRACTURED / BROKEN
reaction_quality = FAILED
participation_quality = WEAK / OPPOSING
conflict = MATERIAL / HIGH
timing = NOT_READY / FAILED
```

Canonical structural events alone move the thesis lifecycle.

A prior `LONG INVALIDATED` must **not** immediately become `SHORT INTACT`. The system may pass through:

```text
LONG / INVALIDATED
→ direction UNRESOLVED
→ new SHORT thesis established later by Structure
```

---

## 5. Long-Term and Short-Term Are Independent Assessments

The system must never collapse LT and ST into one market direction.

Legitimate combinations include:

```text
LT LONG / ST LONG
LT LONG / ST SHORT COUNTER_REACTION
LT LONG / ST WAIT
LT SHORT / ST LONG COUNTER_REACTION
LT UNRESOLVED / ST LONG
LT prior LONG INVALIDATED / ST SHORT INTACT
```

LT and ST are peer assessments with different authorities; ST is not a child state machine that becomes unavailable simply because LT is transitioning or unresolved.

---

## 6. Direction, Durability, Timing and Opportunity Are Different Questions

The system must not hide these inside one number.

Conceptual separation:

```text
Direction   → What structural side exists?
Durability  → How healthy is the thesis foundation?
Reaction    → How did price behave at relevant levels?
Participation → Is the move supported by market participation?
Environment → What volatility regime are we operating in?
Timing      → Is the setup mature now?
Opportunity → Is meaningful room still available?
Conflict    → Are independent families materially contradicting the action?
Coverage    → How complete and reliable is the evidence set?
```

A valid output can therefore be:

```text
Direction: LONG
Thesis: INTACT
Durability: HEALTHY
Timing: READY
Opportunity: COMPRESSED
Final action: WAIT
```

No master score is required or desired.

---

## 7. No Majority Voting

Forbidden architectures include:

```text
3/5 timeframes bullish → BUY
```

and:

```text
Structure +2
OB +1
Volume +1
Pattern -1
Liquidity +1
Total +4 → BUY
```

Timeframes and domains have different authority and semantics. They cannot be treated as peer votes.

---

## 8. Missing Is Not Neutral

Strict distinction:

```text
NO_PATTERN   = pattern engine evaluated successfully and found no pattern
UNAVAILABLE  = pattern could not be evaluated reliably
```

Likewise:

```text
Volume UNAVAILABLE != Volume NEUTRAL
```

Unavailable evidence must reduce coverage/reliability, not manufacture neutral evidence.

---

## 9. Scores Are Not Probabilities

Any continuous value used internally is a quality/readiness/confidence measure unless calibrated empirically against outcomes.

Forbidden before calibration:

```text
confidence_score = 0.80 → “80% win probability”
```

Allowed:

```text
confidence = HIGH
confidence_strength = 0.80
```

Probability terminology is permitted only after proper out-of-sample calibration and reliability validation.

---

# PART II — TIMEFRAME AUTHORITY

## 10. Long-Term Timeframe Roles

| Timeframe | Accepted role |
|---|---|
| **1D** | Primary LT structural authority; long-term thesis; Stabil foundation |
| **4H** | Secondary structural confirmation / transition context; important reaction context |
| **2H** | Bridge / early-transition observer; cannot independently change LT thesis |
| **1H** | LT timing and intermediate confirmation |
| **30m** | Trigger / micro-reaction / execution timing; no LT directional authority |

Important:

- 4H disagreement may contribute to `TRANSITIONING` only through canonical structural rules.
- 2H may warn but cannot silently become primary authority.
- 30m never flips LT direction.

---

## 11. Short-Term Timeframe Roles

| Timeframe | Accepted role |
|---|---|
| **1D** | Background structural context |
| **4H** | Risk / confirmation context |
| **2H** | Bridge between 4H and 1H |
| **1H** | **Primary ST structural authority** |
| **30m** | Timing / trigger / micro-reaction only |

If 1H is unavailable in v1:

```text
ST structural authority = UNAVAILABLE / UNRESOLVED
```

Do **not** silently promote 2H.

If 30m is unavailable while 1H is valid:

```text
ST direction = still valid from 1H
Timing / trigger = UNAVAILABLE
READY / BUY cannot be completed if 30m execution evidence is required
```

---

# PART III — DOMAIN AUTHORITY MAP

## 12. Market Structure

**Primary role:** structural authority.

Available facts include external/internal structure, protected/weak levels, BOS/CHoCH, confirmation, relevance, validity, outcome, BOS maturity.

Allowed to influence:

- direction
- thesis state
- structural transition/invalidation
- structural reliability metadata

Must never be reduced to one vote among supporting domains.

---

## 13. Support / Resistance

**Primary role:** location / obstacle / reaction environment.

Useful inputs include:

- range lifecycle
- boundaries / midpoint
- quality
- boundary stability
- touch count
- close violations
- break attempt / confirmation
- role reversal
- nearest support/resistance
- qualified-zone freshness, relevance, distance ATR, interaction, qualification
- HTF parent/child relation

Critical rule:

`touch_count` is a fact, not a monotonic “strength” score. Repeated tests may strengthen validation initially but later indicate degradation. Interpret touch count together with lifecycle, freshness, interaction and boundary stability.

S/R never manufactures direction.

---

## 14. Stabil

**Primary role:** long-term structural durability / foundation health.

Key facts:

- support level / floor
- validity
- progression
- distance ATR
- bars above / below
- reclaim count
- motion
- relation
- interaction
- bars since rebase
- cross count
- reclaim active

Critical rule:

```text
Stabil broken ≠ LT thesis invalidated
```

Possible output:

```text
LT Structure: LONG / INTACT
Stabil: broken
Durability: BROKEN
Continuation permission/readiness: strongly restricted
```

Structure must later invalidate the thesis if appropriate.

---

## 15. Liquidity

**Primary role:** objective / magnet / sweep context.

Facts include:

- side
- internal/external scope
- target eligibility
- maturity
- relation
- removal/aftermath
- distance ATR
- distance delta ATR
- touches
- age
- landscape: NO_NEARBY_OBJECTIVE / ONE_SIDED_OBJECTIVE / COMPETING_OBJECTIVES

Critical rules:

- uncollected liquidity does not guarantee future price path;
- sweep/reclaim and acceptance beyond are different states;
- `NO_NEARBY_OBJECTIVE != CLEAR_PATH`;
- liquidity is one input to opportunity, not opportunity itself.

Liquidity may affect objective pressure, reaction context, timing and room-to-move, never structural direction.

---

## 16. Order Block

**Primary role:** reaction-zone behavior.

Useful behavior facts:

- bullish/bearish native orientation
- zone boundaries
- lifecycle / interaction
- age
- mitigation count
- visit count
- deepest fill
- distance ATR
- inside bars / closes inside
- current visit length
- favorable exit
- favorable hold duration
- maximum favorable move ATR
- reaction confirmed
- failure reason

Accepted interaction lifecycle:

```text
OUTSIDE
APPROACHING
ENTERED
DWELLING_INSIDE
EXITING_FAVORABLE
HOLDING_FAVORABLE
REACTION_CONFIRMED
FAILED
```

Critical rule:

Five consecutive bars inside one OB are **one visit**, not five mitigations.

OB failure degrades reaction/readiness/conflict but does not flip thesis.

Implementation prerequisite before BUY/SELL uses advanced OB metrics: ensure full OB behavior projection is available in the decision input contract / cross-domain build result.

---

## 17. FVG

**Primary role:** imbalance / reaction-zone condition.

Important facts:

- direction
- lifecycle state
- boundaries
- quality
- gap ATR
- fill ratios
- first test
- reaction evidence
- reaction confirmed
- failed reaction
- full fill
- invalidation

Untouched, partially filled, deeply filled, reaction-confirmed and failed FVGs must not be treated identically.

FVG is supporting reaction evidence, not structural authority.

---

## 18. Engulfing

**Primary role:** confirmation.

Its direction/quality/retrace/continuation facts may support an existing thesis or timing condition.

It must never manufacture structural direction by itself.

---

## 19. Volume / Participation

**Primary role:** participation / move quality.

High-level states include:

- participation: NONE / BUILDING / CONFIRMED / PROTECTED / FADING / ENDED
- effort-result: NEUTRAL / EFFICIENT / WEAK_RESULT
- absorption: NONE / CANDIDATE / CONFIRMED / RESOLVED
- break participation: NONE / DEVELOPING / SUPPORTED / PROTECTED / UNSUPPORTED / RECLAIMED
- shock state

Quantitative facts include RVOL, relative traded value, directional value pressure, net progress ATR and directional efficiency.

Accepted severity distinction:

```text
WEAK            → confidence/readiness modifier, not veto by itself
OPPOSING        → material conflict input
HEAVY_CONFLICT  → serious conflict input
UNSUPPORTED_BREAK → serious conflict/readiness input
```

A strong setup with weak-but-not-opposing volume may still be READY.

Volume never replaces Structure.

---

## 20. Volatility

**Primary role:** environment / regime suitability.

Important states:

- BALANCED
- CONTRACTING
- MATURE_SQUEEZE
- EXPANDING
- NORMALIZING
- SHOCK

Expansion character includes directional confirmation, mean reversion, false excursion and unstable conflict.

Accepted treatment:

```text
SHOCK → hard gate for fresh entries
UNSTABLE_CONFLICT → high environment risk / soft constraint, not automatic hard gate
```

`UNSTABLE_CONFLICT` may cap or reduce readiness only through a calibratable rule; it must never alter structural direction.

---

## 21. Pattern / Compression

**Primary role:** setup maturity / timing.

Accepted lifecycle:

```text
NO_PATTERN
FORMING
MATURE_COMPRESSION
BREAK_ATTEMPT
BREAK_CONFIRMING
BREAK_CONFIRMED
POST_BREAK_RETEST
RETEST_HELD
BREAK_FAILED
WEAKENING
INVALIDATED
COMPLETED
```

Pattern native direction facts may remain visible, but they are **consistency/timing evidence only**.

Compression itself is not directional.

Pattern absence is not a hard gate.

---

## 22. HAM

**Primary role:** evidence coverage / supporting breadth.

Families:

- PRICE
- MOMENTUM
- TIMING
- FLOW

Each provides balance/activity/coverage/readiness.

Accepted rule:

HAM must not become a hidden master signal, structural authority or standalone hard gate.

HAM degraded/unavailable reduces evidence coverage/confidence. It should not automatically cap READY when the genuinely required path is otherwise complete.

---

## 23. Targeting

**Primary role:** opportunity / room-to-move / objective context.

Useful facts:

- nearest upside/downside target
- highest-confluence targets
- distance ATR / %
- independent origin/family count
- roles / quality
- internal/external liquidity references

Target confluence does not establish direction.

Accepted public opportunity states:

```text
AMPLE
MODERATE
COMPRESSED
NONE
UNKNOWN
```

Accepted semantics:

```text
AMPLE      → normal opportunity
MODERATE   → acceptable but less attractive
COMPRESSED → fresh entry normally remains WAIT; not equivalent to hard NO_TRADE
NONE       → legitimate hard block for fresh directional entry
UNKNOWN    → evidence insufficient; do not pretend NONE or AMPLE
```

No fixed ATR cutoffs are architectural truths. Thresholds require historical calibration.

---

## 24. Qualified Zone Intelligence

**Primary role:** location/reaction summary, not new independent evidence.

It may combine S/R, protected levels, Stabil, OB, FVG, Engulfing confirmation and liquidity overlays.

Critical rule:

Do not count:

```text
OB + FVG + Qualified Zone + Reaction Context
```

as four independent confirmations if they share causal origin.

Qualified zones are downstream synthesis and must retain lineage.

---

## 25. Cross-Domain Context

Existing semantic axes remain useful:

- Structural thesis
- Continuation
- Reaction
- Reversal
- Objective
- Participation
- Volatility
- Pattern readiness
- MTF
- HAM readiness
- Conflict

Context is a semantic synthesis layer. It must **not** be re-injected as extra evidence after the original domain facts have already contributed.

---

## 26. Permission Envelope

Permission remains a separate pre-action eligibility layer.

Current conceptual fields:

```text
scope: NONE / REACTION_ONLY / CONTINUATION_ONLY / STRUCTURAL_TRANSITION
side: NONE / LONG / SHORT
gate: BLOCKED / WAITING / CONDITIONAL / OPEN
allowed_reasons[]
blocking_reasons[]
waiting_for[]
source_refs[]
```

Critical rule:

```text
Permission OPEN != BUY
```

Permission is a gate/summary, not independent evidence.

---

# PART IV — DECISION OBJECTS

## 27. Long-Term Assessment

Recommended contract:

```text
LongTermAssessment
    direction:
        LONG | SHORT | UNRESOLVED

    thesis_state:
        INTACT | TRANSITIONING | INVALIDATED | UNRESOLVED

    durability:
        HEALTHY | SOFTENING | FRACTURED | BROKEN | UNKNOWN

    continuation
    reaction_quality
    participation_quality
    environment
    opportunity
    conflict
    coverage
    permission_implication

    reasons[]
    blockers[]
    waiting_for[]
    source_lineage[]
```

LT thesis direction/state remains structurally owned; the rest describes quality and eligibility.

---

## 28. Short-Term Assessment

Recommended contract:

```text
ShortTermAssessment
    direction:
        LONG | SHORT | UNRESOLVED

    thesis_state:
        INTACT | TRANSITIONING | INVALIDATED | UNRESOLVED

    relation_to_LT:
        ALIGNED
        PULLBACK
        COUNTER_REACTION
        EARLY_TRANSITION
        STRUCTURAL_CONFLICT
        LT_UNRESOLVED
        POST_INVALIDATION  # optional explicit refinement

    timing:
        EARLY
        DEVELOPING
        READY
        EXTENDED
        FAILED
        UNAVAILABLE

    trigger:
        ABSENT
        FORMING
        CONFIRMED
        FAILED
        UNAVAILABLE

    opportunity
    conflict
    coverage

    reasons[]
    blockers[]
    waiting_for[]
    source_lineage[]
```

A separate `pullback_state` is not required in v1 if `relation_to_LT` captures the same information cleanly.

---

## 29. Horizon Relation

`LT LONG + ST SHORT` is insufficiently descriptive by itself.

The relation layer should distinguish at least:

```text
ALIGNED
PULLBACK
COUNTER_REACTION
EARLY_TRANSITION
STRUCTURAL_CONFLICT
LT_UNRESOLVED
```

Possible optional refinements:

```text
TACTICAL_ONLY
POST_INVALIDATION
```

These labels are descriptive relations; they must never feed back upstream to rewrite LT or ST structural thesis.

---

## 30. Final Action States

Accepted v1 action vocabulary:

```text
WAIT
READY
BUY
SELL
NO_TRADE
HOLD
```

`WATCH` is intentionally omitted because `WAIT + waiting_for[]` already expresses an actionable watch state.

Semantics:

### WAIT
A defensible thesis/action path exists, but known conditions are incomplete.

```text
WAIT
waiting_for = ["30m bullish trigger", "reaction confirmation"]
```

### READY
Market conditions are action-eligible, but final execution event has not yet fired.

### BUY / SELL
A final execution event has occurred while eligibility remains valid.

### NO_TRADE
No defensible action path exists, or a hard blocker is active.

### HOLD
Reserved mainly for existing-position logic; not needed for pure new-entry market assessment.

---

# PART V — MATHEMATICAL / COMPUTATIONAL ARCHITECTURE

## 31. Multi-Dimensional Decision Vector

Recommended dimensions:

```text
StructuralState
Durability
ReactionQuality
ParticipationQuality
EnvironmentQuality
TimingQuality
OpportunityQuality
ConflictRisk
EvidenceCoverage
Permission
```

The architecture is a **vector of typed dimensions + hierarchical gating**, not a scalar.

No operation should reduce all dimensions to `FINAL_SCORE`.

---

## 32. Category-Then-Continuum Principle

For supporting dimensions, the safest mathematical pattern is:

1. Determine the categorical state from explicit native/lifecycle rules.
2. Optionally compute a bounded continuous strength **inside that state** for confidence, calibration or hysteresis.
3. Do not allow the continuous value to silently override the categorical semantics.

Examples:

```text
Durability:
    public = HEALTHY / SOFTENING / FRACTURED / BROKEN
    internal strength = optional [0,1]

Participation:
    public = SUPPORTIVE / WEAK / OPPOSING / NEUTRAL
    internal strength = optional [0,1]

Reaction:
    public = CONFIRMED / DEVELOPING / ABSENT / FAILED / UNKNOWN
    internal strength/maturity = optional vector
```

Structure remains purely categorical plus quality metadata; no “0.7 LONG.”

---

## 33. Continuous vs Categorical Recommendations

| Dimension | Recommended representation |
|---|---|
| Structure | Pure categorical state machine + quality metadata |
| Durability | Ordinal category; optional continuous strength |
| Reaction | Lifecycle category + optional strength/failure vector |
| Participation | Ordinal category + optional continuous refinement |
| Environment | Native categorical regime + optional suitability/risk metadata |
| Timing | State machine; optional progress within state |
| Opportunity | Category + continuous room metric |
| Conflict | Category + optional continuous intensity |
| Coverage | Continuous fraction / quality mask |
| Confidence | Optional; continuous + LOW/MED/HIGH only after validation |

---

## 34. Normalization Philosophy

Native metrics use incompatible scales. Accepted principles:

- Preserve bounded native metrics if their semantics are already meaningful.
- ATR-normalize price distances.
- Never use future-aware/global min-max normalization.
- Prefer causal rolling/expanding transforms.
- Robust transforms are preferred over Gaussian assumptions.
- Use clipping/winsorization only when justified and documented.
- Rolling percentiles / robust z-scores may be used for unbounded metrics such as RVOL or extension measures.
- Every threshold introduced by the decision layer must be classified as architectural, native or calibratable.

No fixed values such as `0.3 ATR`, `1 ATR`, `RVOL 2x`, etc. should be treated as universal market truths.

---

## 35. Authority-Aware Timeframe Composition

Do not use fixed TF weights such as:

```text
1D 40% + 4H 30% + 2H 15% + ...
```

Preferred approach:

- Primary authority determines the structural base.
- Secondary authority may confirm/flag transition according to explicit structural rules.
- Bridge timeframe provides early-warning context.
- Lower timeframes affect timing/trigger only.

Supporting-dimension composition may use bounded modifiers only when there is a clear semantic reason, and such parameters remain calibration parameters rather than hidden directional voting.

---

# PART VI — LINEAGE AND DOUBLE COUNTING

## 36. Lineage-Aware Evidence Deduplication

This is foundational and must exist from v1.

Each evidence item should carry:

```text
lineage_id
causal_family
source_family
timeframe
native_id
data_quality
is_derived
```

Core rules:

1. Group evidence by `lineage_id`.
2. Within the same lineage, do not count multiple representations as independent confirmations.
3. Within a causal family, prefer representative/max-pool or diminishing returns rather than summation.
4. Across genuinely independent families, use dimension-specific composition.
5. Context and Permission are `is_derived = true`: use as summary/gate, never as new evidence.
6. Cross-timeframe echoes of the same root event must not be counted independently.

Example to prevent:

```text
1 BOS
→ Structure LONG
→ Context UP
→ MTF aligned
→ Permission LONG OPEN
```

This is **one structural lineage**, not four confirmations.

---

## 37. Lineage Integrity Is a Prerequisite

The mathematical reviews correctly identified lineage quality as a hidden dependency risk.

Before relying on sophisticated deduplication/conflict math, implement dedicated tests that verify obvious common-origin facts actually share lineage metadata.

If lineage metadata is wrong, double-counting protection can silently fail while still producing plausible outputs.

Therefore:

```text
lineage-integrity tests = required before advanced weighting/conflict calibration
```

---

# PART VII — CONFLICT

## 38. Conflict Is Not Voting

Conflict is evaluated relative to an intended structural side.

It should consider:

- severity
- authority
- causal independence
- relevance
- recency
- data quality

Accepted public states:

```text
NONE
LOW
MATERIAL
HIGH
UNRESOLVED
```

Normal LT/ST disagreement is not automatically conflict. Example:

```text
LT LONG
ST SHORT / COUNTER_REACTION
```

may be a healthy pullback state rather than `HIGH` conflict.

---

## 39. Conflict V1 vs Advanced Math

Advanced proposals such as noisy-OR are plausible:

```text
conflict_intensity = 1 - Π(1 - w_i)
```

where `w_i` depends on severity/authority/quality/recency.

However, this requires well-identified calibration parameters.

Accepted implementation strategy:

### V1
Use a transparent rule table over **independent families**.

Examples:

- WEAK participation alone → not MATERIAL.
- OPPOSING participation → conflict input.
- Reaction FAILED → conflict input.
- Multiple independent severe conflicts → HIGH candidate.

### V1.1+
Only move to continuous noisy-OR when historical replay demonstrates that the coarse rule table is limiting decision quality and parameters can be calibrated robustly.

---

# PART VIII — MISSING DATA AND QUALITY

## 40. Data Availability States

At minimum distinguish:

```text
VALID
DEGRADED
WARMUP
UNAVAILABLE
```

Policy:

- VALID → normal use
- DEGRADED → may contribute with reduced reliability; exact discount is calibratable
- WARMUP → generally unavailable for reliable decision contribution
- UNAVAILABLE → excluded from evidence computation

Never replace unavailable data with an invented neutral value.

---

## 41. Critical-Path Missingness

Critical structural authority is a hard dependency.

Examples:

```text
LT action requires valid 1D structural authority.
ST action requires valid 1H structural authority.
```

30m is critical for execution readiness if the chosen action contract requires a 30m trigger, but missing 30m does **not** invalidate 1H ST direction.

Supporting domains such as Pattern, FVG, OB, HAM and weak Volume are not individually hard dependencies.

---

## 42. Evidence Coverage

Coverage should be represented independently of direction.

Possible fields:

```text
coverage_fraction
critical_path_missing[]
degraded_families[]
unavailable_families[]
```

Coverage may reduce confidence and, if materially insufficient on the execution-critical path, cap readiness.

Do not let low-authority missing families disproportionately destroy confidence.

---

# PART IX — TIMING, READINESS AND EXECUTION

## 43. Timing State Machine

Accepted target states:

```text
EARLY
DEVELOPING
READY
EXTENDED
FAILED
UNAVAILABLE
```

Timing should be an explicit state machine, not a score threshold alone.

Key inputs may include:

- reaction lifecycle
- pattern phase
- liquidity sweep/reclaim context
- volatility transition
- relevant zone proximity
- setup trigger
- 30m execution context

State transitions should be explainable as named guard conditions.

---

## 44. Setup Trigger vs Execution Trigger

To avoid circularity, distinguish two concepts.

### Setup Trigger
Confirms that the setup has matured enough for READY.

Examples may include:

- reaction confirmed
- retest held
- pattern break confirmed
- equivalent mature contextual confirmation

### Execution Trigger
A fresh event while READY that fires the actual BUY/SELL action.

Examples are implementation-specific but must be causal and evaluated on a closed bar.

Conceptually:

```text
READY(t) = market is eligible
BUY(t) = READY(t) AND execution_trigger(t)
```

Do not use the exact same fact twice as both setup and execution confirmation unless the contract explicitly defines why the two semantic roles are distinct and prevents duplicate counting.

---

## 45. WAIT vs READY vs NO_TRADE

### WAIT
A thesis exists and the missing condition is known.

Examples:

```text
waiting_for = 30m trigger
waiting_for = reaction confirmation
waiting_for = volatility normalization
waiting_for = more room / resistance clearance
```

### READY
All market-eligibility requirements are satisfied; execution is permitted but not yet fired.

### NO_TRADE
One of the following applies:

- no defensible structural thesis for intended action
- critical structural data unavailable
- explicit permission block
- hard shock gate
- no meaningful room
- severe independent-family conflict (after v1 rules classify it as hard)

---

## 46. Early vs Extended

`EARLY` means the setup has not yet matured.

Possible evidence:

- counter-reaction still active
- price has not reached relevant reaction context
- setup trigger absent
- pattern immature
- liquidity interaction incomplete
- volatility transition immature

`EXTENDED` means an entry opportunity existed but price has moved beyond the attractive entry window.

Potential later-calibrated features:

- ATR-normalized distance from reaction zone
- distance from breakout/retest level
- volatility expansion age
- participation fading/ended
- nearby target/obstacle proximity
- missed retest

No fixed ATR threshold is accepted yet.

---

# PART X — OPPORTUNITY / ROOM

## 47. Opportunity Must Consider Multiple Obstacle Families

`NO_NEARBY_LIQUIDITY` alone does not mean AMPLE opportunity.

Room should consider independent obstacle/objective families such as:

- opposing qualified S/R zone
- high-quality HTF zone
- structural level
- relevant liquidity objective
- high-confluence target
- other canonical target/obstacle facts already present in the system

Use lineage/parent-child relations to avoid counting the same zone and its source components multiple times.

A quality-aware nearest-obstacle model is acceptable, but exact formulas and thresholds remain calibration work.

---

# PART XI — HARD GATES AND SOFT CONSTRAINTS

## 48. Accepted Hard Gates

Keep the set small.

### G1 — Critical structural authority unavailable/invalid
No directionally defensible action exists.

### G2 — Structural direction unresolved for the horizon being acted upon
Do not manufacture direction from supporting evidence.

### G3 — Prior thesis invalidated with no new valid thesis
Do not act on the invalidated side; do not assume automatic reversal.

### G4 — Permission BLOCKED
Permission remains an authoritative pre-action gate.

### G5 — Volatility SHOCK for fresh positions
Accepted v1 hard gate.

### G6 — Opportunity NONE
No meaningful room for a fresh directional entry.

### G7 — HIGH conflict from genuinely independent causal families
Accepted as a gate concept, but **the exact classification from evidence to HIGH is calibration/rule-table work**.

`UNRESOLVED` conflict should not automatically equal HIGH; depending on cause it may cap readiness instead of hard-blocking.

---

## 49. Explicit Non-Gates

The following must not independently hard-block an otherwise valid setup:

- weak volume
- HAM degraded/unavailable
- Pattern absent
- FVG absent
- OB absent
- COMPRESSED opportunity
- UNSTABLE_CONFLICT volatility alone
- LT/ST directional disagreement alone

Their effects are dimension-specific soft constraints, confidence reductions or WAIT conditions.

---

# PART XII — CONFIDENCE

## 50. Confidence Is Optional and Must Not Duplicate Readiness

If implemented later:

```text
Readiness = Are required conditions present?
Confidence = How strong/reliable/independent is the available supporting evidence?
```

Therefore this is legitimate:

```text
READY + LOW confidence
```

Confidence must not be shown as probability until calibrated.

For v1, omitting confidence entirely is preferable to shipping an unvalidated pseudo-precision number.

---

# PART XIII — CASH-EQUITY ACTION SEMANTICS

## 51. Market Side vs Executable Action Side

The market assessment must remain independent of brokerage/action capability.

```text
market_side = LONG | SHORT | NONE
action_side = LONG | SHORT | NONE
```

For long-only cash-equity v1:

```text
permitted_sides = {LONG}
```

Example:

```text
LT LONG / INTACT
ST SHORT / COUNTER_REACTION
```

This may mean:

```text
Long entry = WAIT
```

It does **not** automatically mean “open a short position.”

Future existing-position logic may interpret a bearish state as reduce/exit a long, but that requires explicit position context and should remain a thin separate action layer.

---

# PART XIV — HISTORICAL DECISION AUDIT

## 52. Two Strictly Separate Modes

### Mode A — Causal Backtest
The decision engine receives only information available at that bar.

### Mode B — Hindsight Audit
After the decision is frozen, later bars may be used to evaluate whether that historical decision was early, late or inefficient.

Hindsight values are NEVER decision inputs.

---

## 53. Core Trade Metrics

Every historical test should report at least:

- number of BUY/SELL events
- completed trades
- win/loss/breakeven counts
- win rate
- average return
- median return
- compounded return
- average winner / loser
- best / worst trade
- holding duration
- MFE
- MAE
- move capture ratio

---

## 54. Entry Quality Audit

For every BUY, measure:

```text
entry_local_low_miss_pct
entry_local_low_miss_atr
entry_early_bars
entry_late_bars
post_entry_additional_downside_pct
post_entry_additional_downside_atr
bars_to_post_entry_low
```

Purpose:

- Did the engine buy too early before a deeper low?
- Did it wait too long and miss the local bottom?
- How large was the timing error in percent, ATR and bars?

This directly supports questions such as:

> “Why did BUY fire while a deeper low was still ahead?”

---

## 55. Exit Quality Audit

For every SELL/exit, measure:

```text
exit_peak_miss_pct
exit_peak_miss_atr
exit_early_bars
exit_late_bars
post_exit_missed_upside_pct
post_exit_missed_upside_atr
profit_giveback_pct
profit_giveback_atr
```

Purpose:

- Did the engine exit too early and leave major upside?
- Did it exit too late after giving back open profit?
- How much of the available move was captured?

---

## 56. MFE, MAE and Capture Ratio

Required definitions:

### MFE — Maximum Favorable Excursion
Best open-profit excursion while the trade was active.

### MAE — Maximum Adverse Excursion
Worst adverse excursion while the trade was active.

### Move Capture Ratio

Conceptually for a long trade:

```text
realized favorable move / maximum favorable excursion
```

Report both trade-level and aggregate average/median capture.

---

## 57. Signal Timeline Audit

Do not inspect only BUY/SELL bars.

Store the complete decision path:

```text
WAIT
WAIT
DEVELOPING
READY
BUY
...
SELL
```

Useful metrics:

- average WAIT duration
- average READY duration
- READY→WAIT reversals
- READY→BUY delay
- decision churn
- failure/cooldown behavior

This is essential for tuning hysteresis and readiness logic.

---

## 58. Missed Opportunity Audit

The system must also measure meaningful price moves it failed to capture.

A hindsight-only swing detector may label a significant historical move, then audit whether the causal engine remained WAIT/NO_TRADE throughout.

Store:

```text
missed_move_pct
missed_move_atr
reason_not_entered
blocking_state
waiting_for_state
```

This prevents optimizing only for false positives while ignoring excessive conservatism.

---

## 59. Decision Snapshot / Explainability Record

Every decision event should preserve a full snapshot:

```text
symbol
timestamp
action
price
ATR

LT direction / thesis / durability
ST direction / thesis / relation_to_LT
Timing
Trigger
Opportunity
Conflict
Permission
Coverage

Structure refs
Stabil refs
Liquidity refs
OB/FVG refs
Volume state
Volatility state
Pattern state
HAM coverage

reasons[]
blockers[]
waiting_for[]
source_lineage[]
```

A poor historical trade must be traceable back to its exact domain states.

---

## 60. Audit Segmentation

Historical performance must be sliced by semantic state, not only globally.

At minimum:

- LT direction/thesis state
- ST direction/thesis state
- LT/ST relation
- timing state
- opportunity state
- volatility regime
- reaction state
- participation state
- conflict category
- data-quality/coverage state

This is how calibration decisions should be justified.

---

# PART XV — CALIBRATION AND VALIDATION

## 61. Parameter Taxonomy

Every rule/threshold must be classified.

### Class A — Architectural constants
Examples:

- Structure owns direction.
- Missing does not become neutral.
- Derived summaries do not count as independent evidence.
- 30m does not set ST direction.
- SHOCK blocks fresh entry in v1.
- Permission BLOCKED is a gate.

### Class B — Native domain parameters
Already owned by native engines and must not be silently redefined by BUY/SELL.

Examples:

- BOS/CHoCH semantics
- OB visit logic
- Pattern lifecycle definitions
- Volatility regime definitions
- FVG fill semantics
- native quality calculations

### Class C — Decision calibration parameters
Examples:

- what counts as COMPRESSED room
- exact HIGH conflict rule thresholds
- hysteresis/dwell duration
- extension thresholds
- degraded quality modifiers
- confidence boundaries

Class C values require causal historical validation.

---

## 62. Calibration Strategy

Accepted principles:

- chronological train/validation/test splits
- walk-forward / expanding-window validation
- purge/embargo around overlapping windows where necessary
- pooled calibration across multiple liquid BIST names before symbol-specific tuning
- regime-stratified evaluation
- prefer parameter stability over peak in-sample performance
- avoid optimizing one stock
- avoid large parameter sets in v1
- simplicity wins when alternatives perform similarly

Do not introduce machine learning merely because it is available. A deterministic rule/state system is acceptable and preferable for v1 if it remains explainable.

---

## 63. What to Optimize

Do not optimize solely for total return at this stage.

Primary evaluation targets should include:

- directional follow-through after READY/BUY
- false READY rate
- WAIT→READY conversion quality
- MFE/MAE
- early-entry severity
- late-entry severity
- early-exit missed upside
- profit giveback
- move capture ratio
- missed-opportunity rate
- post-signal structural invalidation
- signal churn/stability
- opportunity-state validity
- performance by regime/state

PnL/portfolio optimization belongs later.

---

# PART XVI — HYSTERESIS AND STATE STABILITY

## 64. Stability Rules

State churn such as:

```text
READY → WAIT → READY → WAIT
```

must be measurable and controlled.

Accepted approach:

- event-driven structural changes are immediate;
- hard-gate activation is immediate;
- score/quality-based upgrades may require persistence/hysteresis;
- risk deterioration may react faster than improvement;
- cooldown after a failed trigger/reaction may be lineage-specific;
- different timeframes require different persistence parameters.

Do not apply a universal “3 bars” rule to both 30m and 1D.

Exact dwell/hysteresis values are Class C calibration parameters.

---

# PART XVII — REJECTED / DEFERRED PROPOSALS

## 65. Explicitly Rejected

The following proposals must not be introduced without a new architecture review:

1. **Universal master score** for BUY/SELL.
2. **Timeframe majority voting**.
3. **Domain majority voting**.
4. Supporting domains changing structural thesis.
5. Stabil directly invalidating thesis.
6. 30m becoming ST structural authority.
7. Automatic 2H promotion when 1H is unavailable.
8. Missing Volume → NEUTRAL.
9. Missing Pattern → NO_PATTERN.
10. `NO_NEARBY_LIQUIDITY → CLEAR_PATH`.
11. Touch count as monotonic zone strength.
12. Weak Volume as automatic veto.
13. HAM degraded as automatic hard READY cap.
14. UNSTABLE_CONFLICT as automatic hard gate.
15. COMPRESSED opportunity treated as the same thing as NONE.
16. `LT invalidated → ST invalidated` coupling.
17. `LT transitioning → ST unavailable` coupling.
18. Automatic physical short action from an ST SHORT state in long-only cash-equity mode.
19. Uncalibrated score presented as probability.
20. Fixed universal ATR/RVOL magic thresholds treated as market truths.

---

## 66. Deferred to Later Versions

Potentially useful after v1 validation:

- full continuous reaction strength
- full continuous participation strength
- noisy-OR conflict with calibrated family weights
- confidence score and reliability curves
- sophisticated EXTENDED model
- dimension-specific adaptive hysteresis
- cross-symbol regime-aware quantile calibration
- relative strength / index / sector context
- corporate event/calendar awareness
- spread/transaction-cost feasibility
- borrow/short availability
- advanced session/auction handling

These should not block v1.

---

# PART XVIII — IMPLEMENTATION ORDER

## 67. Required Pre-Decision Integration Work

Before the final BUY/SELL composer depends on every behavior metric:

1. Ensure full **OrderBlockBehaviorProjection** is available to the decision input contract.
2. Expose **Liquidity Landscape** cleanly in the decision input contract.
3. Add lineage-integrity tests across obvious cross-domain shared origins.
4. Confirm data-quality/available-at propagation is intact for every decision fact.

These are data-contract tasks, not new indicators.

---

## 68. V1 Implementation Sequence

### Step 1 — Decision input contract
Create immutable typed input views over existing outputs. Do not change native engine ownership.

### Step 2 — Structural assessments
Implement LT and ST direction/thesis using existing canonical Structure only.

### Step 3 — Horizon relation
Implement ALIGNED / PULLBACK / COUNTER_REACTION / EARLY_TRANSITION / STRUCTURAL_CONFLICT / LT_UNRESOLVED semantics without feeding back to Structure.

### Step 4 — Supporting categorical assessments
Implement initial categorical forms of:

- durability
- reaction
- participation
- environment
- opportunity
- coverage

Prefer native lifecycle reuse over new scores.

### Step 5 — Conflict v1 rule table
Use independent family severity rules and lineage dedup. Avoid over-parameterized math initially.

### Step 6 — Timing/readiness state machine
Implement causal EARLY / DEVELOPING / READY / FAILED / UNAVAILABLE. EXTENDED may be deferred until baseline behavior is validated.

### Step 7 — Hard gates / permission composition
Implement the accepted small hard-gate set.

### Step 8 — Action composer
Produce WAIT / READY / BUY / SELL / NO_TRADE. Keep action capability separate from market-side assessment.

### Step 9 — Decision snapshots
Every state/action must persist reasons, blockers, waiting_for and lineage.

### Step 10 — Historical causal replay + hindsight audit
Use the decision audit engine to evaluate the actual behavior before adding sophisticated scores.

### Step 11 — Calibrate only demonstrated weaknesses
Add continuous math only where the audit proves categorical v1 is insufficient.

---

# PART XIX — MINIMUM VIABLE V1

## 69. V1 Scope

The smallest defensible BUY/SELL system should include:

- full structural authority model
- LT/ST independent assessments
- horizon relation
- categorical durability
- categorical reaction lifecycle
- categorical participation severity
- SHOCK hard gate / UNSTABLE soft environment treatment
- opportunity states with explicitly provisional/calibrated boundaries
- lineage dedup from day one
- simplified independent-family conflict rules
- timing/readiness state machine
- Permission integration
- WAIT / READY / BUY / SELL / NO_TRADE
- complete decision snapshots
- historical audit integration

Confidence can be omitted initially.

EXTENDED can be deferred if needed, but historical audit must already collect enough features to study late entries/exits.

---

# PART XX — REQUIRED TEST SCENARIOS

## 70. Canonical Scenario Expectations

### Scenario A — LT bullish, ST bearish pullback, no 30m trigger

```text
LT: LONG / INTACT
ST: SHORT / COUNTER_REACTION or PULLBACK
Durability: HEALTHY
Timing for new long: EARLY/DEVELOPING
Action: WAIT
```

### Scenario B — Fully aligned bullish, trigger confirmed, weak-not-opposing volume

```text
LT: LONG / INTACT
ST: LONG / INTACT
Reaction: confirmed
Volume: WEAK
Opportunity: AMPLE
Action: READY/BUY depending on execution trigger
```

Weak volume must not veto by itself.

### Scenario C — 1D bullish, 4H structural transition down, 1H bearish

```text
LT: LONG / TRANSITIONING
ST: SHORT / INTACT
relation_to_LT: EARLY_TRANSITION
new LT-long continuation: suspended / WAIT or NO_TRADE depending on permission/conflict
```

### Scenario D — liquidity sweep/reclaim + bullish OB reaction, trigger absent

```text
Reaction: strong/confirmed
Timing: not complete
Action: WAIT
```

### Scenario E — bullish Structure, OB/FVG reactions fail

```text
Thesis: still LONG / INTACT
Reaction: FAILED
Conflict/readiness: degraded
Action: WAIT or NO_TRADE only if an actual hard gate is reached
```

### Scenario F — direction/timing excellent, major resistance very near

```text
Timing quality: strong
Opportunity: COMPRESSED
Final action: WAIT
```

### Scenario G — otherwise strong setup, volatility UNSTABLE_CONFLICT

```text
Direction unchanged
Environment risk high
Readiness reduced/capped according to calibrated soft rule
Not automatic hard NO_TRADE
```

### Scenario H — otherwise strong setup, volatility SHOCK

```text
Fresh entry: NO_TRADE
```

### Scenario I — LT unresolved, ST LONG valid

```text
LT: UNRESOLVED
ST: LONG / INTACT
ST market assessment remains valid
Whether ST-only long action is permitted is an explicit action-scope/config decision
```

### Scenario J — prior LT LONG invalidated, ST SHORT already intact

```text
LT: UNRESOLVED after invalidation
ST: SHORT / INTACT
No automatic new LT SHORT thesis
```

### Scenario K — LT/ST LONG but 30m unavailable

```text
ST direction: remains LONG from 1H
Trigger/timing: UNAVAILABLE
Action: WAIT
```

### Scenario L — LT LONG, ST SHORT counter-reaction near major LT support

```text
ST bearish state is analytically valid
New long entry: WAIT
Physical short action: not automatic in long-only cash-equity mode
Short-side opportunity may itself be poor due to nearby LT support
```

These scenarios should become unit/integration tests for the decision layer.

---

# PART XXI — GOVERNANCE

## 71. How This Document Must Be Used

Before implementing or changing BUY/SELL logic:

1. Read this master reference.
2. Read the native contract(s) for any domain being changed.
3. Determine whether the proposed change affects:
   - structural authority,
   - domain ownership,
   - LT/ST independence,
   - causal availability,
   - lineage/deduplication,
   - hard-gate semantics,
   - calibration parameters.
4. If a change violates a non-negotiable rule, do not implement it as a local patch; update the architecture intentionally first.
5. Keep all new mathematical thresholds labeled Class A/B/C.
6. Add decision-audit observability for every new state used by BUY/SELL.

---

## 72. Final Architectural Summary

The accepted model is:

```text
Structure → thesis and direction
Stabil → long-term durability
S/R + Qualified Zones → location / obstacles
OB + FVG → reaction behavior
Engulfing → confirmation
Liquidity → objectives / sweeps
Volume → participation quality
Volatility → environment
Pattern → setup maturity
HAM → evidence coverage
Targeting → opportunity / room
Context → semantic synthesis
Permission → pre-action eligibility

LT and ST → independent horizon assessments
Lineage → prevents double counting
Timing state machine → says whether now is early/developing/ready
Opportunity → says whether enough room remains
Conflict → evaluates independent contradiction, not voting
Final composer → WAIT / READY / BUY / SELL / NO_TRADE
Historical audit → measures whether those decisions were early, late, profitable, stable and explainable
```

The first implementation should be **small, categorical, causal and fully auditable**. Sophisticated continuous mathematics should be added only where historical causal replay shows a real deficiency and provides enough evidence to calibrate the added complexity.
