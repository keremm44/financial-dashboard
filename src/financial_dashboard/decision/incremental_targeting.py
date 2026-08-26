from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Iterable

import pandas as pd

from financial_dashboard.targeting.clustering import (
    TargetClusterConfig,
    _interval_gap,
    _origin_group,
)
from financial_dashboard.targeting.models import TargetEvidence


def deduplicate_origin_events_indexed(
    evidence: Iterable[TargetEvidence],
    *,
    reference_atr: float,
    config: TargetClusterConfig | None = None,
) -> tuple[TargetEvidence, ...]:
    """Indexed equivalent of ``deduplicate_origin_events``.

    The canonical implementation scans every evidence item for every seed. Its
    grouping rule already requires the final origin-index diameter to be no larger
    than ``origin_bar_tolerance``. Therefore every candidate that can ever join a
    seed must lie inside the seed's finite origin-index window. Indexing candidates
    by (timeframe, origin-group, origin-index) removes unrelated history while
    preserving the canonical deterministic candidate order and greedy grouping.
    """

    cfg = config or TargetClusterConfig()
    atr = max(float(reference_atr), 1e-12)
    items = list(evidence)
    grouped_indices: set[int] = set()
    replacements: dict[int, str] = {}

    order = sorted(
        range(len(items)),
        key=lambda idx: (
            items[idx].timeframe,
            pd.Timestamp(items[idx].confirmed_at),
            items[idx].origin_index,
            items[idx].uid,
        ),
    )
    rank = {item_index: position for position, item_index in enumerate(order)}
    by_origin: dict[tuple[str, str, int], list[int]] = defaultdict(list)
    for idx in order:
        item = items[idx]
        group_kind = _origin_group(item)
        if group_kind is None:
            continue
        by_origin[(item.timeframe, group_kind, int(item.origin_index))].append(idx)

    tolerance = int(cfg.origin_bar_tolerance)
    for seed_idx in order:
        if seed_idx in grouped_indices:
            continue
        seed = items[seed_idx]
        group_kind = _origin_group(seed)
        if group_kind is None:
            continue

        candidate_indices: list[int] = []
        seed_origin = int(seed.origin_index)
        for origin_index in range(seed_origin - tolerance, seed_origin + tolerance + 1):
            candidate_indices.extend(
                by_origin.get((seed.timeframe, group_kind, origin_index), ())
            )
        candidate_indices.sort(key=rank.__getitem__)

        group = [seed_idx]
        grouped_indices.add(seed_idx)
        min_origin = max_origin = seed.origin_index
        low = seed.low
        high = seed.high
        for candidate_idx in candidate_indices:
            if candidate_idx in grouped_indices:
                continue
            candidate = items[candidate_idx]
            next_min_origin = min(min_origin, candidate.origin_index)
            next_max_origin = max(max_origin, candidate.origin_index)
            if next_max_origin - next_min_origin > cfg.origin_bar_tolerance:
                continue
            if _interval_gap(low, high, candidate.low, candidate.high) / atr > cfg.origin_price_tolerance_atr:
                continue
            next_low = min(low, candidate.low)
            next_high = max(high, candidate.high)
            if (next_high - next_low) / atr > cfg.origin_max_span_atr:
                continue
            group.append(candidate_idx)
            grouped_indices.add(candidate_idx)
            min_origin = next_min_origin
            max_origin = next_max_origin
            low = next_low
            high = next_high

        if len(group) <= 1:
            continue
        anchor_idx = min(
            group,
            key=lambda idx: (pd.Timestamp(items[idx].confirmed_at), items[idx].uid),
        )
        event_id = f"EVT:{group_kind}:{seed.timeframe}:{items[anchor_idx].native_origin_id}"
        for idx in group:
            replacements[idx] = event_id

    return tuple(
        replace(item, origin_event_id=replacements.get(idx, item.origin_event_id))
        for idx, item in enumerate(items)
    )


__all__ = ["deduplicate_origin_events_indexed"]
