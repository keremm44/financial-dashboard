from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Mapping


class EvidenceRole(StrEnum):
    CONTEXT = "CONTEXT"
    STRUCTURE = "STRUCTURE"
    LOCATION = "LOCATION"
    TRIGGER = "TRIGGER"
    CONFIRMATION = "CONFIRMATION"
    TIMING = "TIMING"
    RISK = "RISK"


class EvidenceFamily(StrEnum):
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    PATTERN = "PATTERN"
    MTF_STORY = "MTF_STORY"
    LIQUIDITY = "LIQUIDITY"
    AUCTION = "AUCTION"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    VOLUME = "VOLUME"
    STABIL = "STABIL"
    VOLATILITY = "VOLATILITY"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    MOMENTUM = "MOMENTUM"
    TIMING = "TIMING"


class EvidenceDirection(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


class ProvenanceType(StrEnum):
    ROOT = "ROOT"
    DERIVED = "DERIVED"
    AGGREGATED = "AGGREGATED"
    CONTEXTUAL = "CONTEXTUAL"


class EvidenceDataQuality(StrEnum):
    OK = "OK"
    WARMUP = "WARMUP"
    INCOMPLETE_BAR = "INCOMPLETE_BAR"
    SOURCE_GAP = "SOURCE_GAP"
    DATA_LIMITED = "DATA_LIMITED"
    DATA_INVALID = "DATA_INVALID"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NormalizedLevel:
    id: str
    source_engine: str
    level_type: str
    timeframe: str
    price: float | None = None
    lower: float | None = None
    upper: float | None = None
    polarity: EvidenceDirection = EvidenceDirection.NEUTRAL
    quality: float | None = None
    freshness: float | None = None
    source_bar: int | None = None
    known_bar: int | None = None
    timestamp: Any | None = None
    state: str | int | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.price is None and self.lower is None and self.upper is None:
            raise ValueError("normalized level requires price or zone bounds")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("normalized level lower cannot exceed upper")
        _validate_unit_interval("freshness", self.freshness)
        _validate_score("quality", self.quality)


@dataclass(frozen=True, slots=True)
class TechnicalEvidenceItem:
    id: str
    source_engine: str
    evidence_type: str
    timeframe: str
    role: EvidenceRole
    family: EvidenceFamily
    direction: EvidenceDirection = EvidenceDirection.NEUTRAL
    strength: float | None = None
    quality: float | None = None
    freshness: float | None = None
    data_quality: EvidenceDataQuality = EvidenceDataQuality.UNKNOWN
    source_data_quality: str | None = None
    source_bar: int | None = None
    known_bar: int | None = None
    timestamp: Any | None = None
    level_refs: tuple[str, ...] = ()
    provenance_type: ProvenanceType = ProvenanceType.ROOT
    depends_on: tuple[str, ...] = ()
    source_state: str | int | None = None
    raw_export: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_score("strength", self.strength)
        _validate_score("quality", self.quality)
        _validate_unit_interval("freshness", self.freshness)
        if self.source_bar is not None and self.known_bar is not None and self.source_bar > self.known_bar:
            raise ValueError("source_bar cannot be after known_bar")
        if self.id in self.depends_on:
            raise ValueError("evidence cannot depend on itself")


@dataclass(frozen=True, slots=True)
class TechnicalEvidencePacket:
    timeframe: str
    known_bar: int | None
    timestamp: Any | None
    evidence: tuple[TechnicalEvidenceItem, ...] = ()
    levels: tuple[NormalizedLevel, ...] = ()

    def __post_init__(self) -> None:
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate technical evidence id")
        level_ids = [level.id for level in self.levels]
        if len(level_ids) != len(set(level_ids)):
            raise ValueError("duplicate normalized level id")
        valid_levels = set(level_ids)
        dangling = sorted({ref for item in self.evidence for ref in item.level_refs if ref not in valid_levels})
        if dangling:
            raise ValueError(f"dangling normalized level references: {dangling}")

    def level_by_id(self, level_id: str) -> NormalizedLevel | None:
        return next((level for level in self.levels if level.id == level_id), None)

    def evidence_by_id(self, evidence_id: str) -> TechnicalEvidenceItem | None:
        return next((item for item in self.evidence if item.id == evidence_id), None)


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    timeframe: str
    known_bar: int | None
    timestamp: Any | None
    source_data_quality: str | None = None
    is_closed: bool = True
    is_complete: bool = True

    @property
    def can_advance(self) -> bool:
        return self.is_closed and self.is_complete


class TechnicalEvidenceBuilder:
    """Small state guard for confirmed TEL packets.

    Tur-1 deliberately contains no decision logic. The builder only freezes the
    last confirmed packet when the current source bar is open or incomplete.
    """

    def __init__(self) -> None:
        self._snapshot: TechnicalEvidencePacket | None = None

    def reset(self) -> None:
        self._snapshot = None

    def snapshot(self) -> TechnicalEvidencePacket | None:
        return self._snapshot

    def update(self, context: EvidenceContext, packet: TechnicalEvidencePacket) -> TechnicalEvidencePacket | None:
        if not context.can_advance:
            return self._snapshot
        if packet.timeframe != context.timeframe:
            raise ValueError("packet timeframe must match evidence context")
        if packet.known_bar != context.known_bar:
            raise ValueError("packet known_bar must match evidence context")
        self._snapshot = packet
        return packet


def normalize_data_quality(value: Any) -> tuple[EvidenceDataQuality, str | None]:
    if value is None:
        return EvidenceDataQuality.UNKNOWN, None
    raw = getattr(value, "value", value)
    text = str(raw).upper()
    aliases = {
        "OK": EvidenceDataQuality.OK,
        "DATA_OK": EvidenceDataQuality.OK,
        "WARMUP": EvidenceDataQuality.WARMUP,
        "INCOMPLETE_BAR": EvidenceDataQuality.INCOMPLETE_BAR,
        "OPEN_OR_INCOMPLETE_BAR": EvidenceDataQuality.INCOMPLETE_BAR,
        "SOURCE_GAP": EvidenceDataQuality.SOURCE_GAP,
        "UPSTREAM_OR_COMMON_MARKET_DATA_GAP": EvidenceDataQuality.SOURCE_GAP,
        "DATA_LIMITED": EvidenceDataQuality.DATA_LIMITED,
        "LIMITED": EvidenceDataQuality.DATA_LIMITED,
        "DATA_INVALID": EvidenceDataQuality.DATA_INVALID,
        "INVALID": EvidenceDataQuality.DATA_INVALID,
        "UNSUPPORTED_TIMEFRAME": EvidenceDataQuality.UNSUPPORTED_TIMEFRAME,
    }
    return aliases.get(text, EvidenceDataQuality.UNKNOWN), str(raw)


def direction_from_value(value: Any) -> EvidenceDirection:
    raw = getattr(value, "value", value)
    if raw is None:
        return EvidenceDirection.NEUTRAL
    if isinstance(raw, str):
        text = raw.upper()
        if text in {"UP", "BULL", "BULLISH", "+1", "1"}:
            return EvidenceDirection.BULL
        if text in {"DOWN", "BEAR", "BEARISH", "-1"}:
            return EvidenceDirection.BEAR
        return EvidenceDirection.NEUTRAL
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return EvidenceDirection.NEUTRAL
    if number > 0:
        return EvidenceDirection.BULL
    if number < 0:
        return EvidenceDirection.BEAR
    return EvidenceDirection.NEUTRAL


def score_0_100(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number))


