from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from financial_dashboard.engines.support_resistance_zones import (
    ZoneKind,
    ZoneLifecycle,
    ZoneSide,
)
from tests.test_support_resistance_engine import _bar, _mature_engine


def _by_suffix(engine, suffix: str):
    return next(zone for zone in engine.zones if zone.zone_uid.endswith(suffix))


def test_range_zone_identities_are_stable_and_lifecycle_is_append_only() -> None:
    engine, _ = _mature_engine()
    upper = _by_suffix(engine, "UPPER")
    lower = _by_suffix(engine, "LOWER")
    event_prefix = engine.zone_lifecycle_events

    assert upper.source_range_identity == lower.source_range_identity
    assert upper.side is ZoneSide.RESISTANCE
    assert lower.side is ZoneSide.SUPPORT
    assert upper.kind is lower.kind is ZoneKind.RANGE_BOUNDARY
    assert upper.lifecycle in {ZoneLifecycle.CONFIRMED, ZoneLifecycle.ACTIVE, ZoneLifecycle.WEAK}

    engine.update(_bar(80, o=104.0, h=108.0, l=101.0, c=104.0))

    assert _by_suffix(engine, "UPPER").zone_uid == upper.zone_uid
    assert _by_suffix(engine, "LOWER").zone_uid == lower.zone_uid
    assert engine.zone_lifecycle_events[: len(event_prefix)] == event_prefix
    with pytest.raises(FrozenInstanceError):
        upper.low = 1.0  # type: ignore[misc]


def test_confirmed_range_break_creates_typed_role_reversal_and_archives_old_pair() -> None:
    engine, _ = _mature_engine()
    upper = engine.export_contract.upper_top
    assert upper is not None

    engine.update(_bar(80, o=upper - 0.5, h=upper + 8.0, l=upper - 1.0, c=upper + 7.0))
    assert _by_suffix(engine, "UPPER").lifecycle is ZoneLifecycle.BREAK_CANDIDATE
    assert _by_suffix(engine, "LOWER").lifecycle in {
        ZoneLifecycle.CONFIRMED,
        ZoneLifecycle.ACTIVE,
        ZoneLifecycle.WEAK,
    }

    engine.update(_bar(81, o=upper + 7.0, h=upper + 9.0, l=upper + 5.0, c=upper + 8.0))

    assert _by_suffix(engine, "UPPER").lifecycle is ZoneLifecycle.BROKEN
    assert _by_suffix(engine, "LOWER").lifecycle is ZoneLifecycle.ARCHIVED
    role = _by_suffix(engine, "ROLE_SUPPORT")
    assert role.kind is ZoneKind.ROLE_REVERSAL
    assert role.side is ZoneSide.SUPPORT
    assert role.lifecycle is ZoneLifecycle.ACTIVE
    assert role.source_range_identity == _by_suffix(engine, "UPPER").source_range_identity


def test_role_reversal_requires_two_closed_breaches_before_invalidation() -> None:
    engine, _ = _mature_engine()
    upper = engine.export_contract.upper_top
    assert upper is not None
    engine.update(_bar(80, o=upper - 0.5, h=upper + 8.0, l=upper - 1.0, c=upper + 7.0))
    engine.update(_bar(81, o=upper + 7.0, h=upper + 9.0, l=upper + 5.0, c=upper + 8.0))
    role = _by_suffix(engine, "ROLE_SUPPORT")

    first_close_below = role.low - role.reference_atr
    engine.update(
        _bar(82, o=role.low, h=role.low + 0.2, l=first_close_below - 0.2, c=first_close_below)
    )
    assert _by_suffix(engine, "ROLE_SUPPORT").lifecycle is ZoneLifecycle.BREAK_CANDIDATE

    engine.update(
        _bar(83, o=role.low, h=role.low + 0.1, l=first_close_below - 0.3, c=first_close_below - 0.1)
    )
    assert _by_suffix(engine, "ROLE_SUPPORT").lifecycle is ZoneLifecycle.INVALIDATED
    assert not _by_suffix(engine, "ROLE_SUPPORT").is_active


def test_role_reversal_breach_recovery_is_recorded_as_failed_break_not_invalidation() -> None:
    engine, _ = _mature_engine()
    upper = engine.export_contract.upper_top
    assert upper is not None
    engine.update(_bar(80, o=upper - 0.5, h=upper + 8.0, l=upper - 1.0, c=upper + 7.0))
    engine.update(_bar(81, o=upper + 7.0, h=upper + 9.0, l=upper + 5.0, c=upper + 8.0))
    role = _by_suffix(engine, "ROLE_SUPPORT")

    engine.update(
        _bar(82, o=role.low, h=role.low + 0.2, l=role.low - role.reference_atr, c=role.low - role.reference_atr)
    )
    engine.update(
        _bar(83, o=role.center, h=role.high + 0.2, l=role.low, c=role.center)
    )

    assert _by_suffix(engine, "ROLE_SUPPORT").lifecycle is ZoneLifecycle.ACTIVE
    assert engine.zone_lifecycle_events[-1].reason == "ROLE_REVERSAL_BREAK_FAILED"
