from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from .lifecycle import ExitStage, PositionState, TradeLifecycleState
from .position_metadata import PositionEntryMetadata
from .scenario import ScenarioKind
from .structural import DecisionHorizon


TRADE_LIFECYCLE_STATE_SCHEMA_VERSION = 1
CANONICAL_LIFECYCLE_CONTRACT_VERSION = 1


class LifecycleCheckpointStatus(StrEnum):
    ABSENT = "ABSENT"
    LOADED = "LOADED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class TradeLifecycleCheckpoint:
    symbol: str
    state: TradeLifecycleState
    prefix_count: int
    last_as_of: Any | None
    snapshot_prefix_digest: str

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("trade lifecycle checkpoint symbol must be non-empty")
        if self.prefix_count < 0:
            raise ValueError("trade lifecycle checkpoint prefix_count must be non-negative")
        if len(self.snapshot_prefix_digest) != 64:
            raise ValueError("trade lifecycle checkpoint digest must be sha256 hex")
        try:
            int(self.snapshot_prefix_digest, 16)
        except ValueError as exc:
            raise ValueError("trade lifecycle checkpoint digest must be sha256 hex") from exc
        if self.prefix_count == 0:
            if self.last_as_of is not None:
                raise ValueError("empty lifecycle prefix cannot carry last_as_of")
            if self.state.position is not PositionState.FLAT:
                raise ValueError("empty lifecycle prefix must remain FLAT")
        elif self.last_as_of is None:
            raise ValueError("non-empty lifecycle prefix requires last_as_of")
        if self.state.entry_metadata is not None and self.state.entry_metadata.symbol != self.symbol:
            raise ValueError("persisted position metadata symbol must match checkpoint symbol")
        if self.state.position is PositionState.OPEN and self.state.entry_metadata is None:
            raise ValueError("canonical persisted OPEN lifecycle requires entry metadata")


@dataclass(frozen=True, slots=True)
class LifecycleCheckpointLoadResult:
    status: LifecycleCheckpointStatus
    checkpoint: TradeLifecycleCheckpoint | None
    reason: str

    def __post_init__(self) -> None:
        if self.status is LifecycleCheckpointStatus.LOADED and self.checkpoint is None:
            raise ValueError("LOADED lifecycle result requires checkpoint")
        if self.status is not LifecycleCheckpointStatus.LOADED and self.checkpoint is not None:
            raise ValueError("non-LOADED lifecycle result cannot carry checkpoint")
        if not self.reason.strip():
            raise ValueError("lifecycle load result requires reason")


def _safe_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned or "unknown"


def _timestamp_payload(value: Any | None) -> str | None:
    return None if value is None else pd.Timestamp(value).isoformat()


def _timestamp_from_payload(value: Any, *, field: str, required: bool) -> pd.Timestamp | None:
    if value is None:
        if required:
            raise ValueError(f"{field} must be present")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO timestamp string")
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO timestamp string") from exc


def serialize_trade_lifecycle_state(state: TradeLifecycleState) -> dict[str, Any]:
    if state.position is PositionState.OPEN and state.entry_metadata is None:
        raise ValueError("canonical persisted OPEN lifecycle requires entry metadata")

    metadata = state.entry_metadata
    metadata_payload = None
    if metadata is not None:
        metadata_payload = {
            "symbol": metadata.symbol,
            "entry_horizon": metadata.entry_horizon.value,
            "scenario_kind": metadata.scenario_kind.value,
            "entry_as_of": _timestamp_payload(metadata.entry_as_of),
            "entry_price": float(metadata.entry_price),
            "active_target_identity": metadata.active_target_identity,
            "execution_timeframe": metadata.execution_timeframe,
            "execution_observed_at": _timestamp_payload(metadata.execution_observed_at),
            "execution_reason": metadata.execution_reason,
            "source_lineage": list(metadata.source_lineage),
        }

    return {
        "position": state.position.value,
        "exit_stage": None if state.exit_stage is None else state.exit_stage.value,
        "trade_id": state.trade_id,
        "entry_as_of": _timestamp_payload(state.entry_as_of),
        "entry_metadata": metadata_payload,
    }


