"""Stateful analysis-engine interfaces and shared result models."""

from .auction_engine import AuctionConfig, AuctionExport, AuctionVolumeProfileEngine
from .fvg_engulfing_engine import EngulfingFormation, FormationSnapshot, FvgFormation
from .fvg_engulfing import FvgEngulfingEngine
from .fvg_engulfing_final import EngulfingLifecycleRecord, EngulfingSideExport, FvgEngulfingExport, FvgLifecycleRecord, FvgSideExport
from .fvg_engulfing_models import EngulfingDirection, EngulfingState, FvgDirection, FvgEngulfingConfig, FvgEngulfingDataQuality, FvgState, SensitivityProfile
from .ham_evidence import FamilySnapshot, HamEvidenceConfig, HamEvidenceEngine, HamEvidenceSnapshot, HamFamily, HamFamilyEvidence, HamFamilyEvidenceSet, build_ham_family_evidence
from .liquidity_engine import LiquidityEngine, LiquidityExport
from .liquidity_models import LiquidityConfig, LiquidityPool, LiquidityPoolState, LiquiditySide, LiquidityTouch
from .market_structure import MarketStructureConfig
from .market_structure_engine import MarketStructureEngine
from .market_structure_events import (
    MarketStructureEventRecord,
    MarketStructureScopeSnapshot,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from .market_structure_evidence import MarketStructureExport
from .market_structure_history import (
    StructureHistoryBoundaryState,
    StructureHistoryDiagnostic,
    assess_structure_history,
)
from .market_structure_state import BosMaturity, BreakConfig
from .mtf_story_context import MTFStoryContextError, classify_context
from .mtf_story_engine import classify_story
from .mtf_story_models import ConflictSeverity, ContextAssessment, ContextState, MTFStoryResult, MTFStoryState, RawTimeframeEvidence, StoryConflict, TimeframeRole, TimeframeStoryState, TriggerAssessment, TriggerState, role_for_timeframe
from .mtf_story_normalizer import MTFStoryNormalizationError, normalize_timeframe_evidence
from .mtf_story_replay import replay_story_results
from .mtf_story_state_machine import MTFStoryStateMachine
from .mtf_story_trigger import MTFStoryTriggerError, classify_trigger
from .order_block import OrderBlockDataQuality, OrderBlockEngine, OrderBlockExport, OrderBlockSideExport
from .order_block_engine import OrderBlockConfig, OrderBlockRecord
from .pattern_compression_core import PatternCandidate


def _pattern_candidate_deepcopy(candidate: PatternCandidate, memo: dict[int, object]) -> PatternCandidate:
    """Clone scalar-only PatternCandidate state without recursive deepcopy traversal."""
    clone = PatternCandidate()
    memo[id(candidate)] = clone
    for slot in PatternCandidate.__slots__:
        setattr(clone, slot, getattr(candidate, slot))
    return clone


# PatternCandidate contains only scalar/immutable values. Preserve deepcopy isolation
# semantics while avoiding recursive deepcopy dispatch on every active-bar refresh.
PatternCandidate.__deepcopy__ = _pattern_candidate_deepcopy

from .pattern_compression_engine import PatternCompressionEngine
from .raw_indicator_dashboard import (
    EffectiveTrendSettings,
    IndicatorEvidence,
    RawDataQuality,
    RawIndicatorConfig,
    RawIndicatorDashboardEngine,
    RawIndicatorSnapshot,
    TrendProfile,
    TrendReason,
    VolumeQuality,
)
from .raw_indicator_dashboard_decision import (
    DecisionConfig,
    HamDashboardDecisionEngine,
    HamDashboardDecisionSnapshot,
    HamDashboardExport,
    SystemState,
)
from .stabil_trend_engine import ConfirmedStabilPivot, DailyRawState, DailyTrendSnapshot, DailyTrendState, GapState, H4EvidenceStatus, H4Lifecycle, H4TrendSnapshot, H4TrendState, StabilTrendConfig, StabilTrendContext, WeeklyTrendSnapshot, WeeklyTrendState
from .stabil_trend_final import StabilMainState, StabilReason, StabilTrendExport
from .stabil_trend_public import StabilTrendEngine
from .structure_location import (
    CausalZoneObservation,
    StructureLocationAnchor,
    StructureLocationMeaning,
    StructureLocationOutcome,
    StructureLocationOutcomeStatus,
    StructureZoneLink,
    StructureZoneLinkConfig,
    StructureZoneRelation,
    ZoneConfluenceCluster,
    ZoneConfluenceConfig,
    build_zone_confluence,
    evaluate_structure_event_location,
    link_structure_event_to_zones,
)
from .support_resistance_engine import ConfirmedPivot, RangeSnapshot, RangeState, SupportResistanceConfig, SupportResistanceExport, SupportResistanceRangeEngine
from .support_resistance_zones import SupportResistanceZone, ZoneKind, ZoneLifecycle, ZoneLifecycleEvent, ZoneSide
from .three_domain_observer import (
    CausalStructureEventObservation,
    CombinedObservationState,
    DirectionalStructureProgression,
    FOUNDATION_OBSERVER_TIMEFRAMES,
    LocationContextSnapshot,
    LocationContextState,
    MTFPressureSnapshot,
    MTFPressureState,
    ObserverTensionCode,
    OpposingZoneConflict,
    OpposingZoneConflictConfig,
    PressureChange,
    RecoveryStatus,
    StructureProgressionSnapshot,
    StructureProgressionStage,
    ThreeDomainObservation,
    ZoneRoleConflictKind,
    build_location_context,
    build_mtf_pressure,
    build_structure_progression,
    combine_three_domains,
    find_opposing_zone_conflicts,
)
from .volume_participation_engine import EffortResultClass, ParticipationExport, ParticipationState, VolumeLevel, VolumeParticipationConfig, VolumeParticipationMetrics
from .volume_participation_lifecycle import AbsorptionEvent, AbsorptionSide, AbsorptionStage, BreakParticipationEvent, BreakStage, ConfirmedParticipationPivot, LifecycleStage, ParticipationLifecycleConfig, ParticipationLifecycleExport
from .volume_participation_final import FinalParticipationState, UnifiedParticipationExport, VolumeParticipationEngine
from .volume_evidence import (
    ParticipationWithoutStructure,
    StructureVolumeLink,
    StructureVolumeRelation,
    StructureVolumeTiming,
    VolumeEvidenceDataQuality,
    VolumeEvidenceEngine,
    VolumeEvidenceSnapshot,
    VolumeEvidenceStatus,
    VolumeWindowEvidence,
    find_participation_without_structure,
    link_structure_event_to_volume,
    link_structure_events_to_volume,
)
from .volume_round2 import (
    CorrelatedVolumeChannel,
    CorrelatedVolumeDeduplication,
    LowerTimeframeImportance,
    LowerTimeframeInflowState,
    LowerTimeframeVolumeInflow,
    StructuralPropagationPhase,
    StructuralPropagationStep,
    StructureVolumeMTFAssessment,
    StructureVolumeRiskAssessment,
    StructureVolumeRiskState,
    StructureVolumeRiskTransition,
    StructureVolumeRiskTrigger,
    VolumeMTFContribution,
    VolumeMTFPressureContext,
    VolumeMTFPressureState,
    VolumeRound2Assessment,
    VolumeShockLifecycle,
    VolumeShockLifecycleConfig,
    VolumeShockStage,
    VolumeShockTransition,
    VolumeStructurePropagation,
    VolumeStructurePropagationState,
    build_correlated_volume_deduplication,
    build_event_mtf_assessments,
    build_mtf_pressure_context,
    build_shock_lifecycles,
    build_structural_propagations,
    build_structure_volume_risk,
    build_volume_round2_assessment,
)
from .volatility_bands_fib_engine import BandAgreement, BandState, DataQualityStatus, VolatilityBandsConfig, VolatilityBandsExport, VolatilityState
from .volatility_bands_fib_final import CoherenceState, DirectionBias, FibonacciState, StructureFibAlignment, StructureState, VolatilityBandsFibFinalExport
from .volatility_bands_fib import VolatilityBandsFibEngine

__all__ = [
    "MarketStructureEngine", "MarketStructureConfig", "BreakConfig", "BosMaturity", "MarketStructureExport", "MarketStructureEventRecord", "MarketStructureScopeSnapshot", "StructureEventConfirmation", "StructureEventValidity", "StructureEventRelevance", "StructureEventOutcome", "StructureHistoryBoundaryState", "StructureHistoryDiagnostic", "assess_structure_history",
    "PatternCompressionEngine", "OrderBlockEngine", "OrderBlockConfig", "OrderBlockRecord", "OrderBlockExport", "OrderBlockSideExport", "OrderBlockDataQuality",
    "FvgEngulfingEngine", "FvgEngulfingConfig", "FvgEngulfingDataQuality", "FvgFormation", "EngulfingFormation", "FormationSnapshot", "FvgLifecycleRecord", "EngulfingLifecycleRecord", "FvgEngulfingExport", "FvgSideExport", "EngulfingSideExport", "FvgDirection", "FvgState", "EngulfingDirection", "EngulfingState", "SensitivityProfile",
    "RawIndicatorDashboardEngine", "RawIndicatorConfig", "RawIndicatorSnapshot", "IndicatorEvidence", "RawDataQuality", "VolumeQuality", "TrendProfile", "TrendReason", "EffectiveTrendSettings",
    "HamEvidenceEngine", "HamEvidenceConfig", "HamEvidenceSnapshot", "HamFamily", "HamFamilyEvidence", "HamFamilyEvidenceSet", "build_ham_family_evidence",
    "HamDashboardDecisionEngine", "HamDashboardDecisionSnapshot", "HamDashboardExport", "DecisionConfig", "FamilySnapshot", "SystemState",
    "LiquidityEngine", "LiquidityExport", "LiquidityConfig", "LiquidityPool", "LiquidityPoolState", "LiquiditySide", "LiquidityTouch",
    "AuctionVolumeProfileEngine", "AuctionConfig", "AuctionExport",
    "SupportResistanceRangeEngine", "SupportResistanceConfig", "SupportResistanceExport", "RangeState", "RangeSnapshot", "ConfirmedPivot", "SupportResistanceZone", "ZoneKind", "ZoneSide", "ZoneLifecycle", "ZoneLifecycleEvent",
    "CausalZoneObservation", "ZoneConfluenceConfig", "ZoneConfluenceCluster", "StructureZoneLinkConfig", "StructureZoneLink", "StructureLocationAnchor", "StructureZoneRelation", "StructureLocationMeaning", "StructureLocationOutcomeStatus", "StructureLocationOutcome", "build_zone_confluence", "link_structure_event_to_zones", "evaluate_structure_event_location",
    "FOUNDATION_OBSERVER_TIMEFRAMES", "MTFPressureState", "PressureChange", "RecoveryStatus", "MTFPressureSnapshot", "CausalStructureEventObservation", "StructureProgressionStage", "DirectionalStructureProgression", "StructureProgressionSnapshot", "OpposingZoneConflictConfig", "ZoneRoleConflictKind", "OpposingZoneConflict", "LocationContextState", "LocationContextSnapshot", "ObserverTensionCode", "CombinedObservationState", "ThreeDomainObservation", "build_mtf_pressure", "build_structure_progression", "find_opposing_zone_conflicts", "build_location_context", "combine_three_domains",
    "VolumeParticipationEngine", "VolumeParticipationConfig", "VolumeParticipationMetrics", "ParticipationExport", "ParticipationState", "VolumeLevel", "EffortResultClass", "ParticipationLifecycleConfig", "ParticipationLifecycleExport", "LifecycleStage", "AbsorptionSide", "AbsorptionStage", "AbsorptionEvent", "BreakStage", "BreakParticipationEvent", "ConfirmedParticipationPivot", "FinalParticipationState", "UnifiedParticipationExport", "VolumeEvidenceEngine", "VolumeEvidenceSnapshot", "VolumeEvidenceStatus", "VolumeEvidenceDataQuality", "VolumeWindowEvidence", "StructureVolumeTiming", "StructureVolumeRelation", "StructureVolumeLink", "ParticipationWithoutStructure", "link_structure_event_to_volume", "link_structure_events_to_volume", "find_participation_without_structure",
    "VolumeRound2Assessment", "VolumeMTFPressureState", "VolumeMTFContribution", "VolumeMTFPressureContext", "LowerTimeframeInflowState", "LowerTimeframeImportance", "LowerTimeframeVolumeInflow", "StructureVolumeMTFAssessment", "StructureVolumeRiskState", "StructureVolumeRiskTrigger", "StructureVolumeRiskTransition", "StructureVolumeRiskAssessment", "VolumeShockStage", "VolumeShockTransition", "VolumeShockLifecycle", "VolumeShockLifecycleConfig", "StructuralPropagationPhase", "StructuralPropagationStep", "VolumeStructurePropagationState", "VolumeStructurePropagation", "CorrelatedVolumeChannel", "CorrelatedVolumeDeduplication", "build_mtf_pressure_context", "build_event_mtf_assessments", "build_structure_volume_risk", "build_shock_lifecycles", "build_structural_propagations", "build_correlated_volume_deduplication", "build_volume_round2_assessment",
    "StabilTrendEngine", "StabilTrendConfig", "StabilTrendContext", "StabilTrendExport", "StabilMainState", "StabilReason", "WeeklyTrendState", "WeeklyTrendSnapshot", "DailyRawState", "DailyTrendState", "DailyTrendSnapshot", "GapState", "H4TrendState", "H4Lifecycle", "H4EvidenceStatus", "H4TrendSnapshot", "ConfirmedStabilPivot",
    "VolatilityBandsFibEngine", "VolatilityBandsConfig", "VolatilityBandsExport", "VolatilityBandsFibFinalExport", "VolatilityState", "BandState", "BandAgreement", "DataQualityStatus", "StructureState", "FibonacciState", "StructureFibAlignment", "DirectionBias", "CoherenceState",
    "TimeframeRole", "RawTimeframeEvidence", "TimeframeStoryState", "ContextAssessment", "TriggerAssessment", "ContextState", "TriggerState", "MTFStoryState", "ConflictSeverity", "StoryConflict", "MTFStoryResult", "role_for_timeframe", "MTFStoryNormalizationError", "normalize_timeframe_evidence", "MTFStoryContextError", "classify_context", "MTFStoryTriggerError", "classify_trigger", "classify_story", "MTFStoryStateMachine", "replay_story_results",
]
