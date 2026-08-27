from __future__ import annotations

from dataclasses import asdict
from json import dumps
from typing import Any, Callable

from .trade_quality import HorizonAwareTradeQualityReport, HorizonTradeQuality, TradeQualityAggregate


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.2f}{suffix}"


def _aggregate_line(name: str, metrics: TradeQualityAggregate) -> str:
    return (
        f"{name}: trades={metrics.trade_count} win={_fmt(metrics.win_rate_pct, '%')} "
        f"avg={_fmt(metrics.average_return_pct, '%')} MFE={_fmt(metrics.average_mfe_pct, '%')} "
        f"MAE={_fmt(metrics.average_mae_pct, '%')} capture={_fmt(metrics.average_move_capture_ratio_pct, '%')}"
    )


def _trade_line(trade: HorizonTradeQuality) -> str:
    return (
        f"{trade.entry_time} -> {trade.exit_time} [{trade.horizon}/{trade.scenario_kind}] "
        f"return={_fmt(trade.return_pct, '%')} MFE={_fmt(trade.mfe_pct, '%')} MAE={_fmt(trade.mae_pct, '%')}"
    )


def _rank(
    trades: tuple[HorizonTradeQuality, ...],
    key: Callable[[HorizonTradeQuality], float],
    *,
    reverse: bool,
    limit: int,
) -> list[str]:
    if not trades:
        return ["None."]
    return [_trade_line(trade) for trade in sorted(trades, key=key, reverse=reverse)[:limit]]


def render_trade_quality_text(
    report: HorizonAwareTradeQualityReport,
    *,
    worst_trade_limit: int = 5,
) -> str:
    metrics = report.metrics
    lines = [
        "=" * 66,
        "HORIZON-AWARE TRADE QUALITY AUDIT (HINDSIGHT ONLY)",
        "=" * 66,
        f"Symbol: {report.symbol}",
        f"Audit timeframe: {report.timeframe}",
        f"Completed trades: {metrics.trade_count}",
        f"Censored open trades: {report.censored_open_trades}",
        f"Unmatched BUY / SELL: {report.unmatched_buy_events} / {report.unmatched_sell_events}",
        "",
        "OVERALL",
        "-------",
        _aggregate_line("ALL", metrics),
        f"Entry local-bottom miss: {_fmt(metrics.average_entry_local_low_miss_pct, '%')}",
        f"Scenario-qualified -> BUY: {_fmt(metrics.average_scenario_to_buy_bars)} bars / {_fmt(metrics.average_scenario_to_buy_price_change_pct, '%')}",
        f"READY -> BUY: {_fmt(metrics.average_ready_to_buy_bars)} bars / {_fmt(metrics.average_ready_to_buy_price_change_pct, '%')}",
        f"Exit peak miss: {_fmt(metrics.average_exit_peak_miss_pct, '%')}",
        f"EXIT_READY -> SELL: {_fmt(metrics.average_exit_ready_to_sell_bars)} bars / giveback {_fmt(metrics.average_exit_ready_to_sell_giveback_pct, '%')}",
        f"Trade peak -> SELL giveback: {_fmt(metrics.average_profit_giveback_pct, '%')}",
        f"Target defended -> SELL: {_fmt(metrics.average_target_defended_to_sell_bars)} bars",
        "",
        "BY HORIZON",
        "----------",
    ]
    if report.metrics_by_horizon:
        lines.extend(_aggregate_line(name, value) for name, value in report.metrics_by_horizon.items())
    else:
        lines.append("None.")

    lines.extend(("", "BY SCENARIO", "-----------"))
    if report.metrics_by_scenario:
        lines.extend(_aggregate_line(name, value) for name, value in report.metrics_by_scenario.items())
    else:
        lines.append("None.")

    if metrics.short_target_reach_rate_pct:
        lines.extend(("", "SHORT-TERM RESEARCH TARGETS", "---------------------------"))
        for target, rate in metrics.short_target_reach_rate_pct.items():
            lines.append(
                f"{target}: reached={_fmt(rate, '%')} avg bars={_fmt(metrics.average_short_bars_to_target.get(target))}"
            )

    limit = max(1, worst_trade_limit)
    lines.extend(("", "DIAGNOSTIC OUTLIERS", "-------------------", "Worst entry local-bottom miss:"))
    lines.extend(
        _rank(
            report.trades,
            lambda trade: -1.0 if trade.entry_local_low_miss_pct is None else trade.entry_local_low_miss_pct,
            reverse=True,
            limit=limit,
        )
    )
    lines.append("Worst exit peak miss:")
    lines.extend(
        _rank(
            report.trades,
            lambda trade: -1.0 if trade.exit_peak_miss_pct is None else trade.exit_peak_miss_pct,
            reverse=True,
            limit=limit,
        )
    )
    lines.append("Worst MAE:")
    lines.extend(_rank(report.trades, lambda trade: trade.mae_pct, reverse=False, limit=limit))
    lines.append("Worst profit giveback:")
    lines.extend(
        _rank(
            report.trades,
            lambda trade: -1.0 if trade.profit_giveback_pct is None else trade.profit_giveback_pct,
            reverse=True,
            limit=limit,
        )
    )
    lines.append("Lowest move capture:")
    lines.extend(
        _rank(
            report.trades,
            lambda trade: 10_000.0 if trade.move_capture_ratio is None else trade.move_capture_ratio,
            reverse=False,
            limit=limit,
        )
    )
    return "\n".join(lines)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def render_trade_quality_json(report: HorizonAwareTradeQualityReport, *, indent: int = 2) -> str:
    return dumps(asdict(report), ensure_ascii=False, indent=indent, default=_json_default)


__all__ = ["render_trade_quality_json", "render_trade_quality_text"]
