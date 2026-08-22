from __future__ import annotations

import pandas as pd

from financial_dashboard.market_workspace import MarketAnalysisWorkspace, WorkspaceDomainResult


def _row(name: str, domain: WorkspaceDomainResult) -> dict[str, str]:
    return {
        "Domain": name,
        "Status": domain.status.value,
        "Error": "" if domain.error_type is None else f"{domain.error_type}: {domain.error_message}",
    }


def workspace_domain_status_frame(workspace: MarketAnalysisWorkspace) -> pd.DataFrame:
    rows = [
        {"Domain": "Observer foundation", "Status": "READY", "Error": ""},
        _row("Ham evidence", workspace.ham),
        _row("Volume Participation", workspace.volume),
        _row("Stabil Support Lifecycle", workspace.stabil_support),
        _row("Volatility / Bands / Fib", workspace.volatility),
        _row("Liquidity", workspace.liquidity),
        _row("Order Block", workspace.order_block),
        _row("FVG / Engulfing", workspace.fvg_engulfing),
        _row("Targeting", workspace.targeting),
    ]
    return pd.DataFrame(rows, columns=("Domain", "Status", "Error"))


__all__ = ["workspace_domain_status_frame"]
