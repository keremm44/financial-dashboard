from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .bist_session import BistEquitySession, filter_bist_session
from .provider import MarketDataProvider
from .schema import CANONICAL_COLUMNS, canonicalize_ohlcv


@dataclass(frozen=True, slots=True)
class GapFillReport:
    expected_closed_slots: int
    tv_bars: int
    yahoo_bars: int
    yahoo_overlap_with_tv: int
    yahoo_missing_slot_candidates: int
    yahoo_filled: int
    unresolved_gaps: int
    fallback_ratio: float
    unresolved_timestamps: tuple[pd.Timestamp, ...] = ()


class HybridBist5mProvider(MarketDataProvider):
    """TradingView-primary 5m provider with Yahoo used only for missing closed slots.

    Invariants:
    - a TradingView timestamp is never overwritten by Yahoo;
    - only closed, structurally valid Yahoo bars may fill a missing TV timestamp;
    - session filtering is applied before merge;
    - unresolved gaps remain gaps; no market data is fabricated.
    """

    def __init__(
        self,
        primary: MarketDataProvider,
        fallback: MarketDataProvider,
        *,
        session: BistEquitySession | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.session = session or BistEquitySession()
        self.last_gap_report = GapFillReport(0, 0, 0, 0, 0, 0, 0, 0.0, ())

    def _ts(self, value: datetime) -> pd.Timestamp:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize(self.session.timezone)
        return ts.tz_convert(self.session.timezone)

    @staticmethod
    def _valid_bar(row: pd.Series) -> bool:
        try:
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            v = float(row["volume"])
        except (TypeError, ValueError, KeyError):
            return False
        if any(pd.isna(x) for x in (o, h, l, c, v)):
            return False
        if v < 0:
            return False
        return h >= max(o, c) and l <= min(o, c) and h >= l

    def _expected_closed_slots(
        self,
        *,
        dates: set,
        start: datetime,
        end: datetime,
    ) -> pd.DatetimeIndex:
        start_ts = self._ts(start)
        end_ts = self._ts(end)
        slots: list[pd.Timestamp] = []
        for session_date in sorted(dates):
            if not self.session.is_trading_date(session_date):
                continue
            open_ts = pd.Timestamp.combine(session_date, self.session.open_time).tz_localize(self.session.timezone)
            close_ts = pd.Timestamp.combine(session_date, self.session.close_for(session_date)).tz_localize(self.session.timezone)
            current = open_ts
            while current + pd.Timedelta(minutes=5) <= close_ts:
                if current >= start_ts and current + pd.Timedelta(minutes=5) <= end_ts:
                    slots.append(current)
                current += pd.Timedelta(minutes=5)
        return pd.DatetimeIndex(slots)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        if timeframe.strip().lower() != "5m":
            raise ValueError("HybridBist5mProvider exposes only 5m bars")
        if pd.Timestamp(start) > pd.Timestamp(end):
            raise ValueError("start must be <= end")

        primary = filter_bist_session(
            self.primary.get_ohlcv(symbol, "5m", start, end), self.session
        )
        fallback = filter_bist_session(
            self.fallback.get_ohlcv(symbol, "5m", start, end), self.session
        )

        if not primary.empty:
            primary = primary.sort_values("timestamp", kind="stable").drop_duplicates(
                subset=["timestamp"], keep="last"
            )
        if not fallback.empty:
            fallback = fallback.sort_values("timestamp", kind="stable").drop_duplicates(
                subset=["timestamp"], keep="last"
            )

        primary_ts = set(pd.to_datetime(primary["timestamp"])) if not primary.empty else set()
        fallback_ts = set(pd.to_datetime(fallback["timestamp"])) if not fallback.empty else set()
        yahoo_overlap = len(primary_ts.intersection(fallback_ts))
        yahoo_missing_candidates = len(fallback_ts.difference(primary_ts))

        accepted_rows: list[pd.Series] = []
        if not fallback.empty:
            for _, row in fallback.iterrows():
                ts = pd.Timestamp(row["timestamp"])
                if ts in primary_ts:
                    continue
                if not bool(row.get("is_closed", False)):
                    continue
                if not self._valid_bar(row):
                    continue
                accepted = row.copy()
                accepted["source"] = "YAHOO_FALLBACK"
                accepted_rows.append(accepted)

        accepted_frame = (
            pd.DataFrame(accepted_rows, columns=fallback.columns)
            if accepted_rows
            else pd.DataFrame(columns=CANONICAL_COLUMNS)
        )
        merged = pd.concat([primary, accepted_frame], ignore_index=True)
        if merged.empty:
            self.last_gap_report = GapFillReport(
                0, 0, len(fallback), yahoo_overlap, yahoo_missing_candidates, 0, 0, 0.0, ()
            )
            return canonicalize_ohlcv(
                pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]),
                symbol=symbol,
                timeframe="5m",
                source="HYBRID_TV_YAHOO",
            )

        merged = merged.sort_values("timestamp", kind="stable").drop_duplicates(
            subset=["timestamp"], keep="first"
        ).reset_index(drop=True)

        observed_dates = set(pd.to_datetime(merged["timestamp"]).dt.date)
        expected = self._expected_closed_slots(
            dates=observed_dates,
            start=start,
            end=end,
        )
        merged_ts = set(pd.to_datetime(merged["timestamp"]))
        expected_set = set(expected)
        unresolved_set = expected_set.difference(merged_ts)
        unresolved_timestamps = tuple(sorted(unresolved_set))
        yahoo_filled = int((merged["source"] == "YAHOO_FALLBACK").sum())
        tv_bars = int(len(merged) - yahoo_filled)
        denominator = max(1, tv_bars + yahoo_filled)
        self.last_gap_report = GapFillReport(
            expected_closed_slots=len(expected),
            tv_bars=tv_bars,
            yahoo_bars=int(len(fallback)),
            yahoo_overlap_with_tv=yahoo_overlap,
            yahoo_missing_slot_candidates=yahoo_missing_candidates,
            yahoo_filled=yahoo_filled,
            unresolved_gaps=len(unresolved_timestamps),
            fallback_ratio=yahoo_filled / denominator,
            unresolved_timestamps=unresolved_timestamps,
        )
        return canonicalize_ohlcv(
            merged,
            symbol=symbol,
            timeframe="5m",
            source="HYBRID_TV_YAHOO",
            default_is_closed=False,
        )
