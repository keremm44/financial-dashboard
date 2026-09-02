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
from .lifecycle_replay import ReplayAuditMarkerState
from .position_metadata import (
    PositionEntryMetadata,
    STInitialDefendedAnchor,
    STInitialTargetContext,
    STTradeMemory,
)
from .scenario import ScenarioKind
from .st_economic_history import (
    deserialize_st_economic_history,
    serialize_st_economic_history,
)
from .st_exit_intent import STClosedExitRecord, STExitFamily, STExitIntent
from .st_thesis_identity import STDefendedAnchorKind, STEconomicMission, STThesisFamily
from .structural import DecisionHorizon
from .target_path import TargetPathRole


TRADE_LIFECYCLE_STATE_SCHEMA_VERSION = 5
CANONICAL_LIFECYCLE_CONTRACT_VERSION = 7


class LifecycleCheckpointStatus(StrEnum):
    ABSENT = "ABSENT"
    LOADED = "LOADED"
    INVALID = "INVALID"


def _validate_sha256(value: str, *, field: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{field} must be sha256 hex")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be sha256 hex") from exc


@dataclass(frozen=True, slots=True)
class TradeLifecycleCheckpoint:
    """Persistent ownership, audit progression and exact causal prefix identity."""

    symbol: str
    state: TradeLifecycleState
    prefix_count: int
    last_as_of: Any | None
    causal_prefix_digest: str
    decision_config_digest: str
    audit_markers: ReplayAuditMarkerState = ReplayAuditMarkerState()

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("trade lifecycle checkpoint symbol must be non-empty")
        if self.prefix_count < 0:
            raise ValueError("trade lifecycle checkpoint prefix_count must be non-negative")
        _validate_sha256(self.causal_prefix_digest, field="causal_prefix_digest")
        _validate_sha256(self.decision_config_digest, field="decision_config_digest")
        if self.prefix_count == 0:
            if self.last_as_of is not None:
                raise ValueError("empty lifecycle prefix cannot carry last_as_of")
            if self.state.position is not PositionState.FLAT:
                raise ValueError("empty lifecycle prefix must remain FLAT")
            if self.state.last_closed_st_exit is not None:
                raise ValueError("empty lifecycle prefix cannot carry closed-trade exit history")
            if not self.audit_markers.is_empty:
                raise ValueError("empty lifecycle prefix cannot carry audit markers")
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


def serialize_replay_audit_marker_state(markers: ReplayAuditMarkerState) -> dict[str, Any]:
    return {
        "scenario_qualified_at": _timestamp_payload(markers.scenario_qualified_at),
        "scenario_qualified_price": markers.scenario_qualified_price,
        "scenario_key": None if markers.scenario_key is None else list(markers.scenario_key),
        "ready_for_execution_at": _timestamp_payload(markers.ready_for_execution_at),
        "ready_for_execution_price": markers.ready_for_execution_price,
        "exit_watch_at": _timestamp_payload(markers.exit_watch_at),
        "exit_watch_price": markers.exit_watch_price,
        "exit_ready_at": _timestamp_payload(markers.exit_ready_at),
        "exit_ready_price": markers.exit_ready_price,
    }


