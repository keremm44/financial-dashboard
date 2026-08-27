from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.engines.fvg_engulfing_models import SUPPORTED_TIMEFRAMES as FVG_TIMEFRAMES
from financial_dashboard.volatility_mtf_replay import VOLATILITY_TIMEFRAMES


class DomainDependency(StrEnum):
    """How a domain consumes causal closed-bar history."""

    STATELESS = "STATELESS"
    ROLLING_STATEFUL = "ROLLING_STATEFUL"
    STATEFUL_INCREMENTAL = "STATEFUL_INCREMENTAL"
    CAUSAL_PREFIX_DERIVED = "CAUSAL_PREFIX_DERIVED"


class RuntimeOwner(StrEnum):
    NATIVE = "NATIVE"
    SUPPORTING = "SUPPORTING"
    DERIVED = "DERIVED"


@dataclass(frozen=True, slots=True)
class DomainRuntimeContract:
    name: str
    timeframes: tuple[str, ...]
    dependency: DomainDependency
    owner: RuntimeOwner
    checkpointed: bool
    history_requirement: str
    new_bar_effect: str
    current_state_can_evolve: bool = True
    frozen_outputs_revisable: bool = False

    def supports(self, timeframe: str) -> bool:
        return timeframe.strip().lower() in self.timeframes


_ALL = tuple(ANALYSIS_TIMEFRAMES)

DOMAIN_RUNTIME_CONTRACTS: Mapping[str, DomainRuntimeContract] = MappingProxyType(
    {
        "structure": DomainRuntimeContract(
            name="structure",
            timeframes=_ALL,
            dependency=DomainDependency.STATEFUL_INCREMENTAL,
            owner=RuntimeOwner.NATIVE,
            checkpointed=True,
            history_requirement="continuation state plus the newly closed bar",
            new_bar_effect="may advance pivots, breaks, event lifecycle and current relevance",
        ),
        "support_resistance": DomainRuntimeContract(
            name="support_resistance",
            timeframes=_ALL,
            dependency=DomainDependency.STATEFUL_INCREMENTAL,
            owner=RuntimeOwner.NATIVE,
            checkpointed=True,
            history_requirement="continuation state plus the newly closed bar",
            new_bar_effect="may create, test, weaken, break or expire current zones",
        ),
        "pattern": DomainRuntimeContract(
            name="pattern",
            timeframes=_ALL,
            dependency=DomainDependency.STATEFUL_INCREMENTAL,
            owner=RuntimeOwner.NATIVE,
            checkpointed=True,
            history_requirement="continuation state, bounded geometry caches and the newly closed bar",
            new_bar_effect="may confirm pivots and advance candidate/break/retest lifecycle",
        ),
        "liquidity": DomainRuntimeContract(
            name="liquidity",
            timeframes=_ALL,
            dependency=DomainDependency.STATEFUL_INCREMENTAL,
            owner=RuntimeOwner.NATIVE,
            checkpointed=True,
            history_requirement="continuation state plus the newly closed bar",
            new_bar_effect="may create/test/sweep/consume liquidity pools and behavior",
        ),
        "order_block": DomainRuntimeContract(
            name="order_block",
            timeframes=_ALL,
            dependency=DomainDependency.STATEFUL_INCREMENTAL,
            owner=RuntimeOwner.NATIVE,
            checkpointed=True,
            history_requirement="continuation state plus the newly closed bar",
            new_bar_effect="may create, mitigate, consume or change interaction state",
        ),
        "fvg_engulfing": DomainRuntimeContract(
            name="fvg_engulfing",
            timeframes=tuple(tf for tf in ANALYSIS_TIMEFRAMES if tf in FVG_TIMEFRAMES),
            dependency=DomainDependency.STATEFUL_INCREMENTAL,
            owner=RuntimeOwner.NATIVE,
            checkpointed=True,
            history_requirement="continuation state plus the newly closed bar",
            new_bar_effect="may form, fill, invalidate or advance FVG/engulfing lifecycle",
        ),
        "ham": DomainRuntimeContract(
            name="ham",
            timeframes=_ALL,
            dependency=DomainDependency.ROLLING_STATEFUL,
            owner=RuntimeOwner.SUPPORTING,
            checkpointed=True,
            history_requirement="rolling indicator continuation state plus the newly closed bar",
            new_bar_effect="updates rolling indicators and HAM evidence state",
        ),
        "volume": DomainRuntimeContract(
            name="volume",
            timeframes=_ALL,
            dependency=DomainDependency.ROLLING_STATEFUL,
            owner=RuntimeOwner.SUPPORTING,
            checkpointed=True,
            history_requirement="participation/lifecycle continuation state plus the newly closed bar",
            new_bar_effect="updates participation state; derived Structure links are rebuilt from frozen histories",
        ),
        "volatility": DomainRuntimeContract(
            name="volatility",
            timeframes=tuple(tf for tf in ANALYSIS_TIMEFRAMES if tf in VOLATILITY_TIMEFRAMES),
            dependency=DomainDependency.ROLLING_STATEFUL,
            owner=RuntimeOwner.SUPPORTING,
            checkpointed=True,
            history_requirement="rolling volatility continuation state plus the newly closed bar",
            new_bar_effect="updates bands, volatility state and direction-transition lifecycle",
        ),
        "stabil": DomainRuntimeContract(
            name="stabil",
            timeframes=("1d",),
            dependency=DomainDependency.CAUSAL_PREFIX_DERIVED,
            owner=RuntimeOwner.DERIVED,
            checkpointed=False,
            history_requirement="the bounded canonical 1d causal prefix (currently max 200 bars)",
            new_bar_effect="rebuilds only the requested 1d causal support point; exact warm runs read frozen decision snapshots",
        ),
    }
)


def domain_contract(name: str) -> DomainRuntimeContract:
    key = name.strip().lower()
    try:
        return DOMAIN_RUNTIME_CONTRACTS[key]
    except KeyError as error:
        raise KeyError(f"unknown decision domain: {name}") from error


__all__ = [
    "DOMAIN_RUNTIME_CONTRACTS",
    "DomainDependency",
    "DomainRuntimeContract",
    "RuntimeOwner",
    "domain_contract",
]
