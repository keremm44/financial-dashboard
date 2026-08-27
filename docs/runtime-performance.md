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
- Historical decision replay now uses the canonical append-only causal timeline by default. The same reducer/watermark model is reusable by historical and live/catch-up orchestration.
- Structure / S-R / Pattern / Liquidity / Order Block / FVG native state is advanced exactly once per closed bar and frozen by causal watermark.
- Frozen native read-models are cached per `(timeframe, watermark)` so unchanged higher-timeframe state is not rebuilt on every lower-timeframe decision cutoff.
- Support/resistance evidence is reused by exact timeframe watermark.
- Volume event-link selection uses an indexed cutoff path when canonical ordering is available, with a semantics-preserving fallback for non-canonical input.
- Target origin-event de-duplication has an indexed implementation that preserves the canonical deterministic greedy grouping order while avoiding unrelated full-history scans.
- The canonical backtest no longer performs an avoidable second origin-event de-duplication pass before targeting.

The canonical full workspace remains available unchanged for Cross-Domain Context and evidence/diagnostics inspection. The optimization changes execution/reuse boundaries; it does not change native domain authority, lifecycle semantics, or no-lookahead rules.

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

## Historical/live causal timeline

The canonical historical runner is `HistoricalDecisionInputReplayRunner`. It is backed by the shared append-only causal reducer rather than the old capture-per-cutoff orchestration.

The reducer enforces:

1. closed bars are consumed in deterministic `available_at` order;
2. each timeframe watermark is contiguous;
3. a frozen decision state references exact causal watermarks only;
4. repeated freezes at an unchanged native watermark reuse the immutable native read-model;
5. live/catch-up can append new bars to the same native runtime instead of replaying the full prior history.

The retired `SinglePassHistoricalDecisionInputReplayRunner` remains available only as an explicit equivalence/debug fallback. `scripts/decision_backtest.py` uses the canonical causal timeline by default; `--legacy-single-pass` opts into the old path. The hidden compatibility flag `--incremental-state-timeline` remains accepted so older command lines do not break, but it no longer changes behavior.

Use the equivalence tool when validating a cache or engine change:

```powershell
python scripts/compare_incremental_decision_replay.py .cache/live-smoke-15m ASELS --max-bars 20
```

A matching run ends with:

```text
INCREMENTAL_DECISION_REPLAY_EQUIVALENT
```

For a full lifecycle/audit run, omit `--max-bars`:

```powershell
python scripts/decision_backtest.py .cache/live-smoke-15m ASELS --lifecycle-readiness-proxy --json-out artifacts/asels_lifecycle_audit.json --timeline-json-out artifacts/asels_lifecycle_timeline.json
```

## Structure replay boundary

The profiler intentionally exposes `structure_location` call count and time. Full observer replay and target-bounded replay cannot be merged blindly because `target_as_of` may causally remove the latest bars from one or more timeframes. Watermark-keyed immutable native state is the safe reuse boundary; state is reused only when the causal input prefix is exactly identical.

## What remains measurement-only

Repository-side deterministic implementation is complete for this migration. GitHub Actions cannot reproduce the final ASELS performance ranking because the private/local cache is unavailable there. The remaining local work is measurement, not additional speculative rewriting:

1. Run `scripts/compare_incremental_decision_replay.py` on the real cache to verify golden equivalence against the retired path.
2. Run `scripts/runtime_profile.py` and `scripts/runtime_matrix.py` to measure the remaining dominant runtime.
3. Run the full lifecycle backtest/timeline to inspect BUY/HOLD/EXIT_WATCH/EXIT_READY/SELL behavior.
4. Only after those measurements, change a deeper domain algorithm or exit policy if the evidence shows a real systemic issue.

## Optimization policy

1. Remove duplicate I/O and immutable snapshot preparation.
2. Avoid eager domain execution on UI startup.
3. Skip normalization work that is provably a no-op.
4. Measure actual domain runtime.
5. Reuse a calculation only when its causal input prefix is provably identical.
6. Keep one append-only causal state model for historical and live/catch-up execution.
7. Preserve an explicit legacy/equivalence path until real-cache golden comparison has been completed.
8. Do not tune decision or exit semantics from one symbol's hindsight P/L.

Causal availability and no-lookahead semantics remain hard boundaries.
