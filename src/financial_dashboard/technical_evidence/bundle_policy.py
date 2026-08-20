from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

import pandas as pd

from .models import NormalizedLevel, TechnicalEvidenceItem, TechnicalEvidencePacket
from .tur2 import (
    EvidenceGraphError,
    FreshnessClass,
    FreshnessRecord,
    TechnicalEvidenceBundle,
    build_technical_evidence_bundle as _build_core_bundle,
)


def build_technical_evidence_bundle(
    packets: Iterable[TechnicalEvidencePacket],
    *,
    as_of_timestamp: Any | None = None,
    as_of_known_bars: Mapping[str, int] | None = None,
) -> TechnicalEvidenceBundle:
    """Public Tur-2 bundle gate.

    Multiple engine adapter packets for the same timeframe are allowed only when
    they represent the exact same snapshot (same known_bar and timestamp). They
    are losslessly coalesced before Tur-2 enrichment. Different snapshots for the
    same timeframe are rejected so old and current states cannot coexist as if
    both were current evidence.
    """

    packet_tuple = _coalesce_same_snapshot_packets(tuple(packets))
    normalized_as_of_bars = _normalize_as_of_bars(as_of_known_bars)
    _validate_packet_as_of(packet_tuple, as_of_timestamp, normalized_as_of_bars)

    bundle = _build_core_bundle(
        packet_tuple,
        as_of_timestamp=as_of_timestamp,
        as_of_known_bars=normalized_as_of_bars,
    )
    return _remove_inferred_level_freshness(bundle)


def _coalesce_same_snapshot_packets(
    packets: tuple[TechnicalEvidencePacket, ...],
) -> tuple[TechnicalEvidencePacket, ...]:
    groups: dict[str, list[TechnicalEvidencePacket]] = {}
    for packet in packets:
        key = packet.timeframe.strip().lower()
        groups.setdefault(key, []).append(packet)

    out: list[TechnicalEvidencePacket] = []
    for key, group in sorted(groups.items()):
        unique: list[TechnicalEvidencePacket] = []
        for packet in group:
            if packet not in unique:
                unique.append(packet)
        first = unique[0]
        for packet in unique[1:]:
            if packet.known_bar != first.known_bar or not _timestamps_equal(packet.timestamp, first.timestamp):
                raise EvidenceGraphError(
                    f"multiple snapshots for timeframe {key}: bundle requires one as-of snapshot per timeframe"
                )

        evidence = _dedupe_evidence(item for packet in unique for item in packet.evidence)
        levels = _dedupe_levels(level for packet in unique for level in packet.levels)
        out.append(
            TechnicalEvidencePacket(
                timeframe=first.timeframe,
                known_bar=first.known_bar,
                timestamp=first.timestamp,
                evidence=evidence,
                levels=levels,
            )
        )
    return tuple(out)


def _normalize_as_of_bars(values: Mapping[str, int] | None) -> dict[str, int] | None:
    if values is None:
        return None
    out: dict[str, int] = {}
    for timeframe, known_bar in values.items():
        key = str(timeframe).strip().lower()
        value = int(known_bar)
        if key in out and out[key] != value:
            raise EvidenceGraphError(f"conflicting as-of bar for timeframe {key}")
        out[key] = value
    return out


def _validate_packet_as_of(
    packets: tuple[TechnicalEvidencePacket, ...],
    as_of_timestamp: Any | None,
    as_of_known_bars: Mapping[str, int] | None,
) -> None:
    for packet in packets:
        key = packet.timeframe.strip().lower()
        if as_of_known_bars is not None and key in as_of_known_bars:
            limit = int(as_of_known_bars[key])
            if packet.known_bar is not None and int(packet.known_bar) > limit:
                raise EvidenceGraphError(
                    f"packet beyond as-of bar for timeframe {key}: {packet.known_bar} > {limit}"
                )
        if as_of_timestamp is not None:
            relation = _timestamp_relation(packet.timestamp, as_of_timestamp)
            if relation is not None and relation > 0:
                raise EvidenceGraphError(
                    f"packet beyond as-of timestamp for timeframe {key}: {packet.timestamp}"
                )


def _remove_inferred_level_freshness(bundle: TechnicalEvidenceBundle) -> TechnicalEvidenceBundle:
    """A level needs its own causal source_bar to receive numeric freshness.

    The Tur-2 core historically allowed a level without an origin anchor to
    inherit freshness from referencing evidence. That is useful presentation
    metadata but it is not a valid level age. The public contract is stricter:
    no source_bar means UNKNOWN freshness.
    """

    unanchored = {level.id for level in bundle.levels if level.source_bar is None}
    if not unanchored:
        return bundle

    levels = tuple(
        replace(level, freshness=None) if level.id in unanchored else level
        for level in bundle.levels
    )
    freshness = tuple(
        FreshnessRecord(
            target_id=record.target_id,
            target_kind=record.target_kind,
            value=None,
            classification=FreshnessClass.UNKNOWN,
            age_bars=None,
            anchor="UNKNOWN",
            horizon_bars=None,
        )
        if record.target_kind == "LEVEL" and record.target_id in unanchored
        else record
        for record in bundle.freshness
    )
    return replace(bundle, levels=levels, freshness=freshness)


def _dedupe_evidence(values: Iterable[TechnicalEvidenceItem]) -> tuple[TechnicalEvidenceItem, ...]:
    by_id: dict[str, TechnicalEvidenceItem] = {}
    for item in values:
        existing = by_id.get(item.id)
        if existing is not None and existing != item:
            raise ValueError(f"conflicting duplicate evidence id: {item.id}")
        by_id[item.id] = item
    return tuple(sorted(by_id.values(), key=lambda item: (item.source_engine, item.evidence_type, item.id)))


def _dedupe_levels(values: Iterable[NormalizedLevel]) -> tuple[NormalizedLevel, ...]:
    by_id: dict[str, NormalizedLevel] = {}
    for level in values:
        existing = by_id.get(level.id)
        if existing is not None and existing != level:
            raise ValueError(f"conflicting duplicate level id: {level.id}")
        by_id[level.id] = level
    return tuple(sorted(by_id.values(), key=lambda level: (level.source_engine, level.level_type, level.id)))


def _timestamps_equal(left: Any, right: Any) -> bool:
    relation = _timestamp_relation(left, right)
    if relation is None:
        return left is None and right is None or str(left) == str(right)
    return relation == 0


def _timestamp_relation(left: Any, right: Any) -> int | None:
    if left is None or right is None:
        return None
    try:
        left_ts = pd.Timestamp(left)
        right_ts = pd.Timestamp(right)
    except (TypeError, ValueError):
        return None
    if pd.isna(left_ts) or pd.isna(right_ts):
        return None
    left_aware = left_ts.tzinfo is not None
    right_aware = right_ts.tzinfo is not None
    if left_aware != right_aware:
        return None
    if left_aware:
        left_ts = left_ts.tz_convert("UTC")
        right_ts = right_ts.tz_convert("UTC")
    return -1 if left_ts < right_ts else 1 if left_ts > right_ts else 0
