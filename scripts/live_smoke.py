from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_dashboard.data.engine_input import EngineInputError, prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider
from financial_dashboard.engines import (
    AuctionConfig,
    AuctionVolumeProfileEngine,
    LiquidityEngine,
    MarketStructureEngine,
    PatternCompressionEngine,
    SupportResistanceRangeEngine,
    VolumeParticipationEngine,
)
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner


TZ = ZoneInfo("Europe/Istanbul")


def _summary(frame) -> dict[str, object]:
    if frame.empty:
        return {"rows": 0}
    return {
        "rows": len(frame),
        "first": str(frame.iloc[0]["timestamp"]),
        "last": str(frame.iloc[-1]["timestamp"]),
        "closed": int(frame["is_closed"].fillna(False).astype(bool).sum()) if "is_closed" in frame.columns else None,
        "complete": int(frame["is_complete"].fillna(False).astype(bool).sum()) if "is_complete" in frame.columns else None,
        "volume_zero_or_missing": int(((frame["volume"].isna()) | (frame["volume"] <= 0)).sum()) if "volume" in frame.columns else None,
    }


def _run_volume_round2(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
) -> int:
    print("\n[volume-round2-mtf]")
    try:
        volume = VolumeMTFEvidenceReplayRunner(store).replay(symbol)
    except Exception as error:
        print("VOLUME_ROUND2_FAILED")
        print(f"reason={type(error).__name__}: {error}")
        return 5
    for replay in volume.timeframe_replays:
        print(
            f"{replay.timeframe}=bars:{replay.bar_count} "
            f"ready:{replay.ready_bar_count} warmup:{replay.warmup_bar_count} "
            f"unavailable:{replay.unavailable_bar_count} "
            f"links:{len(replay.event_links)} quality:{replay.replay_data_quality.value}"
        )
    round2 = volume.round2
    print(
        f"context={round2.pressure.state.value} "
        f"score={round2.pressure.directional_score:.4f} "
        f"coverage={round2.pressure.evidence_coverage:.4f} "
        f"authority={round2.pressure.decision_authority}"
    )
    print(
        f"event_assessments={len(round2.event_assessments)} "
        f"risks={len(round2.risks)} shocks={len(round2.shocks)} "
        f"propagations={len(round2.structural_propagations)}"
    )
    dedup = round2.deduplication
    print(
        f"dedup_family={dedup.source_family} vote_cap={dedup.independent_vote_cap} "
        f"raw_mtf_volume_summed={dedup.raw_mtf_volume_summed} policy={dedup.policy}"
    )
    unsafe_promotion = any(
        assessment.lower_timeframe_can_confirm
        for assessment in round2.event_assessments
    )
    if (
        round2.pressure.raw_volume_summed
        or dedup.raw_mtf_volume_summed
        or dedup.independent_vote_cap != 1
        or unsafe_promotion
    ):
        print("VOLUME_ROUND2_FAILED")
        print("reason=authority or shared-source dedup invariant failed")
        return 6
    print("VOLUME_ROUND2_OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Real BIST tvDatafeed -> cache -> resample -> engine smoke test")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--cache-root", default=".cache/live-smoke")
    parser.add_argument("--max-bars", type=int, default=5000)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "request the complete provider window and merge it into the existing "
            "cache; without this flag refresh remains right-edge incremental"
        ),
    )
    parser.add_argument(
        "--volume-round2",
        action="store_true",
        help=(
            "after cache refresh, replay the five-timeframe Volume Round 2 "
            "inspection contract and print causal/risk/dedup diagnostics"
        ),
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help=(
            "skip the provider and validate the five-timeframe Volume replay "
            "already present under --cache-root; requires --volume-round2"
        ),
    )
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be positive")
    if args.max_bars <= 0:
        parser.error("--max-bars must be positive")
    if args.cache_only and not args.volume_round2:
        parser.error("--cache-only requires --volume-round2")

    end = datetime.now(TZ)
    start = end - timedelta(days=args.days)

    print("=== ARGENT LIVE SMOKE ===")
    refresh_mode = (
        "cache-only"
        if args.cache_only
        else "backfill-merge"
        if args.backfill
        else "incremental"
    )
    print(
        f"symbol={args.symbol} exchange=BIST start={start.isoformat()} "
        f"end={end.isoformat()} mode={refresh_mode}"
    )

    store = ParquetOHLCVStore(Path(args.cache_root))
    if args.cache_only:
        print("\n[cached-five-timeframe-input]")
        for timeframe in ("1d", "4h", "2h", "1h", "30m"):
            print(f"{timeframe}={_summary(store.load(args.symbol, timeframe))}")
        status = _run_volume_round2(store, symbol=args.symbol)
        if status:
            return status
        print("\nSMOKE_OK")
        return 0

    provider = TvDatafeedProvider(exchange="BIST", max_bars=args.max_bars)
    pipeline = MarketDataPipeline(provider, store)
    cached_before = store.load(args.symbol, "5m")
    left_edge_before = (
        None if cached_before.empty else cached_before.iloc[0]["timestamp"]
    )

    if args.backfill:
        # A full-window provider request can extend the cache's left edge.  The
        # Parquet store merges by timestamp, so existing rows are preserved unless
        # the provider returns a newer value for the same timestamp.
        result = pipeline.refresh_bist_5m(
            symbol=args.symbol,
            start=start,
            end=end,
        )
    else:
        result = pipeline.refresh_bist_5m_incremental(
            symbol=args.symbol,
            requested_start=start,
            end=end,
        )

    print("\n[provider]")
    print(f"volume_status={provider.last_volume_status} volume_type={provider.volume_type}")
    print(f"5m={_summary(result.base)}")
    if args.backfill:
        left_edge_after = (
            None if result.base.empty else result.base.iloc[0]["timestamp"]
        )
        if left_edge_before is None and left_edge_after is not None:
            left_edge_status = "CACHE_POPULATED"
        elif (
            left_edge_before is not None
            and left_edge_after is not None
            and left_edge_after < left_edge_before
        ):
            left_edge_status = "LEFT_EDGE_EXTENDED"
        else:
            left_edge_status = "LEFT_EDGE_UNCHANGED"
        print(
            "backfill="
            f"{left_edge_status} before={left_edge_before} after={left_edge_after} "
            f"requested_days={args.days} provider_max_bars={args.max_bars}"
        )

    print("\n[derived]")
    for timeframe, frame in result.derived.items():
        print(f"{timeframe}={_summary(frame)}")

    if result.base.empty:
        print("\nSMOKE_NO_DATA")
        print("reason=provider returned no rows for the requested window; retry before treating as a symbol/data failure")
        return 3

    if "1h" not in result.derived or result.derived["1h"].empty:
        print("\nSMOKE_NO_ENGINE_INPUT")
        print("reason=1h frame was not produced")
        return 4

    try:
        batch = prepare_engine_input(result.derived["1h"])
    except EngineInputError as exc:
        print("\nSMOKE_NO_ENGINE_INPUT")
        print(f"reason={exc}")
        return 4

    print("\n[engine-input 1h]")
    print(f"quality={batch.source_quality.status.value} rows={len(batch.frame)}")
    if batch.source_quality.warnings:
        print(f"warnings={list(batch.source_quality.warnings)}")

    market = MarketStructureEngine()
    market_results = market.replay(batch.frame)
    print("\n[market-structure 1h]")
    print(f"results={len(market_results)}")
    print(f"snapshot={market.snapshot()}")
    print(f"export={market.export_contract}")

    pattern = PatternCompressionEngine()
    pattern_results = pattern.replay(batch.frame)
    print("\n[pattern-compression 1h]")
    print(f"results={len(pattern_results)}")
    print(f"snapshot={pattern.snapshot()}")
    print(f"export={pattern.export_contract}")

    liquidity = LiquidityEngine()
    liquidity_results = liquidity.replay(batch.frame)
    print("\n[liquidity 1h]")
    print(f"results={len(liquidity_results)}")
    print(f"snapshot={liquidity.snapshot()}")
    print(f"export={liquidity.export_contract}")
    active_pools = [pool for pool in liquidity.pools if pool.state.value not in {"CONSUMED", "INVALIDATED"}]
    print(f"pools_total={len(liquidity.pools)} active={len(active_pools)}")

    auction = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    auction_results = auction.replay(batch.frame)
    print("\n[auction-volume-profile 1h]")
    print(f"results={len(auction_results)}")
    print(f"snapshot={auction.snapshot()}")
    print(f"export={auction.export_contract}")

    support_resistance = SupportResistanceRangeEngine()
    support_results = support_resistance.replay(batch.frame)
    print("\n[support-resistance-range 1h]")
    print(f"results={len(support_results)}")
    print(f"snapshot={support_resistance.snapshot()}")
    print(f"export={support_resistance.export_contract}")

    participation = VolumeParticipationEngine()
    participation_results = participation.replay(batch.frame)
    print("\n[volume-participation-absorption 1h]")
    print(f"results={len(participation_results)}")
    print(f"snapshot={participation.snapshot()}")
    print(f"core_export={participation.export_contract}")
    print(f"lifecycle_export={participation.lifecycle_export}")
    print(f"final_export={participation.final_export}")

    if args.volume_round2:
        status = _run_volume_round2(store, symbol=args.symbol)
        if status:
            return status

    print("\nSMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
