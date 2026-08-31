from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class DecisionFingerprint:
    timestamp: str
    action: str
    selected_horizon: str | None
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    execution: str
    lifecycle: str


@dataclass(frozen=True, slots=True)
class BarDecisionChange:
    timestamp: str
    classifications: tuple[str, ...]
    changed_fields: tuple[str, ...]
    before: DecisionFingerprint | None
    after: DecisionFingerprint | None


@dataclass(frozen=True, slots=True)
class DecisionDiffReport:
    status: str
    bar_changes: tuple[BarDecisionChange, ...]
    action_timing_changes: tuple[str, ...]
    classification_counts: Mapping[str, int]

    @property
    def is_empty(self) -> bool:
        return not self.bar_changes and not self.action_timing_changes

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "is_empty": self.is_empty,
            "classification_counts": dict(sorted(self.classification_counts.items())),
            "action_timing_changes": list(self.action_timing_changes),
            "bar_changes": [
                {
                    "timestamp": change.timestamp,
                    "classifications": list(change.classifications),
                    "changed_fields": list(change.changed_fields),
                    "before": None if change.before is None else asdict(change.before),
                    "after": None if change.after is None else asdict(change.after),
                }
                for change in self.bar_changes
            ],
        }


def _plain(value: Any) -> Any:
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, (str, int, float, bool)):
        return _plain(enum_value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return value


def _stable_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), default=str)


def _record_value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _snapshot(record: Any) -> Mapping[str, Any]:
    value = _record_value(record, "snapshot", {})
    return value if isinstance(value, Mapping) else {}


