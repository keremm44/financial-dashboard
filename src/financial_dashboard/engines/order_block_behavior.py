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
    DWELLING_INSIDE = "DWELLING_INSIDE"
    REACTION_HOLDING = "REACTION_HOLDING"
    HOLDING_FAVORABLE = "HOLDING_FAVORABLE"
    REACTION_CONFIRMED = "REACTION_CONFIRMED"
    CONSUMED = "CONSUMED"
    EXPIRED_CANDIDATE = "EXPIRED_CANDIDATE"


class OrderBlockInteractionState(StrEnum):
    """Current closed-bar relation between price and one canonical OB.

    This is deliberately separate from ``OrderBlockBehaviorState``. The existing
    behavior state keeps freshness/mitigation semantics, while this axis answers
    whether price is entering, dwelling inside, exiting, accepting outside, or has
    already confirmed a favorable reaction.
    """

    UNAVAILABLE = "UNAVAILABLE"
    OUTSIDE = "OUTSIDE"
    APPROACHING = "APPROACHING"
    ENTERED = "ENTERED"
    DWELLING_INSIDE = "DWELLING_INSIDE"
    EXITING_FAVORABLE = "EXITING_FAVORABLE"
    HOLDING_FAVORABLE = "HOLDING_FAVORABLE"
    REACTION_CONFIRMED = "REACTION_CONFIRMED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class OrderBlockBehaviorConfig:
    atr_length: int = 14
    near_atr: float = 0.75
    deep_fill_ratio: float = 0.50
    reaction_move_atr: float = 0.50
    dwell_bars: int = 2
    favorable_hold_bars: int = 2
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
        if self.dwell_bars < 2:
            raise ValueError("dwell_bars must be >= 2")
        if self.favorable_hold_bars < 1:
            raise ValueError("favorable_hold_bars must be >= 1")
        if self.terminal_retention_bars < 1:
            raise ValueError("terminal_retention_bars must be >= 1")


@dataclass(frozen=True, slots=True)
class OrderBlockBehaviorSnapshot:
    identity: str
    bullish: bool
    top: float
    bottom: float
    state: OrderBlockBehaviorState
    interaction: OrderBlockInteractionState
    active: bool
    age_bars: int
    bars_since_confirmation: int | None
    mitigation_count: int
    visit_count: int
    deepest_fill_ratio: float
    distance_atr: float | None
    total_inside_bars: int
    inside_close_bars: int
    current_visit_bars: int
    close_inside: bool
    range_intersects: bool
    first_entry_index: int | None
    last_entry_index: int | None
    favorable_exit_index: int | None
    bars_held_favorable: int
    max_favorable_move_atr: float
    terminal_reason: str | None = None


@dataclass
class _Episode:
    record: OrderBlockRecord
    first_seen_index: int
    confirmed_index: int | None = None
    mitigation_count: int = 0
    visit_count: int = 0
    deepest_fill_ratio: float = 0.0
    touched_previous_bar: bool = False
    last_touch_index: int | None = None
    last_touch_close: float | None = None
    total_inside_bars: int = 0
    inside_close_bars: int = 0
    current_visit_bars: int = 0
    visit_open: bool = False
    reentry_armed: bool = True
    first_entry_index: int | None = None
    last_entry_index: int | None = None
    favorable_exit_index: int | None = None
    bars_held_favorable: int = 0
    max_favorable_move_atr: float = 0.0
    reaction_confirmed: bool = False
    interaction: OrderBlockInteractionState = OrderBlockInteractionState.OUTSIDE
    terminal_index: int | None = None
    terminal_reason: str | None = None


