from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from .order_block_engine import OrderBlockConfig, OrderBlockRecord


class OrderBlockBehaviorState(StrEnum):
    CANDIDATE = "CANDIDATE"
    FRESH = "FRESH"
    APPROACHING = "APPROACHING"
    FIRST_MITIGATION = "FIRST_MITIGATION"
    PARTIALLY_MITIGATED = "PARTIALLY_MITIGATED"
    DEEP_MITIGATION = "DEEP_MITIGATION"
    REPEATED_MITIGATION = "REPEATED_MITIGATION"
    REACTION_HOLDING = "REACTION_HOLDING"
    CONSUMED = "CONSUMED"
    EXPIRED_CANDIDATE = "EXPIRED_CANDIDATE"


@dataclass(frozen=True, slots=True)
class OrderBlockBehaviorConfig:
    atr_length: int = 14
    near_atr: float = 0.75
    deep_fill_ratio: float = 0.50
    reaction_move_atr: float = 0.50
    terminal_retention_bars: int = 24

    def __post_init__(self) -> None:
        if self.atr_length < 2:
            raise ValueError("atr_length must be >= 2")
        if self.near_atr <= 0:
            raise ValueError("near_atr must be positive")
        if not 0 < self.deep_fill_ratio < 1:
            raise ValueError("deep_fill_ratio must be between 0 and 1")
        if self.reaction_move_atr <= 0:
            raise ValueError("reaction_move_atr must be positive")
        if self.terminal_retention_bars < 1:
            raise ValueError("terminal_retention_bars must be >= 1")


@dataclass(frozen=True, slots=True)
class OrderBlockBehaviorSnapshot:
    identity: str
    bullish: bool
    top: float
    bottom: float
    state: OrderBlockBehaviorState
    active: bool
    age_bars: int
    bars_since_confirmation: int | None
    mitigation_count: int
    deepest_fill_ratio: float
    distance_atr: float | None
    terminal_reason: str | None = None


@dataclass
class _Episode:
    record: OrderBlockRecord
    first_seen_index: int
    confirmed_index: int | None = None
    mitigation_count: int = 0
    deepest_fill_ratio: float = 0.0
    touched_previous_bar: bool = False
    last_touch_index: int | None = None
    last_touch_close: float | None = None
    terminal_index: int | None = None
    terminal_reason: str | None = None


