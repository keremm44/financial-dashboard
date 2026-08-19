# Financial Dashboard

Local-first financial analysis platform built around canonical OHLCV data, deterministic timeframe resampling, data-quality gates, stateful analysis engines, and TradingView parity tests.

## Current phase

Phase 1 establishes the data contract and engine foundations before any indicator/decision logic or Streamlit UI is added.

Planned core sequence:

1. Canonical OHLCV schema
2. Session-aware resampling
3. Data-quality validation
4. Engine contracts
5. Market Structure port + TradingView parity tests
6. Remaining analysis engines
7. Evidence-family and Decision Engine
8. Streamlit dashboard

The main decision path must use confirmed candles. Live/incomplete candles are treated as preview data and must not silently enter confirmed analysis.
