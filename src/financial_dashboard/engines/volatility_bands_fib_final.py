from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import math
import pandas as pd

from .models import Direction, EngineResult
from .volatility_bands_fib_engine import (
    BandAgreement,
    BandState,
    DataQualityStatus,
    VolatilityBandsConfig,
    VolatilityBandsExport,
    VolatilityBandsFibEngine as _VolatilityBandsCoreEngine,
    VolatilityState,
    _clamp,
    _rma,  # noqa: F401  (retained import: mirrors the core ATR series definition)
    _safe_div,
)


class StructureState(IntEnum):
    PENDING = 0
    INSUFFICIENT = 1
    NEUTRAL = 2
    BULLISH_SEQUENCE = 3
    BEARISH_SEQUENCE = 4
    UP_BREAK_CANDIDATE = 5
    UP_BREAK_CONFIRMED = 6
    DOWN_BREAK_CANDIDATE = 7
    DOWN_BREAK_CONFIRMED = 8
    BULLISH_WEAKENING = 9
    BEARISH_WEAKENING = 10
    CONFLICT = 11


class FibonacciState(IntEnum):
    PENDING = 0
    UNAVAILABLE = 1
    EXTENSION = 2
    AT_EXTREME = 3
    SHALLOW_RETRACEMENT = 4
    NORMAL_RETRACEMENT = 5
    DEEP_RETRACEMENT = 6
    CRITICAL_RETRACEMENT = 7
    RECLAIM = 8
    INVALIDATED = 9


class StructureFibAlignment(IntEnum):
    PENDING = 0
    UP = 1
    DOWN = 2
    HEALTHY_PULLBACK = 3
    DEEP_PULLBACK = 4
    CONFLICT = 5
    NEUTRAL = 6


class DirectionBias(IntEnum):
    PENDING = 0
    UP = 1
    DOWN = -1
    NEUTRAL = 2
    CONFLICT = 3


class CoherenceState(IntEnum):
    PENDING = 0
    STRONG_UP = 1
    STRONG_DOWN = 2
    UP_UNCONFIRMED = 3
    DOWN_UNCONFIRMED = 4
    UP_HEALTHY_PULLBACK = 5
    DOWN_HEALTHY_PULLBACK = 6
    UP_WEAKENING = 7
    DOWN_WEAKENING = 8
    CONTRACTION = 9
    CONFLICT = 10
    NEUTRAL = 11
    LOCAL_CONFLICT = 12


PIVOT_NONE = 0
PIVOT_HIGH = 1
PIVOT_LOW = -1
SWING_NONE = 0
SWING_BULLISH = 1
SWING_BEARISH = -1
HIGH_PENDING = 0
HIGH_HH = 1
HIGH_LH = -1
HIGH_EQH = 2
LOW_PENDING = 0
LOW_HL = 1
LOW_LL = -1
LOW_EQL = 2


@dataclass(frozen=True, slots=True)
class MeaningfulPivot:
    pivot_type: int
    price: float
    source_atr: float
    source_index: int
    known_index: int


@dataclass(frozen=True, slots=True)
class BreakCandidate:
    direction: int = 0
    reference_level: float | None = None
    reference_atr: float | None = None
    buffer_price: float | None = None
    reference_pivot_index: int | None = None
    consecutive_bars: int = 0


@dataclass(frozen=True, slots=True)
class VolatilityBandsFibFinalExport:
    regime: int | None = None
    direction: float | None = None
    quality: float | None = None
    band_state: int | None = None
    band_agreement: int | None = None
    fib_state: int | None = None
    data_quality: str = DataQualityStatus.WARMUP.value
    structure_state: int | None = None
    structure_quality: float | None = None
    fib_quality: float | None = None
    structure_fib_alignment: int | None = None
    direction_bias: int | None = None
    coherence: int | None = None
    regime_band_family_quality: float | None = None
    structure_swing_family_quality: float | None = None
    active_swing_direction: int = 0
    active_swing_start: float | None = None
    active_swing_end: float | None = None
    active_swing_start_index: int | None = None
    active_swing_end_index: int | None = None
    break_candidate_direction: int = 0
    break_reference_level: float | None = None
    break_reference_atr: float | None = None
    break_buffer_price: float | None = None
    break_reference_pivot_index: int | None = None
    break_consecutive_bars: int = 0
    fib_retracement_ratio: float | None = None
    fib_invalidation_level: float | None = None


def _pair_reference_atr(first: float | None, second: float | None, fallback: float | None) -> float | None:
    a = None if first is None or pd.isna(first) else float(first)
    b = None if second is None or pd.isna(second) else float(second)
    f = None if fallback is None or pd.isna(fallback) else float(fallback)
    if a is not None and b is not None:
        return (a + b) * 0.5
    return a if a is not None else b if b is not None else f


def _band_state_from_result(result: EngineResult | None) -> BandState:
    if result is None:
        return BandState.PENDING
    for reason in result.reasons:
        if reason.startswith("band=BAND_"):
            name = reason.split("=BAND_", 1)[1]
            try:
                return BandState[name]
            except KeyError:
                break
    return BandState.PENDING


