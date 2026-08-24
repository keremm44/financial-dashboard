from __future__ import annotations

from typing import Any
import math

from .models import Direction, EngineResult
from .volatility_bands_fib import VolatilityBandsFibEngine as _CanonicalVolatilityBandsFibEngine
from .volatility_bands_fib_engine import (
    BandAgreement,
    BandState,
    DataQualityStatus,
    VolatilityBandsExport,
    VolatilityState,
    _BAND_NAMES,
    _STATE_NAMES,
    _bars_since,
    _clamp,
    _confirm,
    _inverse_threshold_strength,
    _recent_prior,
    _safe_div,
    _share,
    _threshold_strength,
)


class RuntimeVolatilityBandsFibEngine(_CanonicalVolatilityBandsFibEngine):
    """Canonical Volatility/Bands/Fib semantics with prefix-stable runtime caches.

    The canonical implementation rebuilds every technical series and every prior
    decision row after each appended candle. All of those historical values are
    causal/prefix-stable, so replay can retain them and calculate only the newly
    appended index without changing any decision threshold or lifecycle rule.
    """

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._reset_runtime_cache()

    def _reset_runtime_cache(self) -> None:
        self._runtime_opens: list[float] = []
        self._runtime_highs: list[float] = []
        self._runtime_lows: list[float] = []
        self._runtime_closes: list[float] = []
        self._runtime_tr: list[float] = []
        self._runtime_atr: list[float | None] = []
        self._runtime_atr_avg: list[float | None] = []
        self._runtime_basis: list[float | None] = []
        self._runtime_stdev: list[float | None] = []
        self._runtime_upper: list[float | None] = []
        self._runtime_lower: list[float | None] = []
        self._runtime_norm_width: list[float | None] = []
        self._runtime_avg_width: list[float | None] = []
        self._runtime_calc: list[dict[str, Any]] = []

    def _reset(self) -> None:
        super()._reset()
        self._reset_runtime_cache()

    @staticmethod
    def _last_sma(values: list[float], length: int) -> float | None:
        if len(values) < length:
            return None
        return sum(values[-length:]) / float(length)

    @staticmethod
    def _last_optional_sma(values: list[float | None], length: int) -> float | None:
        if len(values) < length:
            return None
        window = values[-length:]
        if any(value is None for value in window):
            return None
        return sum(float(value) for value in window if value is not None) / float(length)

    @staticmethod
    def _last_population_std(values: list[float], length: int) -> float | None:
        if len(values) < length:
            return None
        window = values[-length:]
        mean = sum(window) / float(length)
        return math.sqrt(sum((value - mean) ** 2 for value in window) / float(length))

    def _append_runtime_row(self, row: dict[str, Any]) -> None:
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        prior_close = self._runtime_closes[-1] if self._runtime_closes else None

        self._runtime_opens.append(open_)
        self._runtime_highs.append(high)
        self._runtime_lows.append(low)
        self._runtime_closes.append(close)

        true_range = high - low
        if prior_close is not None:
            true_range = max(true_range, abs(high - prior_close), abs(low - prior_close))
        self._runtime_tr.append(true_range)

        count = len(self._runtime_tr)
        if count < self.ATR_LENGTH:
            atr = None
        elif count == self.ATR_LENGTH:
            atr = sum(self._runtime_tr[: self.ATR_LENGTH]) / float(self.ATR_LENGTH)
        else:
            prior_atr = self._runtime_atr[-1]
            atr = (
                sum(self._runtime_tr[-self.ATR_LENGTH :]) / float(self.ATR_LENGTH)
                if prior_atr is None
                else (float(prior_atr) * (self.ATR_LENGTH - 1) + true_range) / float(self.ATR_LENGTH)
            )
        self._runtime_atr.append(atr)
        self._runtime_atr_avg.append(
            self._last_optional_sma(self._runtime_atr, self.ATR_AVERAGE_LENGTH)
        )

        basis = self._last_sma(self._runtime_closes, self.BOLLINGER_LENGTH)
        stdev = self._last_population_std(self._runtime_closes, self.BOLLINGER_LENGTH)
        self._runtime_basis.append(basis)
        self._runtime_stdev.append(stdev)
        if basis is None or stdev is None:
            upper = lower = norm_width = None
        else:
            dev = stdev * self.BOLLINGER_MULTIPLIER
            upper = basis + dev
            lower = basis - dev
            norm_width = _safe_div(
                upper - lower,
                max(abs(basis), self.config.minimum_tick),
                0.0,
            )
        self._runtime_upper.append(upper)
        self._runtime_lower.append(lower)
        self._runtime_norm_width.append(norm_width)
        self._runtime_avg_width.append(
            self._last_optional_sma(self._runtime_norm_width, self.BOLLINGER_LENGTH)
        )

    def _sync_runtime_series(self) -> None:
        if len(self._runtime_closes) > len(self._rows):
            self._reset_runtime_cache()
        for index in range(len(self._runtime_closes), len(self._rows)):
            self._append_runtime_row(self._rows[index])

    def _atr_series(self) -> list[float | None]:
        self._sync_runtime_series()
        return self._runtime_atr

    def _raw_metrics(self, atr: float | None) -> dict[str, float | bool]:
        self._sync_runtime_series()
        i = len(self._rows) - 1
        row = self._rows[i]
        o, h, l, c = map(float, (row["open"], row["high"], row["low"], row["close"]))
        closes = self._runtime_closes
        net = c - closes[i - 3] if i >= 3 else 0.0
        net_atr = _safe_div(net, atr, 0.0)
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - 2, i + 1)) if i >= 3 else 0.0
        efficiency = _safe_div(abs(net), path, 0.0)
        close_location = _safe_div(c - l, h - l, .5)
        prior_atr = self._runtime_atr[i - 1] if i >= 1 else None
        body_to_prior_atr = _safe_div(abs(c - o), prior_atr, 0.0)
        min_body = float(self._p["min_body"])
        return {
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "net_atr": net_atr,
            "efficiency": efficiency,
            "close_location": close_location,
            "body_to_prior_atr": body_to_prior_atr,
            "strong_counter_down": c < o and body_to_prior_atr >= min_body and close_location <= .35,
            "strong_counter_up": c > o and body_to_prior_atr >= min_body and close_location >= .65,
        }

    @property
    def runtime_closes(self) -> list[float]:
        self._sync_runtime_series()
        return self._runtime_closes

    @property
    def runtime_atr(self) -> list[float | None]:
        self._sync_runtime_series()
        return self._runtime_atr

    def _compute_last(self) -> tuple[EngineResult, VolatilityBandsExport, DataQualityStatus]:
        rows = self._rows
        self._sync_runtime_series()
        n = len(rows)
        opens = self._runtime_opens
        highs = self._runtime_highs
        lows = self._runtime_lows
        closes = self._runtime_closes
        tr = self._runtime_tr
        atr = self._runtime_atr
        atr_avg = self._runtime_atr_avg
        basis = self._runtime_basis
        upper = self._runtime_upper
        lower = self._runtime_lower
        norm_width = self._runtime_norm_width
        avg_width = self._runtime_avg_width

        calc = self._runtime_calc
        if len(calc) > n:
            calc.clear()
        p = self._p
        for i in range(len(calc), n):
            d: dict[str, Any] = {}
            prior_atr = atr[i-1] if i >= 1 else None
            atr_ratio = _safe_div(atr[i], atr_avg[i], 0.0)
            atr_slope = _safe_div((atr[i] - atr[i-3]) if i>=3 and atr[i] is not None and atr[i-3] is not None else None, atr_avg[i], 0.0)
            tr_to_atr = _safe_div(tr[i], prior_atr, 0.0)
            body = abs(closes[i]-opens[i])
            body_to_atr = _safe_div(body, prior_atr, 0.0)
            close_loc = _safe_div(closes[i]-lows[i], highs[i]-lows[i], .5)
            bw = (upper[i]-lower[i]) if upper[i] is not None and lower[i] is not None else None
            bw_usable = bw is not None and bw > max(self.config.minimum_tick*.10,1e-10)
            bw_ratio = _safe_div(norm_width[i], avg_width[i], 0.0)
            bw_slope = _safe_div((norm_width[i]-norm_width[i-3]) if i>=3 and norm_width[i] is not None and norm_width[i-3] is not None else None, avg_width[i], 0.0)
            band_pos = _safe_div(closes[i]-lower[i] if lower[i] is not None else None, bw, .5)
            prev_pos = calc[i-1]["band_pos"] if i else band_pos
            pos_change = band_pos-prev_pos
            dist_basis_atr = _safe_div(abs(closes[i]-basis[i]) if basis[i] is not None else None, atr[i],0.0)
            net = closes[i]-closes[i-3] if i>=3 else None
            net_atr = _safe_div(net,atr[i],0.0)
            path = sum(abs(closes[j]-closes[j-1]) for j in range(i-2,i+1)) if i>=3 else None
            efficiency = _safe_div(abs(net) if net is not None else None,path,0.0)
            recent_body = sum(abs(closes[j]-opens[j]) for j in range(i-2,i+1))/3 if i>=2 else None
            prev_body = sum(abs(closes[j]-opens[j]) for j in range(i-5,i-2))/3 if i>=5 else None
            body_compress = recent_body is not None and prev_body is not None and recent_body <= prev_body*float(p["body_compress"])
            width_window = norm_width[max(0,i-49):i+1]
            low_width = min(x for x in width_window if x is not None) if any(x is not None for x in width_window) else None
            width_near_low = low_width is not None and norm_width[i] is not None and norm_width[i] <= low_width*float(p["width_low_tol"])
            ready = i>=119 and all(x is not None for x in (atr[i],prior_atr,atr_avg[i],basis[i],upper[i],lower[i],norm_width[i],avg_width[i]))

            c_ar=atr_ratio<=p["contract_atr"]; c_wr=bw_ratio<=p["contract_width"]; c_as=atr_slope<=0; c_ws=bw_slope<=0
            c_prog=abs(net_atr)<=p["low_progress"]; c_eff=efficiency<=p["low_eff"]
            c_count=sum(map(int,(c_ar,c_wr,c_as,c_ws,body_compress,c_prog,c_eff)))
            c_ag=c_ar or c_as; c_wg=c_wr or c_ws; c_calm=c_prog or (body_compress and c_eff)
            e_ar=atr_ratio>=p["expand_atr"]; e_wr=bw_ratio>=p["expand_width"]; e_as=atr_slope>=p["expand_atr_slope"]; e_ws=bw_slope>=p["expand_width_slope"]
            e_vol=sum(map(int,(e_ar,e_wr,e_as,e_ws))); e_ag=e_ar or e_as; e_wg=e_wr or e_ws
            up_prog=net_atr>=p["min_progress"]; up_eff=efficiency>=p["min_eff"]; up_basis=basis[i] is not None and closes[i]>basis[i]; up_bp=band_pos>=.65; up_cl=close_loc>=.60
            up_dir_count=sum(map(int,(up_prog,up_eff,up_basis,up_bp,up_cl)))
            up_sign=net_atr>0 and (up_prog or (i>0 and closes[i]>closes[i-1])); up_candle_dir=(closes[i]>opens[i] or (i>0 and closes[i]>closes[i-1])) and close_loc>=.60
            up_c_count=sum(map(int,(closes[i]>opens[i],body_to_atr>=p["min_body"],close_loc>=.60)))
            up_e=e_vol+up_dir_count+up_c_count
            up_base=ready and bw_usable and e_ag and e_wg and up_dir_count>=2 and up_sign and up_candle_dir and up_c_count>=1 and up_e>=p["exp_evidence"]
            dn_prog=net_atr<=-p["min_progress"]; dn_eff=efficiency>=p["min_eff"]; dn_basis=basis[i] is not None and closes[i]<basis[i]; dn_bp=band_pos<=.35; dn_cl=close_loc<=.40
            dn_dir_count=sum(map(int,(dn_prog,dn_eff,dn_basis,dn_bp,dn_cl)))
            dn_sign=net_atr<0 and (dn_prog or (i>0 and closes[i]<closes[i-1])); dn_candle_dir=(closes[i]<opens[i] or (i>0 and closes[i]<closes[i-1])) and close_loc<=.40
            dn_c_count=sum(map(int,(closes[i]<opens[i],body_to_atr>=p["min_body"],close_loc<=.40)))
            dn_e=e_vol+dn_dir_count+dn_c_count
            dn_base=ready and bw_usable and e_ag and e_wg and dn_dir_count>=2 and dn_sign and dn_candle_dir and dn_c_count>=1 and dn_e>=p["exp_evidence"]
            if up_base and dn_base:
                if net_atr>=p["min_progress"]: dn_base=False
                elif net_atr<=-p["min_progress"]: up_base=False
                else: up_base=dn_base=False
            prior_evidence = 0
            if i>=1:
                prev=calc[i-1]
                prior_evidence=sum(map(int,(prev["atr_slope"]>0,prev["bw_slope"]>0,prev["atr_ratio"]>=p["expand_atr"]*.95,prev["bw_ratio"]>=p["expand_width"]*.95)))
            prior_dev=prior_evidence>=2
            prior_up = i>=1 and (calc[i-1].get("up_base",False) or (calc[i-1]["net_atr"]>=p["min_progress"]*.75 and (calc[i-2]["net_atr"] if i>=2 else 0)>0 and calc[i-1]["eff"]>=p["min_eff"]*.90))
            prior_dn = i>=1 and (calc[i-1].get("dn_base",False) or (calc[i-1]["net_atr"]<=-p["min_progress"]*.75 and (calc[i-2]["net_atr"] if i>=2 else 0)<0 and calc[i-1]["eff"]>=p["min_eff"]*.90))
            pre_up=i>=1 and (calc[i-1].get("up_base",False) or (prior_dev and prior_up)); pre_dn=i>=1 and (calc[i-1].get("dn_base",False) or (prior_dev and prior_dn))
            extreme=tr_to_atr>=p["shock_range"] or body_to_atr>=p["shock_body"]
            sudden=atr_ratio>max(1.0,(calc[i-1]["atr_ratio"] if i else atr_ratio)*1.2) or bw_ratio>max(1.0,(calc[i-1]["bw_ratio"] if i else bw_ratio)*1.2)
            shock_up=ready and bw_usable and extreme and closes[i]>opens[i] and close_loc>=.65 and net_atr>0 and not pre_up and sudden
            shock_dn=ready and bw_usable and extreme and closes[i]<opens[i] and close_loc<=.35 and net_atr<0 and not pre_dn and sudden
            shock_none=ready and bw_usable and extreme and not shock_up and not shock_dn and not pre_up and not pre_dn and sudden
            shock=shock_up or shock_dn or shock_none; shock_dir=1 if shock_up else -1 if shock_dn else 0
            up_cand=up_base and not shock and not dn_base; dn_cand=dn_base and not shock and not up_base
            strong_dn=closes[i]<opens[i] and body_to_atr>=p["min_body"] and close_loc<=.35
            strong_up=closes[i]>opens[i] and body_to_atr>=p["min_body"] and close_loc>=.65
            up_ret=basis[i] is not None and closes[i]>basis[i] and net_atr>0 and efficiency>=p["min_eff"]*.75 and not strong_dn and band_pos>=.5
            dn_ret=basis[i] is not None and closes[i]<basis[i] and net_atr<0 and efficiency>=p["min_eff"]*.75 and not strong_up and band_pos<=.5
            up_hist=[x.get("up_cand",False) for x in calc]+[up_cand]; dn_hist=[x.get("dn_cand",False) for x in calc]+[dn_cand]
            up_conf=ready and _confirm(up_hist,i,int(p["confirm"])) and up_ret; dn_conf=ready and _confirm(dn_hist,i,int(p["confirm"])) and dn_ret
            if up_conf and dn_conf:
                if net_atr>=p["min_progress"]: dn_conf=False
                elif net_atr<=-p["min_progress"]: up_conf=False
                else: up_conf=dn_conf=False
            contraction=ready and c_count>=4 and c_ag and c_wg and c_calm and not any((up_cand,dn_cand,up_conf,dn_conf,shock))
            consecutive=(calc[i-1].get("contract_consecutive",0)+1 if i and contraction else 1 if contraction else 0)
            squeeze=ready and bw_usable and contraction and atr_ratio<=p["mature_atr"] and bw_ratio<=p["mature_width"] and c_count>=5 and consecutive>=p["maturity_bars"] and width_near_low and c_prog and c_eff and not any((up_cand,dn_cand,up_conf,dn_conf,shock))
            up_conf_hist=[x.get("up_conf",False) for x in calc]+[up_conf]; dn_conf_hist=[x.get("dn_conf",False) for x in calc]+[dn_conf]
            up_since=_bars_since(up_conf_hist,i); dn_since=_bars_since(dn_conf_hist,i)
            recent_up=(up_since is not None and up_since<=6) or sum(up_hist[max(0,i-5):i+1])>=2
            recent_dn=(dn_since is not None and dn_since<=6) or sum(dn_hist[max(0,i-5):i+1])>=2
            recent_dir=1 if recent_up and not recent_dn else -1 if recent_dn and not recent_up else (1 if recent_up and recent_dn and (up_since or 100000)<(dn_since or 100000) else -1 if recent_up and recent_dn and (dn_since or 100000)<(up_since or 100000) else 1 if recent_up and recent_dn and net_atr>p["low_progress"] else -1 if recent_up and recent_dn and net_atr<-p["low_progress"] else 0)
            weak_atr=atr_slope<=0; weak_bw=bw_slope<=0
            prev_net=calc[i-1]["net_atr"] if i else net_atr; prev_eff=calc[i-1]["eff"] if i else efficiency
            up_prog_w=net_atr<p["min_progress"]*.5 and net_atr<prev_net; up_eff_w=efficiency<p["min_eff"]*.85 and efficiency<=prev_eff
            up_basis_ret=(basis[i] is not None and closes[i]<=basis[i]) or band_pos<prev_pos; up_band_ret=band_pos<.65
            dn_prog_w=net_atr>-p["min_progress"]*.5 and net_atr>prev_net; dn_eff_w=efficiency<p["min_eff"]*.85 and efficiency<=prev_eff
            dn_basis_ret=(basis[i] is not None and closes[i]>=basis[i]) or band_pos>prev_pos; dn_band_ret=band_pos>.35
            up_w_count=sum(map(int,(weak_atr,weak_bw,up_prog_w,up_eff_w,up_basis_ret,up_band_ret))); dn_w_count=sum(map(int,(weak_atr,weak_bw,dn_prog_w,dn_eff_w,dn_basis_ret,dn_band_ret)))
            up_w_signal=ready and recent_dir==1 and up_w_count>=3 and not up_cand and not up_conf and not dn_conf and not shock
            dn_w_signal=ready and recent_dir==-1 and dn_w_count>=3 and not dn_cand and not dn_conf and not up_conf and not shock
            up_weak=up_w_signal and i>=1 and calc[i-1].get("up_w_signal",False); dn_weak=dn_w_signal and i>=1 and calc[i-1].get("dn_w_signal",False)
            weakening=up_weak or dn_weak
            if not ready: vol_state=VolatilityState.PENDING
            elif shock: vol_state=VolatilityState.ONE_BAR_SHOCK
            elif up_conf: vol_state=VolatilityState.UP_CONFIRMED
            elif dn_conf: vol_state=VolatilityState.DOWN_CONFIRMED
            elif up_cand: vol_state=VolatilityState.UP_CANDIDATE
            elif dn_cand: vol_state=VolatilityState.DOWN_CANDIDATE
            elif weakening: vol_state=VolatilityState.WEAKENING
            elif squeeze: vol_state=VolatilityState.SQUEEZE_MATURING
            elif contraction: vol_state=VolatilityState.CONTRACTING
            else: vol_state=VolatilityState.BALANCED

            exp_mag=(_threshold_strength(atr_ratio,p["expand_atr"])+_threshold_strength(bw_ratio,p["expand_width"]))*0.5
            exp_chg=(_threshold_strength(max(atr_slope,0),p["expand_atr_slope"])+_threshold_strength(max(bw_slope,0),p["expand_width_slope"]))*0.5
            up_del=_threshold_strength(max(net_atr,0),p["min_progress"])*.6+_threshold_strength(efficiency,p["min_eff"])*.4
            dn_del=_threshold_strength(max(-net_atr,0),p["min_progress"])*.6+_threshold_strength(efficiency,p["min_eff"])*.4
            body_strength=_threshold_strength(body_to_atr,p["min_body"])
            up_close=_threshold_strength(max(close_loc-.5,0),.10); dn_close=_threshold_strength(max(.5-close_loc,0),.10)
            up_cq=body_strength*.45+up_close*.35+(100 if (closes[i]>opens[i] or (i>0 and closes[i]>closes[i-1])) else 0)*.2
            dn_cq=body_strength*.45+dn_close*.35+(100 if (closes[i]<opens[i] or (i>0 and closes[i]<closes[i-1])) else 0)*.2
            up_family=exp_mag*.25+exp_chg*.20+up_del*.30+up_cq*.25; dn_family=exp_mag*.25+exp_chg*.20+dn_del*.30+dn_cq*.25
            c_mag=(_inverse_threshold_strength(atr_ratio,p["contract_atr"])+_inverse_threshold_strength(bw_ratio,p["contract_width"]))*0.5
            c_chg=((100 if atr_slope<=0 else 0)+(100 if bw_slope<=0 else 0))*.5
            c_del=_inverse_threshold_strength(abs(net_atr),p["low_progress"])*.6+_inverse_threshold_strength(efficiency,p["low_eff"])*.4
            c_family=c_mag*.30+c_chg*.20+c_del*.30+(100 if body_compress else 0)*.20
            sq_mag=(_inverse_threshold_strength(atr_ratio,p["mature_atr"])+_inverse_threshold_strength(bw_ratio,p["mature_width"]))*0.5
            sq_persist=_threshold_strength(float(consecutive),max(float(p["maturity_bars"]),1.0))
            sq_family=sq_mag*.35+c_chg*.15+c_del*.25+(100 if body_compress else 0)*.10+sq_persist*.10+(100 if width_near_low else 0)*.05
            weak_vol=((100 if weak_atr else 0)+(100 if weak_bw else 0))*.5
            up_wq=weak_vol*.35+((100 if up_prog_w else 0)+(100 if up_eff_w else 0))*.5*.40+((100 if up_basis_ret else 0)+(100 if up_band_ret else 0))*.5*.25
            dn_wq=weak_vol*.35+((100 if dn_prog_w else 0)+(100 if dn_eff_w else 0))*.5*.40+((100 if dn_basis_ret else 0)+(100 if dn_band_ret else 0))*.5*.25
            if vol_state==VolatilityState.PENDING: vol_q=0
            elif vol_state==VolatilityState.BALANCED: vol_q=min(64,_clamp(64-max(up_family,dn_family,c_family)*.35))
            elif vol_state==VolatilityState.CONTRACTING: vol_q=_clamp(c_family)
            elif vol_state==VolatilityState.SQUEEZE_MATURING: vol_q=_clamp(sq_family)
            elif vol_state in (VolatilityState.UP_CANDIDATE,VolatilityState.UP_CONFIRMED):
                vol_q=_clamp(up_family); vol_q=min(vol_q,84) if vol_state==VolatilityState.UP_CANDIDATE else vol_q; vol_q=min(vol_q,64) if dn_e>=p["exp_evidence"] else vol_q
            elif vol_state in (VolatilityState.DOWN_CANDIDATE,VolatilityState.DOWN_CONFIRMED):
                vol_q=_clamp(dn_family); vol_q=min(vol_q,84) if vol_state==VolatilityState.DOWN_CANDIDATE else vol_q; vol_q=min(vol_q,64) if up_e>=p["exp_evidence"] else vol_q
            elif vol_state==VolatilityState.WEAKENING: vol_q=_clamp(up_wq if recent_dir==1 else dn_wq if recent_dir==-1 else max(up_wq,dn_wq))
            else:
                range_s=min(1.5,_safe_div(tr_to_atr,p["shock_range"],0)); body_s=min(1.5,_safe_div(body_to_atr,p["shock_body"],0)); vol_q=_clamp(max(range_s,body_s)/1.5*70+(20 if sudden else 0)+(10 if shock_dir else 5))

            d.update(atr_ratio=atr_ratio,atr_slope=atr_slope,bw_ratio=bw_ratio,bw_slope=bw_slope,band_pos=band_pos,eff=efficiency,net_atr=net_atr,dist_basis=dist_basis_atr,ready=ready,bw_usable=bw_usable,
                     up_base=up_base,dn_base=dn_base,up_cand=up_cand,dn_cand=dn_cand,up_conf=up_conf,dn_conf=dn_conf,contract_consecutive=consecutive,up_w_signal=up_w_signal,dn_w_signal=dn_w_signal,
                     vol_state=vol_state,vol_quality=vol_q,shock_dir=shock_dir,up_e=up_e,dn_e=dn_e,
                     basis=basis[i],upper=upper[i],lower=lower[i],body_to_atr=body_to_atr,close_loc=close_loc,
                     width_near_low=width_near_low)

            above=basis[i] is not None and closes[i]>basis[i]; below=basis[i] is not None and closes[i]<basis[i]
            uz=band_pos>=p["upper_accept"]; lz=band_pos<=p["lower_accept"]
            uoc=upper[i] is not None and (closes[i]>upper[i] or band_pos>1.0); loc=lower[i] is not None and (closes[i]<lower[i] or band_pos<0.0)
            ut=upper[i] is not None and highs[i]>=upper[i]; lt=lower[i] is not None and lows[i]<=lower[i]
            uot=upper[i] is not None and highs[i]>upper[i]; lot=lower[i] is not None and lows[i]<lower[i]
            higher=i>0 and closes[i]>closes[i-1]; lowerc=i>0 and closes[i]<closes[i-1]
            for key,val in (("above",above),("below",below),("uz",uz),("lz",lz),("uoc",uoc),("loc",loc),("ut",ut),("lt",lt),("uot",uot),("lot",lot),("higher",higher),("lowerc",lowerc)):
                d[key]=val
            hist=lambda k:[x.get(k,False) for x in calc]+[d[k]]
            obs=int(p["band_obs"])
            above_s=_share(hist("above"),i,obs); below_s=_share(hist("below"),i,obs); uz_s=_share(hist("uz"),i,obs); lz_s=_share(hist("lz"),i,obs); uoc_s=_share(hist("uoc"),i,obs); loc_s=_share(hist("loc"),i,obs); ut_s=_share(hist("ut"),i,obs); lt_s=_share(hist("lt"),i,obs); higher_s=_share(hist("higher"),i,obs); lower_s=_share(hist("lowerc"),i,obs)
            d.update(above_s=above_s,below_s=below_s,uz_s=uz_s,lz_s=lz_s,uoc_s=uoc_s,loc_s=loc_s,ut_s=ut_s,lt_s=lt_s,higher_s=higher_s,lower_s=lower_s)
            dual=ut and lt; utest_event=ready and bw_usable and not dual and (ut or band_pos>=p["upper_test"]); ltest_event=ready and bw_usable and not dual and (lt or band_pos<=p["lower_test"])
            d["utest_event"]=utest_event; d["ltest_event"]=ltest_event
            ua_loc=band_pos>=p["upper_accept"] and uz_s>=p["zone_share"]; la_loc=band_pos<=p["lower_accept"] and lz_s>=p["zone_share"]
            ua_basis=above_s>=p["basis_share"]; la_basis=below_s>=p["basis_share"]
            ua_prog_e=sum(map(int,(net_atr>=p["accept_progress"],efficiency>=p["accept_eff"],higher_s>=p["higher_lower_share"])))
            la_prog_e=sum(map(int,(net_atr<=-p["accept_progress"],efficiency>=p["accept_eff"],lower_s>=p["higher_lower_share"])))
            ua_v=vol_state not in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING,VolatilityState.DOWN_CONFIRMED) and bw_slope>-p["expand_width_slope"]
            la_v=vol_state not in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING,VolatilityState.UP_CONFIRMED) and bw_slope>-p["expand_width_slope"]
            ua_def=below_s<p["basis_share"] and net_atr>-p["accept_progress"] and lz_s<p["zone_share"]; la_def=above_s<p["basis_share"] and net_atr<p["accept_progress"] and uz_s<p["zone_share"]
            ua_sig=ready and bw_usable and not dual and ua_loc and ua_basis and ua_prog_e>=2 and ua_v and ua_def
            la_sig=ready and bw_usable and not dual and la_loc and la_basis and la_prog_e>=2 and la_v and la_def
            d["ua_sig"]=ua_sig; d["la_sig"]=la_sig
            ua=_confirm([x.get("ua_sig",False) for x in calc]+[ua_sig],i,int(p["band_confirm"])); la=_confirm([x.get("la_sig",False) for x in calc]+[la_sig],i,int(p["band_confirm"]));
            if ua and la: ua=la=False
            up_persist=uz_s>=p["trend_zone_share"] and above_s>=p["basis_share"] and band_pos>=p["upper_accept"]
            lo_persist=lz_s>=p["trend_zone_share"] and below_s>=p["basis_share"] and band_pos<=p["lower_accept"]
            up_prog_grp=net_atr>=p["trend_progress"] and efficiency>=p["trend_eff"] and higher_s>=p["higher_lower_share"]
            lo_prog_grp=net_atr<=-p["trend_progress"] and efficiency>=p["trend_eff"] and lower_s>=p["higher_lower_share"]
            narrow=bw_ratio<p["trend_width"] and vol_state in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING)
            up_vol_grp=bw_ratio>=p["trend_width"] and bw_slope>-p["expand_width_slope"] and not narrow and vol_state not in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING,VolatilityState.DOWN_CONFIRMED)
            lo_vol_grp=bw_ratio>=p["trend_width"] and bw_slope>-p["expand_width_slope"] and not narrow and vol_state not in (VolatilityState.CONTRACTING,VolatilityState.SQUEEZE_MATURING,VolatilityState.UP_CONFIRMED)
            up_def=below_s<p["zone_share"] and lower_s<p["basis_share"] and net_atr>0 and not la; lo_def=above_s<p["zone_share"] and higher_s<p["basis_share"] and net_atr<0 and not ua
            up_tr_sig=ready and bw_usable and not dual and up_persist and up_prog_grp and up_vol_grp and up_def; lo_tr_sig=ready and bw_usable and not dual and lo_persist and lo_prog_grp and lo_vol_grp and lo_def
            d["up_tr_sig"]=up_tr_sig; d["lo_tr_sig"]=lo_tr_sig
            up_tr=_confirm([x.get("up_tr_sig",False) for x in calc]+[up_tr_sig],i,int(p["band_confirm"])); lo_tr=_confirm([x.get("lo_tr_sig",False) for x in calc]+[lo_tr_sig],i,int(p["band_confirm"]));
            if up_tr and lo_tr: up_tr=lo_tr=False
            d.update(ua=ua,la=la,up_tr=up_tr,lo_tr=lo_tr)
            memory=int(p["band_memory"])
            recent_ua=_recent_prior([x.get("ua",False) for x in calc]+[ua],i,memory); recent_la=_recent_prior([x.get("la",False) for x in calc]+[la],i,memory); recent_ut=_recent_prior([x.get("up_tr",False) for x in calc]+[up_tr],i,memory); recent_lt=_recent_prior([x.get("lo_tr",False) for x in calc]+[lo_tr],i,memory)
            upper_out_hist=[x.get("uot",False) or x.get("uoc",False) for x in calc]+[uot or uoc]; lower_out_hist=[x.get("lot",False) or x.get("loc",False) for x in calc]+[lot or loc]
            recent_uout=_recent_prior(upper_out_hist,i,memory); recent_lout=_recent_prior(lower_out_hist,i,memory); recent_uoc=_recent_prior([x.get("uoc",False) for x in calc]+[uoc],i,memory); recent_loc=_recent_prior([x.get("loc",False) for x in calc]+[loc],i,memory)
            prev2_pos=calc[i-2]["band_pos"] if i>=2 else band_pos; prev_uzs=calc[i-1]["uz_s"] if i else uz_s; prev_lzs=calc[i-1]["lz_s"] if i else lz_s; prev_lower_s=calc[i-1]["lower_s"] if i else lower_s; prev_higher_s=calc[i-1]["higher_s"] if i else higher_s; prev_dist=calc[i-1]["dist_basis"] if i else dist_basis_atr
            uz_fall=uz_s<prev_uzs; lz_fall=lz_s<prev_lzs; up_retreat=band_pos<prev2_pos and pos_change<0; lo_retreat=band_pos>prev2_pos and pos_change>0
            up_net_fade=net_atr<prev_net and net_atr<p["trend_progress"]; lo_net_fade=net_atr>prev_net and net_atr>-p["trend_progress"]; eff_fall=efficiency<prev_eff
            up_basis_app=dist_basis_atr<prev_dist or band_pos<p["basis_upper"]; lo_basis_app=dist_basis_atr<prev_dist or band_pos>p["basis_lower"]
            up_wband_e=sum(map(int,(uz_fall,up_retreat,up_net_fade,eff_fall,bw_slope<=0,up_basis_app,lower_s>prev_lower_s))); lo_wband_e=sum(map(int,(lz_fall,lo_retreat,lo_net_fade,eff_fall,bw_slope<=0,lo_basis_app,higher_s>prev_higher_s)))
            up_wband_sig=ready and not dual and (recent_ua or recent_ut) and up_wband_e>=3 and not up_tr and not up_conf and not lo_tr; lo_wband_sig=ready and not dual and (recent_la or recent_lt) and lo_wband_e>=3 and not lo_tr and not dn_conf and not up_tr
            d["up_wband_sig"]=up_wband_sig; d["lo_wband_sig"]=lo_wband_sig
            up_wband=_confirm([x.get("up_wband_sig",False) for x in calc]+[up_wband_sig],i,int(p["band_confirm"])); lo_wband=_confirm([x.get("lo_wband_sig",False) for x in calc]+[lo_wband_sig],i,int(p["band_confirm"]));
            if up_wband and lo_wband: up_wband=lo_wband=False
            up_mr_pos=band_pos<p["basis_upper"] and up_retreat; lo_mr_pos=band_pos>p["basis_lower"] and lo_retreat
            up_mr_prog=net_atr<=0 or (up_net_fade and efficiency<p["trend_eff"]); lo_mr_prog=net_atr>=0 or (lo_net_fade and efficiency<p["trend_eff"])
            up_mr_basis=(basis[i] is not None and closes[i]<=basis[i]) or dist_basis_atr<=.35; lo_mr_basis=(basis[i] is not None and closes[i]>=basis[i]) or dist_basis_atr<=.35
            up_mr_e=sum(map(int,(up_mr_pos,up_mr_prog,up_mr_basis,lower_s>=p["higher_lower_share"],bw_slope<=0))); lo_mr_e=sum(map(int,(lo_mr_pos,lo_mr_prog,lo_mr_basis,higher_s>=p["higher_lower_share"],bw_slope<=0)))
            up_mr_sig=ready and not dual and (recent_ua or recent_ut or recent_uoc) and sum(map(int,(up_mr_pos,up_mr_prog,up_mr_basis)))>=2 and up_mr_e>=3 and not up_tr and not up_conf and not lo_tr
            lo_mr_sig=ready and not dual and (recent_la or recent_lt or recent_loc) and sum(map(int,(lo_mr_pos,lo_mr_prog,lo_mr_basis)))>=2 and lo_mr_e>=3 and not lo_tr and not dn_conf and not up_tr
            d["up_mr_sig"]=up_mr_sig; d["lo_mr_sig"]=lo_mr_sig
            up_mr=_confirm([x.get("up_mr_sig",False) for x in calc]+[up_mr_sig],i,int(p["band_confirm"])); lo_mr=_confirm([x.get("lo_mr_sig",False) for x in calc]+[lo_mr_sig],i,int(p["band_confirm"]));
            if up_mr and lo_mr: up_mr=lo_mr=False
            up_false_sig=ready and not dual and recent_uout and not recent_ut and not recent_ua and upper[i] is not None and closes[i]<=upper[i] and band_pos<.98 and band_pos<prev_pos and net_atr<p["accept_progress"] and bw_slope<=p["expand_width_slope"] and not up_conf and not uoc
            lo_false_sig=ready and not dual and recent_lout and not recent_lt and not recent_la and lower[i] is not None and closes[i]>=lower[i] and band_pos>.02 and band_pos>prev_pos and net_atr>-p["accept_progress"] and bw_slope<=p["expand_width_slope"] and not dn_conf and not loc
            d["up_false_sig"]=up_false_sig; d["lo_false_sig"]=lo_false_sig
            up_false=_confirm([x.get("up_false_sig",False) for x in calc]+[up_false_sig],i,int(p["band_confirm"])); lo_false=_confirm([x.get("lo_false_sig",False) for x in calc]+[lo_false_sig],i,int(p["band_confirm"]));
            if up_false and lo_false: up_false=lo_false=False
            basis_bal=ready and bw_usable and p["basis_lower"]<=band_pos<=p["basis_upper"] and abs(above_s-below_s)<=p["basis_tol"] and abs(net_atr)<=p["basis_progress"] and efficiency<=p["basis_eff"] and not any((ua,la,up_tr,lo_tr,up_mr,lo_mr,up_false,lo_false))
            utest=utest_event and not recent_ua and not recent_ut and not any((ua,up_tr,up_wband,up_mr,up_false,la,lo_tr)); ltest=ltest_event and not recent_la and not recent_lt and not any((la,lo_tr,lo_wband,lo_mr,lo_false,ua,up_tr))
            if utest and ltest: utest=ltest=False
            if not ready or not bw_usable: band_state=BandState.PENDING
            elif up_false: band_state=BandState.UPPER_FALSE_EXCURSION
            elif lo_false: band_state=BandState.LOWER_FALSE_EXCURSION
            elif up_mr: band_state=BandState.UPPER_MEAN_REVERSION
            elif lo_mr: band_state=BandState.LOWER_MEAN_REVERSION
            elif up_wband: band_state=BandState.UPPER_WEAKENING
            elif lo_wband: band_state=BandState.LOWER_WEAKENING
            elif up_tr: band_state=BandState.UPPER_TREND
            elif lo_tr: band_state=BandState.LOWER_TREND
            elif ua: band_state=BandState.UPPER_ACCEPTANCE
            elif la: band_state=BandState.LOWER_ACCEPTANCE
            elif utest: band_state=BandState.UPPER_TEST
            elif ltest: band_state=BandState.LOWER_TEST
            elif basis_bal: band_state=BandState.BASIS_BALANCE
            else: band_state=BandState.BALANCED
            if band_state==BandState.PENDING: band_q=0.0
            elif band_state in (BandState.BALANCED,BandState.BASIS_BALANCE): band_q=min(64,_clamp(55+(9 if basis_bal else 0)-max(uz_s,lz_s)*15))
            elif band_state in (BandState.UPPER_TEST,BandState.LOWER_TEST):
                touch=ut_s if band_state==BandState.UPPER_TEST else lt_s; ps=max(0,band_pos-p["upper_test"]) if band_state==BandState.UPPER_TEST else max(0,p["lower_test"]-band_pos); band_q=_clamp(35+touch*30+min(20,ps*100))
            elif band_state==BandState.UPPER_ACCEPTANCE: band_q=_clamp(uz_s*30+above_s*25+min(20,efficiency*20)+ua_prog_e/3*15+(10 if ua_def else 0))
            elif band_state==BandState.LOWER_ACCEPTANCE: band_q=_clamp(lz_s*30+below_s*25+min(20,efficiency*20)+la_prog_e/3*15+(10 if la_def else 0))
            elif band_state==BandState.UPPER_TREND: band_q=_clamp(uz_s*25+above_s*20+higher_s*15+efficiency*20+min(10,_safe_div(bw_ratio,p["trend_width"],0)*8)+10)
            elif band_state==BandState.LOWER_TREND: band_q=_clamp(lz_s*25+below_s*20+lower_s*15+efficiency*20+min(10,_safe_div(bw_ratio,p["trend_width"],0)*8)+10)
            elif band_state==BandState.UPPER_WEAKENING: band_q=_clamp(up_wband_e/7*80+20)
            elif band_state==BandState.LOWER_WEAKENING: band_q=_clamp(lo_wband_e/7*80+20)
            elif band_state==BandState.UPPER_MEAN_REVERSION: band_q=_clamp(up_mr_e/5*80+20)
            elif band_state==BandState.LOWER_MEAN_REVERSION: band_q=_clamp(lo_mr_e/5*80+20)
            else: band_q=_clamp(70+min(20,(uoc_s if band_state==BandState.UPPER_FALSE_EXCURSION else loc_s)*40))
            d.update(band_state=band_state,band_quality=band_q,basis_bal=basis_bal,up_wband_e=up_wband_e,lo_wband_e=lo_wband_e,up_mr_e=up_mr_e,lo_mr_e=lo_mr_e)
            calc.append(d)

        last=calc[-1]
        vol_state: VolatilityState=last["vol_state"]; band_state: BandState=last["band_state"]
        if vol_state in (VolatilityState.UP_CANDIDATE,VolatilityState.UP_CONFIRMED): direction=Direction.UP
        elif vol_state in (VolatilityState.DOWN_CANDIDATE,VolatilityState.DOWN_CONFIRMED): direction=Direction.DOWN
        else: direction=Direction.NEUTRAL
        vol_up = vol_state in (VolatilityState.UP_CANDIDATE, VolatilityState.UP_CONFIRMED)
        vol_down = vol_state in (VolatilityState.DOWN_CANDIDATE, VolatilityState.DOWN_CONFIRMED)
        band_up = band_state in (BandState.UPPER_ACCEPTANCE, BandState.UPPER_TREND)
        band_down = band_state in (BandState.LOWER_ACCEPTANCE, BandState.LOWER_TREND)
        vol_contract = vol_state in (VolatilityState.CONTRACTING, VolatilityState.SQUEEZE_MATURING)
        band_neutral = band_state in (BandState.BALANCED, BandState.BASIS_BALANCE)
        band_mean_reversion = band_state in (BandState.UPPER_MEAN_REVERSION, BandState.LOWER_MEAN_REVERSION)
        band_direction_conflict = (vol_state == VolatilityState.UP_CONFIRMED and band_down) or (vol_state == VolatilityState.DOWN_CONFIRMED and band_up)
        if not last["ready"]:
            agreement = BandAgreement.PENDING
        elif band_direction_conflict or (vol_up and band_down) or (vol_down and band_up):
            agreement = BandAgreement.CONFLICT
        elif vol_up and band_up:
            agreement = BandAgreement.UP
        elif vol_down and band_down:
            agreement = BandAgreement.DOWN
        elif vol_contract and band_neutral:
            agreement = BandAgreement.CONTRACTION
        elif vol_state == VolatilityState.WEAKENING and band_mean_reversion:
            agreement = BandAgreement.MEAN_REVERSION
        else:
            agreement = BandAgreement.NEUTRAL
        quality_status=DataQualityStatus.OK if last["ready"] else DataQualityStatus.WARMUP
        ts=rows[-1].get("timestamp")
        reasons=(f"band={_BAND_NAMES[band_state.value]}",f"data_quality={quality_status.value}","fib=UNAVAILABLE_TUR1")
        result=EngineResult(engine="VOLATILITY_BANDS_FIB",state=_STATE_NAMES[vol_state.value],timestamp=ts,direction=direction,score=float(agreement),quality=float(last["vol_quality"]),levels={"basis":float(last["basis"]) if last["basis"] is not None else math.nan,"upper_band":float(last["upper"]) if last["upper"] is not None else math.nan,"lower_band":float(last["lower"]) if last["lower"] is not None else math.nan},reasons=reasons,is_confirmed=True)
        export=VolatilityBandsExport(regime=vol_state.value if last["ready"] else None,direction=(2.0 if vol_state==VolatilityState.UP_CONFIRMED else 1.0 if vol_state==VolatilityState.UP_CANDIDATE else -1.0 if vol_state==VolatilityState.DOWN_CANDIDATE else -2.0 if vol_state==VolatilityState.DOWN_CONFIRMED else 0.0) if last["ready"] else None,quality=float(last["vol_quality"]) if last["ready"] else None,band_state=band_state.value if last["ready"] else None,band_agreement=agreement.value if last["ready"] else None,fib_state=None,data_quality=quality_status.value)
        return result,export,quality_status
