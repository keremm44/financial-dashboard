# Market Analysis Workspace Contract

## Purpose

`MarketAnalysisWorkspace` is the execution and inspection boundary for the Streamlit product line. It is not a Decision Engine and has no trading authority.

The workspace coordinates the existing deterministic domains without changing their semantics:

```text
canonical Parquet cache
  -> one AnalysisInputSnapshot
  -> Three-Domain observer foundation
     -> Market Structure
     -> Support/Resistance + causal location
     -> Pattern/Compression + MTF story/pressure
  -> Ham evidence
  -> Volume Participation
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
- Yahoo `1d` production cache bars are session-close labelled (BIST close timestamp), so daily evidence is available at the stored timestamp and must not receive an additional one-day delay;
- an explicit `CausalBarClock` duration override takes precedence for tests or alternate providers.

No cross-domain relation may use a fact before that fact is causally available.

## One input snapshot per replay

A workspace loads every requested timeframe once into `AnalysisInputSnapshot`:

- the original cached frame is retained for source/tail diagnostics;
- `EngineInputBatch` contains the validated closed+complete replay rows;
- Observer, Ham, and Volume reuse the same prepared batch object for a timeframe;
- the cache fingerprint is captured before loading and verified again after the complete workspace replay;
- a cache mutation during loading or replay fails closed instead of returning a mixed-snapshot result.

## Authority and isolation

Three-Domain observer foundation is required. A foundation failure invalidates the workspace replay.

Ham and Volume are inspection/support domains. Their runtime failures are isolated and exposed as domain health errors so one optional domain does not hide otherwise valid foundation output.

Permanent rules remain:

- lower timeframe evidence cannot manufacture or promote a higher-timeframe structural fact;
- weakening is not recovery or reversal;
- Volume cannot create or replace BOS/CHoCH;
- Ham cannot rewrite Market Structure, S/R, action, blocker, or position state;
- open/incomplete candles do not advance confirmed engine state;
- the workspace produces no BUY/SELL/WAIT, entry, stop, target, recommendation, or global confidence.

## Streamlit presentation

The UI is a read-only inspection surface over workspace results.

- top-level metrics are descriptive; `Observer state` is not a trade decision;
- domain health is visible explicitly;
- detailed Ham/Volume histories remain diagnostics rather than top-level authority;
- chart overlays are composable domain layers;
- BOS/CHoCH is shown by default, while confluence and opposing-conflict overlays are opt-in to avoid visual overload.

## Extension rule for future engines

Liquidity, Auction/Volume Profile, Order Block, FVG/Engulfing, Volatility/Bands/Fibonacci, Stabil Trend, and later engines should be added as independent workspace domains rather than inserted into `ThreeDomainObserver`.

Each new domain must provide:

1. deterministic per-timeframe replay from the shared input snapshot where compatible;
2. open/incomplete-bar safety and future-tail invariance;
3. typed output with a documented authority boundary;
4. causal timestamp validation for cross-domain links;
5. no lower-to-higher timeframe promotion;
6. an isolated workspace domain result when it is non-foundational;
7. a focused view-model module;
8. an opt-in chart layer if visual overlays are needed;
9. regression tests before Streamlit exposure.
