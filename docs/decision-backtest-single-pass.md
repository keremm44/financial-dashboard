# Single-Pass Historical Decision Replay

## Purpose

Historical BUY/SELL evaluation must not rebuild the market workspace once per decision bar.
The canonical runtime contract is:

```text
closed OHLCV history
  -> each native engine advances forward once over its own timeframe history
  -> causal states are frozen only at required 1h decision cutoffs
  -> DecisionEngine evaluates the frozen states bar by bar
  -> hindsight audit runs only after decisions are frozen
```

`--max-bars` is a smoke/debug selector for decision cutoffs. It does **not** shorten or multiply native replay history.

## Runtime invariants

- Native engine update count is determined by input history, not decision-point count.
- Increasing decision points from 3 to the full 1h history must not rerun native engines from the beginning.
- Closed/complete bars only.
- Every captured fact remains bounded by its `available_at` time.
- Future bars are allowed only in the separate hindsight-audit stage.
- Historical snapshots must remain prefix-equivalent to the former causal reference implementation.

## Current implementation

`src/financial_dashboard/decision/history_single_pass.py`

Structure, S/R, Pattern, Liquidity, Order Block and FVG/Engulfing are advanced together in one forward capture pass per timeframe. HAM, Volume and Volatility use their existing history-producing MTF runners once. Stabil observations are built once and only requested causal daily states are materialized.

The production backtest entry point is:

```powershell
python scripts/decision_backtest.py storage/cache ASELS --horizon st
```

For a smoke check only:

```powershell
python scripts/decision_backtest.py storage/cache ASELS --horizon st --max-bars 3
```

Runtime output separates:

- `NATIVE_CAPTURE_PASS_SECONDS`
- `HAM_REPLAY_SECONDS`
- `VOLUME_REPLAY_SECONDS`
- `VOLATILITY_REPLAY_SECONDS`
- `STABIL_REPLAY_SECONDS`
- `NATIVE_REPLAY_SECONDS`
- `SNAPSHOT_ASSEMBLY_SECONDS`
- `DECISION_LAYER_SECONDS`
- `HINDSIGHT_AUDIT_SECONDS`

The decision layer must remain cheap relative to native replay.