class VolatilityBandsFibEngine(_VolatilityBandsCoreEngine):
    """Final Pine v0.4.6 decision-math port over the frozen Tur-1 core."""

    def __init__(self, config: VolatilityBandsConfig | None = None) -> None:
        super().__init__(config)
        self._reset_tur2()

    def _reset_tur2(self) -> None:
        self._accepted_pivot: MeaningfulPivot | None = None
        self._last_high: MeaningfulPivot | None = None
        self._previous_high: MeaningfulPivot | None = None
        self._last_low: MeaningfulPivot | None = None
        self._previous_low: MeaningfulPivot | None = None
        self._break_candidate = BreakCandidate()
        self._structure_weak_up_history: list[bool] = []
        self._structure_weak_down_history: list[bool] = []
        self._bull_context_history: list[bool] = []
        self._bear_context_history: list[bool] = []
        self._vol_state_history: list[VolatilityState] = []
        self._band_state_history: list[BandState] = []
        self._last_fib_identity: tuple[int, int, int] | None = None
        self._last_fib_ratio: float | None = None
        self.final_export = VolatilityBandsFibFinalExport()
        self._tur2_snapshot: EngineResult | None = None

    def _reset(self) -> None:
        super()._reset()
        self._reset_tur2()

    @property
    def pivot_length(self) -> int:
        return 3 if self.config.profile == "Hassas" else 5 if self.config.profile == "Seçici" else 4

    @property
    def minimum_swing_range_atr(self) -> float:
        return 1.10 if self.config.profile == "Hassas" else 1.80 if self.config.profile == "Seçici" else 1.40

    @property
    def minimum_swing_bar_distance(self) -> int:
        return 3 if self.config.profile == "Hassas" else 5 if self.config.profile == "Seçici" else 4

    @property
    def structure_break_buffer_atr(self) -> float:
        return 0.05 if self.config.profile == "Hassas" else 0.12 if self.config.profile == "Seçici" else 0.08

    @property
    def structure_confirmation_bars(self) -> int:
        return 2 if self.config.profile == "Hassas" else 3 if self.config.profile == "Seçici" else 2

    @property
    def swing_equality_tolerance_atr(self) -> float:
        return 0.05 if self.config.profile == "Hassas" else 0.12 if self.config.profile == "Seçici" else 0.08

    @property
    def fib_invalidation_buffer_atr(self) -> float:
        return 0.03 if self.config.profile == "Hassas" else 0.10 if self.config.profile == "Seçici" else 0.06

    @property
    def fib_reclaim_improvement(self) -> float:
        return 0.08 if self.config.profile == "Hassas" else 0.12 if self.config.profile == "Seçici" else 0.10

    @property
    def structure_context_memory_bars(self) -> int:
        return 8 if self.config.profile == "Hassas" else 12 if self.config.profile == "Seçici" else 10

    @property
    def structure_approach_atr(self) -> float:
        return 0.30 if self.config.profile == "Hassas" else 0.20 if self.config.profile == "Seçici" else 0.25

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        before = len(self._rows)
        core_result = super().update(bar)
        if len(self._rows) == before:
            return self._tur2_snapshot if self._tur2_snapshot is not None else core_result
        final = self._update_tur2(core_result)
        self._snapshot = final
        self._tur2_snapshot = final
        return final

    def snapshot(self) -> EngineResult | None:
        return self._tur2_snapshot if self._tur2_snapshot is not None else super().snapshot()

    def _atr_series(self) -> list[float | None]:
        # The core engine maintains the identical _rma(tr, ATR_LENGTH) series
        # incrementally; recomputing it per bar made Tur-2 updates quadratic.
        return list(self._s["atr"])

    def _raw_metrics(self, atr: float | None) -> dict[str, float | bool]:
        i = len(self._rows)-1
        row = self._rows[i]
        o,h,l,c = map(float,(row["open"],row["high"],row["low"],row["close"]))
        closes=self._s["closes"]
        net=c-closes[i-3] if i>=3 else 0.0
        net_atr=_safe_div(net,atr,0.0)
        path=sum(abs(closes[j]-closes[j-1]) for j in range(i-2,i+1)) if i>=3 else 0.0
        efficiency=_safe_div(abs(net),path,0.0)
        close_location=_safe_div(c-l,h-l,.5)
        atrs=self._atr_series(); prior_atr=atrs[i-1] if i>=1 else None
        body_to_prior_atr=_safe_div(abs(c-o),prior_atr,0.0)
        min_body=float(self._p["min_body"])
        return {"open":o,"high":h,"low":l,"close":c,"net_atr":net_atr,"efficiency":efficiency,"close_location":close_location,"body_to_prior_atr":body_to_prior_atr,"strong_counter_down":c<o and body_to_prior_atr>=min_body and close_location<=.35,"strong_counter_up":c>o and body_to_prior_atr>=min_body and close_location>=.65}

    def _detect_confirmed_pivots(self, atrs: list[float | None]) -> tuple[MeaningfulPivot | None, MeaningfulPivot | None]:
        i=len(self._rows)-1; length=self.pivot_length; source_i=i-length
        if source_i<length: return None,None
        lo=source_i-length; hi=source_i+length
        highs=[float(self._rows[j]["high"]) for j in range(lo,hi+1)]; lows=[float(self._rows[j]["low"]) for j in range(lo,hi+1)]
        source_high=float(self._rows[source_i]["high"]); source_low=float(self._rows[source_i]["low"])
        source_atr=atrs[source_i] if source_i<len(atrs) else None; fallback=atrs[i] if i<len(atrs) else None; a=source_atr if source_atr is not None else fallback
        if a is None: return None,None
        hp=MeaningfulPivot(PIVOT_HIGH,source_high,float(a),source_i,i) if source_high>=max(highs) else None
        lp=MeaningfulPivot(PIVOT_LOW,source_low,float(a),source_i,i) if source_low<=min(lows) else None
        return hp,lp

    def _accept_pivot(self, high: MeaningfulPivot | None, low: MeaningfulPivot | None, data_ready: bool) -> None:
        if high is not None and low is not None: return
        candidate=high if high is not None else low
        if candidate is None: return
        if self._accepted_pivot is None:
            self._accepted_pivot=candidate
            if candidate.pivot_type==PIVOT_HIGH: self._last_high=candidate
            else: self._last_low=candidate
            return
        accepted=self._accepted_pivot
        if candidate.pivot_type==accepted.pivot_type:
            more=(candidate.pivot_type==PIVOT_HIGH and candidate.price>accepted.price) or (candidate.pivot_type==PIVOT_LOW and candidate.price<accepted.price)
            if not more: return
            replaced=accepted.source_index; self._accepted_pivot=candidate
            if candidate.pivot_type==PIVOT_HIGH and (self._last_high is None or self._last_high.source_index==replaced): self._last_high=candidate
            elif candidate.pivot_type==PIVOT_LOW and (self._last_low is None or self._last_low.source_index==replaced): self._last_low=candidate
            return
        distance=candidate.source_index-accepted.source_index; price_distance=abs(candidate.price-accepted.price)
        ref=_pair_reference_atr(accepted.source_atr,candidate.source_atr,candidate.source_atr)
        ok=data_ready and price_distance>max(self.config.minimum_tick,1e-10) and _safe_div(price_distance,ref,0)>=self.minimum_swing_range_atr and distance>=self.minimum_swing_bar_distance
        if not ok: return
        self._accepted_pivot=candidate
        if candidate.pivot_type==PIVOT_HIGH: self._previous_high,self._last_high=self._last_high,candidate
        else: self._previous_low,self._last_low=self._last_low,candidate

    def _active_swing(self, atr_now: float | None) -> dict[str, Any]:
        direction=SWING_NONE; start=end=None; start_i=end_i=None; ref=None
        if self._accepted_pivot is not None and self._accepted_pivot.pivot_type==PIVOT_HIGH and self._last_low is not None and self._last_high is not None and self._last_low.source_index<self._last_high.source_index:
            direction=SWING_BULLISH; start,end=self._last_low.price,self._last_high.price; start_i,end_i=self._last_low.source_index,self._last_high.source_index; ref=_pair_reference_atr(self._last_low.source_atr,self._last_high.source_atr,atr_now)
        elif self._accepted_pivot is not None and self._accepted_pivot.pivot_type==PIVOT_LOW and self._last_high is not None and self._last_low is not None and self._last_high.source_index<self._last_low.source_index:
            direction=SWING_BEARISH; start,end=self._last_high.price,self._last_low.price; start_i,end_i=self._last_high.source_index,self._last_low.source_index; ref=_pair_reference_atr(self._last_high.source_atr,self._last_low.source_atr,atr_now)
        rng=abs(float(end)-float(start)) if start is not None and end is not None else 0.0; range_atr=_safe_div(rng,ref,0.0); distance=(end_i-start_i) if start_i is not None and end_i is not None else 0; age=max(0,len(self._rows)-1-end_i) if end_i is not None else 0
        valid=len(self._rows)>=self.MINIMUM_HISTORY and direction!=0 and start is not None and end is not None and end_i is not None and start_i is not None and end_i>start_i and rng>max(self.config.minimum_tick,1e-10) and range_atr>=self.minimum_swing_range_atr and distance>=self.minimum_swing_bar_distance
        chronology=self._accepted_pivot is not None and self._last_high is not None and self._last_low is not None and ((self._accepted_pivot.pivot_type==PIVOT_HIGH and self._last_low.source_index>=self._last_high.source_index) or (self._accepted_pivot.pivot_type==PIVOT_LOW and self._last_high.source_index>=self._last_low.source_index))
        return {"direction":direction,"start":start,"end":end,"start_i":start_i,"end_i":end_i,"range":rng,"reference_atr":ref,"range_atr":range_atr,"distance":distance,"age":age,"valid":valid,"chronology_conflict":chronology}

    def _classifications(self, atr_now: float | None) -> tuple[int,int,bool,bool,bool,bool,bool]:
        hc=HIGH_PENDING; lc=LOW_PENDING
        if self._last_high is not None and self._previous_high is not None:
            ref=_pair_reference_atr(self._last_high.source_atr,self._previous_high.source_atr,atr_now) or 0; tol=ref*self.swing_equality_tolerance_atr; hc=HIGH_HH if self._last_high.price>self._previous_high.price+tol else HIGH_LH if self._last_high.price<self._previous_high.price-tol else HIGH_EQH
        if self._last_low is not None and self._previous_low is not None:
            ref=_pair_reference_atr(self._last_low.source_atr,self._previous_low.source_atr,atr_now) or 0; tol=ref*self.swing_equality_tolerance_atr; lc=LOW_HL if self._last_low.price>self._previous_low.price+tol else LOW_LL if self._last_low.price<self._previous_low.price-tol else LOW_EQL
        bull=hc==HIGH_HH and lc==LOW_HL; bear=hc==HIGH_LH and lc==LOW_LL; mixed=(hc==HIGH_HH and lc==LOW_LL) or (hc==HIGH_LH and lc==LOW_HL); equality=hc==HIGH_EQH or lc==LOW_EQL; ready=all(x is not None for x in (self._last_high,self._previous_high,self._last_low,self._previous_low))
        return hc,lc,bull,bear,mixed,equality,ready

    def _update_break_candidate(self, *, m: dict[str,float|bool], atr_now: float|None, up_expansion_confirmed: bool, down_expansion_confirmed: bool) -> tuple[bool,bool,bool,bool]:
        data_ready=len(self._rows)>=self.MINIMUM_HISTORY and atr_now is not None; up_level=self._last_high.price if self._last_high else None; dn_level=self._last_low.price if self._last_low else None; up_ref=self._last_high.source_atr if self._last_high else atr_now; dn_ref=self._last_low.source_atr if self._last_low else atr_now; up_buf=up_ref*self.structure_break_buffer_atr if up_ref is not None else None; dn_buf=dn_ref*self.structure_break_buffer_atr if dn_ref is not None else None
        c=float(m["close"]); cl=float(m["close_location"]); net=float(m["net_atr"])
        up_base=bool(data_ready and up_level is not None and up_buf is not None and c>up_level+up_buf and cl>=.60 and net>0 and not down_expansion_confirmed); dn_base=bool(data_ready and dn_level is not None and dn_buf is not None and c<dn_level-dn_buf and cl<=.40 and net<0 and not up_expansion_confirmed); up_start=up_base and not dn_base; dn_start=dn_base and not up_base
        if up_base and dn_base:
            if net>0: dn_start=False
            elif net<0: up_start=False
            else: up_start=dn_start=False
        bc=self._break_candidate; active=False
        if bc.direction==1: active=bool(bc.reference_level is not None and bc.buffer_price is not None and self._last_high is not None and bc.reference_pivot_index==self._last_high.source_index and c>bc.reference_level+bc.buffer_price and cl>=.60 and net>0 and not down_expansion_confirmed)
        elif bc.direction==-1: active=bool(bc.reference_level is not None and bc.buffer_price is not None and self._last_low is not None and bc.reference_pivot_index==self._last_low.source_index and c<bc.reference_level-bc.buffer_price and cl<=.40 and net<0 and not up_expansion_confirmed)
        if bc.direction==0:
            if up_start: bc=BreakCandidate(1,up_level,up_ref,up_buf,self._last_high.source_index if self._last_high else None,1)
            elif dn_start: bc=BreakCandidate(-1,dn_level,dn_ref,dn_buf,self._last_low.source_index if self._last_low else None,1)
        elif active: bc=BreakCandidate(bc.direction,bc.reference_level,bc.reference_atr,bc.buffer_price,bc.reference_pivot_index,bc.consecutive_bars+1)
        else:
            bc=BreakCandidate()
            if up_start: bc=BreakCandidate(1,up_level,up_ref,up_buf,self._last_high.source_index if self._last_high else None,1)
            elif dn_start: bc=BreakCandidate(-1,dn_level,dn_ref,dn_buf,self._last_low.source_index if self._last_low else None,1)
        self._break_candidate=bc
        up_cand=bool(bc.direction==1 and bc.reference_level is not None and bc.buffer_price is not None and c>bc.reference_level+bc.buffer_price and cl>=.60 and net>0 and not down_expansion_confirmed); dn_cand=bool(bc.direction==-1 and bc.reference_level is not None and bc.buffer_price is not None and c<bc.reference_level-bc.buffer_price and cl<=.40 and net<0 and not up_expansion_confirmed); up_ret=bool(bc.direction==1 and bc.reference_level is not None and bc.buffer_price is not None and c>bc.reference_level+bc.buffer_price and net>=0 and cl>=.50 and not bool(m["strong_counter_down"])); dn_ret=bool(bc.direction==-1 and bc.reference_level is not None and bc.buffer_price is not None and c<bc.reference_level-bc.buffer_price and net<=0 and cl<=.50 and not bool(m["strong_counter_up"])); up_conf=bool(data_ready and up_cand and bc.consecutive_bars>=self.structure_confirmation_bars and up_ret); dn_conf=bool(data_ready and dn_cand and bc.consecutive_bars>=self.structure_confirmation_bars and dn_ret)
        if up_conf and dn_conf: up_conf=dn_conf=False
        return up_cand,dn_cand,up_conf,dn_conf

    def _fibonacci(self, *, swing: dict[str,Any], m: dict[str,float|bool], bull_seq: bool, bear_seq: bool, up_break_conf: bool, dn_break_conf: bool) -> dict[str,Any]:
        ready=len(self._rows)>=self.MINIMUM_HISTORY; valid=bool(swing["valid"]); direction=int(swing["direction"]); start=swing["start"]; end=swing["end"]; rng=float(swing["range"]); levels={k:None for k in ("236","382","500","618","786","1000")}; ratio=None
        if valid and start is not None and end is not None and rng>0:
            c=float(m["close"])
            if direction==SWING_BULLISH: levels={"236":end-rng*.236,"382":end-rng*.382,"500":end-rng*.5,"618":end-rng*.618,"786":end-rng*.786,"1000":start}; ratio=_safe_div(end-c,rng,math.nan)
            else: levels={"236":end+rng*.236,"382":end+rng*.382,"500":end+rng*.5,"618":end+rng*.618,"786":end+rng*.786,"1000":start}; ratio=_safe_div(c-end,rng,math.nan)
            if pd.isna(ratio): ratio=None
        extension=bool(valid and ratio is not None and ratio<-.05); at_extreme=bool(valid and ratio is not None and -.05<=ratio<.236); shallow=bool(valid and ratio is not None and .236<=ratio<.382); normal=bool(valid and ratio is not None and .382<=ratio<.618); deep=bool(valid and ratio is not None and .618<=ratio<.786); critical=bool(valid and ratio is not None and ratio>=.786)
        ref=swing["reference_atr"] if valid else None; buf=ref*self.fib_invalidation_buffer_atr if ref is not None else None; invalid_level=(start-buf if direction==SWING_BULLISH else start+buf) if valid and start is not None and buf is not None else None; c=float(m["close"]); invalidated=bool(valid and invalid_level is not None and ((direction==SWING_BULLISH and c<invalid_level) or (direction==SWING_BEARISH and c>invalid_level)) and ratio is not None and ratio>1)
        identity=(int(swing["start_i"]),int(swing["end_i"]),direction) if valid else None; same=valid and identity==self._last_fib_identity; prior_depth=bool(same and self._last_fib_ratio is not None and .5<=self._last_fib_ratio<=1); improvement=bool(prior_depth and ratio is not None and self._last_fib_ratio is not None and ratio<=self._last_fib_ratio-self.fib_reclaim_improvement); prev_close=float(self._rows[-2]["close"]) if len(self._rows)>=2 else c; bull_level=bool(direction==SWING_BULLISH and levels["500"] is not None and ((c>levels["500"] and prev_close<=levels["500"]) or (c>levels["382"] and prev_close<=levels["382"]))); bear_level=bool(direction==SWING_BEARISH and levels["500"] is not None and ((c<levels["500"] and prev_close>=levels["500"]) or (c<levels["382"] and prev_close>=levels["382"]))); directional=bool((direction==SWING_BULLISH and float(m["net_atr"])>0 and (c>float(m["open"]) or c>prev_close) and float(m["close_location"])>=.55) or (direction==SWING_BEARISH and float(m["net_atr"])<0 and (c<float(m["open"]) or c<prev_close) and float(m["close_location"])<=.45)); strength=bool(float(m["efficiency"])>=float(self._p["min_eff"])*.70 or float(m["body_to_prior_atr"])>=float(self._p["min_body"])*.70); reclaim=bool(valid and not invalidated and prior_depth and improvement and (bull_level or bear_level) and directional and strength and sum(map(int,(improvement,bull_level or bear_level,directional,strength)))>=3)
        if not ready: state=FibonacciState.PENDING
        elif not valid: state=FibonacciState.UNAVAILABLE
        elif invalidated: state=FibonacciState.INVALIDATED
        elif reclaim: state=FibonacciState.RECLAIM
        elif critical: state=FibonacciState.CRITICAL_RETRACEMENT
        elif deep: state=FibonacciState.DEEP_RETRACEMENT
        elif normal: state=FibonacciState.NORMAL_RETRACEMENT
        elif shallow: state=FibonacciState.SHALLOW_RETRACEMENT
        elif extension: state=FibonacciState.EXTENSION
        else: state=FibonacciState.AT_EXTREME
        if state in (FibonacciState.PENDING,FibonacciState.UNAVAILABLE): q=0.0
        else:
            q=_clamp(25+min(20,_safe_div(float(swing["range_atr"]),self.minimum_swing_range_atr,0)*14)+min(10,_safe_div(float(swing["distance"]),float(self.minimum_swing_bar_distance),0)*8)+(10 if ratio is not None else 0)+(15 if bull_seq or bear_seq or up_break_conf or dn_break_conf else 5)+(20 if state in (FibonacciState.RECLAIM,FibonacciState.INVALIDATED) else 15)-(15 if int(swing["age"])>self.structure_context_memory_bars*3 else 8 if int(swing["age"])>self.structure_context_memory_bars*2 else 0))
        self._last_fib_identity=identity; self._last_fib_ratio=ratio
        return {"state":state,"quality":q,"ratio":ratio,"levels":levels,"invalidated":invalidated,"reclaim":reclaim,"extension":extension,"at_extreme":at_extreme,"shallow":shallow,"normal":normal,"deep":deep,"critical":critical,"invalid_level":invalid_level}

    @staticmethod
    def _structure_direction(state: StructureState) -> int:
        if state in (StructureState.BULLISH_SEQUENCE,StructureState.UP_BREAK_CANDIDATE,StructureState.UP_BREAK_CONFIRMED,StructureState.BULLISH_WEAKENING): return 1
        if state in (StructureState.BEARISH_SEQUENCE,StructureState.DOWN_BREAK_CANDIDATE,StructureState.DOWN_BREAK_CONFIRMED,StructureState.BEARISH_WEAKENING): return -1
        return 0

    def _band_quality_exact(self, band_state: BandState, vol_state: VolatilityState) -> float:
        n=len(self._rows); i=n-1
        if band_state==BandState.PENDING or n<20: return 0.0
        closes=[float(r["close"]) for r in self._rows]; highs=[float(r["high"]) for r in self._rows]; lows=[float(r["low"]) for r in self._rows]; atrs=self._atr_series()
        def basic(k:int):
            if k<19:return None
            w=closes[k-19:k+1]; b=sum(w)/20; sd=(sum((x-b)**2 for x in w)/20)**.5; u=b+2*sd; l=b-2*sd; wd=u-l; return b,u,l,wd,_safe_div(wd,max(abs(b),self.config.minimum_tick),0)
        def vals(k:int):
            z=basic(k)
            if z is None:return None
            b,u,l,wd,norm=z; norms=[]
            for j in range(max(19,k-19),k+1):
                bj=basic(j)
                if bj is not None:norms.append(bj[4])
            avg=sum(norms)/len(norms) if len(norms)==20 else None; ratio=_safe_div(norm,avg,0) if avg else 0; old=basic(k-3) if k>=22 else None; slope=_safe_div(norm-old[4],avg,0) if old is not None and avg else 0; pos=_safe_div(closes[k]-l,wd,.5); a=atrs[k]; net=closes[k]-closes[k-3] if k>=3 else 0; neta=_safe_div(net,a,0); path=sum(abs(closes[j]-closes[j-1]) for j in range(k-2,k+1)) if k>=3 else 0; eff=_safe_div(abs(net),path,0); dist=_safe_div(abs(closes[k]-b),a,0); return b,u,l,pos,ratio,slope,neta,eff,dist
        cur=vals(i)
        if cur is None:return 0.0
        basis,upper,lower,pos,bw_ratio,bw_slope,net_atr,eff,dist=cur; p=self._p; obs=int(p["band_obs"])
        def metric(k:int):
            v=vals(k)
            if v is None:return None
            b,u,l,po,br,bs,na,ef,di=v; return {"above":closes[k]>b,"below":closes[k]<b,"uz":po>=p["upper_accept"],"lz":po<=p["lower_accept"],"uoc":closes[k]>u or po>1,"loc":closes[k]<l or po<0,"ut":highs[k]>=u,"lt":lows[k]<=l,"higher":k>0 and closes[k]>closes[k-1],"lowerc":k>0 and closes[k]<closes[k-1],"pos":po,"net":na,"eff":ef,"dist":di}
        ms=[metric(k) for k in range(max(0,i-obs+1),i+1)]; ms=[x for x in ms if x is not None]
        if not ms:return 0.0
        sh=lambda k:sum(int(x[k]) for x in ms)/len(ms); above,below,uz,lz,uoc,loc,ut,lt,higher,lowerc=(sh(k) for k in ("above","below","uz","lz","uoc","loc","ut","lt","higher","lowerc")); basis_bal=p["basis_lower"]<=pos<=p["basis_upper"] and abs(above-below)<=p["basis_tol"] and abs(net_atr)<=p["basis_progress"] and eff<=p["basis_eff"]
        if band_state in (BandState.BALANCED,BandState.BASIS_BALANCE): return min(64,_clamp(55+(9 if basis_bal else 0)-max(uz,lz)*15))
        if band_state in (BandState.UPPER_TEST,BandState.LOWER_TEST):
            touch=ut if band_state==BandState.UPPER_TEST else lt; strength=max(0,pos-p["upper_test"]) if band_state==BandState.UPPER_TEST else max(0,p["lower_test"]-pos); return _clamp(35+touch*30+min(20,strength*100))
        ua_prog=sum(map(int,(net_atr>=p["accept_progress"],eff>=p["accept_eff"],higher>=p["higher_lower_share"]))); la_prog=sum(map(int,(net_atr<=-p["accept_progress"],eff>=p["accept_eff"],lowerc>=p["higher_lower_share"]))); ua_def=below<p["basis_share"] and net_atr>-p["accept_progress"] and lz<p["zone_share"]; la_def=above<p["basis_share"] and net_atr<p["accept_progress"] and uz<p["zone_share"]
        if band_state==BandState.UPPER_ACCEPTANCE:return _clamp(uz*30+above*25+min(20,eff*20)+ua_prog/3*15+(10 if ua_def else 0))
        if band_state==BandState.LOWER_ACCEPTANCE:return _clamp(lz*30+below*25+min(20,eff*20)+la_prog/3*15+(10 if la_def else 0))
        narrow=bw_ratio<p["trend_width"] and vol_state in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING); up_p=uz>=p["trend_zone_share"] and above>=p["basis_share"] and pos>=p["upper_accept"]; lo_p=lz>=p["trend_zone_share"] and below>=p["basis_share"] and pos<=p["lower_accept"]; up_pg=net_atr>=p["trend_progress"] and eff>=p["trend_eff"] and higher>=p["higher_lower_share"]; lo_pg=net_atr<=-p["trend_progress"] and eff>=p["trend_eff"] and lowerc>=p["higher_lower_share"]; up_v=bw_ratio>=p["trend_width"] and bw_slope>-p["expand_width_slope"] and not narrow and vol_state not in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING,VolatilityState.DOWN_CONFIRMED); lo_v=bw_ratio>=p["trend_width"] and bw_slope>-p["expand_width_slope"] and not narrow and vol_state not in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING,VolatilityState.UP_CONFIRMED); up_d=below<p["zone_share"] and lowerc<p["basis_share"] and net_atr>0; lo_d=above<p["zone_share"] and higher<p["basis_share"] and net_atr<0; up_f=sum(map(int,(up_p,up_pg,up_v,up_d))); lo_f=sum(map(int,(lo_p,lo_pg,lo_v,lo_d)))
        if band_state==BandState.UPPER_TREND:return _clamp(uz*25+above*20+higher*15+eff*20+min(10,_safe_div(bw_ratio,p["trend_width"],0)*8)+up_f/4*10)
        if band_state==BandState.LOWER_TREND:return _clamp(lz*25+below*20+lowerc*15+eff*20+min(10,_safe_div(bw_ratio,p["trend_width"],0)*8)+lo_f/4*10)
        if band_state in (BandState.UPPER_FALSE_EXCURSION,BandState.LOWER_FALSE_EXCURSION):return _clamp(70+min(20,(uoc if band_state==BandState.UPPER_FALSE_EXCURSION else loc)*40))
        return 60.0

    def _update_tur2(self, core_result: EngineResult | None) -> EngineResult:
        i=len(self._rows)-1; atrs=self._atr_series(); atr_now=atrs[i]; data_ready=bool(self.export.regime is not None and len(self._rows)>=self.MINIMUM_HISTORY and atr_now is not None); vol_state=VolatilityState(self.export.regime) if self.export.regime is not None else VolatilityState.PENDING; band_state=_band_state_from_result(core_result); agreement=BandAgreement(self.export.band_agreement) if self.export.band_agreement is not None else BandAgreement.PENDING; self._vol_state_history.append(vol_state); self._band_state_history.append(band_state); m=self._raw_metrics(atr_now); hp,lp=self._detect_confirmed_pivots(atrs); self._accept_pivot(hp,lp,data_ready); swing=self._active_swing(atr_now); hc,lc,bull_seq,bear_seq,mixed,equality,history_ready=self._classifications(atr_now); up_cand,dn_cand,up_conf,dn_conf=self._update_break_candidate(m=m,atr_now=atr_now,up_expansion_confirmed=vol_state==VolatilityState.UP_CONFIRMED,down_expansion_confirmed=vol_state==VolatilityState.DOWN_CONFIRMED); fib=self._fibonacci(swing=swing,m=m,bull_seq=bull_seq,bear_seq=bear_seq,up_break_conf=up_conf,dn_break_conf=dn_conf)
        recent_bull=any(self._bull_context_history[-self.structure_context_memory_bars:]); recent_bear=any(self._bear_context_history[-self.structure_context_memory_bars:]); bull_pressure=bool(self._last_low is not None and atr_now is not None and float(m["close"])<=self._last_low.price+atr_now*self.structure_approach_atr); bear_pressure=bool(self._last_high is not None and atr_now is not None and float(m["close"])>=self._last_high.price-atr_now*self.structure_approach_atr); bull_sig=bool(data_ready and (recent_bull or bull_seq or up_conf) and sum(map(int,(bull_pressure,hc!=HIGH_HH,float(m["net_atr"])<0)))>=2 and not dn_conf); bear_sig=bool(data_ready and (recent_bear or bear_seq or dn_conf) and sum(map(int,(bear_pressure,lc!=LOW_LL,float(m["net_atr"])>0)))>=2 and not up_conf); self._structure_weak_up_history.append(bull_sig); self._structure_weak_down_history.append(bear_sig); nb=self.structure_confirmation_bars; bull_weak=len(self._structure_weak_up_history)>=nb and all(self._structure_weak_up_history[-nb:]); bear_weak=len(self._structure_weak_down_history)>=nb and all(self._structure_weak_down_history[-nb:]);
        if bull_weak and bear_weak: bull_weak=bear_weak=False
        structural_conflict=bool(mixed or swing["chronology_conflict"] or (bull_seq and (dn_cand or dn_conf)) or (bear_seq and (up_cand or up_conf)))
        if not data_ready: structure=StructureState.PENDING
        elif not history_ready: structure=StructureState.INSUFFICIENT
        elif up_conf: structure=StructureState.UP_BREAK_CONFIRMED
        elif dn_conf: structure=StructureState.DOWN_BREAK_CONFIRMED
        elif up_cand: structure=StructureState.UP_BREAK_CANDIDATE
        elif dn_cand: structure=StructureState.DOWN_BREAK_CANDIDATE
        elif bull_weak: structure=StructureState.BULLISH_WEAKENING
        elif bear_weak: structure=StructureState.BEARISH_WEAKENING
        elif bull_seq: structure=StructureState.BULLISH_SEQUENCE
        elif bear_seq: structure=StructureState.BEARISH_SEQUENCE
        elif structural_conflict: structure=StructureState.CONFLICT
        else: structure=StructureState.NEUTRAL
        main_dir=self._structure_direction(structure); active_leg=1 if swing["direction"]==SWING_BULLISH else -1 if swing["direction"]==SWING_BEARISH else 0; natural_counter=bool(swing["valid"] and main_dir and active_leg==-main_dir)
        if structure==StructureState.PENDING: structure_q=0.0
        elif structure==StructureState.INSUFFICIENT: structure_q=15.0
        else:
            structure_q=_clamp((20 if history_ready else 0)+(min(20,_safe_div(float(swing["range_atr"]),self.minimum_swing_range_atr,0)*14) if swing["valid"] else 0)+(25 if bull_seq or bear_seq else 10 if equality else 5)+(25 if up_conf or dn_conf else 15 if up_cand or dn_cand else 0)+(10 if int(swing["age"])<=self.structure_context_memory_bars else 5 if int(swing["age"])<=self.structure_context_memory_bars*2 else 0)-(10 if bull_weak or bear_weak else 0)-(35 if structural_conflict else 0)); structure_q=min(49,structure_q) if structure==StructureState.CONFLICT else structure_q
        self._bull_context_history.append(bool(bull_seq or up_conf)); self._bear_context_history.append(bool(bear_seq or dn_conf)); fib_state:FibonacciState=fib["state"]; fib_healthy=fib_state in (FibonacciState.EXTENSION,FibonacciState.AT_EXTREME,FibonacciState.SHALLOW_RETRACEMENT,FibonacciState.NORMAL_RETRACEMENT,FibonacciState.RECLAIM); deepcrit=fib_state in (FibonacciState.DEEP_RETRACEMENT,FibonacciState.CRITICAL_RETRACEMENT)
        if not data_ready: align=StructureFibAlignment.PENDING
        elif structural_conflict or (fib["invalidated"] and not natural_counter): align=StructureFibAlignment.CONFLICT
        elif natural_counter and deepcrit: align=StructureFibAlignment.DEEP_PULLBACK
        elif natural_counter and (fib["shallow"] or fib["normal"]): align=StructureFibAlignment.HEALTHY_PULLBACK
        elif main_dir==1 and swing["valid"] and (active_leg==1 or (natural_counter and (fib["reclaim"] or fib["extension"] or fib["at_extreme"]))) and not fib["invalidated"]: align=StructureFibAlignment.UP
        elif main_dir==-1 and swing["valid"] and (active_leg==-1 or (natural_counter and (fib["reclaim"] or fib["extension"] or fib["at_extreme"]))) and not fib["invalidated"]: align=StructureFibAlignment.DOWN
        else: align=StructureFibAlignment.NEUTRAL
        vol_up=vol_state in (VolatilityState.UP_CANDIDATE,VolatilityState.UP_CONFIRMED); vol_dn=vol_state in (VolatilityState.DOWN_CANDIDATE,VolatilityState.DOWN_CONFIRMED); band_up=band_state in (BandState.UPPER_ACCEPTANCE,BandState.UPPER_TREND); band_dn=band_state in (BandState.LOWER_ACCEPTANCE,BandState.LOWER_TREND); regime_dir=1 if agreement==BandAgreement.UP else -1 if agreement==BandAgreement.DOWN else 1 if vol_up and not band_dn and agreement!=BandAgreement.CONFLICT else -1 if vol_dn and not band_up and agreement!=BandAgreement.CONFLICT else 0; band_q=self._band_quality_exact(band_state,vol_state); vol_q=float(self.export.quality or 0); regime_q=(vol_q+band_q)*.5 if vol_state!=VolatilityState.PENDING and band_state!=BandState.PENDING else min(64,vol_q) if vol_state!=VolatilityState.PENDING else min(64,band_q); regime_q=min(49,regime_q) if agreement==BandAgreement.CONFLICT else _clamp(regime_q); structure_dir=1 if align==StructureFibAlignment.UP else -1 if align==StructureFibAlignment.DOWN else main_dir if align in (StructureFibAlignment.HEALTHY_PULLBACK,StructureFibAlignment.DEEP_PULLBACK) else 0; structure_available=structure not in (StructureState.PENDING,StructureState.INSUFFICIENT); fib_available=fib_state not in (FibonacciState.PENDING,FibonacciState.UNAVAILABLE); structure_family_q=(structure_q+float(fib["quality"]))*0.5 if structure_available and fib_available else min(64,structure_q) if structure_available else min(64,float(fib["quality"])) if fib_available else 0; structure_family_q=min(39,structure_family_q) if align==StructureFibAlignment.CONFLICT or structural_conflict else min(64,structure_family_q) if align==StructureFibAlignment.DEEP_PULLBACK else _clamp(structure_family_q); opposed=regime_dir and structure_dir and regime_dir==-structure_dir; family_conflict=agreement==BandAgreement.CONFLICT or align==StructureFibAlignment.CONFLICT or structural_conflict or opposed; family_agree=regime_dir and structure_dir and regime_dir==structure_dir
        if not data_ready: bias=DirectionBias.PENDING
        elif family_conflict: bias=DirectionBias.CONFLICT
        elif regime_dir==structure_dir==1: bias=DirectionBias.UP
        elif regime_dir==structure_dir==-1: bias=DirectionBias.DOWN
        else: bias=DirectionBias.NEUTRAL
        recent_up=any(v in (VolatilityState.UP_CANDIDATE,VolatilityState.UP_CONFIRMED) for v in self._vol_state_history[-6:]) or regime_dir==1 or main_dir==1; recent_dn=any(v in (VolatilityState.DOWN_CANDIDATE,VolatilityState.DOWN_CONFIRMED) for v in self._vol_state_history[-6:]) or regime_dir==-1 or main_dir==-1; healthy_bull=main_dir==1 and align==StructureFibAlignment.HEALTHY_PULLBACK and natural_counter and not family_conflict; healthy_bear=main_dir==-1 and align==StructureFibAlignment.HEALTHY_PULLBACK and natural_counter and not family_conflict; bull_aux=band_state in (BandState.UPPER_WEAKENING,BandState.UPPER_MEAN_REVERSION) or (swing["direction"]==SWING_BULLISH and (fib["deep"] or fib["critical"] or fib["invalidated"])); bear_aux=band_state in (BandState.LOWER_WEAKENING,BandState.LOWER_MEAN_REVERSION) or (swing["direction"]==SWING_BEARISH and (fib["deep"] or fib["critical"] or fib["invalidated"])); bull_story=bool(recent_up and (bull_weak or bull_aux or align==StructureFibAlignment.DEEP_PULLBACK) and not family_conflict); bear_story=bool(recent_dn and (bear_weak or bear_aux or align==StructureFibAlignment.DEEP_PULLBACK) and not family_conflict); bull_broken=bool(recent_up and (dn_conf or (fib["invalidated"] and not natural_counter) or align==StructureFibAlignment.CONFLICT)); bear_broken=bool(recent_dn and (up_conf or (fib["invalidated"] and not natural_counter) or align==StructureFibAlignment.CONFLICT)); local=bool(not family_conflict and (align==StructureFibAlignment.DEEP_PULLBACK or (agreement==BandAgreement.MEAN_REVERSION and structure_dir!=0)))
        if not data_ready: coherence=CoherenceState.PENDING
        elif family_conflict: coherence=CoherenceState.CONFLICT
        elif bull_broken: coherence=CoherenceState.UP_WEAKENING
        elif bear_broken: coherence=CoherenceState.DOWN_WEAKENING
        elif healthy_bull and regime_dir!=-1: coherence=CoherenceState.UP_HEALTHY_PULLBACK
        elif healthy_bear and regime_dir!=1: coherence=CoherenceState.DOWN_HEALTHY_PULLBACK
        elif bull_story: coherence=CoherenceState.UP_WEAKENING
        elif bear_story: coherence=CoherenceState.DOWN_WEAKENING
        elif family_agree and regime_dir==1 and regime_q>=65 and structure_family_q>=65: coherence=CoherenceState.STRONG_UP
        elif family_agree and regime_dir==-1 and regime_q>=65 and structure_family_q>=65: coherence=CoherenceState.STRONG_DOWN
        elif regime_dir==1 and structure_dir==0: coherence=CoherenceState.UP_UNCONFIRMED
        elif regime_dir==-1 and structure_dir==0: coherence=CoherenceState.DOWN_UNCONFIRMED
        elif agreement==BandAgreement.CONTRACTION and structure_dir==0: coherence=CoherenceState.CONTRACTION
        elif local: coherence=CoherenceState.LOCAL_CONFLICT
        else: coherence=CoherenceState.NEUTRAL
        status=DataQualityStatus.OK if data_ready else DataQualityStatus.WARMUP; bc=self._break_candidate; official_fib=fib_state.value if data_ready and fib_state not in (FibonacciState.PENDING,FibonacciState.UNAVAILABLE) else None; self.final_export=VolatilityBandsFibFinalExport(regime=self.export.regime,direction=self.export.direction,quality=self.export.quality,band_state=self.export.band_state,band_agreement=self.export.band_agreement,fib_state=official_fib,data_quality=status.value,structure_state=structure.value if data_ready else None,structure_quality=structure_q if data_ready else None,fib_quality=float(fib["quality"]) if data_ready and fib_state!=FibonacciState.UNAVAILABLE else None,structure_fib_alignment=align.value if data_ready else None,direction_bias=bias.value if data_ready else None,coherence=coherence.value if data_ready else None,regime_band_family_quality=regime_q if data_ready else None,structure_swing_family_quality=structure_family_q if data_ready else None,active_swing_direction=int(swing["direction"]),active_swing_start=swing["start"],active_swing_end=swing["end"],active_swing_start_index=swing["start_i"],active_swing_end_index=swing["end_i"],break_candidate_direction=bc.direction,break_reference_level=bc.reference_level,break_reference_atr=bc.reference_atr,break_buffer_price=bc.buffer_price,break_reference_pivot_index=bc.reference_pivot_index,break_consecutive_bars=bc.consecutive_bars,fib_retracement_ratio=fib["ratio"],fib_invalidation_level=fib["invalid_level"]); self.export=self.final_export
        levels=dict(core_result.levels if core_result else {}); levels.update({f"fib_{k}":float(v) for k,v in fib["levels"].items() if v is not None});
        if fib["invalid_level"] is not None: levels["fib_invalidation"]=float(fib["invalid_level"])
        if self._last_high: levels["meaningful_high"]=self._last_high.price
        if self._last_low: levels["meaningful_low"]=self._last_low.price
        if bc.reference_level is not None: levels["break_reference"]=bc.reference_level
        direction=Direction.UP if coherence in (CoherenceState.STRONG_UP,CoherenceState.UP_UNCONFIRMED,CoherenceState.UP_HEALTHY_PULLBACK) else Direction.DOWN if coherence in (CoherenceState.STRONG_DOWN,CoherenceState.DOWN_UNCONFIRMED,CoherenceState.DOWN_HEALTHY_PULLBACK) else Direction.NEUTRAL; reasons=(f"volatility=VOL_{vol_state.name}",f"band=BAND_{band_state.name}",f"structure=STRUCTURE_{structure.name}",f"fib=FIB_{fib_state.name}",f"alignment=STRUCT_FIB_{align.name}",f"bias=BIAS_{bias.name}",f"coherence=COHERENCE_{coherence.name}",f"data_quality={status.value}")
        return EngineResult(engine="VOLATILITY_BANDS_FIB",state=f"COHERENCE_{coherence.name}",timestamp=self._rows[-1].get("timestamp"),direction=direction,score=float(bias.value) if data_ready else None,quality=float((regime_q+structure_family_q)*.5) if data_ready else 0.0,levels=levels,reasons=reasons,is_confirmed=True)
