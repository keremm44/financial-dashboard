from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any, Iterable

import pandas as pd

from .raw_indicator_dashboard import (
    RawDataQuality,
    RawIndicatorConfig,
    RawIndicatorDashboardEngine,
    RawIndicatorSnapshot,
    TrendProfile,
)


class HamFamily(StrEnum):
    PRICE = "PRICE"
    MOMENTUM = "MOMENTUM"
    TIMING = "TIMING"
    FLOW = "FLOW"


@dataclass(frozen=True, slots=True)
class FamilySnapshot:
    balance: float | None
    activity: float | None
    coverage: float
    ready: bool
    confidence: float = 1.0


# Public neutral name. FamilySnapshot remains available for exact Tur-2 source parity.
HamFamilyEvidence = FamilySnapshot


@dataclass(frozen=True, slots=True)
class HamFamilyEvidenceSet:
    price: FamilySnapshot
    momentum: FamilySnapshot
    timing: FamilySnapshot
    flow: FamilySnapshot

    def for_family(self, family: HamFamily | str) -> FamilySnapshot:
        normalized = HamFamily(str(family).upper())
        return {
            HamFamily.PRICE: self.price,
            HamFamily.MOMENTUM: self.momentum,
            HamFamily.TIMING: self.timing,
            HamFamily.FLOW: self.flow,
        }[normalized]

    def as_tuple(self) -> tuple[FamilySnapshot, FamilySnapshot, FamilySnapshot, FamilySnapshot]:
        return self.price, self.momentum, self.timing, self.flow

    @property
    def ready_count(self) -> int:
        return sum(int(family.ready) for family in self.as_tuple())


@dataclass(frozen=True, slots=True)
class HamEvidenceConfig:
    minimum_family_coverage: float = 75.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_family_coverage <= 100.0:
            raise ValueError("minimum_family_coverage must be in [0, 100]")


@dataclass(frozen=True, slots=True)
class HamEvidenceSnapshot:
    """Action-free Ham evidence for one confirmed bar or one transient preview."""

    raw: RawIndicatorSnapshot
    families: HamFamilyEvidenceSet

    @property
    def timestamp(self) -> Any | None:
        return self.raw.timestamp

    @property
    def data_quality(self) -> RawDataQuality:
        return self.raw.data_quality

    @property
    def profile_ready(self) -> bool:
        return self.raw.data_quality == RawDataQuality.OK

    @property
    def indicator_count(self) -> int:
        return len(self.raw.indicators)

    @property
    def ready_family_count(self) -> int:
        return self.families.ready_count


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _norm_balance(value: float | None, maximum: float) -> float | None:
    if value is None:
        return None
    return _clamp(value / max(maximum, 1e-6), -1.0, 1.0) * 100.0


def _norm_activity(value: float | None, maximum: float) -> float | None:
    if value is None:
        return None
    return _clamp(value / max(maximum, 1e-6), 0.0, 1.0) * 100.0


def _weighted_average(items: Iterable[tuple[float | None, float]]) -> tuple[float | None, float]:
    total_weight = 0.0
    total = 0.0
    for value, weight in items:
        if value is None or weight <= 0.0:
            continue
        total += value * weight
        total_weight += weight
    return (total / total_weight if total_weight > 0.0 else None), total_weight


WEIGHTS: dict[str, float] = {
    "PRICE_CONTEXT": 1.50,
    "MACD": 1.30,
    "MOMENTUM": 1.30,
    "RSI": 1.05,
    "CCI": 0.90,
    "SMI": 0.80,
    "CMF": 0.75,
    "OBV": 0.75,
    "STOCHASTIC": 0.55,
    "STOCH_RSI": 0.55,
}

MOMENTUM_ROLE_WEIGHT_IMPULSE = 0.50
MOMENTUM_ROLE_WEIGHT_OSCILLATOR = 0.50


