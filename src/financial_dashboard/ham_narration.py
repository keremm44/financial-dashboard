from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import Any

from financial_dashboard.ham_support import HamAdjustedConfidence


HAM_NARRATION_SCHEMA = "financial-dashboard.ham-narration.v1"
HAM_NARRATION_AUTHORITY = "DETERMINISTIC_CORE_AND_HAM_CONTRACT"
HAM_NARRATION_MODE = "RENDER_FIXED_FACTS_ONLY"
HAM_NARRATION_PROHIBITIONS: tuple[str, ...] = (
    "NO_INDEPENDENT_CALCULATION",
    "NO_FACT_MODIFICATION",
    "NO_ACTION_OR_STATUS_INFERENCE",
    "NO_PREDICTION",
    "NO_RECOMMENDATION",
)


@dataclass(frozen=True, slots=True)
class HamNarrationDecisionFacts:
    direction: str
    action: str
    status: str
    core_confidence: float
    ham_delta: float
    applied_delta: float
    final_confidence: float
    hard_blockers: tuple[str, ...]
    market_structure: str
    support_resistance: str
    risks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HamNarrationFamilyFacts:
    family: str
    balance: float | None
    activity: float | None
    coverage: float
    ready: bool
    directional_alignment: float | None
    effective_weight: float
    weighted_alignment: float


@dataclass(frozen=True, slots=True)
class HamNarrationTimeframeFacts:
    timeframe: str
    available: bool
    as_of: str | None
    source_quality: str
    raw_quality: str
    profile_ready: bool
    volume_quality: str
    volume_trust: float | None
    directional_score: float
    evidence_coverage: float
    families: tuple[HamNarrationFamilyFacts, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HamNarrationSupportFacts:
    alignment: str
    directional_score: float
    evidence_coverage: float
    max_abs_delta: float
    reasons: tuple[str, ...]
    timeframes: tuple[HamNarrationTimeframeFacts, ...]


@dataclass(frozen=True, slots=True)
class HamNarrationPolicy:
    authority: str = HAM_NARRATION_AUTHORITY
    mode: str = HAM_NARRATION_MODE
    prohibitions: tuple[str, ...] = HAM_NARRATION_PROHIBITIONS


@dataclass(frozen=True, slots=True)
class HamNarrationPayload:
    """Canonical facts for a future renderer; it contains no generated prose."""

    symbol: str
    as_of: str
    decision: HamNarrationDecisionFacts
    ham: HamNarrationSupportFacts
    schema: str = HAM_NARRATION_SCHEMA
    policy: HamNarrationPolicy = HamNarrationPolicy()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            indent=indent,
            separators=separators,
        )

    @property
    def fingerprint(self) -> str:
        return sha256(self.to_json().encode("utf-8")).hexdigest()


def _text(value: object, *, field: str) -> str:
    if isinstance(value, Enum):
        raw = value.value
        return str(raw)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty")
        return normalized
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"{field} must be finite")
        return str(value)
    raise TypeError(f"{field} must be a scalar string, number, or enum")


def _timestamp(value: object, *, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} must not be None")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        rendered = isoformat()
        if isinstance(rendered, str) and rendered:
            return rendered
    return _text(value, field=field)


def _optional_timestamp(value: object) -> str | None:
    return None if value is None else _timestamp(value, field="Ham timeframe timestamp")


def _text_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be an immutable tuple")
    return tuple(_text(item, field=f"{field} item") for item in value)


def _required_core_attribute(core: object, name: str) -> object:
    if not hasattr(core, name):
        raise TypeError(
            f"core decision must expose authoritative {name!r} for narration"
        )
    return getattr(core, name)


def build_ham_narration_payload(
    adjusted: HamAdjustedConfidence[Any],
    *,
    symbol: str,
    as_of: object,
) -> HamNarrationPayload:
    """Freeze deterministic core + Ham facts for a later render-only narrator.

    The builder deliberately requires action, status, blockers, Market Structure,
    S/R, and risks from the authoritative core object. It never derives them from
    Ham evidence and never calls an LLM.
    """

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must not be empty")
    core = adjusted.core
    assessment = adjusted.assessment

    core_direction = _required_core_attribute(core, "direction")
    if core_direction != assessment.core_direction:
        raise ValueError("core direction changed after Ham assessment")
    core_confidence = float(_required_core_attribute(core, "confidence"))
    if core_confidence != adjusted.core_confidence:
        raise ValueError("core confidence changed after Ham assessment")

    decision = HamNarrationDecisionFacts(
        direction=assessment.core_direction.name,
        action=_text(_required_core_attribute(core, "action"), field="action"),
        status=_text(_required_core_attribute(core, "status"), field="status"),
        core_confidence=adjusted.core_confidence,
        ham_delta=adjusted.ham_delta,
        applied_delta=adjusted.applied_delta,
        final_confidence=adjusted.final_confidence,
        hard_blockers=_text_tuple(
            _required_core_attribute(core, "hard_blockers"),
            field="hard_blockers",
        ),
        market_structure=_text(
            _required_core_attribute(core, "market_structure"),
            field="market_structure",
        ),
        support_resistance=_text(
            _required_core_attribute(core, "support_resistance"),
            field="support_resistance",
        ),
        risks=_text_tuple(
            _required_core_attribute(core, "risks"),
            field="risks",
        ),
    )

    timeframe_facts = tuple(
        HamNarrationTimeframeFacts(
            timeframe=timeframe.timeframe,
            available=timeframe.available,
            as_of=_optional_timestamp(timeframe.timestamp),
            source_quality=timeframe.source_quality,
            raw_quality=(
                "MISSING"
                if timeframe.raw_quality is None
                else timeframe.raw_quality.value
            ),
            profile_ready=timeframe.profile_ready,
            volume_quality=timeframe.volume_quality,
            volume_trust=timeframe.volume_trust,
            directional_score=timeframe.directional_score,
            evidence_coverage=timeframe.evidence_coverage,
            families=tuple(
                HamNarrationFamilyFacts(
                    family=family.family.value,
                    balance=family.balance,
                    activity=family.activity,
                    coverage=family.coverage,
                    ready=family.ready,
                    directional_alignment=family.directional_alignment,
                    effective_weight=family.effective_weight,
                    weighted_alignment=family.weighted_alignment,
                )
                for family in timeframe.families
            ),
            reasons=timeframe.reasons,
        )
        for timeframe in assessment.timeframes
    )
    ham = HamNarrationSupportFacts(
        alignment=assessment.alignment.value,
        directional_score=assessment.directional_score,
        evidence_coverage=assessment.evidence_coverage,
        max_abs_delta=assessment.max_abs_delta,
        reasons=assessment.reasons,
        timeframes=timeframe_facts,
    )
    return HamNarrationPayload(
        symbol=normalized_symbol,
        as_of=_timestamp(as_of, field="as_of"),
        decision=decision,
        ham=ham,
    )


__all__ = [
    "HAM_NARRATION_AUTHORITY",
    "HAM_NARRATION_MODE",
    "HAM_NARRATION_PROHIBITIONS",
    "HAM_NARRATION_SCHEMA",
    "HamNarrationDecisionFacts",
    "HamNarrationFamilyFacts",
    "HamNarrationPayload",
    "HamNarrationPolicy",
    "HamNarrationSupportFacts",
    "HamNarrationTimeframeFacts",
    "build_ham_narration_payload",
]
