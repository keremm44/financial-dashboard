from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from financial_dashboard.data.hybrid_bist_5m_provider import HybridBist5mProvider
from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider
from financial_dashboard.data.yahoo_intraday_provider import YahooFinanceIntradayProvider


TZ = ZoneInfo("Europe/Istanbul")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare/fill TradingView 5m gaps with Yahoo 5m")
    parser.add_argument("--symbols", nargs="+", default=["THYAO", "GARAN", "ASELS"])
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    if args.days <= 0:
        print("HYBRID_PROBE_ERROR: --days must be positive")
        return 2

    end = datetime.now(TZ)
    start = end - timedelta(days=args.days)
    print("=== HYBRID BIST 5M PROBE ===")
    print(f"range={start.isoformat()} -> {end.isoformat()}")

    failures = 0
    for symbol in args.symbols:
        try:
            hybrid = HybridBist5mProvider(
                TvDatafeedProvider(max_bars=5000),
                YahooFinanceIntradayProvider(),
            )
            frame = hybrid.get_ohlcv(symbol, "5m", start, end)
            report = hybrid.last_gap_report
            print(
                f"{symbol}: total={len(frame)} tv={report.tv_bars} "
                f"yahoo_filled={report.yahoo_filled} unresolved={report.unresolved_gaps} "
                f"fallback_ratio={report.fallback_ratio:.4%} expected_closed={report.expected_closed_slots}"
            )
            if not frame.empty:
                print(
                    f"  first={frame.iloc[0]['timestamp']} last={frame.iloc[-1]['timestamp']} "
                    f"sources={','.join(sorted(set(frame['source'].astype(str))))}"
                )
        except Exception as exc:
            failures += 1
            detail = " ".join(str(exc).split())[:300] or exc.__class__.__name__
            print(f"{symbol}: HYBRID_PROBE_ERROR: {detail}")

    if failures:
        print(f"HYBRID_PROBE_PARTIAL_FAILURE: {failures}/{len(args.symbols)} symbols failed")
        return 1
    print("HYBRID_PROBE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