def build_ham_family_evidence(
    raw: RawIndicatorSnapshot,
    *,
    minimum_family_coverage: float = 75.0,
) -> HamFamilyEvidenceSet:
    """Extract the exact Tur-2 family math without producing a decision."""

    if not isfinite(float(minimum_family_coverage)) or not 0.0 <= minimum_family_coverage <= 100.0:
        raise ValueError("minimum_family_coverage must be finite and in [0, 100]")

    ind = raw.indicators
    price = ind.get("PRICE_CONTEXT")
    price_balance = _norm_balance(price.evidence if price and price.valid else None, 1.0)
    price_activity = abs(price_balance) if price_balance is not None else None
    price_coverage = 100.0 if price and price.valid else 0.0
    price_family = FamilySnapshot(
        price_balance,
        price_activity,
        price_coverage,
        price_coverage >= minimum_family_coverage,
    )

    impulse_balance, impulse_weight = _weighted_average([
        (ind["MACD"].evidence if ind.get("MACD") and ind["MACD"].valid else None, WEIGHTS["MACD"]),
        (ind["MOMENTUM"].evidence if ind.get("MOMENTUM") and ind["MOMENTUM"].valid else None, WEIGHTS["MOMENTUM"]),
    ])
    impulse_activity, _ = _weighted_average([
        (abs(ind["MACD"].evidence or 0.0) if ind.get("MACD") and ind["MACD"].valid else None, WEIGHTS["MACD"]),
        (abs(ind["MOMENTUM"].evidence or 0.0) if ind.get("MOMENTUM") and ind["MOMENTUM"].valid else None, WEIGHTS["MOMENTUM"]),
    ])
    impulse_coverage = impulse_weight / (WEIGHTS["MACD"] + WEIGHTS["MOMENTUM"]) * 100.0

    osc_balance, osc_weight = _weighted_average([
        (ind["RSI"].evidence if ind.get("RSI") and ind["RSI"].valid else None, WEIGHTS["RSI"]),
        (ind["CCI"].evidence if ind.get("CCI") and ind["CCI"].valid else None, WEIGHTS["CCI"]),
        (ind["SMI"].evidence if ind.get("SMI") and ind["SMI"].valid else None, WEIGHTS["SMI"]),
    ])
    osc_activity, _ = _weighted_average([
        (abs(ind["RSI"].evidence or 0.0) if ind.get("RSI") and ind["RSI"].valid else None, WEIGHTS["RSI"]),
        (abs(ind["CCI"].evidence or 0.0) if ind.get("CCI") and ind["CCI"].valid else None, WEIGHTS["CCI"]),
        (abs(ind["SMI"].evidence or 0.0) if ind.get("SMI") and ind["SMI"].valid else None, WEIGHTS["SMI"]),
    ])
    osc_coverage = osc_weight / (WEIGHTS["RSI"] + WEIGHTS["CCI"] + WEIGHTS["SMI"]) * 100.0

    role_items: list[tuple[float | None, float]] = []
    role_activity_items: list[tuple[float | None, float]] = []
    if impulse_weight > 0.0:
        role_items.append((impulse_balance, MOMENTUM_ROLE_WEIGHT_IMPULSE))
        role_activity_items.append((impulse_activity, MOMENTUM_ROLE_WEIGHT_IMPULSE))
    if osc_weight > 0.0:
        role_items.append((osc_balance, MOMENTUM_ROLE_WEIGHT_OSCILLATOR))
        role_activity_items.append((osc_activity, MOMENTUM_ROLE_WEIGHT_OSCILLATOR))
    momentum_core, _ = _weighted_average(role_items)
    momentum_activity_core, _ = _weighted_average(role_activity_items)
    momentum_balance = _norm_balance(momentum_core, 1.0)
    momentum_activity = _norm_activity(momentum_activity_core, 1.0)
    momentum_coverage = impulse_coverage * 0.50 + osc_coverage * 0.50
    momentum_family = FamilySnapshot(
        momentum_balance,
        momentum_activity,
        momentum_coverage,
        momentum_coverage >= minimum_family_coverage,
    )

    timing_balance_core, timing_weight = _weighted_average([
        (ind["STOCHASTIC"].evidence if ind.get("STOCHASTIC") and ind["STOCHASTIC"].valid else None, WEIGHTS["STOCHASTIC"]),
        (ind["STOCH_RSI"].evidence if ind.get("STOCH_RSI") and ind["STOCH_RSI"].valid else None, WEIGHTS["STOCH_RSI"]),
    ])
    timing_activity_core, _ = _weighted_average([
        (abs(ind["STOCHASTIC"].evidence or 0.0) if ind.get("STOCHASTIC") and ind["STOCHASTIC"].valid else None, WEIGHTS["STOCHASTIC"]),
        (abs(ind["STOCH_RSI"].evidence or 0.0) if ind.get("STOCH_RSI") and ind["STOCH_RSI"].valid else None, WEIGHTS["STOCH_RSI"]),
    ])
    timing_balance = _norm_balance(timing_balance_core, 0.65)
    timing_activity = _norm_activity(timing_activity_core, 0.65)
    timing_coverage = timing_weight / (WEIGHTS["STOCHASTIC"] + WEIGHTS["STOCH_RSI"]) * 100.0
    timing_family = FamilySnapshot(
        timing_balance,
        timing_activity,
        timing_coverage,
        timing_coverage >= minimum_family_coverage,
    )

    flow_balance_core, flow_weight = _weighted_average([
        (ind["CMF"].evidence if ind.get("CMF") and ind["CMF"].valid else None, WEIGHTS["CMF"]),
        (ind["OBV"].evidence if ind.get("OBV") and ind["OBV"].valid else None, WEIGHTS["OBV"]),
    ])
    flow_activity_core, _ = _weighted_average([
        (abs(ind["CMF"].evidence or 0.0) if ind.get("CMF") and ind["CMF"].valid else None, WEIGHTS["CMF"]),
        (abs(ind["OBV"].evidence or 0.0) if ind.get("OBV") and ind["OBV"].valid else None, WEIGHTS["OBV"]),
    ])
    flow_core_normalized = _norm_balance(flow_balance_core, 1.0)
    flow_activity_normalized = _norm_activity(flow_activity_core, 1.0)
    flow_confidence = raw.volume_trust
    flow_balance = None if flow_core_normalized is None else flow_core_normalized * flow_confidence
    flow_activity = None if flow_activity_normalized is None else flow_activity_normalized * flow_confidence
    flow_coverage = flow_weight / (WEIGHTS["CMF"] + WEIGHTS["OBV"]) * 100.0
    flow_ready = (
        raw.volume_calculable
        and flow_coverage >= minimum_family_coverage
        and flow_confidence >= 0.05
    )
    flow_family = FamilySnapshot(
        flow_balance,
        flow_activity,
        flow_coverage,
        flow_ready,
        flow_confidence,
    )
    return HamFamilyEvidenceSet(
        price=price_family,
        momentum=momentum_family,
        timing=timing_family,
        flow=flow_family,
    )


