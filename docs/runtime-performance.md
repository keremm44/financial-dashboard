# Runtime Performance

The installed `financial-dashboard-ui` entry point uses the fast-start Streamlit surface.

## Completed optimization pass

- `Market (hızlı)` loads only the foundation observer needed for chart, Market Structure, zones and MTF inspection.
- `Tam analiz`, `Evidence` and `Diagnostics` build the full workspace only when selected.
- Streamlit analysis caches are keyed by cache fingerprint, symbol, profile and requested timeframes.
- Parquet reads use a process-local LRU cache keyed by resolved path, file size and `mtime_ns`; rewritten files invalidate automatically and callers receive defensive DataFrame copies.
- Causal clipping reuses an immutable timeframe snapshot when every bar is already available at the cutoff instead of copying and validating the same frame again.
- Engine-input preparation now skips redundant full-frame copies, boolean filtering, stable sorting and index rebuilding when the cached frame is already closed, complete, timestamp-monotonic and canonically indexed. Non-canonical inputs still follow the same normalization path.
- Runtime profiling measures the actual application workspace, not pytest execution.
- A standalone Windows-safe runtime matrix profiler isolates expensive domains with a per-case timeout; it avoids the `multiprocessing spawn` failure caused by piping Python through `<stdin>` on Windows.

The canonical full workspace remains available unchanged for Cross-Domain Context and evidence/diagnostics inspection. The optimization changes when expensive domains are invoked and removes redundant data preparation; it does not change native domain authority.

## Runtime profiling

Measure the real application workspace:

```powershell
python scripts/runtime_profile.py .cache/live-smoke-15m ASELS
```

Output includes total runtime plus stage timings for input loading, observer, HAM, Volume, Stabil Support, Volatility, causal clipping, Liquidity, Order Block, FVG/Engulfing, Structure/Location, Targeting, Semantic Targeting and Cross-Domain Context. A successful run ends with:

```text
RUNTIME_PROFILE_OK
```

For isolated domain timing and hard timeout detection, run the standalone matrix script directly from the repository instead of piping code through PowerShell stdin:

```powershell
python scripts/runtime_matrix.py .cache/live-smoke-15m ASELS --timeout 20
```

Optional narrowing examples:

```powershell
python scripts/runtime_matrix.py .cache/live-smoke-15m ASELS --timeframes 1d 4h --domains structure_location pattern volume
python scripts/runtime_matrix.py .cache/live-smoke-15m ASELS --timeframes 2h 1h --domains liquidity order_block fvg_engulfing --timeout 30
```

The matrix ends with `RUNTIME_MATRIX_OK` when no domain errors or timeouts occurred, or `RUNTIME_MATRIX_COMPLETED_WITH_FAILURES` while preserving the per-domain result table.

## Structure replay boundary

The profiler intentionally exposes `structure_location` call count and time. Full observer replay and target-bounded replay cannot be merged blindly because `target_as_of` may causally remove the latest bars from one or more timeframes. Causal clipping now preserves object identity for unchanged timeframe snapshots, which creates the safe prerequisite for future per-timeframe replay reuse without weakening no-lookahead behavior.

## Incremental replay boundary

This pass does not replace canonical engine replay with a new incremental state machine. Doing so without engine-by-engine equivalence tests could alter event ledgers, lifecycle transitions, warm-up behavior or causal availability. The safe runtime win is therefore delivered by lazy UI execution, shared immutable inputs, I/O caching and no-op normalization elimination while canonical replay semantics remain intact.

## Work that still requires real local data

Repository-side implementation and CI can complete the deterministic optimization work, but the final performance ranking cannot be fabricated in GitHub Actions because the private/local `.cache/live-smoke-15m` dataset is not available there. The remaining local-only work is measurement, not implementation:

1. Run `scripts/runtime_profile.py` against the real cache.
2. Run `scripts/runtime_matrix.py` against the same symbol/cache.
3. Use the measured slowest domains to decide whether a deeper algorithmic or incremental rewrite is justified.

Any deeper rewrite must be evidence-driven by those timings; otherwise it risks adding complexity to a domain that is not the actual bottleneck.

## Optimization policy

1. Remove duplicate I/O and immutable snapshot preparation.
2. Avoid eager domain execution on UI startup.
3. Skip normalization work that is provably a no-op.
4. Measure actual domain runtime.
5. Reuse a calculation only when its causal input prefix is provably identical.
6. Introduce incremental engine state only with engine-by-engine equivalence, prefix-stability and no-lookahead regression coverage.

Causal availability and no-lookahead semantics remain hard boundaries.
