# Financial Dashboard

Local-first, deterministic OHLCV analysis foundation with independent multi-timeframe Market Structure, typed Support/Resistance location context, Pattern/Compression evidence, and a Streamlit inspection UI.

## What is implemented

- Canonical OHLCV cache and data-quality gates
- Closed + complete candle filtering (`is_closed=True`, `is_complete=True`)
- Independent `1d`, `4h`, `2h`, `1h`, and `30m` replay
- Ham Indicator Dashboard v2.3.7 end-to-end support layers:
  - all ten Tur-1 components, exact neutral PRICE/MOMENTUM/TIMING/FLOW families, matching timeframe profiles, and full closed-bar history
  - pure post-core, symmetric confidence support bounded to `ham_delta ∈ [-5, +5]`
  - deterministic fixed-facts narration payload (no provider/Groq integration)
  - Streamlit MTF/detail/history inspection with recent 100 rows by default and explicit all-history mode
- Volume Participation completed evidence layers:
  - independent `1d/4h/2h/1h/30m` replay with full immutable confirmed-bar history
  - explicit warmup, low-participation, unavailable-volume, limited-data, and incomplete-tail boundaries
  - causal same-timeframe BOS/CHoCH links across `PRE_EVENT → AT_EVENT → FOLLOW_THROUGH`
  - bounded lower-timeframe inflow and direct i/eCHoCH/i/eBOS progression without higher-timeframe promotion
  - strict confirmed-opposition blocking risk plus typed recovery/supersession/fake-reclaim release routes
  - one-bar shock/fake/absorption/follow-through/reclaim histories and shared-source volume deduplication
  - Streamlit MTF/link/risk/history diagnostics with the last 100 bars by default and optional all-history mode
- Persistent internal/external BOS and CHoCH history, with typed BOS maturity (`INITIAL_STRUCTURE`, `TRANSITION_CONFIRMATION`, `CONTINUATION`)
- Typed Support/Resistance zones, lifecycle, MTF confluence, and opposing-zone conflicts
- Causal event-location links with explicit no-match outcomes
- Action-free three-domain observation:
  - broad MTF pressure and recovery evidence
  - Market Structure progression
  - Support/Resistance location context
- Streamlit v0.1 local inspection/debug interface with Plotly charts

The three analytical domains run continuously and in parallel. Higher-timeframe context does not disable lower-timeframe calculation or evidence retention. Weakening and recovery remain distinct states. Ham follows the same isolation rule and remains supporting evidence: it can adjust only an already-authoritative core confidence, never direction/action/status, blockers, Market Structure, or S/R. See the Ham [Round 1 evidence contract](docs/ham_evidence_round1_contract.md) and [Round 2 support contract](docs/ham_evidence_round2_contract.md). Volume remains neutral participation evidence and cannot create or replace BOS/CHoCH; see the [Volume Round 1 evidence contract](docs/volume_evidence_round1_contract.md) and [Round 2 MTF/risk/UI contract](docs/volume_evidence_round2_contract.md).

## Local installation

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[ui]"
```

For development and tests:

```bash
python -m pip install -e ".[dev,ui]"
python -m pytest
```

For BIST/tvDatafeed refresh scripts, install the optional live-data dependencies in the same virtual environment used to run the script:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[live]"
```

## Parquet cache

The UI reads the existing local cache only; it does not call a market-data provider. By default it looks under `./data/cache`. A different directory can be selected in the sidebar or configured before launch:

```bash
export FINANCIAL_DASHBOARD_CACHE="/absolute/path/to/cache"
```

Cache files use the store's `SYMBOL__timeframe.parquet` convention:

```text
/path/to/cache/
├── THYAO__1d.parquet
├── THYAO__4h.parquet
├── THYAO__2h.parquet
├── THYAO__1h.parquet
└── THYAO__30m.parquet
```

Required market columns are `timestamp`, `open`, `high`, `low`, `close`, and `volume`. Canonical cache files also preserve `symbol`, `timeframe`, `source`, `is_closed`, and `is_complete`. Missing or invalid foundation timeframes remain visible in the UI as unavailable; they are not silently treated as neutral or switched off.

### BIST cache backfill

`scripts/live_smoke.py` remains right-edge incremental by default. Increasing `--days` alone on an existing cache does **not** extend its left edge. Use `--backfill` to request the complete available provider window and merge older rows into the existing cache without deleting it:

```powershell
.\.venv\Scripts\python.exe .\scripts\live_smoke.py `
  --symbol ASELS `
  --days 90 `
  --max-bars 5000 `
  --cache-root .cache\live-smoke `
  --backfill
```

Add `--volume-round2` to the same command to run the five-timeframe Volume authority, risk, shock, propagation, and shared-source dedup acceptance after refresh. To validate an already populated five-timeframe cache without making another provider request:

```powershell
.\.venv\Scripts\python.exe .\scripts\live_smoke.py `
  --symbol ASELS `
  --cache-root .cache\live-smoke `
  --cache-only `
  --volume-round2
```

The actual history can still be shorter than `--days`: tvDatafeed first returns at most the latest `--max-bars` bars and then the adapter applies the requested date window. The UI therefore reports observed usable first/last candles, closed+complete counts, first external structure event, typed BOS maturity, and left-boundary status. A requested bar count is never treated as proof that pre-cache structure was observed.

## Run Streamlit

Use the installed launcher:

```bash
financial-dashboard-ui
```

Or run the app module directly:

```bash
python -m streamlit run src/financial_dashboard/ui/app.py \
  --server.address=0.0.0.0 \
  --server.headless=true
```

Open `http://localhost:8501` in a browser. Use **Cache'i yeniden tara** after files are added or replaced; replay caching is keyed by the cache path, symbol, engine profile, and each Parquet file's size/modification timestamp.

## Streamlit v0.1 scope

The interface provides:

- combined descriptive observation cards and facts
- the five-timeframe MTF matrix
- internal/external BOS and CHoCH event history
- candlesticks with event, zone, confluence, and conflict overlays
- typed zone lifecycle and confluence/conflict tables
- causal event-location outcomes and links
- cache freshness, usable replay range, row filtering, source-quality, and structural left-boundary diagnostics
- Ham five-timeframe quality/family matrix, all ten latest indicator details, and confirmed history (recent 100 by default; all rows on explicit request)
- Volume five-timeframe participation matrix, causal Structure links, opposition/shock lifecycles, direct structural progression, dedup diagnostics, and confirmed history (recent 100 by default; all rows on explicit request)

It intentionally does **not** produce buy/sell actions, recommendations, predictions, stops, targets, provider-refresh automation, Groq narration, or a global Decision Engine. Since this UI has no authoritative global core direction/confidence, it also does not fabricate one merely to display the Ham `±5` adapter.
