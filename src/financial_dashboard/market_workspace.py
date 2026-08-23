from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from financial_dashboard.context.builder import (
    CrossDomainBuildInputs,
    CrossDomainBuildResult,
    build_cross_domain_context,
)
from financial_dashboard.data.analysis_inputs import cache_fingerprint, load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.pattern_compression_core import PatternCompressionConfig
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplay, HamMTFEvidenceReplayRunner
from financial_dashboard.stabil_support_replay import StabilSupportReplayResult, StabilSupportReplayRunner
from financial_dashboard.structure_location_replay import CachedStructureLocationMTFRunner, CausalBarClock
from financial_dashboard.target_evidence_replay import (
    FvgEngulfingMTFReplayRunner,
    LiquidityMTFReplayRunner,
    OrderBlockMTFReplayRunner,
    TargetEvidenceMTFReplay,
)
from financial_dashboard.targeting.adapters import support_resistance_evidence
from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.causal_inputs import clip_analysis_inputs_at_cutoff
from financial_dashboard.targeting.clustering import build_targeting_snapshot, deduplicate_origin_events
from financial_dashboard.targeting.enrichment import enrich_liquidity_scope
from financial_dashboard.targeting.models import TargetingSnapshot
from financial_dashboard.targeting.proximity import wilder_atr
from financial_dashboard.targeting.semantic_models import SemanticTargetingSnapshot
from financial_dashboard.three_domain_replay import CachedThreeDomainObserverRunner, ThreeDomainReplayResult
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplay, VolumeMTFEvidenceReplayRunner
from financial_dashboard.volatility_mtf_replay import (
    VOLATILITY_TIMEFRAMES,
    VolatilityMTFReplay,
    VolatilityMTFReplayRunner,
)


class WorkspaceDomainStatus(StrEnum):
    READY = "READY"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class WorkspaceDomainResult:
    status: WorkspaceDomainStatus
    result: Any | None = None
    error_type: str | None = None
    error_message: str | None = None

    @classmethod
    def ready(cls, result: Any) -> "WorkspaceDomainResult":
        return cls(status=WorkspaceDomainStatus.READY, result=result)

    @classmethod
    def failed(cls, error: Exception) -> "WorkspaceDomainResult":
        return cls(status=WorkspaceDomainStatus.ERROR, error_type=type(error).__name__, error_message=str(error))

    @property
    def is_ready(self) -> bool:
        return self.status is WorkspaceDomainStatus.READY


@dataclass(frozen=True, slots=True)
class MarketAnalysisWorkspace:
    symbol: str
    timeframes: tuple[str, ...]
    fingerprint: tuple[tuple[str, int, int], ...]
    observer: ThreeDomainReplayResult
    ham: WorkspaceDomainResult
    volume: WorkspaceDomainResult
    stabil_support: WorkspaceDomainResult
    volatility: WorkspaceDomainResult
    liquidity: WorkspaceDomainResult
    order_block: WorkspaceDomainResult
    fvg_engulfing: WorkspaceDomainResult
    targeting: WorkspaceDomainResult
    semantic_targeting: WorkspaceDomainResult
    cross_domain: WorkspaceDomainResult

    @property
    def ham_result(self) -> HamMTFEvidenceReplay | None:
        return self.ham.result if isinstance(self.ham.result, HamMTFEvidenceReplay) else None

    @property
    def volume_result(self) -> VolumeMTFEvidenceReplay | None:
        return self.volume.result if isinstance(self.volume.result, VolumeMTFEvidenceReplay) else None

    @property
    def stabil_support_result(self) -> StabilSupportReplayResult | None:
        return self.stabil_support.result if isinstance(self.stabil_support.result, StabilSupportReplayResult) else None

    @property
    def volatility_result(self) -> VolatilityMTFReplay | None:
        return self.volatility.result if isinstance(self.volatility.result, VolatilityMTFReplay) else None

    @property
    def liquidity_result(self) -> TargetEvidenceMTFReplay | None:
        return self.liquidity.result if isinstance(self.liquidity.result, TargetEvidenceMTFReplay) else None

    @property
    def order_block_result(self) -> TargetEvidenceMTFReplay | None:
        return self.order_block.result if isinstance(self.order_block.result, TargetEvidenceMTFReplay) else None

    @property
    def fvg_engulfing_result(self) -> TargetEvidenceMTFReplay | None:
        return self.fvg_engulfing.result if isinstance(self.fvg_engulfing.result, TargetEvidenceMTFReplay) else None

    @property
    def targeting_result(self) -> TargetingSnapshot | None:
        return self.targeting.result if isinstance(self.targeting.result, TargetingSnapshot) else None

    @property
    def semantic_targeting_result(self) -> SemanticTargetingSnapshot | None:
        return self.semantic_targeting.result if isinstance(self.semantic_targeting.result, SemanticTargetingSnapshot) else None

    @property
    def cross_domain_result(self) -> CrossDomainBuildResult | None:
        return self.cross_domain.result if isinstance(self.cross_domain.result, CrossDomainBuildResult) else None


