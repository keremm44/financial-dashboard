from __future__ import annotations

from dataclasses import dataclass

from financial_dashboard.data.quality import DataQualityStatus

from .market_structure_evidence import HANDSHAKE, MarketStructureExport
from .models import Direction, EngineResult
from .mtf_story_models import RawTimeframeEvidence, TimeframeStoryState
from .pattern_compression_engine import PatternExport


_MARKET_STATE_CODE_TO_NAME: dict[float, str] = {
    2.0: "STATE_BULLISH",
    1.0: "STATE_TRANSITION_UP",
    0.0: "STATE_NEUTRAL",
    -1.0: "STATE_TRANSITION_DOWN",
    -2.0: "STATE_BEARISH",
}

_PATTERN_STATE_CODE_TO_NAME: dict[int, str] = {
    0: "FORMASYON_YOK",
    1: "ADAY_OLUSUYOR",
    2: "GEOMETRI_ADAYI",
    3: "FORMASYON_TANIMLANDI",
    4: "OLGUNLASIYOR",
    5: "SIKISMA_GUCLENIYOR",
    6: "KIRILIM_HAZIRLIGI",
    7: "KIRILIM_DENEMESI",
    8: "KIRILIM_ADAYI",
    9: "KIRILIM_TEYITLI",
    10: "RETEST_BEKLENIYOR",
    11: "RETEST_EDILIYOR",
    12: "RETEST_BASARILI",
    13: "FORMASYON_TAMAMLANDI",
    14: "KIRILIM_TEYIT_ALAMADI",
    15: "BASARISIZ_KIRILIM",
    16: "FORMASYON_ZAYIFLADI",
    17: "FORMASYON_GECERSIZ",
}

_PATTERN_TYPE_CODE_TO_NAME: dict[int, str] = {
    0: "Yok",
    1: "Yükselen Üçgen",
    2: "Alçalan Üçgen",
    3: "Simetrik Üçgen",
    4: "Yükselen Kama",
    5: "Alçalan Kama",
    6: "Boğa Bayrağı",
    7: "Ayı Bayrağı",
    8: "Boğa Flaması",
    9: "Ayı Flaması",
}


class MTFStoryNormalizationError(ValueError):
    pass


def _direction(value: int | float | None) -> Direction:
    if value is None:
        return Direction.NEUTRAL
    numeric = float(value)
    if numeric > 0:
        return Direction.UP
    if numeric < 0:
        return Direction.DOWN
    return Direction.NEUTRAL


def _validate_engine_result(result: EngineResult | None, expected_engine: str) -> None:
    if result is not None and result.engine != expected_engine:
        raise MTFStoryNormalizationError(
            f"expected {expected_engine} EngineResult, got {result.engine}"
        )


def _market_export(export: object | None) -> MarketStructureExport | None:
    if export is None:
        return None
    if not isinstance(export, MarketStructureExport):
        raise MTFStoryNormalizationError(
            f"expected MarketStructureExport, got {type(export).__name__}"
        )
    if export.handshake != HANDSHAKE:
        raise MTFStoryNormalizationError("invalid Market Structure export handshake")
    return export


def _pattern_export(export: object | None) -> PatternExport | None:
    if export is None:
        return None
    if not isinstance(export, PatternExport):
        raise MTFStoryNormalizationError(
            f"expected PatternExport, got {type(export).__name__}"
        )
    return export


def _decode_market_state(code: float | None) -> str | None:
    if code is None:
        return None
    key = float(code)
    if key not in _MARKET_STATE_CODE_TO_NAME:
        raise MTFStoryNormalizationError(f"unsupported Market Structure state code: {code}")
    return _MARKET_STATE_CODE_TO_NAME[key]


def _decode_pattern_state(code: int | None) -> str | None:
    if code is None:
        return None
    key = int(code)
    if key not in _PATTERN_STATE_CODE_TO_NAME:
        raise MTFStoryNormalizationError(f"unsupported Pattern state code: {code}")
    return _PATTERN_STATE_CODE_TO_NAME[key]