class OrderBlockBehaviorTracker:
    """Incremental behavior read-model over canonical OrderBlockRecord objects.

    The source-faithful OrderBlock engine remains the only owner of candidate
    creation, imbalance confirmation, fill accumulation and removal. This tracker
    observes those immutable facts on closed bars and preserves a bounded terminal
    ledger so consumers can distinguish fresh, mitigated and consumed blocks.

    A visit is an *episode*, not a bar count. Consecutive bars interacting with the
    same OB count as one mitigation visit. A new visit is armed only after price has
    first separated to the favorable side and then returns. This prevents a long
    dwell inside one OB from being mislabeled as repeated mitigation.
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
        is_closed: bool = True,
        is_complete: bool = True,
    ) -> tuple[OrderBlockBehaviorSnapshot, ...]:
        # The behavior layer has the same causal contract as the canonical engine:
        # open/incomplete bars must not advance visits, dwell, holds, ATR, or state.
        if not is_closed or not is_complete:
            return self._snapshots

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

            intersects = self._entered(record, high=float(high), low=float(low)) and record.active
            close_inside = self._close_inside(record, close=float(close)) and record.active

            if record.active:
                self._update_interaction(
                    episode,
                    record,
                    bar_index=int(bar_index),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    atr=atr,
                    intersects=intersects,
                    close_inside=close_inside,
                )
            else:
                episode.interaction = OrderBlockInteractionState.UNAVAILABLE

            episode.deepest_fill_ratio = max(episode.deepest_fill_ratio, float(record.fill_ratio))
            episode.touched_previous_bar = intersects
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
                episode.interaction = (
                    OrderBlockInteractionState.UNAVAILABLE
                    if episode.confirmed_index is None
                    else OrderBlockInteractionState.FAILED
                )
                episode.visit_open = False
                episode.current_visit_bars = 0
                episode.bars_held_favorable = 0

        self._prune(int(bar_index))
        rows = [
            self._snapshot_for(
                identity,
                episode,
                bar_index=int(bar_index),
                high=float(high),
                low=float(low),
                close=float(close),
                atr=atr,
            )
            for identity, episode in self._episodes.items()
        ]
        self._snapshots = tuple(sorted(rows, key=lambda row: row.identity))
        return self._snapshots

    def _update_interaction(
        self,
        episode: _Episode,
        record: OrderBlockRecord,
        *,
        bar_index: int,
        high: float,
        low: float,
        close: float,
        atr: float,
        intersects: bool,
        close_inside: bool,
    ) -> None:
        favorable_close = self._favorable_close(record, close=close)
        fully_favorable = self._fully_favorable(record, high=high, low=low)

        # Enter only once per visit. Consecutive contact/inside bars remain the
        # same visit until price separates to the favorable side.
        if intersects and not episode.visit_open and episode.reentry_armed:
            episode.visit_open = True
            episode.reentry_armed = False
            episode.visit_count += 1
            episode.mitigation_count = episode.visit_count
            episode.current_visit_bars = 0
            episode.last_entry_index = bar_index
            if episode.first_entry_index is None:
                episode.first_entry_index = bar_index
            episode.favorable_exit_index = None
            episode.bars_held_favorable = 0
            episode.max_favorable_move_atr = 0.0
            episode.reaction_confirmed = False

        # If price closed favorably but returned before a clean separation bar, it
        # is still the same visit/interaction episode, not a new mitigation.
        if intersects and not episode.visit_open and not episode.reentry_armed:
            episode.visit_open = True
            episode.favorable_exit_index = None
            episode.bars_held_favorable = 0
            episode.max_favorable_move_atr = 0.0
            episode.reaction_confirmed = False

        if episode.visit_open and intersects:
            episode.current_visit_bars += 1
            episode.total_inside_bars += 1
            episode.last_touch_index = bar_index
            episode.last_touch_close = close
            if close_inside:
                episode.inside_close_bars += 1

        if episode.visit_open and favorable_close:
            # A favorable close marks the first accepted exit even if its wick still
            # overlaps the zone. Persistence is evaluated on later closed bars.
            episode.visit_open = False
            episode.favorable_exit_index = bar_index
            episode.bars_held_favorable = 1
            move = self._favorable_move_atr(record, close=close, atr=atr)
            episode.max_favorable_move_atr = max(episode.max_favorable_move_atr, move)
            episode.interaction = OrderBlockInteractionState.EXITING_FAVORABLE
            return

        if episode.favorable_exit_index is not None and favorable_close:
            episode.bars_held_favorable += 1
            move = self._favorable_move_atr(record, close=close, atr=atr)
            episode.max_favorable_move_atr = max(episode.max_favorable_move_atr, move)
            if fully_favorable:
                episode.reentry_armed = True
            if (
                episode.bars_held_favorable >= self.config.favorable_hold_bars
                and episode.max_favorable_move_atr >= self.config.reaction_move_atr
            ):
                episode.reaction_confirmed = True
                episode.interaction = OrderBlockInteractionState.REACTION_CONFIRMED
            elif episode.bars_held_favorable >= self.config.favorable_hold_bars:
                episode.interaction = OrderBlockInteractionState.HOLDING_FAVORABLE
            else:
                episode.interaction = OrderBlockInteractionState.EXITING_FAVORABLE
            return

        if intersects:
            if episode.current_visit_bars >= self.config.dwell_bars:
                episode.interaction = OrderBlockInteractionState.DWELLING_INSIDE
            else:
                episode.interaction = OrderBlockInteractionState.ENTERED
            return

        if episode.visit_count == 0:
            distance = self._distance_to_zone(record, close=close) / atr if atr > 0 else None
            episode.interaction = (
                OrderBlockInteractionState.APPROACHING
                if distance is not None and distance <= self.config.near_atr
                else OrderBlockInteractionState.OUTSIDE
            )
        else:
            episode.interaction = OrderBlockInteractionState.OUTSIDE

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

    @staticmethod
    def _close_inside(record: OrderBlockRecord, *, close: float) -> bool:
        return float(record.bottom) <= close <= float(record.top)

    @staticmethod
    def _favorable_close(record: OrderBlockRecord, *, close: float) -> bool:
        return close > float(record.top) if record.bullish else close < float(record.bottom)

    @staticmethod
    def _fully_favorable(record: OrderBlockRecord, *, high: float, low: float) -> bool:
        return low > float(record.top) if record.bullish else high < float(record.bottom)

    @staticmethod
    def _favorable_move_atr(record: OrderBlockRecord, *, close: float, atr: float) -> float:
        if atr <= 0:
            return 0.0
        raw = close - float(record.top) if record.bullish else float(record.bottom) - close
        return max(0.0, raw / atr)

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
        high: float,
        low: float,
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

        # Promote only mature interaction states into the existing single-state
        # compatibility field. This lets current cross-domain reaction projection
        # see dwell/acceptance without erasing first-touch/deep/revisit semantics.
        if not terminal and record.active:
            if episode.interaction is OrderBlockInteractionState.REACTION_CONFIRMED:
                state = OrderBlockBehaviorState.REACTION_CONFIRMED
            elif episode.interaction is OrderBlockInteractionState.HOLDING_FAVORABLE:
                state = OrderBlockBehaviorState.HOLDING_FAVORABLE
            elif episode.interaction is OrderBlockInteractionState.DWELLING_INSIDE:
                state = OrderBlockBehaviorState.DWELLING_INSIDE

        return OrderBlockBehaviorSnapshot(
            identity=identity,
            bullish=bool(record.bullish),
            top=float(record.top),
            bottom=float(record.bottom),
            state=state,
            interaction=episode.interaction,
            active=bool(record.active and not terminal),
            age_bars=age_bars,
            bars_since_confirmation=bars_since_confirmation,
            mitigation_count=int(episode.mitigation_count),
            visit_count=int(episode.visit_count),
            deepest_fill_ratio=float(episode.deepest_fill_ratio),
            distance_atr=None if distance is None else float(distance),
            total_inside_bars=int(episode.total_inside_bars),
            inside_close_bars=int(episode.inside_close_bars),
            current_visit_bars=int(episode.current_visit_bars),
            close_inside=self._close_inside(record, close=close) if record.active and not terminal else False,
            range_intersects=self._entered(record, high=high, low=low) if record.active and not terminal else False,
            first_entry_index=episode.first_entry_index,
            last_entry_index=episode.last_entry_index,
            favorable_exit_index=episode.favorable_exit_index,
            bars_held_favorable=int(episode.bars_held_favorable),
            max_favorable_move_atr=float(episode.max_favorable_move_atr),
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
    "OrderBlockInteractionState",
]
