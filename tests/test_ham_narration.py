from __future__ import annotations

from dataclasses import dataclass, replace
import json

import pandas as pd
import pytest

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.ham_evidence import FamilySnapshot, HamFamilyEvidenceSet
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.raw_indicator_dashboard import RawDataQuality
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.ham_narration import (
    HAM_NARRATION_PROHIBITIONS,
    HAM_NARRATION_SCHEMA,
    build_ham_narration_payload,
)
from financial_dashboard.ham_support import apply_ham_confidence
from _ui_test_data import make_ui_store


@dataclass(frozen=True, slots=True)
class _NarratableCore:
    direction: Direction
    confidence: float
    action: str
    status: str
    hard_blockers: tuple[str, ...]
    market_structure: str
    support_resistance: str
    risks: tuple[str, ...]


@dataclass(slots=True)
class _MutableCore:
    direction: Direction
    confidence: float
    action: str
    status: str
    hard_blockers: tuple[str, ...]
    market_structure: str
    support_resistance: str
    risks: tuple[str, ...]


def _replay(tmp_path):
    make_ui_store(tmp_path)
    replay = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay("THYAO")
    family = FamilySnapshot(50.0, 50.0, 100.0, True)
    families = HamFamilyEvidenceSet(family, family, family, family)
    return replace(
        replay,
        timeframe_replays=tuple(
            replace(
                timeframe,
                latest=replace(
                    timeframe.latest,
                    raw=replace(
                        timeframe.latest.raw,
                        data_quality=RawDataQuality.OK,
                    ),
                    families=families,
                ),
            )
            for timeframe in replay.timeframe_replays
        ),
    )


def test_narration_payload_is_canonical_render_only_and_restart_deterministic(
    tmp_path,
) -> None:
    replay = _replay(tmp_path)
    core = _NarratableCore(
        direction=Direction.UP,
        confidence=72.5,
        action="WAIT",
        status="BLOCKED",
        hard_blockers=("STRUCTURE_NOT_CONFIRMED",),
        market_structure="H1_CHOCH",
        support_resistance="AT_SUPPORT",
        risks=("LIMITED_VOLUME",),
    )
    adjusted = apply_ham_confidence(core, replay)
    as_of = pd.Timestamp("2026-08-21 15:30", tz="Europe/Istanbul")

    first = build_ham_narration_payload(
        adjusted,
        symbol=" thyao ",
        as_of=as_of,
    )
    restarted = build_ham_narration_payload(
        apply_ham_confidence(core, replay),
        symbol="THYAO",
        as_of=as_of,
    )

    assert first == restarted
    assert first.to_json() == restarted.to_json()
    assert first.fingerprint == restarted.fingerprint
    assert first.schema == HAM_NARRATION_SCHEMA
    assert first.symbol == "THYAO"
    assert first.policy.prohibitions == HAM_NARRATION_PROHIBITIONS
    assert first.decision.direction == "UP"
    assert first.decision.action == core.action
    assert first.decision.status == core.status
    assert first.decision.hard_blockers == core.hard_blockers
    assert first.decision.market_structure == core.market_structure
    assert first.decision.support_resistance == core.support_resistance
    assert first.decision.risks == core.risks
    assert first.decision.final_confidence == adjusted.final_confidence
    assert len(first.ham.timeframes) == 5
    assert all(len(timeframe.families) == 4 for timeframe in first.ham.timeframes)

    decoded = json.loads(first.to_json())
    assert decoded["policy"]["mode"] == "RENDER_FIXED_FACTS_ONLY"
    assert decoded["decision"]["ham_delta"] == adjusted.ham_delta
    assert "prose" not in decoded
    assert "recommendation" not in decoded


def test_narration_builder_requires_authoritative_fixed_core_facts(tmp_path) -> None:
    replay = _replay(tmp_path)

    @dataclass(frozen=True, slots=True)
    class IncompleteCore:
        direction: Direction
        confidence: float

    adjusted = apply_ham_confidence(
        IncompleteCore(Direction.UP, 50.0),
        replay,
    )
    with pytest.raises(TypeError, match="authoritative 'action'"):
        build_ham_narration_payload(
            adjusted,
            symbol="THYAO",
            as_of="2026-08-21T15:30:00+03:00",
        )


def test_narration_builder_detects_core_mutation_after_adjustment(tmp_path) -> None:
    replay = _replay(tmp_path)
    core = _MutableCore(
        direction=Direction.UP,
        confidence=60.0,
        action="WAIT",
        status="READY",
        hard_blockers=(),
        market_structure="H2_BOS",
        support_resistance="NEUTRAL_LOCATION",
        risks=(),
    )
    adjusted = apply_ham_confidence(core, replay)
    core.direction = Direction.DOWN

    with pytest.raises(ValueError, match="direction changed"):
        build_ham_narration_payload(
            adjusted,
            symbol="THYAO",
            as_of="2026-08-21T15:30:00+03:00",
        )


def test_narration_builder_rejects_mutable_or_non_scalar_fixed_facts(tmp_path) -> None:
    replay = _replay(tmp_path)
    core = _MutableCore(
        direction=Direction.UP,
        confidence=60.0,
        action="WAIT",
        status="READY",
        hard_blockers=(),
        market_structure="H2_BOS",
        support_resistance="AT_SUPPORT",
        risks=(),
    )
    adjusted = apply_ham_confidence(core, replay)
    core.hard_blockers = ["MUTABLE"]  # type: ignore[assignment]

    with pytest.raises(TypeError, match="immutable tuple"):
        build_ham_narration_payload(
            adjusted,
            symbol="THYAO",
            as_of="2026-08-21T15:30:00+03:00",
        )