def _decode_pattern_type(code: int | None) -> str | None:
    if code is None:
        return None
    key = int(code)
    if key not in _PATTERN_TYPE_CODE_TO_NAME:
        raise MTFStoryNormalizationError(f"unsupported Pattern type code: {code}")
    return _PATTERN_TYPE_CODE_TO_NAME[key]


def _breakout_direction(export: PatternExport | None) -> Direction:
    if export is None or export.break_state in (None, 0):
        return Direction.NEUTRAL
    return _direction(export.break_state)


def normalize_timeframe_evidence(evidence: RawTimeframeEvidence) -> TimeframeStoryState:
    """Translate raw engine contracts into one normalized timeframe state.

    The translation is intentionally lossless with respect to direction semantics:
    structural direction, classic pattern direction and actual breakout direction are
    preserved as separate fields. No context/trigger classification is performed here.
    """

    _validate_engine_result(evidence.market_structure, "market_structure")
    _validate_engine_result(evidence.pattern_compression, "pattern_compression")
    ms_export = _market_export(evidence.market_structure_export)
    pattern_export = _pattern_export(evidence.pattern_export)

    reasons: list[str] = []

    ms = evidence.market_structure
    structural_direction = ms.direction if ms is not None else Direction.NEUTRAL
    structural_state = ms.state if ms is not None else None
    structural_score = ms.score if ms is not None else None
    structural_quality = ms.quality if ms is not None else None
    timestamp = ms.timestamp if ms is not None else None

    if ms_export is not None:
        exported_state = _decode_market_state(ms_export.external_state)
        if structural_state is None:
            structural_state = exported_state
            structural_direction = _direction(ms_export.external_state)
        elif exported_state is not None and exported_state != structural_state:
            reasons.append(
                f"MARKET_STATE_EXPORT_MISMATCH:{structural_state}!={exported_state}"
            )
        if structural_score is None:
            structural_score = ms_export.evidence_score

    pattern = evidence.pattern_compression
    pattern_direction = pattern.direction if pattern is not None else Direction.NEUTRAL
    pattern_state = pattern.state if pattern is not None else None
    pattern_quality = pattern.quality if pattern is not None else None
    if timestamp is None and pattern is not None:
        timestamp = pattern.timestamp

    pattern_classic_direction = Direction.NEUTRAL
    breakout_direction = Direction.NEUTRAL
    pattern_type = None

    if pattern_export is not None:
        decoded_state = _decode_pattern_state(pattern_export.state)
        decoded_type = _decode_pattern_type(pattern_export.pattern_type)
        pattern_type = decoded_type
        pattern_classic_direction = _direction(pattern_export.classic_direction)
        breakout_direction = _breakout_direction(pattern_export)

        if pattern_state is None:
            pattern_state = decoded_state
        elif decoded_state is not None and decoded_state != pattern_state:
            reasons.append(
                f"PATTERN_STATE_EXPORT_MISMATCH:{pattern_state}!={decoded_state}"
            )

        if pattern_quality is None:
            pattern_quality = pattern_export.quality

        if pattern is None:
            pattern_direction = breakout_direction if breakout_direction is not Direction.NEUTRAL else pattern_classic_direction
        elif breakout_direction is not Direction.NEUTRAL and pattern_direction is not breakout_direction:
            reasons.append(
                f"PATTERN_DIRECTION_EXPORT_MISMATCH:{int(pattern_direction)}!={int(breakout_direction)}"
            )

    if evidence.data_quality is DataQualityStatus.LIMITED:
        reasons.append("DATA_LIMITED")
    elif evidence.data_quality is DataQualityStatus.INVALID:
        reasons.append("DATA_INVALID")

    return TimeframeStoryState(
        timeframe=evidence.timeframe,
        role=evidence.role,
        data_quality=evidence.data_quality,
        timestamp=timestamp,
        structural_direction=structural_direction,
        structural_state=structural_state,
        structural_score=structural_score,
        structural_quality=structural_quality,
        pattern_direction=pattern_direction,
        pattern_classic_direction=pattern_classic_direction,
        pattern_state=pattern_state,
        pattern_type=pattern_type,
        pattern_quality=pattern_quality,
        breakout_direction=breakout_direction,
        reasons=tuple(reasons),
    )
