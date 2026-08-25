from __future__ import annotations

from dataclasses import asdict
from json import dumps
from typing import Any

from .models import DecisionAuditReport, TradeAudit


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.2f}{suffix}"


def _worst_trade_lines(trades: tuple[TradeAudit, ...], limit: int) -> list[str]:
    if not trades:
        return ["No completed trades."]
    rows: list[str] = []
    for rank, trade in enumerate(sorted(trades, key=lambda item: item.return_pct)[:limit], start=1):
        rows.extend(
            (
                f"#{rank} {trade.entry_time} BUY -> {trade.exit_time} SELL",
                f"  Return: {_fmt(trade.return_pct, '%')} | MFE: {_fmt(trade.mfe_pct, '%')} | MAE: {_fmt(trade.mae_pct, '%')}",
                f"  Entry local-low miss: {_fmt(trade.entry_local_low_miss_pct, '%')} / {_fmt(trade.entry_local_low_miss_atr, ' ATR')}",
                f"  Extra downside after BUY: {_fmt(trade.post_entry_additional_downside_pct, '%')}",
                f"  Exit peak miss: {_fmt(trade.exit_peak_miss_pct, '%')} | Giveback: {_fmt(trade.profit_giveback_pct, '%')}",
                "  BUY reasons: " + ("; ".join(trade.entry_reasons) if trade.entry_reasons else "-") ,
                "  BUY waiting_for: " + ("; ".join(trade.entry_waiting_for) if trade.entry_waiting_for else "-"),
                "  SELL reasons: " + ("; ".join(trade.exit_reasons) if trade.exit_reasons else "-"),
            )
        )
    return rows


def render_text(report: DecisionAuditReport, *, worst_trade_limit: int = 5) -> str:
    metrics = report.metrics
    stability = report.signal_stability
    opportunities = report.missed_opportunities
    captured = sum(1 for item in opportunities if item.captured)
    missed = len(opportunities) - captured
    capture_rate = None if not opportunities else captured / len(opportunities) * 100.0

    lines = [
        "=" * 58,
        "BUY/SELL HISTORICAL DECISION AUDIT",
        "=" * 58,
        f"Symbol: {report.symbol}",
        f"Timeframe: {report.timeframe}",
        f"Period: {report.start_time} -> {report.end_time}",
        "",
        "TRADE RESULTS",
        "-------------",
        f"Completed trades: {metrics.completed_trades}",
        f"Wins / losses / breakeven: {metrics.wins} / {metrics.losses} / {metrics.breakeven}",
        f"Win rate: {_fmt(metrics.win_rate_pct, '%')}",
        f"Average return: {_fmt(metrics.average_return_pct, '%')}",
        f"Median return: {_fmt(metrics.median_return_pct, '%')}",
        f"Compounded return: {_fmt(metrics.compounded_return_pct, '%')}",
        f"Average winner: {_fmt(metrics.average_winner_pct, '%')}",
        f"Average loser: {_fmt(metrics.average_loser_pct, '%')}",
        f"Best / worst trade: {_fmt(metrics.best_trade_pct, '%')} / {_fmt(metrics.worst_trade_pct, '%')}",
        f"Average MFE / MAE: {_fmt(metrics.average_mfe_pct, '%')} / {_fmt(metrics.average_mae_pct, '%')}",
        f"Average favorable move captured: {_fmt(metrics.average_move_capture_ratio_pct, '%')}",
        "",
        "ENTRY QUALITY",
        "-------------",
        f"Average local-bottom miss: {_fmt(metrics.average_entry_local_low_miss_pct, '%')}",
        f"Average local-bottom miss ATR: {_fmt(metrics.average_entry_local_low_miss_atr, ' ATR')}",
        f"Early-entry cases: {metrics.early_entry_cases}",
        f"Late-entry cases: {metrics.late_entry_cases}",
        f"Average bars early / late: {_fmt(metrics.average_entry_early_bars)} / {_fmt(metrics.average_entry_late_bars)}",
        f"Average additional downside after BUY: {_fmt(metrics.average_post_entry_additional_downside_pct, '%')}",
        f"Worst additional downside after BUY: {_fmt(metrics.worst_post_entry_additional_downside_pct, '%')}",
        "",
        "EXIT QUALITY",
        "------------",
        f"Average local-peak miss: {_fmt(metrics.average_exit_peak_miss_pct, '%')}",
        f"Average local-peak miss ATR: {_fmt(metrics.average_exit_peak_miss_atr, ' ATR')}",
        f"Early-exit cases: {metrics.early_exit_cases}",
        f"Late-exit cases: {metrics.late_exit_cases}",
        f"Average bars early / late: {_fmt(metrics.average_exit_early_bars)} / {_fmt(metrics.average_exit_late_bars)}",
        f"Average missed upside after SELL: {_fmt(metrics.average_post_exit_missed_upside_pct, '%')}",
        f"Worst missed upside after SELL: {_fmt(metrics.worst_post_exit_missed_upside_pct, '%')}",
        f"Average profit giveback: {_fmt(metrics.average_profit_giveback_pct, '%')}",
        "",
        "DECISION STABILITY",
        "------------------",
        "Action counts: " + ", ".join(f"{key}={value}" for key, value in stability.action_counts.items()),
        f"READY -> WAIT reversals: {stability.ready_to_wait_reversals}",
        f"WAIT episodes / avg bars: {stability.wait_episode_count} / {_fmt(stability.average_wait_duration_bars)}",
        f"READY episodes / avg bars: {stability.ready_episode_count} / {_fmt(stability.average_ready_duration_bars)}",
        f"Average READY -> BUY delay: {_fmt(stability.average_ready_to_buy_delay_bars)} bars",
        "",
        "MISSED OPPORTUNITIES",
        "--------------------",
    ]
    if opportunities:
        lines.extend(
            (
                f"Meaningful moves: {len(opportunities)}",
                f"Captured: {captured}",
                f"Missed: {missed}",
                f"Capture rate: {_fmt(capture_rate, '%')}",
            )
        )
    else:
        lines.append("Not evaluated (set meaningful_move_atr in the audit config).")

    lines.extend(
        (
            "",
            f"Unmatched BUY events: {report.unmatched_buy_events}",
            f"Unmatched SELL events: {report.unmatched_sell_events}",
            "",
            "WORST TRADES / DECISION EXPLANATIONS",
            "------------------------------------",
            *_worst_trade_lines(report.trades, worst_trade_limit),
        )
    )
    return "\n".join(lines)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def render_json(report: DecisionAuditReport, *, indent: int = 2) -> str:
    return dumps(asdict(report), ensure_ascii=False, indent=indent, default=_json_default)


__all__ = ["render_json", "render_text"]
