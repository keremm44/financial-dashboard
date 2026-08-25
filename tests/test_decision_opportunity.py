from types import SimpleNamespace

import pytest

from financial_dashboard.decision.opportunity import OpportunityCalibration, OpportunityState, assess_opportunity
from financial_dashboard.decision.structural import StructuralDirection


def _target(room_atr: float):
    evidence = (SimpleNamespace(origin_event_id="ORIGIN:1"), SimpleNamespace(origin_event_id="ORIGIN:1"))
    return SimpleNamespace(identity="TARGET:1", distance_atr=room_atr, quality="SUPPORTED", evidence=evidence)


def _snapshot(room_atr: float | None):
    target = None if room_atr is None else _target(room_atr)
    return SimpleNamespace(nearest_upside_target=target, nearest_downside_target=target)


def _calibration():
    return OpportunityCalibration(none_max_atr=0.2, compressed_max_atr=0.6, moderate_max_atr=1.2)


def test_no_calibration_means_unknown_not_magic_threshold():
    result = assess_opportunity(StructuralDirection.LONG, _snapshot(0.4), calibration=None)
    assert result.state is OpportunityState.UNKNOWN
    assert result.room_atr == 0.4


def test_directional_room_categories_use_explicit_class_c_boundaries():
    assert assess_opportunity(StructuralDirection.LONG, _snapshot(0.1), calibration=_calibration()).state is OpportunityState.NONE
    assert assess_opportunity(StructuralDirection.LONG, _snapshot(0.4), calibration=_calibration()).state is OpportunityState.COMPRESSED
    assert assess_opportunity(StructuralDirection.LONG, _snapshot(0.9), calibration=_calibration()).state is OpportunityState.MODERATE
    assert assess_opportunity(StructuralDirection.LONG, _snapshot(2.0), calibration=_calibration()).state is OpportunityState.AMPLE


def test_no_directional_target_is_unknown_not_clear_path():
    result = assess_opportunity(StructuralDirection.LONG, _snapshot(None), calibration=_calibration())
    assert result.state is OpportunityState.UNKNOWN
    assert "NO_DIRECTIONAL_TARGET_OBSERVED_NOT_CLEAR_PATH" in result.reasons


def test_target_lineage_is_deduplicated():
    result = assess_opportunity(StructuralDirection.LONG, _snapshot(0.9), calibration=_calibration())
    assert result.source_lineage == ("ORIGIN:1",)


def test_calibration_boundaries_must_be_strictly_ordered():
    with pytest.raises(ValueError):
        OpportunityCalibration(none_max_atr=0.5, compressed_max_atr=0.4, moderate_max_atr=1.0)