def _decision_values(record: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    blockers = _record_value(record, "blockers", ()) or ()
    waiting = _record_value(record, "waiting_for", ()) or ()
    snapshot_decision = _snapshot(record).get("decision")
    if isinstance(snapshot_decision, Mapping):
        if not blockers:
            blockers = snapshot_decision.get("blockers", ()) or ()
        if not waiting:
            waiting = snapshot_decision.get("waiting_for", ()) or ()
    return tuple(str(item) for item in blockers), tuple(str(item) for item in waiting)


def fingerprint_event(record: Any) -> DecisionFingerprint:
    snapshot = _snapshot(record)
    timestamp = _record_value(record, "timestamp")
    action = _record_value(record, "action", "")
    action = getattr(action, "value", action)
    blockers, waiting = _decision_values(record)

    selected_horizon = snapshot.get("entry_horizon")
    if selected_horizon is None:
        entry = snapshot.get("entry_decision")
        if isinstance(entry, Mapping):
            selected_horizon = entry.get("selected_horizon")

    execution = snapshot.get("execution")
    if execution is None:
        entry = snapshot.get("entry_decision")
        if isinstance(entry, Mapping):
            execution = {
                "state": entry.get("execution_state"),
                "event_consumed": entry.get("execution_event_consumed"),
            }
        elif isinstance(snapshot.get("position_exit"), Mapping):
            execution = snapshot["position_exit"].get("execution")

    lifecycle = snapshot.get("trade_lifecycle", {})
    return DecisionFingerprint(
        timestamp=str(_plain(timestamp)),
        action=str(action),
        selected_horizon=None if selected_horizon is None else str(selected_horizon),
        blockers=tuple(blockers),
        waiting_for=tuple(waiting),
        execution=_stable_json(execution),
        lifecycle=_stable_json(lifecycle),
    )


def canonical_fingerprints(events: Iterable[Any]) -> tuple[DecisionFingerprint, ...]:
    values = tuple(fingerprint_event(event) for event in events)
    timestamps = [item.timestamp for item in values]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("decision diff input must contain unique timestamps")
    return values


def _changed_fields(before: DecisionFingerprint, after: DecisionFingerprint) -> tuple[str, ...]:
    fields = (
        "action",
        "selected_horizon",
        "blockers",
        "waiting_for",
        "execution",
        "lifecycle",
    )
    return tuple(name for name in fields if getattr(before, name) != getattr(after, name))


def _field_classifications(changed: Sequence[str]) -> tuple[str, ...]:
    labels = {
        "action": "ACTION CHANGED",
        "selected_horizon": "SELECTED HORIZON CHANGED",
        "blockers": "BLOCKERS CHANGED",
        "waiting_for": "WAITING CHANGED",
        "execution": "EXECUTION CHANGED",
        "lifecycle": "LIFECYCLE CHANGED",
    }
    return tuple(labels[name] for name in changed)


def _action_presence_classification(prefix: str, action: str) -> str:
    action = action.upper()
    if action in {"BUY", "SELL"}:
        return f"{prefix} {action}"
    return f"{prefix} BAR"


def _time_key(value: str) -> tuple[int, Any]:
    text = value.strip()
    try:
        return (0, datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return (1, text)


def _action_times(values: Sequence[DecisionFingerprint], action: str) -> tuple[str, ...]:
    return tuple(item.timestamp for item in values if item.action.upper() == action)


def _timing_changes(
    before: Sequence[DecisionFingerprint],
    after: Sequence[DecisionFingerprint],
) -> tuple[str, ...]:
    changes: list[str] = []
    for action in ("BUY", "SELL"):
        left = _action_times(before, action)
        right = _action_times(after, action)
        paired = min(len(left), len(right))
        for index in range(paired):
            left_key = _time_key(left[index])
            right_key = _time_key(right[index])
            if right_key < left_key:
                changes.append(f"EARLIER {action}:{right[index]}<{left[index]}")
            elif right_key > left_key:
                changes.append(f"LATER {action}:{right[index]}>{left[index]}")
        if len(right) > len(left):
            for timestamp in right[len(left):]:
                changes.append(f"ADDED {action}:{timestamp}")
        elif len(left) > len(right):
            for timestamp in left[len(right):]:
                changes.append(f"REMOVED {action}:{timestamp}")
    return tuple(changes)


def compare_fingerprints(
    before: Iterable[DecisionFingerprint],
    after: Iterable[DecisionFingerprint],
) -> DecisionDiffReport:
    left = tuple(before)
    right = tuple(after)
    left_by_time = {item.timestamp: item for item in left}
    right_by_time = {item.timestamp: item for item in right}
    if len(left_by_time) != len(left) or len(right_by_time) != len(right):
        raise ValueError("decision fingerprints must have unique timestamps")

    changes: list[BarDecisionChange] = []
    counts: Counter[str] = Counter()
    timestamps = sorted(set(left_by_time) | set(right_by_time), key=_time_key)
    for timestamp in timestamps:
        before_item = left_by_time.get(timestamp)
        after_item = right_by_time.get(timestamp)
        if before_item is None:
            classification = _action_presence_classification("ADDED", after_item.action)
            counts[classification] += 1
            changes.append(
                BarDecisionChange(timestamp, (classification,), ("presence",), None, after_item)
            )
            continue
        if after_item is None:
            classification = _action_presence_classification("REMOVED", before_item.action)
            counts[classification] += 1
            changes.append(
                BarDecisionChange(timestamp, (classification,), ("presence",), before_item, None)
            )
            continue
        fields = _changed_fields(before_item, after_item)
        if not fields:
            continue
        classifications = list(_field_classifications(fields))
        if "action" in fields:
            if before_item.action.upper() in {"BUY", "SELL"}:
                classifications.append(f"REMOVED {before_item.action.upper()}")
            if after_item.action.upper() in {"BUY", "SELL"}:
                classifications.append(f"ADDED {after_item.action.upper()}")
        for classification in classifications:
            counts[classification] += 1
        changes.append(
            BarDecisionChange(
                timestamp,
                tuple(dict.fromkeys(classifications)),
                fields,
                before_item,
                after_item,
            )
        )

    timing = _timing_changes(left, right)
    for item in timing:
        counts[item.split(":", 1)[0]] += 1

    empty = not changes and not timing
    return DecisionDiffReport(
        status="UNCHANGED" if empty else "CHANGED",
        bar_changes=tuple(changes),
        action_timing_changes=timing,
        classification_counts=dict(counts),
    )


def compare_events(before: Iterable[Any], after: Iterable[Any]) -> DecisionDiffReport:
    return compare_fingerprints(canonical_fingerprints(before), canonical_fingerprints(after))


def _load_events(path: Path) -> tuple[Any, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        payload = payload.get("events")
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON event list or an object with an events list")
    return tuple(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare canonical decision-event JSON streams")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--require-empty", action="store_true")
    args = parser.parse_args()

    report = compare_events(_load_events(args.before), _load_events(args.after))
    print(json.dumps(report.to_payload(), indent=2, sort_keys=True, default=str))
    if args.require_empty and not report.is_empty:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
