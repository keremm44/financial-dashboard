# Historical Decision Audit Engine

## Purpose

The audit engine grades a causal BUY/SELL decision stream after the fact without feeding future information back into the decision engine.

It deliberately has two boundaries:

1. **Causal replay** produces immutable decisions from information available at each closed bar.
2. **Hindsight audit** may then inspect surrounding/future bars to measure how early, late, profitable, unstable or incomplete those decisions were.

Hindsight fields are evaluation targets only. They must never become inputs to the decision that they grade.

## Decision event contract

The future BUY/SELL engine should emit one event per evaluated closed bar (or at minimum every state transition):

```json
{
  "timestamp": "2026-08-25T13:00:00+03:00",
  "action": "WAIT",
  "side": "LONG",
  "price": 182.4,
  "atr": 4.9,
  "reasons": ["LT_LONG_INTACT"],
  "blockers": [],
  "waiting_for": ["30M_BULLISH_TRIGGER"],
  "source_lineage": ["STRUCT:1D:..."],
  "snapshot": {
    "lt": {"direction": "LONG", "thesis_state": "INTACT"},
    "st": {"direction": "SHORT", "relation_to_lt": "COUNTER_REACTION"},
    "timing": "EARLY",
    "opportunity": "AMPLE"
  }
}
```

`snapshot` is intentionally opaque to the audit engine. It is persisted unchanged so every bad historical trade can be explained with the exact domain/decision state that existed when it was emitted.

## Metrics produced

### Trade performance

- completed trade count
- wins / losses / breakeven
- win rate
- average and median return
- compounded return across completed trades
- average winner / loser
- best / worst trade
- MFE (maximum favorable excursion)
- MAE (maximum adverse excursion)
- favorable-move capture ratio

### Entry timing quality

The audit locates a hindsight local low around each BUY using configurable lookback/lookahead windows.

It reports:

- local-bottom miss %
- local-bottom miss in ATR
- bars early when the benchmark low formed after BUY
- bars late when the benchmark low formed before BUY
- additional downside after BUY %
- additional downside after BUY in ATR

This allows analysis such as: "the BUY was causal and valid, but price made another 1.1 ATR low four bars later."

### Exit timing quality

The audit locates a hindsight local high around each SELL and reports:

- local-peak miss %
- local-peak miss in ATR
- bars early when the benchmark high formed after SELL
- bars late when the benchmark high formed before SELL
- upside missed after SELL
- profit giveback from the best price reached while the position was open

This separates "sold too early" from "sold too late after giving back open profit."

### Decision stability

- counts by WAIT / READY / BUY / SELL / HOLD / NO_TRADE
- READY -> WAIT reversals
- WAIT episode count and average duration
- READY episode count and average duration
- average READY -> BUY delay

These metrics are intended to expose state churn and over-sensitive readiness rules.

### Missed opportunities

Missed-opportunity detection is deliberately opt-in because a "meaningful move" requires a calibration definition.

When `meaningful_move_atr` is supplied, the hindsight layer detects local lows followed by a configured forward move and records whether a BUY occurred within the configured capture window.

The threshold belongs to the audit/calibration layer, not the live decision architecture.

## One-command usage

```powershell
python scripts/decision_audit.py storage/cache ASELS decisions.json --timeframe 30m
```

Optional JSON report:

```powershell
python scripts/decision_audit.py storage/cache ASELS decisions.json --timeframe 30m --json-out storage/audits/ASELS.json
```

Optional missed-opportunity grading:

```powershell
python scripts/decision_audit.py storage/cache ASELS decisions.json --timeframe 30m --meaningful-move-atr 2.0
```

The `2.0` value above is an example audit parameter, not a recommended market threshold. It must be calibrated later.

## Integration rule for the future decision engine

The decision engine should not import hindsight-audit results. Integration is one-way:

```text
causal closed-bar replay
        -> DecisionEvent stream
        -> Historical Decision Audit
        -> reports / calibration datasets
```

Calibration may propose future parameter changes through an explicit research process, but an individual hindsight result must never mutate a historical decision in the same replay.
