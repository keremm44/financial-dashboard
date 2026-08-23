# Runtime Performance

The installed `financial-dashboard-ui` entry point uses the fast-start Streamlit surface.

## Fast-start behavior

- `Market (hızlı)` loads only the foundation observer needed for chart, Market Structure, zones and MTF inspection.
- `Tam analiz`, `Evidence` and `Diagnostics` build the full workspace only when selected.
- Streamlit analysis caches are keyed by cache fingerprint, symbol, profile and requested timeframes.
- Parquet reads use a process-local LRU cache keyed by resolved path, file size and `mtime_ns`; rewritten files invalidate automatically and callers receive defensive DataFrame copies.
- Causal clipping reuses an immutable timeframe snapshot when every bar is already available at the cutoff instead of copying and validating the same frame again.

## Runtime profiling

Measure the real application workspace rather than pytest runtime:

```powershell
python scripts/runtime_profile.py .cache/live-smoke-15m ASELS
```

Output includes total runtime plus stage timings for input loading, observer, HAM, Volume, Stabil Support, Volatility, causal clipping, Liquidity, Order Block, FVG/Engulfing, Structure/Location, Targeting, Semantic Targeting and Cross-Domain Context. A successful run ends with:

```text
RUNTIME_PROFILE_OK
```

## Optimization policy

1. Remove duplicate I/O and immutable snapshot preparation.
2. Avoid eager domain execution on UI startup.
3. Measure actual domain runtime.
4. Reuse a calculation only when its causal input prefix is provably identical.
5. Introduce incremental engine state only with engine-by-engine equivalence, prefix-stability and no-lookahead regression coverage.

The project does not substitute an incremental or truncated algorithm for canonical replay solely to reduce latency. Causal availability and no-lookahead semantics remain hard boundaries.
