from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any

from .ham_evidence import FamilySnapshot, WEIGHTS, build_ham_family_evidence
from .raw_indicator_dashboard import (
    RawDataQuality,
    RawIndicatorConfig,
    RawIndicatorDashboardEngine,
    RawIndicatorSnapshot,
    VolumeQuality,
)


class SystemState(IntEnum):
    DATA_WAIT = 0
    STRONG_UP = 1
    HEALTHY_UP = 2
    DEVELOPING_UP = 3
    REACTION_UP = 4
    WEAKENING_UP = 5
    NEUTRAL = 6
    CONFLICT = 7
    PRESSURE_UP = 8
    SYNTHETIC_BLOCK = 9
    STRONG_DOWN = -1
    HEALTHY_DOWN = -2
    DEVELOPING_DOWN = -3
    REACTION_DOWN = -4
    WEAKENING_DOWN = -5
    PRESSURE_DOWN = -8


@dataclass(frozen=True, slots=True)
class DecisionConfig:
    family_weak_threshold: float = 15.0
    family_healthy_threshold: float = 35.0
    family_strong_threshold: float = 60.0
    minimum_family_coverage: float = 75.0
    pressure_decision_score: float = 15.0
    developing_decision_score: float = 20.0
    healthy_decision_score: float = 45.0
    strong_decision_score: float = 65.0
    weakening_score_drop: float = 15.0
    reaction_min_score: float = 10.0
    reaction_min_quality: float = 35.0
    decision_chart_allowed: bool = True


@dataclass(frozen=True, slots=True)
class HamDashboardExport:
    momentum_state: int | None
    momentum_score: float | None
    timing_state: int | None
    timing_score: float | None


@dataclass(frozen=True, slots=True)
class HamDashboardDecisionSnapshot:
    timestamp: Any | None = None
    data_quality: RawDataQuality = RawDataQuality.WARMUP
    raw: RawIndicatorSnapshot | None = None
    price_family: FamilySnapshot | None = None
    momentum_family: FamilySnapshot | None = None
    timing_family: FamilySnapshot | None = None
    flow_family: FamilySnapshot | None = None
    family_decision_score: float | None = None
    family_decision_valid_weight: float = 0.0
    family_decision_coverage_stable: bool = False
    valid_family_count: int = 0
    up_family_count: int = 0
    down_family_count: int = 0
    strong_up_family_count: int = 0
    strong_down_family_count: int = 0
    decision_quality: float = 0.0
    timing_mismatch: bool = False
    system_conflict: bool = False
    base_system_state: SystemState = SystemState.DATA_WAIT
    system_state: SystemState = SystemState.DATA_WAIT
    system_bias: int = 0
    missing_evidence: str = "10 KANIT VERİ KAPSAMI"
    risk_flags: tuple[str, ...] = ()
    export: HamDashboardExport = HamDashboardExport(None, None, None, None)


FAMILY_DECISION_WEIGHT_PRICE = 1.35
FAMILY_DECISION_WEIGHT_MOMENTUM = 1.35
FAMILY_DECISION_WEIGHT_FLOW = 0.80
FAMILY_DECISION_WEIGHT_TIMING = 0.35
FAMILY_DECISION_MAX_WEIGHT = 3.85
FAMILY_WEIGHT_STABILITY_TOLERANCE = 0.10


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _sign(value: float) -> int:
    return 1 if value > 0.0 else -1 if value < 0.0 else 0


def _family_state(balance: float | None, activity: float | None, coverage: float, cfg: DecisionConfig) -> int:
    if balance is None or activity is None or coverage < cfg.minimum_family_coverage:
        return 0
    weak = _clamp(cfg.family_weak_threshold, 5.0, 75.0)
    healthy = _clamp(max(cfg.family_healthy_threshold, weak + 5.0), weak + 5.0, 85.0)
    if activity < weak or abs(balance) < weak:
        return 0
    if balance >= healthy:
        return 2
    if balance <= -healthy:
        return -2
    if balance >= weak:
        return 1
    if balance <= -weak:
        return -1
    return 0


