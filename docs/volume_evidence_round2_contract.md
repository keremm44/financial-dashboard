# Volume Participation — Round 2 MTF, Risk, Lifecycle, and UI Contract

## Scope and authority

Round 2 completes Volume Participation as deterministic inspection evidence. It does
not produce an action, entry, recommendation, prediction, global direction, or
confidence. Market Structure remains authoritative for every BOS/CHoCH identity,
scope, direction, maturity, confirmation time, and broken level.

The completed path is:

```text
independent closed+complete OHLCV caches
  -> independent same-timeframe Structure and Volume replay
  -> causal Structure–Volume links
  -> bounded lower-timeframe inflow + direct structural progression inspection
  -> domain-specific opposition/shock risk lifecycles
  -> inspection-only Streamlit projections
```

All timeframes continue running independently. Higher-timeframe state never gates a
lower-timeframe calculation or removes retained evidence.

## Causal lower-timeframe inflow

For each authoritative Structure–Volume link, Round 2 examines only lower-timeframe
Volume snapshots whose bar-close availability is inside the target event and bounded
follow-through interval. Each lower timeframe is reduced to one categorical state:

- `ALIGNED`
- `OPPOSED`
- `MIXED`
- `WEAK`
- `SHOCK_UNCONFIRMED`
- `UNKNOWN`

Raw volumes and lower-timeframe bar counts are never summed into a target-timeframe
volume. The MTF score combines bounded categorical signs and configured timeframe
weights over the full available lower-timeframe capacity.

When same-timeframe Volume is unavailable/unknown or weak, lower-timeframe
inspection receives respectively
`ELEVATED_SAME_TIMEFRAME_UNAVAILABLE` or
`ELEVATED_SAME_TIMEFRAME_WEAK` diagnostic importance. In every case:

```text
lower_timeframe_can_confirm = False
same_timeframe_authoritative = True
```

Lower-timeframe Volume may enrich, oppose, or keep a fact under observation. It
cannot turn weak/unknown same-timeframe Volume into `STRUCTURE_SUPPORTED`, and it
cannot create a higher-timeframe Structure event.

## Participation without same-timeframe Structure

Round 1 retains every causal `PARTICIPATION_WITHOUT_STRUCTURE` bar. Round 2 takes the
latest directional origin per timeframe/direction and displays direct structural
distribution around it:

- lower-timeframe directly confirmed internal/external CHoCH or BOS already
  available at the Volume origin;
- same-timeframe directly confirmed follow-through events available after the
  origin;
- higher-timeframe directly confirmed follow-through events available after the
  origin.

Every step preserves the authoritative event UID, timeframe, scope, event type,
direction, confirmation time, and causal availability time. Steps are explicitly
marked `directly_confirmed=True` and `promoted_or_inferred=False`. An iCHoCH can be
observed as early progression but is never renamed as an eCHoCH or a completed
higher-timeframe turn.

## Structure–Volume blocking risk

A confirmed same-timeframe Structure event and confirmed opposing same-timeframe
Volume produce `BLOCKED_CONFIRMED_OPPOSITION`. Same-timeframe unresolved conflict or
an aligned break that was reclaimed produces its own blocked risk state. The risk
record retains both original Structure and Volume facts; neither is deleted or
rewritten.

Lower-timeframe opposition remains a visible caution but cannot create this hard
same-timeframe block. Once a block exists, weaker/neutral/candidate pressure moves it
to `MONITORING_OPPOSITION_WEAKENED` and **remains blocked**. Weakening is not
recovery.

Release is allowed only by one of three typed triggers:

1. `ALIGNED_RECOVERY`: later non-shock, mature, aligned same-timeframe Volume;
2. `AUTHORITATIVE_STRUCTURE_SUPERSESSION`: a later authoritative event in the same
   timeframe and scope retires the monitored Structure event;
3. `COMPLETED_FAKE_RECLAIM_RESOLUTION`: the opposing break/absorption state is
   explicitly invalidated or reclaimed and price has re-accepted the original
   authoritative Structure level.

A released record can be blocked again by later confirmed opposition unless the
original Structure event was authoritatively superseded. The output is a
Volume-domain risk fact, not a global trade action.

