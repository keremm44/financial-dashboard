from __future__ import annotations

import re
from collections import Counter
from typing import Iterable


_HEADER_KEYS = {
    "CAUSAL_WARMUP_START",
    "CAUSAL_SNAPSHOTS",
    "DECISION_EVENTS",
    "PRIMARY_EXECUTION_TIMEFRAME",
    "EXECUTION_EVENTS_ENTRY_1H",
    "EXECUTION_EVENTS_EXIT_1H",
    "MICRO_EVENTS_ENTRY_30M",
    "MICRO_EVENTS_EXIT_30M",
    "OPPORTUNITY_CALIBRATION",
    "INPUT_REPLAY_PATH",
    "FROZEN_CACHE_STATUS",
    "DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS",
    "DECISION_LAYER_SECONDS",
    "REPLAY_MODE",
}

_METRIC_MARKERS = (
    "completed trades",
    "open/censored trades",
    "wins/losses",
    "win rate",
    "average return",
    "avg return",
    "compounded return",
    "cumulative",
    "mfe",
    "mae",
    "buy=",
    "sell=",
    "hold=",
    "no_trade=",
    "wait=",
)

_TRANSITION_MARKERS = (
    "EARLY_TRANSITION",
    "ST_LONG_TRANSITION",
    "DECISION_ST_TRANSITION_LONG_OVERLAY",
    "CURRENT_EXTERNAL_BULLISH_CHOCH",
)


def _clean(line: str) -> str:
    return line.rstrip()


def _is_header(line: str) -> bool:
    key = line.split("\t", 1)[0].strip()
    return key in _HEADER_KEYS


def _is_metric(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _METRIC_MARKERS)


def _large_move_section(lines: list[str]) -> list[str]:
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "4H LARGE MOVE AUDIT")
    except StopIteration:
        return []

    result = ["4H LARGE MOVE AUDIT", "-------------------"]
    i = start + 1
    while i < len(lines):
        text = lines[i].strip()
        if text.startswith("#") and re.match(r"^#\d+\s+(UP|DOWN)\s+", text):
            result.append(text)
            i += 1
            while i < len(lines):
                detail = lines[i].strip()
                if detail.startswith("#") and re.match(r"^#\d+\s+(UP|DOWN)\s+", detail):
                    break
                if detail.startswith(("attribution:", "action:", "dominant waiting:", "dominant blockers:", "dominant non-action reasons:")):
                    result.append("  " + detail)
                if detail.startswith(("EXECUTION P/L AUDIT", "TARGET PATH", "SCENARIO AUTHORITY")):
                    return result
                i += 1
            continue
        if text.startswith("Up moves >=") or text.startswith("Down moves >="):
            result.append(text)
        if text.startswith(("TARGET PATH", "SCENARIO AUTHORITY", "EXECUTION P/L AUDIT")):
            break
        i += 1
    return result


def _executed_event_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        text = line.strip()
        if not text.startswith(("BUY ", "SELL ")):
            continue
        if "[" not in text or "]" not in text or "price=" not in text:
            continue
        result.append(text)
    return list(dict.fromkeys(result))


def _transition_lines(lines: list[str], *, limit: int = 12) -> list[str]:
    rows: list[str] = []
    for line in lines:
        text = line.strip()
        if any(marker in text for marker in _TRANSITION_MARKERS):
            rows.append(text)
    return list(dict.fromkeys(rows))[:limit]


def _execution_section(lines: list[str]) -> list[str]:
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "EXECUTION P/L AUDIT")
    except StopIteration:
        return []
    result = ["EXECUTION P/L AUDIT"]
    for line in lines[start + 1 :]:
        text = line.strip()
        if not text:
            continue
        if text == "BUY_SELL_BACKTEST_OK":
            break
        if "_REPORT\t" in text or text.startswith(("JSON_REPORT", "TIMELINE_JSON")):
            continue
        if "\t" in text and text.split("\t", 1)[0] in {
            "FILL_MODEL",
            "SPREAD_BPS",
            "SLIPPAGE_BPS",
            "COMMISSION_BPS",
            "CLOSED_TRADES",
            "OPEN_TRADES",
            "WIN_RATE_PCT",
            "AVERAGE_NET_RETURN_PCT",
            "CUMULATIVE_NET_RETURN_PCT",
            "MAX_DRAWDOWN_PCT",
        }:
            result.append(text)
    return result


def compact_backtest_output(text: str) -> str:
    """Reduce repeated diagnostics while retaining trading and missed-move evidence.

    This function only transforms console text. It has no authority over the replay,
    decision engine, audits, fills, or P/L calculations.
    """

    lines = [_clean(line) for line in text.splitlines()]
    output: list[str] = [
        "COMPACT BUY/SELL BACKTEST REPORT",
        "================================",
        "Reporting only: calculations are identical to the normal backtest.",
        "",
        "RUN / CACHE",
    ]
    output.extend(line.strip() for line in lines if _is_header(line))

    metrics = list(dict.fromkeys(line.strip() for line in lines if _is_metric(line)))
    if metrics:
        output.extend(("", "CORE TRADE METRICS"))
        output.extend(metrics[:24])

    events = _executed_event_lines(lines)
    if events:
        output.extend(("", "EXECUTED BUY / SELL EVENTS"))
        output.extend(events)

    transition = _transition_lines(lines)
    output.extend(("", "EARLY TRANSITION EVIDENCE"))
    output.extend(transition or ("No EARLY_TRANSITION/ST transition lines in rendered report.",))

    large_moves = _large_move_section(lines)
    if large_moves:
        output.extend(("", *large_moves))

    execution = _execution_section(lines)
    if execution:
        output.extend(("", *execution))

    output.extend(("", "BUY_SELL_BACKTEST_OK"))
    return "\n".join(output)


__all__ = ["compact_backtest_output"]