def _bias(state: SystemState) -> int:
    if state in {SystemState.STRONG_UP, SystemState.HEALTHY_UP, SystemState.DEVELOPING_UP, SystemState.REACTION_UP, SystemState.WEAKENING_UP, SystemState.PRESSURE_UP}:
        return 1
    if state in {SystemState.STRONG_DOWN, SystemState.HEALTHY_DOWN, SystemState.DEVELOPING_DOWN, SystemState.REACTION_DOWN, SystemState.WEAKENING_DOWN, SystemState.PRESSURE_DOWN}:
        return -1
    return 0


class HamDashboardDecisionEngine:
    """Ham Dashboard v2.3.7 Tur-2 family/quorum/system/export layer.

    Tur-1 owns raw indicator math. This layer consumes only causal Tur-1
    snapshots and never reconstructs OHLCV math. Open/source-gap rows therefore
    freeze both raw and decision state atomically.
    """

    def __init__(self, raw_config: RawIndicatorConfig | None = None, decision_config: DecisionConfig | None = None) -> None:
        self.raw_engine = RawIndicatorDashboardEngine(raw_config)
        self.config = decision_config or DecisionConfig()
        self._snapshot = HamDashboardDecisionSnapshot()
        self._history: list[HamDashboardDecisionSnapshot] = []

    @property
    def snapshot(self) -> HamDashboardDecisionSnapshot:
        return self._snapshot

    def reset(self) -> None:
        self.raw_engine.reset()
        self._snapshot = HamDashboardDecisionSnapshot()
        self._history.clear()

    def update(self, bar: Any) -> HamDashboardDecisionSnapshot:
        raw = self.raw_engine.update(bar)
        if raw.data_quality in {RawDataQuality.INCOMPLETE_BAR, RawDataQuality.SOURCE_GAP}:
            return replace(self._snapshot, data_quality=raw.data_quality)
        self._snapshot = self._decide(raw)
        self._history.append(self._snapshot)
        return self._snapshot

    def replay(self, frame: Any) -> list[HamDashboardDecisionSnapshot]:
        self.reset()
        return [self.update(row) for row in frame.to_dict("records")]

    def _thresholds(self) -> tuple[float, float, float, float, float, float, float]:
        cfg = self.config
        weak = _clamp(cfg.family_weak_threshold, 5.0, 75.0)
        healthy = _clamp(max(cfg.family_healthy_threshold, weak + 5.0), weak + 5.0, 85.0)
        strong_family = _clamp(max(cfg.family_strong_threshold, healthy + 5.0), healthy + 5.0, 95.0)
        pressure = _clamp(cfg.pressure_decision_score, 5.0, 75.0)
        developing = _clamp(max(cfg.developing_decision_score, pressure + 5.0), pressure + 5.0, 80.0)
        healthy_score = _clamp(max(cfg.healthy_decision_score, developing + 5.0), developing + 5.0, 85.0)
        strong_score = _clamp(max(cfg.strong_decision_score, healthy_score + 5.0), healthy_score + 5.0, 90.0)
        return weak, healthy, strong_family, pressure, developing, healthy_score, strong_score

    def _families(self, raw: RawIndicatorSnapshot) -> tuple[FamilySnapshot, FamilySnapshot, FamilySnapshot, FamilySnapshot]:
        families = build_ham_family_evidence(
            raw,
            minimum_family_coverage=self.config.minimum_family_coverage,
        )
        return families.as_tuple()

    def _decide(self, raw: RawIndicatorSnapshot) -> HamDashboardDecisionSnapshot:
        cfg = self.config
        weak, healthy, strong_family, pressure_score, developing_score, healthy_score, strong_score = self._thresholds()
        price, momentum, timing, flow = self._families(raw)

        valid_family_count = sum((price.ready, momentum.ready, timing.ready, flow.ready))
        effective_flow_weight = FAMILY_DECISION_WEIGHT_FLOW * flow.confidence if flow.ready else 0.0
        valid_weight = (
            (FAMILY_DECISION_WEIGHT_PRICE if price.ready else 0.0)
            + (FAMILY_DECISION_WEIGHT_MOMENTUM if momentum.ready else 0.0)
            + (FAMILY_DECISION_WEIGHT_TIMING if timing.ready else 0.0)
            + effective_flow_weight
        )
        weighted_sum = (
            ((price.balance or 0.0) * FAMILY_DECISION_WEIGHT_PRICE if price.ready else 0.0)
            + ((momentum.balance or 0.0) * FAMILY_DECISION_WEIGHT_MOMENTUM if momentum.ready else 0.0)
            + ((timing.balance or 0.0) * FAMILY_DECISION_WEIGHT_TIMING if timing.ready else 0.0)
            + (((flow.balance or 0.0) / max(flow.confidence, 1e-12)) * effective_flow_weight if flow.ready else 0.0)
        )
        family_score = weighted_sum / valid_weight if valid_weight > 0.0 else None

        def flags(f: FamilySnapshot) -> tuple[bool, bool, bool, bool, bool, bool]:
            b = f.balance or 0.0
            return (
                f.ready and b >= weak,
                f.ready and b <= -weak,
                f.ready and b >= healthy,
                f.ready and b <= -healthy,
                f.ready and b >= strong_family,
                f.ready and b <= -strong_family,
            )

        p_up_w, p_dn_w, p_up_h, p_dn_h, p_up_s, p_dn_s = flags(price)
        m_up_w, m_dn_w, m_up_h, m_dn_h, m_up_s, m_dn_s = flags(momentum)
        t_up_w, t_dn_w, t_up_h, t_dn_h, t_up_s, t_dn_s = flags(timing)
        f_up_w, f_dn_w, _, _, f_up_s, f_dn_s = flags(flow)

        up_count = sum((p_up_w, m_up_w, t_up_w, f_up_w))
        down_count = sum((p_dn_w, m_dn_w, t_dn_w, f_dn_w))
        strong_up_count = sum((p_up_s, m_up_s, t_up_s, f_up_s))
        strong_down_count = sum((p_dn_s, m_dn_s, t_dn_s, f_dn_s))

        quality_direction = _sign(family_score or 0.0)
        price_contrib = (price.balance or 0.0) * FAMILY_DECISION_WEIGHT_PRICE if price.ready else 0.0
        momentum_contrib = (momentum.balance or 0.0) * FAMILY_DECISION_WEIGHT_MOMENTUM if momentum.ready else 0.0
        timing_contrib = (timing.balance or 0.0) * FAMILY_DECISION_WEIGHT_TIMING if timing.ready else 0.0
        flow_contrib = (flow.balance or 0.0) * FAMILY_DECISION_WEIGHT_FLOW if flow.ready else 0.0
        family_activity = sum(abs(v) for v in (price_contrib, momentum_contrib, timing_contrib, flow_contrib))
        family_aligned = sum(max(quality_direction * v, 0.0) for v in (price_contrib, momentum_contrib, timing_contrib, flow_contrib))
        agreement = family_aligned / family_activity * 100.0 if quality_direction != 0 and family_activity > 0.0 else 0.0

        core_weight = (FAMILY_DECISION_WEIGHT_PRICE if price.ready else 0.0) + (FAMILY_DECISION_WEIGHT_MOMENTUM if momentum.ready else 0.0)
        core_sum = quality_direction * price_contrib + quality_direction * momentum_contrib
        core_confirmation = _clamp(core_sum / core_weight if core_weight > 0.0 and quality_direction != 0 else 0.0, 0.0, 100.0)
        family_strength = _clamp(abs(family_score or 0.0), 0.0, 100.0)
        family_coverage_quality = _clamp(valid_weight / FAMILY_DECISION_MAX_WEIGHT * 100.0, 0.0, 100.0)

        consistency_weight = 0.0
        consistency_sum = 0.0
        for name, ev in raw.indicators.items():
            if not ev.valid:
                continue
            weight = WEIGHTS[name]
            if name in {"CMF", "OBV"}:
                weight *= raw.volume_trust
            if name == "PRICE_CONTEXT":
                consistency = abs(ev.evidence or 0.0) * 100.0
            else:
                consistency = ev.consistency or 0.0
            consistency_sum += consistency * weight
            consistency_weight += weight
        average_consistency = consistency_sum / consistency_weight if consistency_weight > 0.0 else 0.0

        strong_up_core = sum((p_up_s, m_up_s, f_up_s))
        strong_dn_core = sum((p_dn_s, m_dn_s, f_dn_s))
        strong_opposite_core = strong_dn_core > 0 if quality_direction > 0 else strong_up_core > 0 if quality_direction < 0 else False

        pending_count = sum(
            1
            for name, ev in raw.indicators.items()
            if name != "PRICE_CONTEXT" and ev.direction == 0 and ev.pending_direction != 0 and abs(ev.relative_evidence or 0.0) >= 0.15
        )
        confirmed_count = raw.up_evidence_count + raw.down_evidence_count - pending_count
        quality_base = family_strength * 0.40 + agreement * 0.20 + core_confirmation * 0.20 + family_coverage_quality * 0.10 + average_consistency * 0.10
        quality_penalty = (15.0 if strong_opposite_core else 0.0) + (10.0 if pending_count > max(confirmed_count, 0) else 0.0)
        decision_quality = _clamp(quality_base - quality_penalty, 0.0, 100.0)

        price_momentum_conflict = price.ready and momentum.ready and abs(price.balance or 0.0) >= healthy and abs(momentum.balance or 0.0) >= healthy and _sign(price.balance or 0.0) != _sign(momentum.balance or 0.0)
        strong_split = strong_up_core > 0 and strong_dn_core > 0 and abs(family_score or 0.0) < developing_score
        balanced_split = up_count >= 2 and down_count >= 2 and abs(family_score or 0.0) < healthy_score
        system_conflict = cfg.decision_chart_allowed and (price_momentum_conflict or strong_split or balanced_split)
        timing_mismatch = momentum.ready and timing.ready and abs(momentum.balance or 0.0) >= healthy and abs(timing.balance or 0.0) >= healthy and _sign(momentum.balance or 0.0) != _sign(timing.balance or 0.0)

        strong_opp_up = strong_dn_core > 0
        strong_opp_dn = strong_up_core > 0
        upward_reaction = t_up_h and p_dn_w and (m_up_w or f_up_w) and (family_score or 0.0) >= cfg.reaction_min_score and decision_quality >= cfg.reaction_min_quality and not strong_opp_up
        downward_reaction = t_dn_h and p_up_w and (m_dn_w or f_dn_w) and (family_score or 0.0) <= -cfg.reaction_min_score and decision_quality >= cfg.reaction_min_quality and not strong_opp_dn

        secondary_up_weak = t_up_w or f_up_w
        secondary_dn_weak = t_dn_w or f_dn_w
        secondary_up_strong = t_up_s or f_up_s
        secondary_dn_strong = t_dn_s or f_dn_s
        strong_up_quorum = p_up_h and m_up_s and secondary_up_strong and up_count >= 3 and not strong_opp_up
        strong_dn_quorum = p_dn_h and m_dn_s and secondary_dn_strong and down_count >= 3 and not strong_opp_dn
        healthy_up_quorum = p_up_w and m_up_h and secondary_up_weak and up_count >= 3 and not strong_opp_up
        healthy_dn_quorum = p_dn_w and m_dn_h and secondary_dn_weak and down_count >= 3 and not strong_opp_dn
        developing_up_quorum = m_up_w and secondary_up_weak and up_count >= 2 and not p_dn_h and not strong_opp_up
        developing_dn_quorum = m_dn_w and secondary_dn_weak and down_count >= 2 and not p_up_h and not strong_opp_dn
        pressure_up_quorum = up_count >= 2 and (p_up_w or m_up_w) and not strong_opp_up
        pressure_dn_quorum = down_count >= 2 and (p_dn_w or m_dn_w) and not strong_opp_dn

        if not cfg.decision_chart_allowed:
            base_state = SystemState.SYNTHETIC_BLOCK
        elif raw.valid_evidence_count < 6 or valid_family_count < 3:
            base_state = SystemState.DATA_WAIT
        elif system_conflict:
            base_state = SystemState.CONFLICT
        elif upward_reaction:
            base_state = SystemState.REACTION_UP
        elif downward_reaction:
            base_state = SystemState.REACTION_DOWN
        elif strong_up_quorum and (family_score or 0.0) >= strong_score and decision_quality >= 75.0:
            base_state = SystemState.STRONG_UP
        elif strong_dn_quorum and (family_score or 0.0) <= -strong_score and decision_quality >= 75.0:
            base_state = SystemState.STRONG_DOWN
        elif healthy_up_quorum and (family_score or 0.0) >= healthy_score and decision_quality >= 60.0:
            base_state = SystemState.HEALTHY_UP
        elif healthy_dn_quorum and (family_score or 0.0) <= -healthy_score and decision_quality >= 60.0:
            base_state = SystemState.HEALTHY_DOWN
        elif developing_up_quorum and (family_score or 0.0) >= developing_score:
            base_state = SystemState.DEVELOPING_UP
        elif developing_dn_quorum and (family_score or 0.0) <= -developing_score:
            base_state = SystemState.DEVELOPING_DOWN
        elif pressure_up_quorum and (family_score or 0.0) >= pressure_score:
            base_state = SystemState.PRESSURE_UP
        elif pressure_dn_quorum and (family_score or 0.0) <= -pressure_score:
            base_state = SystemState.PRESSURE_DOWN
        else:
            base_state = SystemState.NEUTRAL

        previous = self._history[-1] if self._history else None
        coverage_stable = False
        if previous and previous.family_decision_valid_weight > 0.0:
            weight_ratio = abs(valid_weight - previous.family_decision_valid_weight) / previous.family_decision_valid_weight
            coverage_stable = valid_family_count == previous.valid_family_count and weight_ratio <= FAMILY_WEIGHT_STABILITY_TOLERANCE
        previous_bullish = previous is not None and previous.system_state in {SystemState.STRONG_UP, SystemState.HEALTHY_UP, SystemState.DEVELOPING_UP, SystemState.REACTION_UP, SystemState.WEAKENING_UP}
        previous_bearish = previous is not None and previous.system_state in {SystemState.STRONG_DOWN, SystemState.HEALTHY_DOWN, SystemState.DEVELOPING_DOWN, SystemState.REACTION_DOWN, SystemState.WEAKENING_DOWN}
        base_bias = _bias(base_state)
        prev_score = previous.family_decision_score if previous else None
        upward_weakening = previous_bullish and base_bias >= 0 and not system_conflict and coverage_stable and (family_score or 0.0) > 5.0 and prev_score is not None and prev_score - (family_score or 0.0) >= cfg.weakening_score_drop
        downward_weakening = previous_bearish and base_bias <= 0 and not system_conflict and coverage_stable and (family_score or 0.0) < -5.0 and prev_score is not None and (family_score or 0.0) - prev_score >= cfg.weakening_score_drop
        state = SystemState.WEAKENING_UP if upward_weakening else SystemState.WEAKENING_DOWN if downward_weakening else base_state
        system_bias = _bias(state)

        if state == SystemState.SYNTHETIC_BLOCK:
            missing = "STANDART MUM GRAFİĞİ"
        elif state == SystemState.DATA_WAIT:
            missing = "10 KANIT VERİ KAPSAMI" if raw.valid_evidence_count < 6 else "AİLE VERİ KAPSAMI" if valid_family_count < 3 else "VERİ HAZIRLANIYOR"
        elif system_bias == 0:
            missing = "KARAR AİLELERİ ÇELİŞİYOR" if state == SystemState.CONFLICT else "AİLE QUORUM'U"
        elif system_bias > 0:
            missing = "FİYAT BAĞLAMI" if (price.balance or 0.0) < weak else "ANA MOMENTUM AİLESİ" if (momentum.balance or 0.0) < healthy else "ZAMANLAMA / PARA AKIŞI" if (timing.balance or 0.0) < weak and (flow.balance or 0.0) < weak else "TERS ÇEKİRDEK AİLE" if strong_opp_up else "ZAMANLAMA TERS — BEKLE" if timing_mismatch else "YOK"
        else:
            missing = "FİYAT BAĞLAMI" if (price.balance or 0.0) > -weak else "ANA MOMENTUM AİLESİ" if (momentum.balance or 0.0) > -healthy else "ZAMANLAMA / PARA AKIŞI" if (timing.balance or 0.0) > -weak and (flow.balance or 0.0) > -weak else "TERS ÇEKİRDEK AİLE" if strong_opp_dn else "ZAMANLAMA TERS — BEKLE" if timing_mismatch else "YOK"

        risk: list[str] = []
        if state == SystemState.SYNTHETIC_BLOCK:
            risk.append("SENTETİK MOTOR KAPALI")
        if system_conflict:
            risk.append("ÇELİŞKİ")
        if timing_mismatch:
            risk.append("ZAMANLAMA TERS")
        if raw.atr_ratio is not None and raw.atr_ratio >= 1.50:
            risk.append("VOLATİLİTE ÇOK YÜKSEK")
        elif raw.atr_ratio is not None and raw.atr_ratio >= 1.25:
            risk.append("VOLATİLİTE YÜKSEK")
        if raw.volume_quality == VolumeQuality.MISSING:
            risk.append("HACİM YOK")
        elif raw.volume_quality == VolumeQuality.LIMITED:
            risk.append("HACİM SINIRLI")

        momentum_state = _family_state(momentum.balance, momentum.activity, momentum.coverage, cfg) if cfg.decision_chart_allowed and momentum.ready else None
        timing_state = _family_state(timing.balance, timing.activity, timing.coverage, cfg) if cfg.decision_chart_allowed and timing.ready else None
        export = HamDashboardExport(
            momentum_state=momentum_state,
            momentum_score=momentum.balance if momentum_state is not None else None,
            timing_state=timing_state,
            timing_score=timing.balance if timing_state is not None else None,
        )

        return HamDashboardDecisionSnapshot(
            timestamp=raw.timestamp,
            data_quality=raw.data_quality,
            raw=raw,
            price_family=price,
            momentum_family=momentum,
            timing_family=timing,
            flow_family=flow,
            family_decision_score=family_score,
            family_decision_valid_weight=valid_weight,
            family_decision_coverage_stable=coverage_stable,
            valid_family_count=valid_family_count,
            up_family_count=up_count,
            down_family_count=down_count,
            strong_up_family_count=strong_up_count,
            strong_down_family_count=strong_down_count,
            decision_quality=decision_quality,
            timing_mismatch=timing_mismatch,
            system_conflict=system_conflict,
            base_system_state=base_state,
            system_state=state,
            system_bias=system_bias,
            missing_evidence=missing,
            risk_flags=tuple(risk),
            export=export,
        )


__all__ = [
    "DecisionConfig",
    "FamilySnapshot",
    "HamDashboardDecisionEngine",
    "HamDashboardDecisionSnapshot",
    "HamDashboardExport",
    "SystemState",
]
