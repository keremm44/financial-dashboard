"""Backwards-compatible UI view-model facade.

Domain implementations live in focused modules so adding Liquidity/Auction/OB/FVG
inspection does not grow one monolithic file. Existing imports from
``financial_dashboard.ui.view_models`` remain stable.
"""

from .view_model_ham import (
    ham_history_frame,
    ham_indicator_evidence_frame,
    ham_mtf_evidence_frame,
)
from .view_model_observer import (
    cache_status_frame,
    confluence_frame,
    event_zone_links_frame,
    location_outcomes_frame,
    mtf_matrix_frame,
    observer_facts_frame,
    opposing_conflicts_frame,
    overview_values,
    structure_events_frame,
    structure_history_frame,
    zones_frame,
)
from .view_model_volume import (
    volume_deduplication_frame,
    volume_diagnostics_frame,
    volume_event_links_frame,
    volume_history_frame,
    volume_mtf_matrix_frame,
    volume_propagations_frame,
    volume_risk_transitions_frame,
    volume_shocks_frame,
)

__all__ = [
    "cache_status_frame",
    "confluence_frame",
    "event_zone_links_frame",
    "ham_history_frame",
    "ham_indicator_evidence_frame",
    "ham_mtf_evidence_frame",
    "location_outcomes_frame",
    "mtf_matrix_frame",
    "observer_facts_frame",
    "opposing_conflicts_frame",
    "overview_values",
    "structure_events_frame",
    "structure_history_frame",
    "volume_deduplication_frame",
    "volume_diagnostics_frame",
    "volume_event_links_frame",
    "volume_history_frame",
    "volume_mtf_matrix_frame",
    "volume_propagations_frame",
    "volume_risk_transitions_frame",
    "volume_shocks_frame",
    "zones_frame",
]
