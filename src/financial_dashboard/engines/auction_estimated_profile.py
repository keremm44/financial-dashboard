from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from .auction_engine import (
    AuctionConfig,
    AuctionExport,
    AuctionProfile,
    AuctionVolumeProfileEngine,
    build_profile,
)
from .models import EngineResult


class AuctionProfileSource(StrEnum):
    OHLCV_ESTIMATED = "OHLCV_ESTIMATED"


class AuctionProfileQuality(StrEnum):
    OK = "OK"
    LIMITED_HISTORY = "LIMITED_HISTORY"
    NO_VALID_VOLUME = "NO_VALID_VOLUME"
    NO_COMPLETE_BARS = "NO_COMPLETE_BARS"


@dataclass(frozen=True, slots=True)
class AuctionProfileProvenance:
    source: AuctionProfileSource = AuctionProfileSource.OHLCV_ESTIMATED
    method: str = "BAR_VOLUME_DISTRIBUTED_BY_HIGH_LOW_BIN_OVERLAP"
    is_true_price_at_volume: bool = False
    is_tick_profile: bool = False
    is_footprint: bool = False
    bars_used: int = 0
    expected_lookback_bars: int = 0
    history_fraction: float = 0.0
    source_volume: float = 0.0
    allocation_error_pct: float | None = None
    value_area_coverage_pct: float | None = None


@dataclass(frozen=True, slots=True)
class EstimatedAuctionSnapshot:
    timeframe: str
    as_of: object | None
    result: EngineResult | None
    export: AuctionExport
    profile: AuctionProfile
    provenance: AuctionProfileProvenance
    data_quality: AuctionProfileQuality

    @property
    def poc(self) -> float | None:
        return self.export.poc

    @property
    def vah(self) -> float | None:
        return self.export.vah

    @property
    def val(self) -> float | None:
        return self.export.val


class EstimatedAuctionProfileEngine:
    """Causal OHLCV-estimated Auction/Volume Profile facade.

    This intentionally preserves the legacy Auction engine math while making the
    data boundary explicit: candle volume is distributed across each candle's
    High-Low range by price-bin overlap. Therefore POC/VAH/VAL/HVN/LVN are estimated
    OHLCV geometry, not exchange price-at-volume, tick, footprint or bid/ask data.
    """

    def __init__(self, config: AuctionConfig | None = None) -> None:
        self.config = config or AuctionConfig()

    @staticmethod
    def _clean(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        out = frame.copy()
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = sorted(required.difference(out.columns))
        if missing:
            raise ValueError(f"auction frame missing required columns: {missing}")
        if "is_closed" in out.columns:
            out = out[out["is_closed"].fillna(False).astype(bool)]
        if "is_complete" in out.columns:
            out = out[out["is_complete"].fillna(False).astype(bool)]
        return out.sort_values("timestamp", kind="stable").reset_index(drop=True)

    def analyze(self, frame: pd.DataFrame) -> EstimatedAuctionSnapshot:
        clean = self._clean(frame)
        if clean.empty:
            profile = AuctionProfile()
            return EstimatedAuctionSnapshot(
                timeframe=self.config.timeframe,
                as_of=None,
                result=None,
                export=AuctionExport(),
                profile=profile,
                provenance=AuctionProfileProvenance(
                    expected_lookback_bars=self.config.preset.lookback,
                ),
                data_quality=AuctionProfileQuality.NO_COMPLETE_BARS,
            )

        rows = clean.to_dict("records")
        profile = build_profile(rows, self.config)
        engine = AuctionVolumeProfileEngine(self.config)
        results = engine.replay(clean)
        result = results[-1] if results else engine.snapshot()
        export = engine.export_contract or AuctionExport()
        expected = self.config.preset.lookback
        fraction = min(1.0, profile.bars_used / float(expected)) if expected else 1.0
        if not profile.valid or profile.source_volume <= 0:
            quality = AuctionProfileQuality.NO_VALID_VOLUME
        elif profile.bars_used < expected:
            quality = AuctionProfileQuality.LIMITED_HISTORY
        else:
            quality = AuctionProfileQuality.OK

        provenance = AuctionProfileProvenance(
            bars_used=profile.bars_used,
            expected_lookback_bars=expected,
            history_fraction=fraction,
            source_volume=profile.source_volume,
            allocation_error_pct=profile.allocation_error_pct,
            value_area_coverage_pct=profile.value_area_coverage_pct,
        )
        return EstimatedAuctionSnapshot(
            timeframe=self.config.timeframe,
            as_of=clean.iloc[-1]["timestamp"],
            result=result,
            export=export,
            profile=profile,
            provenance=provenance,
            data_quality=quality,
        )


__all__ = [
    "AuctionProfileProvenance",
    "AuctionProfileQuality",
    "AuctionProfileSource",
    "EstimatedAuctionProfileEngine",
    "EstimatedAuctionSnapshot",
]