def deserialize_trade_lifecycle_state(payload: Mapping[str, Any]) -> TradeLifecycleState:
    if not isinstance(payload, Mapping):
        raise ValueError("trade lifecycle state payload must be a mapping")
    try:
        position = PositionState(str(payload["position"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("trade lifecycle state position is invalid") from exc

    if position is PositionState.FLAT:
        if any(payload.get(key) is not None for key in ("exit_stage", "trade_id", "entry_as_of", "entry_metadata")):
            raise ValueError("persisted FLAT lifecycle cannot carry open-position fields")
        return TradeLifecycleState()

    try:
        exit_stage = ExitStage(str(payload["exit_stage"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("persisted OPEN lifecycle exit_stage is invalid") from exc
    trade_id = payload.get("trade_id")
    if not isinstance(trade_id, str) or not trade_id.strip():
        raise ValueError("persisted OPEN lifecycle requires trade_id")
    entry_as_of = _timestamp_from_payload(payload.get("entry_as_of"), field="entry_as_of", required=True)

    raw = payload.get("entry_metadata")
    if not isinstance(raw, Mapping):
        raise ValueError("canonical persisted OPEN lifecycle requires entry_metadata")
    try:
        entry_horizon = DecisionHorizon(str(raw["entry_horizon"]))
        scenario_kind = ScenarioKind(str(raw["scenario_kind"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("persisted entry metadata horizon/scenario is invalid") from exc
    source_lineage = raw.get("source_lineage")
    if not isinstance(source_lineage, list) or any(not isinstance(item, str) for item in source_lineage):
        raise ValueError("persisted entry metadata source_lineage must be a string list")

    metadata = PositionEntryMetadata(
        symbol=str(raw.get("symbol", "")),
        entry_horizon=entry_horizon,
        scenario_kind=scenario_kind,
        entry_as_of=_timestamp_from_payload(raw.get("entry_as_of"), field="entry_metadata.entry_as_of", required=True),
        entry_price=float(raw.get("entry_price")),
        active_target_identity=raw.get("active_target_identity"),
        execution_timeframe=str(raw.get("execution_timeframe", "")),
        execution_observed_at=_timestamp_from_payload(raw.get("execution_observed_at"), field="entry_metadata.execution_observed_at", required=True),
        execution_reason=str(raw.get("execution_reason", "")),
        source_lineage=tuple(source_lineage),
    )
    return TradeLifecycleState(
        position=position,
        exit_stage=exit_stage,
        trade_id=trade_id,
        entry_as_of=entry_as_of,
        entry_metadata=metadata,
    )


def snapshot_prefix_digest(snapshots: Iterable[Any]) -> str:
    """Hash the deterministic representation of the consumed frozen snapshot prefix."""

    digest = sha256()
    for snapshot in snapshots:
        payload = repr(snapshot).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def serialize_trade_lifecycle_checkpoint(checkpoint: TradeLifecycleCheckpoint) -> dict[str, Any]:
    return {
        "schema_version": TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
        "contract_version": CANONICAL_LIFECYCLE_CONTRACT_VERSION,
        "symbol": checkpoint.symbol,
        "prefix_count": checkpoint.prefix_count,
        "last_as_of": _timestamp_payload(checkpoint.last_as_of),
        "snapshot_prefix_digest": checkpoint.snapshot_prefix_digest,
        "state": serialize_trade_lifecycle_state(checkpoint.state),
    }


def deserialize_trade_lifecycle_checkpoint(payload: Mapping[str, Any], *, expected_symbol: str) -> TradeLifecycleCheckpoint:
    if not isinstance(payload, Mapping):
        raise ValueError("trade lifecycle checkpoint payload must be a mapping")
    if payload.get("schema_version") != TRADE_LIFECYCLE_STATE_SCHEMA_VERSION:
        raise ValueError("trade lifecycle checkpoint schema version mismatch")
    if payload.get("contract_version") != CANONICAL_LIFECYCLE_CONTRACT_VERSION:
        raise ValueError("trade lifecycle checkpoint contract version mismatch")
    if payload.get("symbol") != expected_symbol:
        raise ValueError("trade lifecycle checkpoint symbol mismatch")
    try:
        prefix_count = int(payload["prefix_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("trade lifecycle checkpoint prefix_count is invalid") from exc
    last_as_of = _timestamp_from_payload(payload.get("last_as_of"), field="last_as_of", required=prefix_count > 0)
    if prefix_count == 0 and payload.get("last_as_of") is not None:
        raise ValueError("empty lifecycle checkpoint cannot carry last_as_of")
    prefix_digest = payload.get("snapshot_prefix_digest")
    if not isinstance(prefix_digest, str):
        raise ValueError("trade lifecycle checkpoint digest is invalid")
    state_payload = payload.get("state")
    if not isinstance(state_payload, Mapping):
        raise ValueError("trade lifecycle checkpoint state is invalid")
    return TradeLifecycleCheckpoint(
        symbol=expected_symbol,
        state=deserialize_trade_lifecycle_state(state_payload),
        prefix_count=prefix_count,
        last_as_of=last_as_of,
        snapshot_prefix_digest=prefix_digest,
    )


class PersistentTradeLifecycleStore:
    """Atomic JSON store whose INVALID result can never be mistaken for FLAT."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / ".decision_state"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        directory = self.root / _safe_part(symbol)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "trade_lifecycle.json"

    def load(self, symbol: str) -> LifecycleCheckpointLoadResult:
        if not symbol.strip():
            raise ValueError("lifecycle store symbol must be non-empty")
        path = self.path_for(symbol)
        if not path.exists():
            return LifecycleCheckpointLoadResult(LifecycleCheckpointStatus.ABSENT, None, "LIFECYCLE_CHECKPOINT_ABSENT")
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            checkpoint = deserialize_trade_lifecycle_checkpoint(payload, expected_symbol=symbol)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return LifecycleCheckpointLoadResult(
                LifecycleCheckpointStatus.INVALID,
                None,
                f"LIFECYCLE_CHECKPOINT_INVALID:{type(exc).__name__}",
            )
        return LifecycleCheckpointLoadResult(LifecycleCheckpointStatus.LOADED, checkpoint, "LIFECYCLE_CHECKPOINT_LOADED")

    def save(self, checkpoint: TradeLifecycleCheckpoint) -> Path:
        path = self.path_for(checkpoint.symbol)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(serialize_trade_lifecycle_checkpoint(checkpoint), handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def clear(self, symbol: str) -> None:
        self.path_for(symbol).unlink(missing_ok=True)


__all__ = [
    "CANONICAL_LIFECYCLE_CONTRACT_VERSION",
    "LifecycleCheckpointLoadResult",
    "LifecycleCheckpointStatus",
    "PersistentTradeLifecycleStore",
    "TRADE_LIFECYCLE_STATE_SCHEMA_VERSION",
    "TradeLifecycleCheckpoint",
    "deserialize_trade_lifecycle_checkpoint",
    "deserialize_trade_lifecycle_state",
    "serialize_trade_lifecycle_checkpoint",
    "serialize_trade_lifecycle_state",
    "snapshot_prefix_digest",
]
