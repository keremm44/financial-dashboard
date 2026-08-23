from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterator

from financial_dashboard import market_workspace as workspace_module
from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.market_workspace import MarketAnalysisWorkspace


@dataclass(frozen=True, slots=True)
class RuntimeStageTiming:
    stage: str
    seconds: float
    calls: int


@dataclass(frozen=True, slots=True)
class WorkspaceRuntimeProfile:
    workspace: MarketAnalysisWorkspace
    stages: tuple[RuntimeStageTiming, ...]
    total_seconds: float

    def seconds_for(self, stage: str) -> float:
        return next((item.seconds for item in self.stages if item.stage == stage), 0.0)


@contextmanager
def _timed_attribute(
    owner: Any,
    attribute: str,
    stage: str,
    totals: dict[str, float],
    calls: dict[str, int],
) -> Iterator[None]:
    original = getattr(owner, attribute)

    def wrapped(*args: Any, **kwargs: Any):
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            totals[stage] = totals.get(stage, 0.0) + (perf_counter() - started)
            calls[stage] = calls.get(stage, 0) + 1

    setattr(owner, attribute, wrapped)
    try:
        yield
    finally:
        setattr(owner, attribute, original)


def profile_market_workspace_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
    pattern_profile: str | None = None,
) -> WorkspaceRuntimeProfile:
    """Measure real workspace stages without changing engine semantics.

    Instrumentation is temporary and process-local. It wraps the exact call sites used
    by ``MarketAnalysisWorkspaceRunner`` so timings describe application runtime rather
    than pytest collection/execution time.
    """

    totals: dict[str, float] = {}
    calls: dict[str, int] = {}
    targets: tuple[tuple[Any, str, str], ...] = (
        (workspace_module, "load_analysis_inputs", "input_load"),
        (workspace_module.CachedThreeDomainObserverRunner, "run", "observer"),
        (workspace_module.HamMTFEvidenceReplayRunner, "replay", "ham"),
        (workspace_module.VolumeMTFEvidenceReplayRunner, "replay", "volume"),
        (workspace_module.StabilSupportReplayRunner, "replay", "stabil_support"),
        (workspace_module.VolatilityMTFReplayRunner, "replay", "volatility"),
        (workspace_module, "clip_analysis_inputs_at_cutoff", "causal_clip"),
        (workspace_module.LiquidityMTFReplayRunner, "replay", "liquidity"),
        (workspace_module.OrderBlockMTFReplayRunner, "replay", "order_block"),
        (workspace_module.FvgEngulfingMTFReplayRunner, "replay", "fvg_engulfing"),
        (workspace_module.CachedStructureLocationMTFRunner, "run", "structure_location"),
        (workspace_module, "build_targeting_snapshot", "targeting"),
        (workspace_module, "build_semantic_targeting_snapshot", "semantic_targeting"),
        (workspace_module, "build_cross_domain_context", "cross_domain"),
    )

    started = perf_counter()
    with ExitStack() as stack:
        for owner, attribute, stage in targets:
            stack.enter_context(_timed_attribute(owner, attribute, stage, totals, calls))
        workspace = workspace_module.replay_market_workspace_from_cache(
            cache_root,
            symbol=symbol,
            timeframes=timeframes,
            pattern_profile=pattern_profile,
        )
    total = perf_counter() - started

    stages = tuple(
        RuntimeStageTiming(stage=stage, seconds=seconds, calls=calls.get(stage, 0))
        for stage, seconds in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    )
    return WorkspaceRuntimeProfile(
        workspace=workspace,
        stages=stages,
        total_seconds=total,
    )


__all__ = [
    "RuntimeStageTiming",
    "WorkspaceRuntimeProfile",
    "profile_market_workspace_from_cache",
]
