from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


class STExitFamily(StrEnum):
    """Terminal economic reason for closing one canonical ST trade."""

    PROFIT_HARVEST = "PROFIT_HARVEST"
    PROTECTIVE_EXIT = "PROTECTIVE_EXIT"


def _non_empty(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(f"{label} must be a canonical non-empty string")
    return value


def canonical_exit_reasons(values: Iterable[str]) -> tuple[str, ...]:
    rows = tuple(dict.fromkeys(_non_empty(value, "ST exit reason") for value in values))
    if not rows:
        raise ValueError("terminal ST exit intent requires at least one reason")
    return rows


def canonical_exit_lineage(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(_non_empty(value, "ST exit lineage") for value in values)))


@dataclass(frozen=True, slots=True)
class STExitIntent:
    """Durable terminal economic intent while the ST position remains OPEN.

    This is created only after policy commits to an irreversible exit family. It is
    deliberately distinct from derived pre-decision concepts such as CONSUMED,
    maturity, or healthy-base state.
    """

    family: STExitFamily
    committed_at: Any
    reasons: tuple[str, ...]
    source_lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.family, STExitFamily):
            raise ValueError("terminal ST exit intent family is invalid")
        if self.committed_at is None:
            raise ValueError("terminal ST exit intent committed_at must be known")
        if canonical_exit_reasons(self.reasons) != self.reasons:
            raise ValueError("terminal ST exit intent reasons must be canonical and unique")
        if canonical_exit_lineage(self.source_lineage) != self.source_lineage:
            raise ValueError("terminal ST exit intent lineage must be sorted and unique")


@dataclass(frozen=True, slots=True)
class STClosedExitRecord:
    """Restart-safe economic exit reason copied from terminal intent at execution."""

    trade_id: str
    entry_as_of: Any
    exit_as_of: Any
    family: STExitFamily
    intent_committed_at: Any
    reasons: tuple[str, ...]
    source_lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.trade_id, "closed ST trade_id")
        if not isinstance(self.family, STExitFamily):
            raise ValueError("closed ST exit family is invalid")
        if self.entry_as_of is None:
            raise ValueError("closed ST exit entry_as_of must be known")
        if self.exit_as_of is None:
            raise ValueError("closed ST exit exit_as_of must be known")
        if self.intent_committed_at is None:
            raise ValueError("closed ST exit intent_committed_at must be known")
        try:
            if self.intent_committed_at < self.entry_as_of:
                raise ValueError("closed ST exit intent cannot predate trade entry")
            if self.exit_as_of < self.intent_committed_at:
                raise ValueError("closed ST exit execution cannot predate terminal intent")
        except TypeError as exc:
            raise TypeError("closed ST exit timestamps must be comparable") from exc
        if canonical_exit_reasons(self.reasons) != self.reasons:
            raise ValueError("closed ST exit reasons must be canonical and unique")
        if canonical_exit_lineage(self.source_lineage) != self.source_lineage:
            raise ValueError("closed ST exit lineage must be sorted and unique")


__all__ = [
    "STClosedExitRecord",
    "STExitFamily",
    "STExitIntent",
    "canonical_exit_lineage",
    "canonical_exit_reasons",
]
