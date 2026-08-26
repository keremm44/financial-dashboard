from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import os
import pickle
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot


PERSISTENT_STATE_SCHEMA_VERSION = 2


def _safe_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned or "unknown"


def _stable_digest(parts: Iterable[str]) -> str:
    digest = sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def codebase_semantic_fingerprint() -> str:
    """Hash the installed financial_dashboard Python source tree.

    Persistent state must never silently survive a code change that can alter domain
    or decision-input semantics. The hash is conservative on purpose: any Python
    source change below the package invalidates application-owned cached state. The
    result is memoized for the process and costs only a small file-read pass at first
    cache access.
    """

    package_root = Path(__file__).resolve().parents[1]
    digest = sha256()
    for path in sorted(package_root.rglob("*.py"), key=lambda item: item.as_posix()):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(package_root).as_posix()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative.encode("utf-8"))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PersistentCacheIdentity:
    """Exact semantic identity for one trusted local persistent object."""

    namespace: str
    symbol: str
    semantic_fingerprint: str
    config_fingerprint: str
    source_fingerprint: tuple[tuple[str, int, int], ...]

    @property
    def digest(self) -> str:
        return _stable_digest(
            (
                str(PERSISTENT_STATE_SCHEMA_VERSION),
                codebase_semantic_fingerprint(),
                self.namespace,
                self.symbol,
                self.semantic_fingerprint,
                self.config_fingerprint,
                repr(self.source_fingerprint),
            )
        )


@dataclass(frozen=True, slots=True)
class PersistentCheckpointIdentity:
    """Stable identity for a checkpoint that may accept append-only new source rows."""

    namespace: str
    symbol: str
    semantic_fingerprint: str
    config_fingerprint: str

    @property
    def digest(self) -> str:
        return _stable_digest(
            (
                str(PERSISTENT_STATE_SCHEMA_VERSION),
                codebase_semantic_fingerprint(),
                self.namespace,
                self.symbol,
                self.semantic_fingerprint,
                self.config_fingerprint,
            )
        )


@dataclass(frozen=True, slots=True)
class PrefixFrameFingerprint:
    """Content identity for the already-consumed causal prefix of one timeframe."""

    timeframe: str
    row_count: int
    last_timestamp_ns: int | None
    digest: str


@dataclass(frozen=True, slots=True)
class PersistentCheckpointRecord:
    """Checkpoint payload plus the exact consumed-prefix identity it depends on."""

    identity: PersistentCheckpointIdentity
    prefixes: tuple[PrefixFrameFingerprint, ...]
    cursor: Any
    payload: Any


def fingerprint_frame_prefix(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    row_count: int,
) -> PrefixFrameFingerprint:
    if row_count < 0:
        raise ValueError("row_count must be non-negative")
    if row_count > len(frame):
        raise ValueError(
            f"cannot fingerprint {row_count} {timeframe} rows from frame of length {len(frame)}"
        )

    prefix = frame.iloc[:row_count]
    digest = sha256()
    digest.update(repr(tuple(str(column) for column in prefix.columns)).encode("utf-8"))
    digest.update(repr(tuple(str(dtype) for dtype in prefix.dtypes)).encode("utf-8"))
    if not prefix.empty:
        hashed = pd.util.hash_pandas_object(prefix, index=False, categorize=True)
        digest.update(hashed.to_numpy(copy=False).tobytes())

    last_timestamp_ns: int | None = None
    if row_count and "timestamp" in prefix.columns:
        last_timestamp_ns = int(pd.Timestamp(prefix.iloc[-1]["timestamp"]).value)

    return PrefixFrameFingerprint(
        timeframe=timeframe,
        row_count=row_count,
        last_timestamp_ns=last_timestamp_ns,
        digest=digest.hexdigest(),
    )


def build_prefix_fingerprints(
    inputs: AnalysisInputSnapshot,
    *,
    watermarks: Mapping[str, int],
) -> tuple[PrefixFrameFingerprint, ...]:
    fingerprints: list[PrefixFrameFingerprint] = []
    for timeframe in inputs.timeframes:
        if timeframe not in watermarks:
            raise ValueError(f"missing checkpoint watermark for {timeframe}")
        row_count = int(watermarks[timeframe]) + 1
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        fingerprints.append(
            fingerprint_frame_prefix(
                frame,
                timeframe=timeframe,
                row_count=row_count,
            )
        )
    return tuple(fingerprints)