class HamEvidenceEngine:
    """Causal Tur-1 + neutral family evidence with immutable confirmed history."""

    def __init__(
        self,
        raw_config: RawIndicatorConfig | None = None,
        evidence_config: HamEvidenceConfig | None = None,
    ) -> None:
        self.raw_config = raw_config or RawIndicatorConfig()
        self.evidence_config = evidence_config or HamEvidenceConfig()
        self._raw_engine = RawIndicatorDashboardEngine(self.raw_config)
        self._history: list[HamEvidenceSnapshot] = []
        self._snapshot: HamEvidenceSnapshot | None = None

    @property
    def profile(self) -> TrendProfile:
        return self.raw_config.profile

    @property
    def history(self) -> tuple[HamEvidenceSnapshot, ...]:
        return tuple(self._history)

    @property
    def snapshot(self) -> HamEvidenceSnapshot | None:
        return self._snapshot

    def reset(self) -> None:
        self._raw_engine.reset()
        self._history.clear()
        self._snapshot = None

    def _compose(self, raw: RawIndicatorSnapshot) -> HamEvidenceSnapshot:
        return HamEvidenceSnapshot(
            raw=raw,
            families=build_ham_family_evidence(
                raw,
                minimum_family_coverage=self.evidence_config.minimum_family_coverage,
            ),
        )

    def update(self, bar: pd.Series | dict[str, Any]) -> HamEvidenceSnapshot:
        row = dict(bar)
        raw = self._raw_engine.update(row)
        result = self._compose(raw)
        if bool(row.get("is_closed", True)) and bool(row.get("is_complete", True)):
            self._history.append(result)
            self._snapshot = result
        return result

    def replay(self, frame: pd.DataFrame) -> tuple[HamEvidenceSnapshot, ...]:
        self.reset()
        records = frame.to_dict("records")
        raw_results = self._raw_engine.replay(frame)
        results: list[HamEvidenceSnapshot] = []
        for row, raw in zip(records, raw_results):
            result = self._compose(raw)
            results.append(result)
            if bool(row.get("is_closed", True)) and bool(row.get("is_complete", True)):
                self._history.append(result)
                self._snapshot = result
        return tuple(results)


__all__ = [
    "FamilySnapshot",
    "HamEvidenceConfig",
    "HamEvidenceEngine",
    "HamEvidenceSnapshot",
    "HamFamily",
    "HamFamilyEvidence",
    "HamFamilyEvidenceSet",
    "WEIGHTS",
    "build_ham_family_evidence",
]
