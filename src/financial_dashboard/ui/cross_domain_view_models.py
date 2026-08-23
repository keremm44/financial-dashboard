from __future__ import annotations

import pandas as pd

from financial_dashboard.context.builder import CrossDomainBuildResult


def _value(value) -> str:
    return str(getattr(value, "value", value))


def cross_domain_summary_values(result: CrossDomainBuildResult) -> dict[str, str]:
    axes = result.context.axes
    permission = result.permission
    return {
        "Structural thesis": _value(axes.structural_thesis),
        "Continuation": _value(axes.continuation),
        "Reaction": f"{_value(axes.reaction)} / {_value(axes.reaction_direction)}",
        "Reversal": f"{_value(axes.reversal)} / {_value(axes.reversal_direction)}",
        "Objective": _value(axes.objective),
        "Conflict": _value(axes.conflict),
        "Permission scope": _value(permission.scope),
        "Permitted side": _value(permission.permitted_side),
        "Gate": _value(permission.gate_state),
    }


def cross_domain_context_frame(result: CrossDomainBuildResult) -> pd.DataFrame:
    axes = result.context.axes
    rows = (
        ("Structural thesis", axes.structural_thesis),
        ("Structural direction", axes.structural_direction),
        ("Continuation", axes.continuation),
        ("Reaction", axes.reaction),
        ("Reaction direction", axes.reaction_direction),
        ("Reversal", axes.reversal),
        ("Reversal direction", axes.reversal_direction),
        ("Objective", axes.objective),
        ("Participation", axes.participation),
        ("Volatility", axes.volatility),
        ("Pattern readiness", axes.pattern_readiness),
        ("MTF context", axes.mtf),
        ("HAM readiness", axes.ham_readiness),
        ("Conflict", axes.conflict),
    )
    return pd.DataFrame(
        ({"Axis": name, "State": _value(value)} for name, value in rows),
        columns=("Axis", "State"),
    )


def _zone_row(label: str, zone) -> dict[str, object]:
    if zone is None:
        return {
            "View": label,
            "Zone": "",
            "Side": "",
            "TF": "",
            "Range": "",
            "Qualification": "",
            "Interaction": "",
            "Distance ATR": None,
        }
    return {
        "View": label,
        "Zone": zone.zone_id,
        "Side": _value(zone.side),
        "TF": zone.anchor_timeframe,
        "Range": f"{zone.low:.4f} – {zone.high:.4f}",
        "Qualification": _value(zone.qualification),
        "Interaction": _value(zone.interaction),
        "Distance ATR": float(zone.distance_atr),
    }


def cross_domain_zones_frame(result: CrossDomainBuildResult) -> pd.DataFrame:
    zones = result.context.zones
    rows = (
        _zone_row("Nearest support", zones.nearest_qualified_support),
        _zone_row("Nearest resistance", zones.nearest_qualified_resistance),
        _zone_row("Strongest support", zones.strongest_relevant_support),
        _zone_row("Strongest resistance", zones.strongest_relevant_resistance),
        _zone_row("HTF primary support", zones.htf_primary_support),
        _zone_row("HTF primary resistance", zones.htf_primary_resistance),
    )
    return pd.DataFrame(rows)


def cross_domain_permission_frame(result: CrossDomainBuildResult) -> pd.DataFrame:
    permission = result.permission
    rows = (
        {"Field": "Scope", "Value": _value(permission.scope)},
        {"Field": "Permitted side", "Value": _value(permission.permitted_side)},
        {"Field": "Gate", "Value": _value(permission.gate_state)},
        {"Field": "Allowed reasons", "Value": ", ".join(permission.allowed_reasons)},
        {"Field": "Blocking reasons", "Value": ", ".join(permission.blocking_reasons)},
        {"Field": "Waiting for", "Value": ", ".join(permission.waiting_for)},
    )
    return pd.DataFrame(rows, columns=("Field", "Value"))


def cross_domain_knowledge_frame(result: CrossDomainBuildResult) -> pd.DataFrame:
    boundary = result.context.knowledge_boundary
    rows = (
        {"Boundary": "as_of", "Value": str(boundary.as_of)},
        {"Boundary": "eligible facts", "Value": str(len(boundary.eligible_fact_ids))},
        {"Boundary": "future facts excluded", "Value": str(len(boundary.excluded_future_fact_ids))},
        {"Boundary": "unconfirmed facts", "Value": str(len(boundary.unconfirmed_fact_ids))},
        {"Boundary": "unsupported contexts", "Value": ", ".join(boundary.unsupported_contexts)},
    )
    return pd.DataFrame(rows, columns=("Boundary", "Value"))


__all__ = [
    "cross_domain_context_frame",
    "cross_domain_knowledge_frame",
    "cross_domain_permission_frame",
    "cross_domain_summary_values",
    "cross_domain_zones_frame",
]
