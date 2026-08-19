"""Stateful analysis-engine interfaces and shared result models."""

from .market_structure_engine import MarketStructureEngine
from .pattern_compression_engine import PatternCompressionEngine

__all__ = ["MarketStructureEngine", "PatternCompressionEngine"]
