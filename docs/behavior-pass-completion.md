# Behavior Architecture Pass — Completion Note

This note records the implemented outcome of the behavior-architecture review that guided this pass. It documents what was actually changed, what was deliberately left unchanged, and the safety boundaries that must remain true for future BUY/SELL work.

## Completed behavior/read-model passes

### Stabil structural support

Stabil keeps its canonical support lifecycle and adds a descriptive behavior layer. The context projection now carries support motion, price/support relation, interaction state, persistence/reclaim facts, and the existing lifecycle facts without turning them into an action signal.

### Liquidity

Liquidity keeps the canonical pool lifecycle and adds an incremental behavior tracker. The added behavior separates:

- pool maturity,
- price relation to the pool,
- removal/sweep aftermath,
- nearby one-sided versus competing objectives.

The behavior is projected as separate causal facts. It does not replace target eligibility, invent partial-sweep semantics that the native data cannot support, or vote on direction.

### Order Block

Order Block keeps the source-faithful canonical engine unchanged. A separate tracker retains descriptive lifecycle facts such as fresh/approaching/mitigated/deeply mitigated/repeatedly mitigated/reaction-holding/consumed or expired-candidate behavior. Terminal history is bounded and does not reactivate removed blocks.

### FVG / Engulfing

No second FVG or Engulfing engine was created. Their already-rich native lifecycle is exposed through a typed projection instead. FVG and Engulfing remain separate domains:

- FVG exposes fill depth, testing, reaction/failure and terminal lifecycle facts.
- Engulfing exposes retrace, continuation, weakening and invalidation facts.

The existing reaction-zone / confirmation projection remains intact.

### Volume / Participation

The native participation engine was already richer than the cross-domain projection, so the engine was not rewritten. A typed read model now exposes independent dimensions including:

- participation trend/stage,
- effort versus result,
- absorption stage,
- break participation,
- one-bar shock,
- controlled pullback/reaction,
- directional efficiency and native participation metrics.

Warmup or unavailable data remains distinct from neutral evidence.

### Volatility

The native volatility engine remains canonical. A typed environment projection now separates:

- range regime: balance, contraction, mature squeeze, expansion, normalization or shock,
- expansion character: band test/acceptance/trend, directional candidate/confirmation, mean reversion, false excursion or conflict,
- transition stage: early episode, canonical candidate, confirmation or weakening.

An early directional episode remains an early environment fact; it is not promoted to structural truth.

### Pattern / Compression

The native Pattern/Compression state machine remains unchanged. The final replay snapshot now exposes the already-computed active candidate facts needed by a typed behavior projection:

- native lifecycle phase,
- age and time since the candidate became knowable,
- progress and contraction,
- raw/selection/export quality,
- factual boundary touch counts,
- break and retest facts.

Touch counts are exposed as facts only; they are not renamed into unsupported concepts such as repeated defense.

## Deliberate non-changes after final audit

### Support / Resistance

No new S/R behavior engine was added. The existing zone-interaction model already describes approach, testing, defense, weakening, break attempt, pending/accepted traversal, reclaim, role-reversal test, consumption, invalidation and historical-reference behavior. Creating another layer would duplicate semantics and risk contradictory zone state.

### Market Structure

No additional behavior enum was added. Market Structure already acts as factual structural authority through internal/external scope state, protected and weak levels, confirmed events, validity, relevance, outcome and BOS maturity. Additional inferred behavior would add noise and blur authority boundaries.

### HAM

No HAM trajectory lifecycle was invented in this pass. HAM exposes family-level balance, activity, coverage and readiness, but does not currently own a native lifecycle comparable to Stabil, Liquidity or Pattern. A trajectory layer should only be added later if an explicit, causal native contract is designed and tested; it must not become a hidden super-signal.

## Cross-domain contract

The richer projections are additive read models. Existing context axes remain the established consumers until they are deliberately revised. The new facts are included in the knowledge boundary and are filtered by their own `available_at` timestamps.

The architecture must continue to preserve these rules:

1. closed-bar, causal facts only;
2. no future-tail dependency;
3. canonical domain outputs are not silently rewritten by behavior enrichment;
4. behavior facts do not become BUY/SELL authority;
5. FVG, Engulfing, OB, Liquidity, S/R, Stabil, Volume, Volatility, Pattern and Structure remain conceptually separate;
6. missing evidence is not automatically contradiction;
7. MTF context is role-aware, not majority voting;
8. enriched facts must remain cheap read-model derivations over already-computed native outputs.

## Final implementation shape

The completed path is intentionally asymmetric because the native domains have different maturity:

- **new native/additive behavior trackers where needed:** Stabil, Liquidity, Order Block;
- **typed lifecycle/read-model projection where native semantics were already rich:** FVG, Engulfing, Volume, Volatility, Pattern;
- **no duplicate behavior engine where the existing model is already sufficient:** Support/Resistance, Market Structure;
- **no speculative lifecycle without a native contract:** HAM.

This is the intended upstream state before a future thesis/action layer is designed. It does not itself define BUY, SELL, HOLD or trade readiness.
