# Financial Dashboard

Local-first, deterministic OHLCV analysis workspace with independent multi-timeframe Market Structure, typed Support/Resistance location context, Pattern/Compression evidence, Ham and Volume support domains, and a Streamlit inspection UI.

## What is implemented

- Canonical OHLCV cache and data-quality gates
- Closed + complete candle filtering (`is_closed=True`, `is_complete=True`)
- One canonical analysis-timeframe contract: `1d`, `4h`, `2h`, `1h`, `30m`
- One canonical trimmed-uppercase engine/cache symbol identity
- One shared `AnalysisInputSnapshot` per workspace replay:
  - each requested Parquet timeframe is loaded once
  - the same prepared closed+complete batch is reused across Observer, Ham, and Volume
  - cache fingerprint changes during loading/replay fail closed
- Causal timestamp discipline:
  - intraday cache bars are left-labelled and become available after their bar duration
  - Yahoo/BIST `1d` cache bars are session-close labelled and are available at the stored close timestamp
  - explicit `CausalBarClock` overrides remain supported
- Persistent internal/external BOS and CHoCH history, with typed BOS maturity (`INITIAL_STRUCTURE`, `TRANSITION_CONFIRMATION`, `CONTINUATION`)
- Typed Support/Resistance zones, lifecycle, MTF confluence, opposing-zone conflicts, and causal event-location links
- Frozen Three-Domain observer foundation:
  - MTF pressure/recovery context
  - Market Structure progression
  - Support/Resistance location context
  - Pattern/Compression remains part of the existing MTF Story input contract
- Ham Indicator Dashboard v2.3.7 support layers:
  - all ten Tur-1 components and PRICE/MOMENTUM/TIMING/FLOW families
  - matching timeframe profiles and full closed-bar history
  - bounded post-core confidence support
  - deterministic fixed-facts narration payload
  - Streamlit MTF/detail/history inspection
- Volume Participation completed evidence layers:
  - independent five-timeframe history
  - explicit warmup, low-participation, unavailable-volume, limited-data, and incomplete-tail boundaries
  - causal same-timeframe BOS/CHoCH links across `PRE_EVENT → AT_EVENT → FOLLOW_THROUGH`
  - bounded lower-timeframe inflow without higher-timeframe promotion
  - confirmed-opposition blocking risk plus typed recovery/supersession/fake-reclaim release routes
  - shock/fake/absorption/follow-through/reclaim histories
  - shared-source volume deduplication
  - Streamlit MTF/link/risk/history diagnostics
- `MarketAnalysisWorkspace` execution coordinator:
  - Three-Domain foundation is required
  - Ham and Volume failures are isolated and surfaced as domain health
  - no domain is allowed to manufacture another domain's authority
- Modular UI boundaries:
  - domain-specific view-model modules with a backwards-compatible `ui.view_models` facade
  - composable chart layers for Structure and location overlays
  - confluence/conflict overlays opt-in by default

The full workspace boundary is documented in [Market Analysis Workspace Contract](docs/market_analysis_workspace_contract.md).

The analytical domains run continuously and independently. Higher-timeframe context does not disable lower-timeframe calculation or evidence retention. Lower-timeframe evidence cannot create a higher-timeframe structural fact. Weakening and recovery remain distinct states. Ham remains supporting evidence and cannot rewrite Market Structure, S/R, blockers, actions, or position state. Volume remains participation evidence and cannot create or replace BOS/CHoCH.

## Local installation

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[ui]"
```

For development and the complete repository test suite:

```bash
python -m pip install -e ".[dev,ui]"
python -m pytest
```

GitHub Actions uses the same `.[dev,ui]` dependency set so Streamlit/Plotly tests are part of the normal CI gate.

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

Required market columns are `timestamp`, `open`, `high`, `low`, `close`, and `volume`. Canonical cache files also preserve `symbol`, `timeframe`, `source`, `is_closed`, and `is_complete`. Missing or invalid foundation timeframes remain visible in the UI as unavailable; they are never silently treated as neutral.

### Timestamp contract

The cache label must match its provider/resampling contract:

- intraday TradingView-derived bars use left-labelled starts;
- Yahoo daily bars are normalized to the BIST session-close timestamp (`18:10 Europe/Istanbul` in the current daily provider contract);
- causal cross-domain code converts those labels into evidence availability and does not add another day to an already close-labelled daily bar.

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

Add `--volume-round2` to run the five-timeframe Volume authority/risk/shock/propagation/dedup acceptance after refresh. To validate an already populated five-timeframe cache without another provider request:

```powershell
.\.venv\Scripts\python.exe .\scripts\live_smoke.py `
  --symbol ASELS `
  --cache-root .cache\live-smoke `
  --cache-only `
  --volume-round2
```

The actual history can still be shorter than `--days`: tvDatafeed first returns at most the latest `--max-bars` bars and then the adapter applies the requested date window. The UI reports observed usable first/last candles, closed+complete counts, first external structure event, BOS maturity, and left-boundary status. A requested bar count is never treated as proof that pre-cache structure was observed.

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

Open `http://localhost:8501` in a browser. Use **Cache'i yeniden tara** after files are added or replaced. Streamlit replay caching is keyed by cache path, symbol, profile, requested timeframes, and Parquet fingerprint metadata.

## Streamlit scope

The interface is the inspection surface for `MarketAnalysisWorkspace` and provides:

- descriptive top-level metrics, including `Observer state` rather than a global decision label
- explicit domain-health status
- the five-timeframe MTF matrix
- internal/external BOS and CHoCH event history
- candlesticks with Structure and S/R overlays
- opt-in confluence and opposing-conflict overlays
- typed zone lifecycle, confluence/conflict, causal outcome, and event-zone link tables
- cache freshness, usable replay range, source quality, and structural left-boundary diagnostics
- Ham five-timeframe summary plus latest-indicator and confirmed-history diagnostics
- Volume five-timeframe summary plus causal Structure links, risk transitions, shock lifecycle, direct progression, history, and dedup diagnostics

It intentionally does **not** produce buy/sell actions, recommendations, predictions, stops, targets, provider-refresh automation, or a global Decision Engine. `Observer state` is descriptive workspace context, not trading authority.
