"""Scoped permission envelope and resolver boundary.

Planned responsibility only:
- consume CrossDomainContextSnapshot
- express scope, permitted side, gate state, reasons, blockers, and waiting conditions
- remain independent of native engines

This module must never emit BUY/SELL, entry/exit, sizing, SL, TP, or probability.
"""
