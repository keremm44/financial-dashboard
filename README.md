# Financial Dashboard

Local-first OHLCV analysis and inspection workspace.

## Runtime modes

The installed `financial-dashboard-ui` entry point now launches the fast-start Streamlit surface.

- **Market (hızlı)** loads only the foundation observer required for chart, Market Structure, zones and MTF inspection.
- **Tam analiz** lazily builds the full workspace, including HAM, Volume, Stabil Support, Volatility, Liquidity, Order Block, FVG/Engulfing, Targeting and Cross-Domain Context.
- **Evidence** and **Diagnostics** also request the full workspace only when selected.
- Streamlit cache keys include the cache fingerprint, so UI reruns with unchanged Parquet files reuse analysis results.
- Parquet reads are process-cached by resolved path + file size + `mtime_ns`; rewritten files invalidate automatically and callers receive defensive DataFrame copies.

This split deliberately avoids computing every analysis domain before the user can see the primary market surface.

### Runtime profiling

Profile the real application workspace rather than pytest runtime:

```powershell
python scripts/runtime_profile.py .cache/live-smoke-15m ASELS
```

The output reports total workspace time and stage-level timings for input loading, observer, HAM, Volume, Stabil Support, Volatility, causal clipping, Liquidity, Order Block, FVG/Engulfing, Structure/Location, Targeting and Cross-Domain Context, ending with:

```text
RUNTIME_PROFILE_OK
```

Use these timings before changing replay algorithms. No-lookahead and causal availability boundaries take precedence over raw speed.

## Architecture boundaries

The project separates native engine authority from cross-domain context. Cross-domain context may describe structural thesis, reaction, reversal, objectives, participation, volatility, pattern/MTF context and scoped permission, but it is not a BUY/SELL or future-action layer.

Current prohibited outputs remain:

- BUY/SELL recommendations
- entry/exit instructions
- stop-loss / take-profit policy
- position sizing
- weighted cross-domain voting presented as authority
- future facts entering an earlier `as_of` boundary

## Data and cache

The default local UI cache is `.cache/live-smoke-15m` unless `FINANCIAL_DASHBOARD_CACHE` is configured. Analysis uses closed + complete candles after source-quality validation. Production BIST history may use a 15-minute base while canonical analysis timeframes remain the configured MTF set.

## Installation

Core development + UI dependencies:

```powershell
python -m pip install -e ".[dev,ui]"
```

Launch:

```powershell
financial-dashboard-ui
```

Tests:

```powershell
python -m pytest
```

## Performance policy

Optimization order is intentionally conservative:

1. remove duplicate I/O and immutable snapshot preparation;
2. avoid eager domain execution on UI startup;
3. measure real domain runtime;
4. reuse computations only when causal input prefixes are provably identical;
5. consider incremental engine-state replay only with dedicated equivalence, prefix-stability and no-lookahead regression coverage.

An incremental state engine is not silently substituted for canonical replay merely to reduce latency. Such a change requires engine-by-engine proof that the latest result, event ledger and causal availability remain identical to canonical replay.