class CacheSnapshotChangedError(RuntimeError):
    """Raised when cache files change while one workspace replay is being built."""


class MarketAnalysisWorkspaceRunner:
    """Execution coordinator for independent analysis domains with no trading authority."""

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def run(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        pattern_profile: str | None = None,
    ) -> MarketAnalysisWorkspace:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = normalize_timeframes(timeframes, supported=ANALYSIS_TIMEFRAMES, label="workspace")
        try:
            inputs = load_analysis_inputs(self.store, symbol=normalized_symbol, timeframes=normalized_timeframes)
        except RuntimeError as error:
            if "cache files changed" in str(error):
                raise CacheSnapshotChangedError(str(error)) from error
            raise

        pattern_config = None if pattern_profile is None else PatternCompressionConfig(profile=pattern_profile)
        observer = CachedThreeDomainObserverRunner(self.store, pattern_compression_config=pattern_config).run(
            symbol=normalized_symbol, timeframes=normalized_timeframes, input_snapshot=inputs
        )

        try:
            ham = WorkspaceDomainResult.ready(HamMTFEvidenceReplayRunner(self.store).replay(
                normalized_symbol, timeframes=normalized_timeframes, input_snapshot=inputs
            ))
        except Exception as error:
            ham = WorkspaceDomainResult.failed(error)

        try:
            volume = WorkspaceDomainResult.ready(VolumeMTFEvidenceReplayRunner(self.store).replay(
                normalized_symbol,
                timeframes=normalized_timeframes,
                structure_replay=observer.structure_location,
                input_snapshot=inputs,
            ))
        except Exception as error:
            volume = WorkspaceDomainResult.failed(error)

        try:
            stabil_support = WorkspaceDomainResult.ready(StabilSupportReplayRunner(self.store).replay(
                normalized_symbol, input_snapshot=inputs
            ))
        except Exception as error:
            stabil_support = WorkspaceDomainResult.failed(error)

        volatility_timeframes = tuple(tf for tf in VOLATILITY_TIMEFRAMES if tf in normalized_timeframes)
        if volatility_timeframes:
            try:
                volatility = WorkspaceDomainResult.ready(VolatilityMTFReplayRunner(self.store).replay(
                    normalized_symbol,
                    input_snapshot=inputs,
                    timeframes=volatility_timeframes,
                ))
            except Exception as error:
                volatility = WorkspaceDomainResult.failed(error)
        else:
            volatility = WorkspaceDomainResult.failed(ValueError("workspace has no supported volatility timeframe"))

        target_clock = CausalBarClock()
        reference_timeframe = "1h" if "1h" in normalized_timeframes else normalized_timeframes[0]
        reference_full_frame = inputs.for_timeframe(reference_timeframe).input_batch.frame
        reference_bar_time = reference_full_frame.iloc[-1]["timestamp"]
        target_as_of = target_clock.available_at(reference_bar_time, reference_timeframe)

        target_structure = None
        target_inputs = None
        reference_price: float | None = None
        reference_atr: float | None = None

        try:
            target_inputs = clip_analysis_inputs_at_cutoff(inputs, cutoff=target_as_of, clock=target_clock)
        except Exception as error:
            liquidity = WorkspaceDomainResult.failed(error)
            order_block = WorkspaceDomainResult.failed(error)
            fvg_engulfing = WorkspaceDomainResult.failed(error)
            targeting = WorkspaceDomainResult.failed(error)
            semantic_targeting = WorkspaceDomainResult.failed(error)
            cross_domain = WorkspaceDomainResult.failed(error)
        else:
            try:
                liquidity = WorkspaceDomainResult.ready(LiquidityMTFReplayRunner(self.store, clock=target_clock).replay(
                    normalized_symbol, timeframes=normalized_timeframes, input_snapshot=target_inputs
                ))
            except Exception as error:
                liquidity = WorkspaceDomainResult.failed(error)

            try:
                order_block = WorkspaceDomainResult.ready(OrderBlockMTFReplayRunner(self.store, clock=target_clock).replay(
                    normalized_symbol, timeframes=normalized_timeframes, input_snapshot=target_inputs
                ))
            except Exception as error:
                order_block = WorkspaceDomainResult.failed(error)

            try:
                fvg_engulfing = WorkspaceDomainResult.ready(FvgEngulfingMTFReplayRunner(self.store, clock=target_clock).replay(
                    normalized_symbol, timeframes=normalized_timeframes, input_snapshot=target_inputs
                ))
            except Exception as error:
                fvg_engulfing = WorkspaceDomainResult.failed(error)

            try:
                target_structure = CachedStructureLocationMTFRunner(self.store, clock=target_clock).run(
                    symbol=normalized_symbol, timeframes=normalized_timeframes, input_snapshot=target_inputs
                )
                reference_frame = target_inputs.for_timeframe(reference_timeframe).input_batch.frame
                reference_price = float(reference_frame.iloc[-1]["close"])
                reference_atr = wilder_atr(reference_frame)
                evidence = []
                structure_by_timeframe = {
                    timeframe: target_structure.replay_for(timeframe).market_structure
                    for timeframe in normalized_timeframes
                }
                if liquidity.is_ready and isinstance(liquidity.result, TargetEvidenceMTFReplay):
                    atr_by_timeframe = {
                        timeframe: liquidity.result.for_timeframe(timeframe).atr
                        for timeframe in liquidity.result.timeframes
                    }
                    evidence.extend(enrich_liquidity_scope(
                        liquidity.result.evidence,
                        structure_by_timeframe=structure_by_timeframe,
                        atr_by_timeframe=atr_by_timeframe,
                    ))
                for timeframe in normalized_timeframes:
                    replay = target_structure.replay_for(timeframe)
                    evidence.extend(support_resistance_evidence(
                        symbol=normalized_symbol,
                        timeframe=timeframe,
                        snapshot=replay.support_resistance,
                        clock=target_clock,
                    ))
                if order_block.is_ready and isinstance(order_block.result, TargetEvidenceMTFReplay):
                    evidence.extend(order_block.result.evidence)
                if fvg_engulfing.is_ready and isinstance(fvg_engulfing.result, TargetEvidenceMTFReplay):
                    evidence.extend(fvg_engulfing.result.evidence)
                deduped_evidence = deduplicate_origin_events(evidence, reference_atr=reference_atr)
                targeting = WorkspaceDomainResult.ready(build_targeting_snapshot(
                    symbol=normalized_symbol,
                    as_of=target_as_of,
                    current_price=reference_price,
                    reference_timeframe=reference_timeframe,
                    reference_atr=reference_atr,
                    evidence=deduped_evidence,
                ))
                semantic_targeting = WorkspaceDomainResult.ready(build_semantic_targeting_snapshot(
                    symbol=normalized_symbol,
                    as_of=target_as_of,
                    current_price=reference_price,
                    reference_atr=reference_atr,
                    evidence=deduped_evidence,
                ))
            except Exception as error:
                targeting = WorkspaceDomainResult.failed(error)
                semantic_targeting = WorkspaceDomainResult.failed(error)

            if target_structure is None or reference_price is None:
                cross_domain = WorkspaceDomainResult.failed(
                    RuntimeError("cross-domain shadow build requires target-bounded structure replay")
                )
            else:
                try:
                    quality_by_timeframe = {
                        timeframe: target_inputs.for_timeframe(timeframe).input_batch.source_quality.status
                        for timeframe in normalized_timeframes
                    }
                    atr_by_timeframe = {
                        timeframe: wilder_atr(target_inputs.for_timeframe(timeframe).input_batch.frame)
                        for timeframe in normalized_timeframes
                    }
                    unsupported = tuple(
                        f"{name.upper()}_ERROR"
                        for name, result in (
                            ("ham", ham),
                            ("volume", volume),
                            ("stabil_support", stabil_support),
                            ("volatility", volatility),
                            ("liquidity", liquidity),
                            ("order_block", order_block),
                            ("fvg_engulfing", fvg_engulfing),
                        )
                        if not result.is_ready
                    )
                    anchor_timeframe = (
                        "4h"
                        if "4h" in normalized_timeframes
                        else "2h"
                        if "2h" in normalized_timeframes
                        else reference_timeframe
                    )
                    cross_domain = WorkspaceDomainResult.ready(build_cross_domain_context(
                        CrossDomainBuildInputs(
                            symbol=normalized_symbol,
                            as_of=target_as_of,
                            anchor_timeframe=anchor_timeframe,
                            current_price=reference_price,
                            structure_location=target_structure,
                            available_at=target_clock.available_at,
                            data_quality_by_timeframe=quality_by_timeframe,
                            reference_atr_by_timeframe=atr_by_timeframe,
                            pattern_replay=observer,
                            liquidity_replay=liquidity.result if liquidity.is_ready else None,
                            order_block_replay=order_block.result if order_block.is_ready else None,
                            fvg_engulfing_replay=fvg_engulfing.result if fvg_engulfing.is_ready else None,
                            stabil_support_replay=stabil_support.result if stabil_support.is_ready else None,
                            participation_replay=volume.result if volume.is_ready else None,
                            volatility_replay=volatility.result if volatility.is_ready else None,
                            ham_replay=ham.result if ham.is_ready else None,
                            requested_timeframes=normalized_timeframes,
                            trigger_timeframes=tuple(
                                timeframe for timeframe in ("1h", "30m") if timeframe in normalized_timeframes
                            ),
                            unsupported_contexts=unsupported,
                        )
                    ))
                except Exception as error:
                    cross_domain = WorkspaceDomainResult.failed(error)

        after = cache_fingerprint(self.store, symbol=normalized_symbol, timeframes=normalized_timeframes)
        if after != inputs.fingerprint:
            raise CacheSnapshotChangedError("cache files changed while the analysis workspace was replaying")

        return MarketAnalysisWorkspace(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            fingerprint=inputs.fingerprint,
            observer=observer,
            ham=ham,
            volume=volume,
            stabil_support=stabil_support,
            volatility=volatility,
            liquidity=liquidity,
            order_block=order_block,
            fvg_engulfing=fvg_engulfing,
            targeting=targeting,
            semantic_targeting=semantic_targeting,
            cross_domain=cross_domain,
        )


def replay_market_workspace_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
    pattern_profile: str | None = None,
) -> MarketAnalysisWorkspace:
    return MarketAnalysisWorkspaceRunner(ParquetOHLCVStore(cache_root)).run(
        symbol=symbol, timeframes=timeframes, pattern_profile=pattern_profile
    )


__all__ = [
    "CacheSnapshotChangedError",
    "MarketAnalysisWorkspace",
    "MarketAnalysisWorkspaceRunner",
    "WorkspaceDomainResult",
    "WorkspaceDomainStatus",
    "replay_market_workspace_from_cache",
]