def signed_strength(value: Any) -> tuple[EvidenceDirection, float | None]:
    if value is None:
        return EvidenceDirection.NEUTRAL, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return EvidenceDirection.NEUTRAL, None
    return direction_from_value(number), max(0.0, min(100.0, abs(number)))


def make_level_id(
    *,
    source_engine: str,
    level_type: str,
    timeframe: str,
    source_bar: int | None,
    known_bar: int | None,
    price: float | None,
    lower: float | None,
    upper: float | None,
) -> str:
    return _stable_id(
        "level",
        source_engine,
        level_type,
        timeframe,
        source_bar,
        known_bar,
        price,
        lower,
        upper,
    )


def make_evidence_id(
    *,
    source_engine: str,
    evidence_type: str,
    timeframe: str,
    source_bar: int | None,
    known_bar: int | None,
    timestamp: Any | None,
) -> str:
    return _stable_id(
        "evidence",
        source_engine,
        evidence_type,
        timeframe,
        source_bar,
        known_bar,
        _timestamp_text(timestamp),
    )


def plain_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and value.__class__.__module__ == "enum":
        return plain_payload(value.value)
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: plain_payload(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if isinstance(value, Mapping):
        return {str(key): plain_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain_payload(item) for item in value]
    if hasattr(value, "value"):
        return plain_payload(value.value)
    return str(value)


def _validate_score(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= float(value) <= 100.0:
        raise ValueError(f"{name} must be within 0..100")


def _validate_unit_interval(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be within 0..1")


def _timestamp_text(value: Any | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _stable_id(*parts: Any) -> str:
    body = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    return sha256(body.encode("utf-8")).hexdigest()[:24]