class OrderBlockBehaviorTracker:
    """Incremental behavior read-model over canonical OrderBlockRecord objects.

    The source-faithful OrderBlock engine remains the only owner of candidate
    creation, imbalance confirmation, fill accumulation and removal. This tracker
    observes those immutable facts on closed bars and preserves a bounded terminal
    ledger so consumers can distinguish fresh, mitigated and consumed blocks.
    """

    def __init__(
        self,
        core_config: OrderBlockConfig | None = None,
        behavior_config: OrderBlockBehaviorConfig | None = None,
    ) -> None:
        self.core_config = core_config or OrderBlockConfig()
        self.config = behavior_config or OrderBlockBehaviorConfig()
        self.reset()

    def reset(self) -> None:
        self._episodes: dict[str, _Episode] = {}
        self._snapshots: tuple[OrderBlockBehaviorSnapshot, ...] = ()
        self._prev_close: float | None = None
        self._tr_values: list[float] = []
        self._atr: float | None = None

    @staticmethod
    def identity(record: OrderBlockRecord) -> str:
        return f"OB:{record.source_index}:{1 if record.bullish else -1}"

    @property
    def snapshots(self) -> tuple[OrderBlockBehaviorSnapshot, ...]:
        return self._snapshots

    def update(
        self,
        records: Iterable[OrderBlockRecord],
        *,
        bar_index: int,
        high: float,
        low: float,
        close: float,
    ) -> tuple[OrderBlockBehaviorSnapshot, ...]:
        atr = self._update_atr(float(high), float(low), float(close))
        current = {self.identity(record): record for record in records}

        for identity, record in current.items():
            episode = self._episodes.get(identity)
            if episode is None:
                episode = _Episode(record=record, first_seen_index=int(bar_index))
                self._episodes[identity] = episode
            episode.record = record
            if record.active and episode.confirmed_index is None:
                episode.confirmed_index = int(bar_index)

            touched = self._entered(record, high=float(high), low=float(low)) and record.active
            if touched and episode.last_touch_index != int(bar_index):
                episode.mitigation_count += 1
                episode.last_touch_index = int(bar_index)
                episode.last_touch_close = float(close)
            episode.deepest_fill_ratio = max(episode.deepest_fill_ratio, float(record.fill_ratio))
            episode.touched_previous_bar = touched
            episode.terminal_index = None
            episode.terminal_reason = None

        missing = [identity for identity in self._episodes if identity not in current]
        for identity in missing:
            episode = self._episodes[identity]
            if episode.terminal_index is None:
                episode.terminal_index = int(bar_index)
                episode.terminal_reason = self._terminal_reason(
                    episode.record,
                    bar_index=int(bar_index),
                    high=float(high),
                    low=float(low),
                )

        self._prune(int(bar_index))
        rows = [
            self._snapshot_for(identity, episode, bar_index=int(bar_index), close=float(close), atr=atr)
            for identity, episode in self._episodes.items()
        ]
        self._snapshots = tuple(sorted(rows, key=lambda row: row.identity))
        return self._snapshots

    def _update_atr(self, high: float, low: float, close: float) -> float:
        tr = high - low
        if self._prev_close is not None:
            tr = max(tr, abs(high - self._prev_close), abs(low - self._prev_close))
        self._tr_values.append(float(tr))
        length = self.config.atr_length
        if len(self._tr_values) == length:
            self._atr = sum(self._tr_values[-length:]) / length
        elif len(self._tr_values) > length:
            assert self._atr is not None
            self._atr = (self._atr * (length - 1) + tr) / length
        self._prev_close = close
        return max(float(self._atr if self._atr is not None else tr), self.core_config.minimum_tick)

    @staticmethod
    def _entered(record: OrderBlockRecord, *, high: float, low: float) -> bool:
        return low <= float(record.top) and high >= float(record.bottom)

    def _terminal_reason(self, record: OrderBlockRecord, *, bar_index: int, high: float, low: float) -> str:
        if not record.has_imbalance and bar_index > int(record.imbalance_end_index):
            return "IMBALANCE_NOT_CONFIRMED"
        bullish_gap_through = record.bullish and high < float(record.bottom)
        bearish_gap_through = (not record.bullish) and low > float(record.top)
        if bullish_gap_through or bearish_gap_through:
            return "GAP_THROUGH"
        if float(record.fill_ratio) >= self.core_config.fill_cancel_threshold:
            return "FILL_THRESHOLD"
        return "REMOVED_BY_CANONICAL_ENGINE"

    def _snapshot_for(
        self,
        identity: str,
        episode: _Episode,
        *,
        bar_index: int,
        close: float,
        atr: float,
    ) -> OrderBlockBehaviorSnapshot:
        record = episode.record
        terminal = episode.terminal_index is not None
        distance = self._distance_to_zone(record, close=close) / atr if atr > 0 else None
        age_bars = max(0, bar_index - int(record.source_index))
        bars_since_confirmation = (
            None if episode.confirmed_index is None else max(0, bar_index - episode.confirmed_index)
        )

        if terminal:
            state = (
                OrderBlockBehaviorState.EXPIRED_CANDIDATE
                if episode.confirmed_index is None
                else OrderBlockBehaviorState.CONSUMED
            )
        elif not record.active:
            state = OrderBlockBehaviorState.CANDIDATE
        elif episode.mitigation_count >= 2:
            state = OrderBlockBehaviorState.REPEATED_MITIGATION
        elif episode.deepest_fill_ratio >= self.config.deep_fill_ratio:
            state = OrderBlockBehaviorState.DEEP_MITIGATION
        elif episode.mitigation_count == 1:
            if self._reaction_holding(record, close=close, atr=atr, episode=episode):
                state = OrderBlockBehaviorState.REACTION_HOLDING
            elif episode.deepest_fill_ratio > 0:
                state = OrderBlockBehaviorState.PARTIALLY_MITIGATED
            else:
                state = OrderBlockBehaviorState.FIRST_MITIGATION
        elif distance is not None and distance <= self.config.near_atr:
            state = OrderBlockBehaviorState.APPROACHING
        else:
            state = OrderBlockBehaviorState.FRESH

        return OrderBlockBehaviorSnapshot(
            identity=identity,
            bullish=bool(record.bullish),
            top=float(record.top),
            bottom=float(record.bottom),
            state=state,
            active=bool(record.active and not terminal),
            age_bars=age_bars,
            bars_since_confirmation=bars_since_confirmation,
            mitigation_count=int(episode.mitigation_count),
            deepest_fill_ratio=float(episode.deepest_fill_ratio),
            distance_atr=None if distance is None else float(distance),
            terminal_reason=episode.terminal_reason,
        )

    def _reaction_holding(self, record: OrderBlockRecord, *, close: float, atr: float, episode: _Episode) -> bool:
        if episode.last_touch_close is None or episode.touched_previous_bar:
            return False
        move = (close - episode.last_touch_close) / atr if record.bullish else (episode.last_touch_close - close) / atr
        return move >= self.config.reaction_move_atr

    @staticmethod
    def _distance_to_zone(record: OrderBlockRecord, *, close: float) -> float:
        if float(record.bottom) <= close <= float(record.top):
            return 0.0
        if close < float(record.bottom):
            return float(record.bottom) - close
        return close - float(record.top)

    def _prune(self, bar_index: int) -> None:
        stale = [
            identity
            for identity, episode in self._episodes.items()
            if episode.terminal_index is not None
            and bar_index - episode.terminal_index > self.config.terminal_retention_bars
        ]
        for identity in stale:
            self._episodes.pop(identity, None)


__all__ = [
    "OrderBlockBehaviorConfig",
    "OrderBlockBehaviorSnapshot",
    "OrderBlockBehaviorState",
    "OrderBlockBehaviorTracker",
]
