from __future__ import annotations

import pandas as pd

from financial_dashboard.market_workspace import MarketAnalysisWorkspace


def workspace_domain_status_frame(workspace: MarketAnalysisWorkspace) -> pd.DataFrame:
    rows = [
        {
            "Domain": "Observer foundation",
            "Status": "READY",
            "Error": "",
        },
        {
            "Domain": "Ham evidence",
            "Status": workspace.ham.status.value,
            "Error": (
                ""
                if workspace.ham.error_type is None
                else f"{workspace.ham.error_type}: {workspace.ham.error_message}"
            ),
        },
        {
            "Domain": "Volume Participation",
            "Status": workspace.volume.status.value,
            "Error": (
                ""
                if workspace.volume.error_type is None
                else f"{workspace.volume.error_type}: {workspace.volume.error_message}"
            ),
        },
    ]
    return pd.DataFrame(rows, columns=("Domain", "Status", "Error"))


__all__ = ["workspace_domain_status_frame"]
