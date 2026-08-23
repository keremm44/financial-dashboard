"""Cross-domain context orchestration boundary.

Planned responsibility only:
- accept explicit typed replay/domain outputs
- enforce the single decision-time knowledge boundary
- filter facts where available_at > as_of
- build projections, lineage, zones, context snapshot, and permission envelope
- keep business logic out of market_workspace.py

This is orchestration, not a native engine and not an action layer.
"""
