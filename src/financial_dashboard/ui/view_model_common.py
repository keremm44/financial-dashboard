from __future__ import annotations

from typing import Any

import pandas as pd

from financial_dashboard.engines.models import Direction


_DIRECTION_LABEL = {
    Direction.UP: "UP",
    Direction.DOWN: "DOWN",
    Direction.NEUTRAL: "NEUTRAL",
}


def direction_label(value: Direction | None) -> str:
    return _DIRECTION_LABEL.get(value, "—")


def frame(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


__all__ = ["direction_label", "frame"]
