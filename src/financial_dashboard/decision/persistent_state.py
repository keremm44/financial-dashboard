from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os
import pickle
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot


PERSISTENT_STATE_SCHEMA_VERSION = 1


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


@dataclass(frozen=True, slots=True)
class PersistentCacheIdentity:
    """Exact semantic identity for one trusted local persistent object.

    Persistent state is intentionally fail-closed. A cache entry is reusable only
    when the semantic/config/source identity matches exactly. Append-only runtime
    checkpoints use :class:`PrefixFrameFingerprint` separately so new bars may be
    accepted without treating a changed historical prefix as valid.
    """

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
                self.namespace,
                self.symbol,
                self.semantic_fingerprint,
                self.config_fingerprint,
                repr(self.source_fingerprint),
            )
        )


@dataclass(frozen=True, slots=True)
class PrefixFrameFingerprint:
    """Content identity for the already-consumed causal prefix of one timeframe."""

    timeframe: str
    row_count: int
    last_timestamp_ns: int | None
    digest: str


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
    """Return True only when every previously consumed row is byte-content equivalent.

    Additional rows after the checkpoint are allowed. Any edit/reorder/removal in the
    consumed prefix invalidates the checkpoint instead of silently replaying from a
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
    """Atomic trusted-local pickle store with exact identity validation.

    The files live below the existing OHLCV cache root. They are application-owned
    local state, never user-supplied pickle payloads. Corrupt/incompatible entries are
    treated as cache misses and may be safely replaced by a cold replay.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / ".decision_state"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, identity: PersistentCacheIdentity) -> Path:
        directory = self.root / _safe_part(identity.symbol)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{_safe_part(identity.namespace)}__{identity.digest}.pkl"

    def load(self, identity: PersistentCacheIdentity) -> Any | None:
        path = self.path_for(identity)
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
        if envelope.get("identity") != identity:
            return None
        return envelope.get("payload")

    def save(self, identity: PersistentCacheIdentity, payload: Any) -> Path:
        path = self.path_for(identity)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        envelope = {
            "schema_version": PERSISTENT_STATE_SCHEMA_VERSION,
            "identity": identity,
            "payload": payload,
        }
        try:
            with temporary.open("wb") as handle:
                pickle.dump(envelope, handle, protocol=pickle.HIGHEST_PROTOCOL)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return path

    def remove(self, identity: PersistentCacheIdentity) -> None:
        self.path_for(identity).unlink(missing_ok=True)


__all__ = [
    "PERSISTENT_STATE_SCHEMA_VERSION",
    "PersistentCacheIdentity",
    "PersistentObjectStore",
    "PrefixFrameFingerprint",
    "build_prefix_fingerprints",
    "fingerprint_frame_prefix",
    "validate_append_only_prefix",
]
