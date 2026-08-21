# Financial Dashboard

Local-first, deterministic OHLCV analysis foundation with independent multi-timeframe Market Structure, typed Support/Resistance location context, Pattern/Compression evidence, and a Streamlit inspection UI.

## What is implemented

- Canonical OHLCV cache and data-quality gates
- Closed + complete candle filtering (`is_closed=True`, `is_complete=True`)
- Independent `1d`, `4h`, `2h`, `1h`, and `30m` replay
- Persistent internal/external BOS and CHoCH history
- Typed Support/Resistance zones, lifecycle, MTF confluence, and opposing-zone conflicts
- Causal event-location links with explicit no-match outcomes
- Action-free three-domain observation:
  - broad MTF pressure and recovery evidence
  - Market Structure progression
  - Support/Resistance location context
- Streamlit v0.1 local inspection/debug interface with Plotly charts

The three analytical domains run continuously and in parallel. Higher-timeframe context does not disable lower-timeframe calculation or evidence retention. Weakening and recovery remain distinct states.

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
- cache freshness, row filtering, and source-quality diagnostics

It intentionally does **not** produce buy/sell actions, recommendations, predictions, stops, targets, provider-refresh automation, Groq narration, or a global Decision Engine.
