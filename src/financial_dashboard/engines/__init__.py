"""Stateful analysis-engine interfaces and shared result models."""

from .market_structure_engine import MarketStructureEngine
from .mtf_story_models import (
    ConflictSeverity,
    ContextState,
    MTFStoryResult,
    MTFStoryState,
    RawTimeframeEvidence,
    StoryConflict,
    TimeframeRole,
    TimeframeStoryState,
    TriggerState,
    role_for_timeframe,
)
from .mtf_story_normalizer import MTFStoryNormalizationError, normalize_timeframe_evidence
from .pattern_compression_engine import PatternCompressionEngine

__all__ = [
    "MarketStructureEngine",
    "PatternCompressionEngine",
    "TimeframeRole",
    "RawTimeframeEvidence",
    "TimeframeStoryState",
    "ContextState",
    "TriggerState",
    "MTFStoryState",
    "ConflictSeverity",
    "StoryConflict",
    "MTFStoryResult",
    "role_for_timeframe",
    "MTFStoryNormalizationError",
    "normalize_timeframe_evidence",
]
