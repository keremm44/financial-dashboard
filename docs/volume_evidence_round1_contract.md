# Volume Participation — Round 1 Evidence Contract

## Scope and authority

Round 1 exposes Volume Participation as deterministic, action-free evidence. It does
not produce buy/sell output, entry, recommendation, blocker, or a global confidence
or direction. Market Structure remains the sole authority for BOS/CHoCH identity,
scope, maturity, confirmation time, direction, and broken level.

The data path is:

```text
independent closed+complete OHLCV caches
  -> independent Market Structure replay
  -> independent existing Volume Participation replay
  -> immutable per-bar Volume history
  -> causal same-timeframe Structure–Volume links
```

Volume may support, oppose, qualify, or remain unknown for a Structure event. It
never creates, deletes, replaces, or changes that event.

## Public modules

- `financial_dashboard.engines.volume_evidence`
  - `VolumeEvidenceEngine`
  - `VolumeEvidenceSnapshot`
  - `VolumeEvidenceStatus`
  - `VolumeEvidenceDataQuality`
  - `StructureVolumeLink`
  - `VolumeWindowEvidence`
  - `ParticipationWithoutStructure`
  - causal link/history helper functions
- `financial_dashboard.volume_mtf_replay`
  - `VolumeMTFEvidenceReplayRunner`
  - `VolumeMTFEvidenceReplay`
  - `VolumeTimeframeEvidenceReplay`
  - `replay_volume_evidence_from_cache`

## Existing Volume math is preserved

`VolumeEvidenceEngine` wraps, rather than rewrites, the existing final Volume
engine. Each confirmed snapshot retains immutable copies of:

- RVOL and relative traded value;
- volume and capital level/regime;
- directional volume/capital pressure;
- progress and directional/effort-result efficiency;
- candidate and confirmed participation;
- protected, weakening, and ended lifecycle states;
- absorption candidate/confirmed/invalidated lifecycle;
- local break supported/unsupported/reclaimed lifecycle;
- heavy conflict and one-bar shock evidence.

The legacy `support_direction` and `engine_direction` remain only inside the
`audit_export` source-parity boundary. `evidence_direction` is a local Volume
measurement used for relation matching, not global action authority.

## Status and data-quality separation

Participation meaning and source-data quality are separate axes:

| Axis | States |
|---|---|
| Per-bar participation | `READY`, `WARMUP`, `LOW_PARTICIPATION`, `VOLUME_UNAVAILABLE` |
| Replay/data boundary | `READY`, `DATA_LIMITED`, `INCOMPLETE_TAIL` |

A short valid history is `WARMUP`. Missing/non-finite Volume is
`VOLUME_UNAVAILABLE` plus `DATA_LIMITED`; the Volume engine is restarted after the
gap so later calculations cannot silently bridge unknown observations. An all-zero
or unusable mature Volume window is unavailable, never low participation.

Open/incomplete cache-tail rows are excluded before confirmed replay. Direct engine
updates return a transient `INCOMPLETE_TAIL` snapshot without advancing confirmed
snapshot or history.

## Independent MTF replay

Default order is `1d`, `4h`, `2h`, `1h`, `30m`. Every timeframe receives a fresh
Volume engine and reads only its own full cache. Each closed+complete row, including
warmup and unavailable-Volume rows, receives one immutable history snapshot.

The runner requires the Structure and Volume filtered timestamp sequences to match
exactly before linking. Symbol/timeframe namespace mismatches fail closed. No raw
volumes are aggregated or summed across timeframes.

## Same-timeframe Structure linkage

Only authoritative confirmed BOS/CHoCH ledger records are linked. Every link copies:

- namespaced `event_uid`;
- internal/external scope;
- BOS/CHoCH type and typed BOS maturity;
- event direction and confirmation bar/time;
- broken Structure level.

Default windows contain two confirmed bars before the event, the event bar, and two
available confirmed bars after it:

```text
PRE_EVENT -> AT_EVENT -> FOLLOW_THROUGH
```

Follow-through is observed only when those bars exist. An `as_of_bar` boundary can
rebuild an earlier assessment without reading later snapshots.

Possible neutral outcomes are:

- `STRUCTURE_SUPPORTED`
- `STRUCTURE_PARTICIPATION_WEAK`
- `STRUCTURE_VOLUME_OPPOSED`
- `STRUCTURE_VOLUME_CONFLICT`
- `STRUCTURE_SHOCK_UNCONFIRMED`
- `STRUCTURE_ABSORPTION_RISK`
- `PARTICIPATION_WITHOUT_STRUCTURE`
- `STRUCTURE_VOLUME_UNKNOWN`

Only mature aligned/opposing Volume counts as confirmed support/opposition.
Candidates remain weak evidence. A one-bar shock remains unconfirmed unless later
mature aligned evidence appears. Missing, unavailable, or warmup Volume produces
unknown rather than weak evidence. Confirmed opposing absorption and same-direction
break reclaim remain explicit risk evidence.

`ParticipationWithoutStructure` is a causal per-bar record: active Volume evidence
on a bar with no same-bar authoritative BOS/CHoCH. It does not look ahead to a future
Structure event.

## Round 1 guarantees

Focused tests verify:

- exact per-bar metric/export parity with the unchanged source engine;
- replay/incremental and prefix/no-lookahead equality;
- immutable full confirmed-history retention;
- warmup, unavailable, low-participation, limited-data, and incomplete-tail safety;
- aligned, weak, opposed, conflict, shock, absorption-risk, unknown, and unlinked
  relation paths;
- event UID/scope/maturity/time/level retention;
- internal/external and timeframe isolation;
- independent five-timeframe replay and one-timeframe mutation isolation.

## Round 2 boundary

Round 1 does not add lower-to-higher propagation, confirmed-opposition risk/release,
shock/fake/reclaim completion, correlated-volume deduplication, Streamlit Volume
views, or live ASELS acceptance. Those remain Round 2 work.
