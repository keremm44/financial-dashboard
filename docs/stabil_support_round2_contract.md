# Stabil Daily Support Lifecycle — Round 2 Integration Contract

## Status

Round 2 promotes the Round 1 daily structural-support lifecycle into an independently inspectable workspace/replay domain. It does **not** restore the legacy all-in-one Stabil Trend authority.

## Preserved source

The observed green stepline remains the existing Stabil confirmed daily pivot-low support source. Round 2 does not replace it with Market Structure or Support/Resistance levels. This preserves comparability with the manual chart observations that motivated the redesign.

## Workspace contract

`MarketAnalysisWorkspace` exposes `stabil_support` as an isolated `WorkspaceDomainResult`.

- It consumes the already prepared shared `1d` `AnalysisInputSnapshot` batch.
- It performs no additional cache load when the workspace already loaded `1d`.
- Failure is isolated from Ham, Volume, Liquidity, Order Block, FVG/Engulfing and Targeting.
- The workspace property `stabil_support_result` exposes the typed daily replay result when READY.
- Missing `1d` is an explicit domain error, not a neutral support opinion.

## Causal replay contract

`StabilSupportReplayRunner` produces the latest typed lifecycle snapshot from the shared daily batch.

`StabilSupportHistoricalReplayRunner` produces prefix snapshots for inspection:

1. only closed + complete daily candles are present in the prepared batch;
2. support pivots are visible only after their existing confirmation/availability boundary;
3. every historical point rebuilds the lifecycle from its causal prefix;
4. future tails cannot alter earlier prefix snapshots;
5. changing the UI display window cannot change overlapping replay-point facts.

The replay has no price-target, probability, BUY/SELL, stop, take-profit or main-trend-reversal output.

## UI projection

The Streamlit `Stabil Support` page projects the internal two-track model into concise descriptive states:

- `DESTEK YOK`
- `DESTEK KORUNUYOR`
- `DESTEK TEST EDİLİYOR`
- `DESTEK ÜSTÜNDE GENİŞLEME`
- `DESTEĞE GERİ DÖNÜŞ`
- `DESTEK ALTINDA`
- `DESTEK TABANI KIRILDI`
- `DESTEK GERİ ALINDI` (only when the current replay point actually emits reclaim)

These labels describe the current support relationship; they are not trend-reversal labels.

## Inspection surfaces

The page exposes:

- prefix-safe lifecycle timeline;
- append-only event ledger;
- breach / floor-break / reclaim / loss diagnostics;
- support test / held diagnostics;
- higher/lower rebase diagnostics;
- current support origin / confirmation / availability provenance;
- `%` and ATR-normalized support distance;
- `bars_above_support`, `bars_below_support`, `reclaim_count` and progression.

The manual 7–8 daily-bar observation remains visible as a research hypothesis only. No 7/8-bar failure threshold is implemented.

## Domain boundaries

Stabil Support does not compute or overwrite:

- BOS / CHoCH / HH-HL or Market Structure progression;
- Volume Participation, absorption, shocks or selling-pressure authority;
- H4 recovery/displacement;
- Weekly trend direction;
- Liquidity, Order Block, FVG or Engulfing;
- Semantic Targeting objectives/reactions;
- global trend health/risk scores;
- execution actions.

Future composition may read Stabil Support + Market Structure + Volume as independent evidence. Composition must preserve provenance and cannot convert `support held + expanding` into `main trend reversed` by itself.

## Replay research left intentionally open

The following are measurements for later research, not Round 2 rules:

- reclaim frequency versus `bars_below_support`, including 7–8 bars;
- MFE/MAE after hold and reclaim;
- retest frequency and time-to-retest;
- distance distributions in percent and ATR;
- higher/lower rebase outcome distributions;
- whether distance-direction noise requires a calibrated filter;
- whether any reclaim/holding persistence threshold survives out-of-sample testing.

## Completion criteria

Round 2 is complete when:

- latest replay is typed and reuses the shared daily input batch;
- historical prefix replay is deterministic and causal;
- workspace exposes the isolated domain and domain health;
- Streamlit exposes typed state + factual diagnostics;
- event/reclaim/retest/rebase/provenance tables are available;
- existing domains and cache-loading guarantees remain unchanged;
- the full test suite passes.
