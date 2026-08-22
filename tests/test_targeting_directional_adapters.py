from types import SimpleNamespace

import pandas as pd

from financial_dashboard.engines.fvg_engulfing_models import EngulfingState, FvgState
from financial_dashboard.targeting.adapters import fvg_engulfing_evidence
from financial_dashboard.targeting.models import TargetEvidenceType, TargetRole
from financial_dashboard.targeting.semantic_models import BehaviorDirection
from financial_dashboard.targeting.semantic_roles import evidence_behavior


TS = pd.Timestamp("2026-08-21 14:00", tz="Europe/Istanbul")


def _fvg(direction: int, index: int):
    return SimpleNamespace(
        formation_index=index,
        direction=direction,
        formation_time=TS,
        lower_boundary=100.0 + index,
        upper_boundary=101.0 + index,
        formation_atr=2.0,
        quality=80.0,
        state=FvgState.ACTIVE,
    )


def _engulf(direction: int, index: int):
    return SimpleNamespace(
        formation_index=index,
        direction=direction,
        formation_time=TS,
        lower_boundary=100.0 + index,
        upper_boundary=101.0 + index,
        body_atr=1.0,
        quality=70.0,
        state=EngulfingState.ACTIVE,
    )


def test_fvg_adapter_preserves_native_direction_as_behavior_role() -> None:
    engine = SimpleNamespace(
        active_bullish_fvg=_fvg(1, 1),
        active_bearish_fvg=_fvg(-1, 2),
        completed_fvg=(),
        active_bullish_engulfing=None,
        active_bearish_engulfing=None,
        completed_engulfing=(),
    )
    evidence = fvg_engulfing_evidence(
        symbol="TEST",
        timeframe="1h",
        engine=engine,
        confirmations={},
    )
    bullish = next(item for item in evidence if item.evidence_type is TargetEvidenceType.FVG and TargetRole.DEMAND in item.roles)
    bearish = next(item for item in evidence if item.evidence_type is TargetEvidenceType.FVG and TargetRole.SUPPLY in item.roles)

    assert TargetRole.IMBALANCE in bullish.roles
    assert TargetRole.REACTION in bullish.roles
    assert evidence_behavior(bullish) is BehaviorDirection.BULLISH
    assert evidence_behavior(bearish) is BehaviorDirection.BEARISH


def test_engulfing_adapter_preserves_direction_but_stays_confirmation_semantics() -> None:
    engine = SimpleNamespace(
        active_bullish_fvg=None,
        active_bearish_fvg=None,
        completed_fvg=(),
        active_bullish_engulfing=_engulf(1, 3),
        active_bearish_engulfing=_engulf(-1, 4),
        completed_engulfing=(),
    )
    evidence = fvg_engulfing_evidence(
        symbol="TEST",
        timeframe="1h",
        engine=engine,
        confirmations={},
    )
    bullish = next(item for item in evidence if item.evidence_type is TargetEvidenceType.ENGULFING and TargetRole.DEMAND in item.roles)
    bearish = next(item for item in evidence if item.evidence_type is TargetEvidenceType.ENGULFING and TargetRole.SUPPLY in item.roles)

    assert evidence_behavior(bullish) is BehaviorDirection.BULLISH
    assert evidence_behavior(bearish) is BehaviorDirection.BEARISH
