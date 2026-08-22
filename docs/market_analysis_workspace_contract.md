# Market Analysis Workspace Contract

## Purpose

`MarketAnalysisWorkspace` is the execution and inspection boundary for the Streamlit product line. It is not a Decision Engine and has no trading authority.

The workspace coordinates deterministic domains without changing their native semantics:

```text
canonical Parquet cache
  -> one AnalysisInputSnapshot
  -> Three-Domain observer foundation
     -> Market Structure
     -> Support/Resistance + causal location
     -> Pattern/Compression + MTF story/pressure
  -> Ham evidence
  -> Volume Participation
  -> Liquidity evidence
  -> Order Block evidence
  -> FVG / Engulfing evidence
  -> causal TargetEvidence adapters
  -> origin-event dedup + proximity clustering
  -> descriptive TargetingSnapshot
  -> domain view models
  -> Streamlit inspection UI
```

## Canonical identity

The default analysis timeframes are defined once in `analysis_config.py`:

```text
1d / 4h / 2h / 1h / 30m
```

Legacy public constants remain aliases for compatibility. Engine/cache symbols use one canonical trimmed uppercase identity. Provider-specific aliases stay at provider boundaries.

## Bar timestamp and causal availability

The timestamp contract is explicit:

- intraday production cache bars are left-labelled starts, so evidence becomes available at `timestamp + timeframe duration`;
- Yahoo `1d` production cache bars are session-close labelled, so daily evidence is available at the stored timestamp and must not receive an additional one-day delay;
- an explicit `CausalBarClock` duration override takes precedence for tests or alternate providers.

No cross-domain relation may use a fact before that fact is causally available.

Target evidence adds a second mandatory distinction:

```text
origin_time != confirmed_at != available_at
```

A Liquidity pivot can originate on an earlier bar but is not evidence until the right-hand pivot confirmation exists. An Order Block source candle can also precede the later imbalance confirmation. Cross-domain targeting must use `available_at`, never the historical origin timestamp, as its knowledge boundary.

## One input snapshot per replay

A workspace loads every requested timeframe once into `AnalysisInputSnapshot`:

- the original cached frame is retained for source/tail diagnostics;
- `EngineInputBatch` contains the validated closed+complete replay rows;
- Observer, Ham, Volume, Liquidity, Order Block and FVG/Engulfing replay from the same prepared snapshot where their timeframe contracts permit it;
- FVG/Engulfing keeps its native supported-timeframe contract (`1d`, `4h`, `2h`) rather than inventing lower-timeframe behavior;
- the cache fingerprint is captured before loading and verified again after the complete workspace replay;
- a cache mutation during loading or replay fails closed instead of returning a mixed-snapshot result.

## Target evidence contract

Existing engines remain authoritative for their own facts. Adapters translate native records into immutable `TargetEvidence`; they do not rewrite engine state.

Each evidence record preserves:

- source engine/type and evidence family;
- semantic roles such as `MAGNET`, `SUPPLY`, `DEMAND`, `IMBALANCE`, `REACTION`;
- interval geometry (`low`, `high`) and optional anchor price;
- source/origin identity;
- `origin_time`, `confirmed_at`, and causal `available_at`;
- source lifecycle state and target eligibility;
- `origin_event_id` for correlated-evidence deduplication;
- timeframe and optional Liquidity internal/external classification.

Liquidity remains the target anchor. A cluster with Liquidity is a `LIQUIDITY_TARGET`; a nearby FVG/Order Block/Engulfing-only group is a `TECHNICAL_ZONE`, not silently promoted into Liquidity.

## Proximity and clustering

Clustering is geometry-first, but current-price proximity does not decide whether evidence facts exist.

```text
native facts
  -> causal eligibility
  -> origin-event dedup
  -> evidence-to-evidence proximity
  -> max-span constrained clusters
  -> cluster geometry
  -> current-price distance
  -> nearest / highest-confluence views
```

Important rules:

- evidence-to-evidence proximity and cluster-to-current-price distance are separate concepts;
- cluster distance is measured from the nearest envelope edge, not an arbitrary mean;
- ATR normalizes proximity, but thresholds are configuration, not claimed market truth;
- max-span constraints prevent single-linkage/chaining from merging a wide ladder into one cluster;
- `raw_source_count` is never treated as independent evidence count;
- FVG, Order Block and Engulfing from the same origin move may share one `origin_event_id`;
- typed quality (`SINGLE`, `SUPPORTED`, `MULTI_EVIDENCE`, `DENSE`) is based on independent origins; there is no fixed Liquidity/FVG/OB point score;
- nearest target and highest-confluence target remain separate outputs.

A target snapshot may only include evidence whose `available_at <= snapshot.as_of`. Later confirmation cannot rewrite an earlier targeting snapshot.

## Market Structure enrichment

Liquidity is calculated independently from Market Structure. The targeting layer may classify a Liquidity pool as `INTERNAL`, `EXTERNAL`, or `UNCLASSIFIED` only by comparing it with authoritative structure roles on the same timeframe.

Lower-timeframe Liquidity cannot manufacture or promote a higher-timeframe structural fact.

## Authority and isolation

Three-Domain observer foundation is required. A foundation failure invalidates the workspace replay.

Ham, Volume, Liquidity, Order Block and FVG/Engulfing are independent support/inspection domains. Their runtime failures are isolated. Targeting can be built from the causal domains that remain available; domain-health output exposes partial coverage instead of hiding failures.

Permanent rules remain:

- lower timeframe evidence cannot manufacture or promote a higher-timeframe structural fact;
- weakening is not recovery or reversal;
- Volume cannot create or replace BOS/CHoCH;
- Ham cannot rewrite Market Structure or S/R;
- FVG, Order Block and Engulfing cannot rewrite Liquidity facts;
- open/incomplete candles do not advance confirmed engine state;
- the workspace produces no BUY/SELL/WAIT, entry, stop, take-profit instruction, recommendation, or global confidence.

`TargetingSnapshot` uses the word target only in the descriptive market-structure sense: a nearby active Liquidity/technical objective. It is not a take-profit instruction.

## Streamlit presentation

The UI is a read-only inspection surface over workspace results.

- top-level observer metrics remain descriptive;
- domain health includes Liquidity, Order Block, FVG/Engulfing and Targeting;
- target view models expose nearest and highest-confluence outputs separately;
- diagnostics can expose origin/confirmation/availability timestamps and dedup identities;
- detailed Ham/Volume histories remain diagnostics rather than top-level authority;
- chart overlays remain composable and opt-in to avoid visual overload.

## Validation requirements

Targeting foundations must preserve the existing replay guarantees and add focused controls:

1. pivot/source origin cannot become evidence before confirmation;
2. future `available_at` evidence is excluded from the current target snapshot;
3. replay prefix results are not rewritten by a future tail;
4. same-origin FVG/Order Block/Engulfing are not counted as three independent events;
5. max-span prevents chaining clusters;
6. cluster distance uses the nearest envelope edge;
7. technical zones without Liquidity are not promoted to Liquidity targets;
8. internal/external Liquidity remain separate outputs;
9. cache input remains one shared snapshot;
10. future evaluation should compare real clusters against distance-matched control levels, plus ablation/walk-forward tests, before introducing calibrated numerical weights.

## Extension rule

Auction/Volume Profile, Volatility/Bands/Fibonacci, Stabil Trend and later engines should remain independent workspace domains. New target evidence sources should join through adapters rather than being inserted into Liquidity, Market Structure, or `ThreeDomainObserver`.