def _optional_positive_float(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if result <= 0.0:
        raise ValueError(f"{field} must be positive")
    return result


def deserialize_replay_audit_marker_state(payload: Mapping[str, Any]) -> ReplayAuditMarkerState:
    if not isinstance(payload, Mapping):
        raise ValueError("replay audit marker payload must be a mapping")
    raw_key = payload.get("scenario_key")
    scenario_key: tuple[str, str] | None
    if raw_key is None:
        scenario_key = None
    else:
        if not isinstance(raw_key, list) or len(raw_key) != 2 or any(not isinstance(item, str) for item in raw_key):
            raise ValueError("replay audit marker scenario_key must be a two-string list")
        scenario_key = (raw_key[0], raw_key[1])

    return ReplayAuditMarkerState(
        scenario_qualified_at=_timestamp_from_payload(
            payload.get("scenario_qualified_at"),
            field="audit_markers.scenario_qualified_at",
            required=False,
        ),
        scenario_qualified_price=_optional_positive_float(
            payload.get("scenario_qualified_price"),
            field="audit_markers.scenario_qualified_price",
        ),
        scenario_key=scenario_key,
        ready_for_execution_at=_timestamp_from_payload(
            payload.get("ready_for_execution_at"),
            field="audit_markers.ready_for_execution_at",
            required=False,
        ),
        ready_for_execution_price=_optional_positive_float(
            payload.get("ready_for_execution_price"),
            field="audit_markers.ready_for_execution_price",
        ),
        exit_watch_at=_timestamp_from_payload(
            payload.get("exit_watch_at"),
            field="audit_markers.exit_watch_at",
            required=False,
        ),
        exit_watch_price=_optional_positive_float(
            payload.get("exit_watch_price"),
            field="audit_markers.exit_watch_price",
        ),
        exit_ready_at=_timestamp_from_payload(
            payload.get("exit_ready_at"),
            field="audit_markers.exit_ready_at",
            required=False,
        ),
        exit_ready_price=_optional_positive_float(
            payload.get("exit_ready_price"),
            field="audit_markers.exit_ready_price",
        ),
    )


def _serialize_st_trade_memory(memory: STTradeMemory | None) -> dict[str, Any] | None:
    if memory is None:
        return None
    anchor = memory.initial_defended_anchor
    target = memory.initial_target_context
    return {
        "thesis_family": memory.thesis_family.value,
        "economic_mission": memory.economic_mission.value,
        "initial_defended_anchor": (
            None
            if anchor is None
            else {
                "kind": anchor.kind.value,
                "identity": anchor.identity,
                "timeframe": anchor.timeframe,
                "low": float(anchor.low),
                "high": float(anchor.high),
            }
        ),
        "initial_target_context": (
            None
            if target is None
            else {
                "identity": target.identity,
                "low": float(target.low),
                "high": float(target.high),
                "anchor_price": float(target.anchor_price),
                "roles": [role.value for role in target.roles],
            }
        ),
    }


def _deserialize_st_trade_memory(payload: Any) -> STTradeMemory | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("persisted ST trade memory must be a mapping")
    try:
        family = STThesisFamily(str(payload["thesis_family"]))
        mission = STEconomicMission(str(payload["economic_mission"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("persisted ST trade memory thesis/mission is invalid") from exc

    raw_anchor = payload.get("initial_defended_anchor")
    anchor: STInitialDefendedAnchor | None
    if raw_anchor is None:
        anchor = None
    else:
        if not isinstance(raw_anchor, Mapping):
            raise ValueError("persisted ST defended anchor must be a mapping")
        raw_identity = raw_anchor.get("identity")
        raw_timeframe = raw_anchor.get("timeframe")
        if not isinstance(raw_identity, str) or not isinstance(raw_timeframe, str):
            raise ValueError("persisted ST defended anchor identity/timeframe must be strings")
        try:
            anchor = STInitialDefendedAnchor(
                kind=STDefendedAnchorKind(str(raw_anchor["kind"])),
                identity=raw_identity,
                timeframe=raw_timeframe,
                low=float(raw_anchor["low"]),
                high=float(raw_anchor["high"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("persisted ST defended anchor is invalid") from exc

    raw_target = payload.get("initial_target_context")
    target: STInitialTargetContext | None
    if raw_target is None:
        target = None
    else:
        if not isinstance(raw_target, Mapping):
            raise ValueError("persisted ST initial target context must be a mapping")
        raw_identity = raw_target.get("identity")
        raw_roles = raw_target.get("roles")
        if not isinstance(raw_identity, str):
            raise ValueError("persisted ST initial target identity must be a string")
        if not isinstance(raw_roles, list) or any(not isinstance(role, str) for role in raw_roles):
            raise ValueError("persisted ST initial target roles must be a string list")
        try:
            target = STInitialTargetContext(
                identity=raw_identity,
                low=float(raw_target["low"]),
                high=float(raw_target["high"]),
                anchor_price=float(raw_target["anchor_price"]),
                roles=tuple(TargetPathRole(role) for role in raw_roles),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("persisted ST initial target context is invalid") from exc

    return STTradeMemory(
        thesis_family=family,
        economic_mission=mission,
        initial_defended_anchor=anchor,
        initial_target_context=target,
    )


def _serialize_st_exit_intent(intent: STExitIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    return {
        "family": intent.family.value,
        "committed_at": _timestamp_payload(intent.committed_at),
        "reasons": list(intent.reasons),
        "source_lineage": list(intent.source_lineage),
    }


def _string_list(payload: Mapping[str, Any], key: str, *, field: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{field} must be a string list")
    return tuple(raw)


def _deserialize_st_exit_intent(payload: Any) -> STExitIntent | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("persisted ST exit intent must be a mapping")
    try:
        family = STExitFamily(str(payload["family"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("persisted ST exit intent family is invalid") from exc
    return STExitIntent(
        family=family,
        committed_at=_timestamp_from_payload(
            payload.get("committed_at"),
            field="st_exit_intent.committed_at",
            required=True,
        ),
        reasons=_string_list(payload, "reasons", field="st_exit_intent.reasons"),
        source_lineage=_string_list(
            payload,
            "source_lineage",
            field="st_exit_intent.source_lineage",
        ),
    )


def _serialize_st_closed_exit(record: STClosedExitRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "trade_id": record.trade_id,
        "entry_as_of": _timestamp_payload(record.entry_as_of),
        "exit_as_of": _timestamp_payload(record.exit_as_of),
        "family": record.family.value,
        "intent_committed_at": _timestamp_payload(record.intent_committed_at),
        "reasons": list(record.reasons),
        "source_lineage": list(record.source_lineage),
    }


def _deserialize_st_closed_exit(payload: Any) -> STClosedExitRecord | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("persisted closed ST exit must be a mapping")
    raw_trade_id = payload.get("trade_id")
    if not isinstance(raw_trade_id, str):
        raise ValueError("persisted closed ST exit trade_id must be a string")
    try:
        family = STExitFamily(str(payload["family"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("persisted closed ST exit family is invalid") from exc
    return STClosedExitRecord(
        trade_id=raw_trade_id,
        entry_as_of=_timestamp_from_payload(
            payload.get("entry_as_of"),
            field="last_closed_st_exit.entry_as_of",
            required=True,
        ),
        exit_as_of=_timestamp_from_payload(
            payload.get("exit_as_of"),
            field="last_closed_st_exit.exit_as_of",
            required=True,
        ),
        family=family,
        intent_committed_at=_timestamp_from_payload(
            payload.get("intent_committed_at"),
            field="last_closed_st_exit.intent_committed_at",
            required=True,
        ),
        reasons=_string_list(payload, "reasons", field="last_closed_st_exit.reasons"),
        source_lineage=_string_list(
            payload,
            "source_lineage",
            field="last_closed_st_exit.source_lineage",
        ),
    )


def _require_canonical_st_state(state: TradeLifecycleState) -> None:
    """Fail closed when a v5 canonical ST position lacks required causal state."""

    metadata = state.entry_metadata
    is_open_st = (
        state.position is PositionState.OPEN
        and metadata is not None
        and metadata.entry_horizon is DecisionHorizon.SHORT_TERM
    )
    if is_open_st and metadata.st_trade_memory is None:
        raise ValueError("canonical persisted ST OPEN lifecycle requires ST trade memory")
    if is_open_st and state.st_economic_history is None:
        raise ValueError("canonical persisted ST OPEN lifecycle requires ST economic history")
    if (
        state.position is PositionState.OPEN
        and metadata is not None
        and metadata.entry_horizon is not DecisionHorizon.SHORT_TERM
        and state.st_economic_history is not None
    ):
        raise ValueError("non-ST persisted lifecycle cannot carry ST economic history")
    if state.st_exit_intent is not None and not is_open_st:
        raise ValueError("terminal ST exit intent requires canonical OPEN ST lifecycle")


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
            "execution_available_at": _timestamp_payload(metadata.execution_available_at),
            "execution_reason": metadata.execution_reason,
            "source_lineage": list(metadata.source_lineage),
            "st_trade_memory": _serialize_st_trade_memory(metadata.st_trade_memory),
        }

    return {
        "position": state.position.value,
        "exit_stage": None if state.exit_stage is None else state.exit_stage.value,
        "trade_id": state.trade_id,
        "entry_as_of": _timestamp_payload(state.entry_as_of),
        "entry_metadata": metadata_payload,
        "st_economic_history": serialize_st_economic_history(state.st_economic_history),
        "st_exit_intent": _serialize_st_exit_intent(state.st_exit_intent),
        "last_closed_st_exit": _serialize_st_closed_exit(state.last_closed_st_exit),
    }


def deserialize_trade_lifecycle_state(payload: Mapping[str, Any]) -> TradeLifecycleState:
    if not isinstance(payload, Mapping):
        raise ValueError("trade lifecycle state payload must be a mapping")
    try:
        position = PositionState(str(payload["position"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("trade lifecycle state position is invalid") from exc

    last_closed_st_exit = _deserialize_st_closed_exit(payload.get("last_closed_st_exit"))

    if position is PositionState.FLAT:
        if any(
            payload.get(key) is not None
            for key in (
                "exit_stage",
                "trade_id",
                "entry_as_of",
                "entry_metadata",
                "st_economic_history",
                "st_exit_intent",
            )
        ):
            raise ValueError("persisted FLAT lifecycle cannot carry open-position fields")
        return TradeLifecycleState(last_closed_st_exit=last_closed_st_exit)

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
        execution_available_at=_timestamp_from_payload(raw.get("execution_available_at"), field="entry_metadata.execution_available_at", required=True),
        execution_reason=str(raw.get("execution_reason", "")),
        source_lineage=tuple(source_lineage),
        st_trade_memory=_deserialize_st_trade_memory(raw.get("st_trade_memory")),
    )
    return TradeLifecycleState(
        position=position,
        exit_stage=exit_stage,
        trade_id=trade_id,
        entry_as_of=entry_as_of,
        entry_metadata=metadata,
        st_economic_history=deserialize_st_economic_history(payload.get("st_economic_history")),
        st_exit_intent=_deserialize_st_exit_intent(payload.get("st_exit_intent")),
        last_closed_st_exit=last_closed_st_exit,
    )


def decision_config_digest(config: Any) -> str:
    return sha256(repr(config).encode("utf-8")).hexdigest()


def causal_prefix_digest(
    snapshots: Iterable[Any],
    *,
    entry_execution_events: Mapping[Any, Any] | None = None,
    exit_execution_events: Mapping[Any, Any] | None = None,
) -> str:
    """Hash snapshots and same-bar execution inputs consumed by the lifecycle replay."""

    entry_events = entry_execution_events or {}
    exit_events = exit_execution_events or {}
    digest = sha256()
    for snapshot in snapshots:
        parts = (
            repr(snapshot),
            repr(entry_events.get(snapshot.as_of)),
            repr(exit_events.get(snapshot.as_of)),
        )
        for part in parts:
            payload = part.encode("utf-8")
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def serialize_trade_lifecycle_checkpoint(checkpoint: TradeLifecycleCheckpoint) -> dict[str, Any]:
    _require_canonical_st_state(checkpoint.state)
    return {
        "schema_version": TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
        "contract_version": CANONICAL_LIFECYCLE_CONTRACT_VERSION,
        "symbol": checkpoint.symbol,
        "prefix_count": checkpoint.prefix_count,
        "last_as_of": _timestamp_payload(checkpoint.last_as_of),
        "causal_prefix_digest": checkpoint.causal_prefix_digest,
        "decision_config_digest": checkpoint.decision_config_digest,
        "state": serialize_trade_lifecycle_state(checkpoint.state),
        "audit_markers": serialize_replay_audit_marker_state(checkpoint.audit_markers),
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
    causal_digest = payload.get("causal_prefix_digest")
    config_digest = payload.get("decision_config_digest")
    if not isinstance(causal_digest, str) or not isinstance(config_digest, str):
        raise ValueError("trade lifecycle checkpoint digests are invalid")
    state_payload = payload.get("state")
    if not isinstance(state_payload, Mapping):
        raise ValueError("trade lifecycle checkpoint state is invalid")
    marker_payload = payload.get("audit_markers")
    if not isinstance(marker_payload, Mapping):
        raise ValueError("trade lifecycle checkpoint audit_markers are invalid")
    state = deserialize_trade_lifecycle_state(state_payload)
    _require_canonical_st_state(state)
    return TradeLifecycleCheckpoint(
        symbol=expected_symbol,
        state=state,
        prefix_count=prefix_count,
        last_as_of=last_as_of,
        causal_prefix_digest=causal_digest,
        decision_config_digest=config_digest,
        audit_markers=deserialize_replay_audit_marker_state(marker_payload),
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
    "causal_prefix_digest",
    "decision_config_digest",
    "deserialize_replay_audit_marker_state",
    "deserialize_trade_lifecycle_checkpoint",
    "deserialize_trade_lifecycle_state",
    "serialize_replay_audit_marker_state",
    "serialize_trade_lifecycle_checkpoint",
    "serialize_trade_lifecycle_state",
]