## One-bar shock, fake, absorption, continuation, and reclaim

The source engine's `one_bar_shock` audit flag takes precedence over any same-bar
candidate/confirmed source state when Structure–Volume evidence is classified. A
shock therefore starts at `DETECTED_UNCONFIRMED` and never confirms participation or
an entry on its own bar.

Default lifecycle windows are two bars for confirmation and five bars for
monitoring:

- later non-shock mature aligned Volume plus directional price progress can produce
  `FOLLOW_THROUGH_CONFIRMED`;
- loss of the shock midpoint or expiration without confirmation produces
  `FAKE_SUSPECTED`;
- confirmed opposing absorption produces `ABSORPTION_RISK`;
- reclaim of the shock origin produces terminal `RECLAIMED`;
- directionless shocks remain `DIRECTIONLESS_UNRESOLVED` after the confirmation
  window.

Every transition records bar index, timestamp, causal availability, stage, and
reason. `immediate_confirmation_allowed=False` and `entry_authority=False` are
immutable public fields.

## Correlated-volume deduplication

Ham FLOW, Volume Participation, and Auction / Volume Profile all consume information
from the same OHLCV source-volume family. The frozen contract registers all three as
correlated channels and enforces:

```text
source_family = OHLCV_SOURCE_VOLUME
independent_vote_cap = 1
raw_mtf_volume_summed = False
policy = SHARED_SOURCE_SINGLE_CORRELATED_FAMILY_NO_VOTE_STACKING
```

Round 2 activates only `VOLUME_PARTICIPATION`; Auction remains unimplemented in this
round. Future integration must join the same family rather than adding an
independent vote. MTF pressure is normalized categorical context only and has
`decision_authority=CONTEXT_ONLY`.

## Streamlit inspection surface

The `Volume Participation` tab exposes:

- a five-timeframe latest-evidence matrix;
- complete confirmed Structure–Volume event links and lower-timeframe inflow facts;
- complete risk-transition history;
- shock/fake/absorption/follow-through/reclaim lifecycles;
- direct i/eCHoCH and i/eBOS progression from unlinked participation;
- per-timeframe full confirmed evidence history, last 100 bars by default and an
  explicit all-history option;
- replay/source/tail diagnostics and the correlated-volume dedup contract.

The tab has no action buttons, trade metrics, recommendations, confidence output, or
decision authority. Empty lifecycle tables remain typed diagnostics rather than
being interpreted as neutral confirmation.

The Streamlit runtime passes the observer's already completed authoritative
`StructureLocationMTFResult` into the Volume replay. Volume therefore links against
the exact Structure facts displayed by the same UI run instead of starting a second
Structure calculation. The lower-level runtime still accepts either supported
Structure result shape, or can deterministically build Structure from the same cache
when used independently outside the observer UI.

## Public modules

- `financial_dashboard.engines.volume_round2`
  - MTF pressure, lower inflow, risk, shock, propagation, and dedup contracts;
  - pure builders for each contract and the combined `VolumeRound2Assessment`.
- `financial_dashboard.volume_mtf_replay`
  - `VolumeMTFEvidenceReplay.round2` integrates the complete assessment while
    preserving Round 1 histories and same-timeframe links.
- `financial_dashboard.ui.runtime`
  - `replay_cached_volume` cache/runtime boundary.
- `financial_dashboard.ui.view_models`
  - pure Volume matrix, event, lifecycle, history, diagnostic, and dedup projections.

## Validation invariants

Focused tests cover:

- shock precedence over a same-bar source confirmation;
- lower-timeframe inflow importance without target-timeframe promotion;
- strict opposition blocking and non-release on weakening;
- all three allowed release routes;
- delayed shock follow-through and fake/origin reclaim;
- direct i/eCHoCH/eBOS progression without inferred promotion;
- shared-source vote cap and no raw MTF volume sum;
- last-100/all-history UI behavior, complete event links, diagnostics, and absence of
  action/recommendation columns;
- both supported authoritative Structure result shapes;
- every-prefix causality and same-cache restart determinism;
- incomplete-tail exclusion and timeframe isolation inherited from the Round 1
  contract.