def validate_append_only_prefix(
    inputs: AnalysisInputSnapshot,
    expected: Iterable[PrefixFrameFingerprint],
) -> bool:
    """Return True only when every previously consumed row is content-equivalent.

    Additional rows after the checkpoint are allowed. Any edit/reorder/removal in the
    consumed prefix invalidates the checkpoint instead of silently continuing from a
    semantically different history.
    """

    expected_by_timeframe = {item.timeframe: item for item in expected}
    if set(expected_by_timeframe) != set(inputs.timeframes):
        return False

    for timeframe in inputs.timeframes:
        item = expected_by_timeframe[timeframe]
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        if len(frame) < item.row_count:
            return False
        current = fingerprint_frame_prefix(
            frame,
            timeframe=timeframe,
            row_count=item.row_count,
        )
        if current != item:
            return False
    return True


class PersistentObjectStore:
    """Atomic trusted-local pickle store with fail-closed identity validation.

    Files live below the existing OHLCV cache root. They are application-owned local
    state, never user-supplied pickle payloads. Corrupt/incompatible entries are cache
    misses and may be replaced safely by a cold replay.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / ".decision_state"
        self.root.mkdir(parents=True, exist_ok=True)

    def _symbol_directory(self, symbol: str) -> Path:
        directory = self.root / _safe_part(symbol)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def path_for(self, identity: PersistentCacheIdentity) -> Path:
        return self._symbol_directory(identity.symbol) / (
            f"{_safe_part(identity.namespace)}__{identity.digest}.pkl"
        )

    def checkpoint_path_for(self, identity: PersistentCheckpointIdentity) -> Path:
        return self._symbol_directory(identity.symbol) / (
            f"{_safe_part(identity.namespace)}__checkpoint__{identity.digest}.pkl"
        )

    @staticmethod
    def _load_envelope(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                envelope = pickle.load(handle)
        except (OSError, EOFError, pickle.PickleError, AttributeError, ImportError, TypeError, ValueError):
            return None
        if not isinstance(envelope, dict):
            return None
        if envelope.get("schema_version") != PERSISTENT_STATE_SCHEMA_VERSION:
            return None
        return envelope

    @staticmethod
    def _atomic_save(path: Path, envelope: Mapping[str, Any]) -> Path:
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                pickle.dump(dict(envelope), handle, protocol=pickle.HIGHEST_PROTOCOL)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return path

    def load(self, identity: PersistentCacheIdentity) -> Any | None:
        envelope = self._load_envelope(self.path_for(identity))
        if envelope is None or envelope.get("identity") != identity:
            return None
        return envelope.get("payload")

    def save(self, identity: PersistentCacheIdentity, payload: Any) -> Path:
        return self._atomic_save(
            self.path_for(identity),
            {
                "schema_version": PERSISTENT_STATE_SCHEMA_VERSION,
                "identity": identity,
                "payload": payload,
            },
        )

    def remove(self, identity: PersistentCacheIdentity) -> None:
        self.path_for(identity).unlink(missing_ok=True)

    def load_checkpoint(
        self,
        identity: PersistentCheckpointIdentity,
    ) -> PersistentCheckpointRecord | None:
        envelope = self._load_envelope(self.checkpoint_path_for(identity))
        if envelope is None or envelope.get("identity") != identity:
            return None
        record = envelope.get("record")
        if not isinstance(record, PersistentCheckpointRecord):
            return None
        if record.identity != identity:
            return None
        return record

    def save_checkpoint(self, record: PersistentCheckpointRecord) -> Path:
        return self._atomic_save(
            self.checkpoint_path_for(record.identity),
            {
                "schema_version": PERSISTENT_STATE_SCHEMA_VERSION,
                "identity": record.identity,
                "record": record,
            },
        )

    def remove_checkpoint(self, identity: PersistentCheckpointIdentity) -> None:
        self.checkpoint_path_for(identity).unlink(missing_ok=True)


__all__ = [
    "PERSISTENT_STATE_SCHEMA_VERSION",
    "PersistentCacheIdentity",
    "PersistentCheckpointIdentity",
    "PersistentCheckpointRecord",
    "PersistentObjectStore",
    "PrefixFrameFingerprint",
    "build_prefix_fingerprints",
    "codebase_semantic_fingerprint",
    "fingerprint_frame_prefix",
    "validate_append_only_prefix",
]
