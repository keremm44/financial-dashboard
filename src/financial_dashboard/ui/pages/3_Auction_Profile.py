from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from financial_dashboard.ui.auction_profile_runtime import replay_cached_auction_profile_history
from financial_dashboard.ui.auction_profile_view_models import (
    auction_profile_nodes_frame,
    auction_profile_provenance_frame,
    auction_profile_replay_frame,
    auction_profile_summary_values,
)
from financial_dashboard.ui.runtime import cache_fingerprint, discover_cached_symbols


st.set_page_config(
    page_title="Financial Dashboard · Auction Profile",
    page_icon="▥",
    layout="wide",
)


def _default_cache_root() -> str:
    configured = os.environ.get("FINANCIAL_DASHBOARD_CACHE")
    if configured:
        return configured
    return str(Path.cwd() / "data" / "cache")


@st.cache_data(show_spinner="Auction profile replay çalıştırılıyor…")
def _cached_replay(
    cache_root: str,
    symbol: str,
    timeframe: str,
    fingerprint: tuple[tuple[str, int, int], ...],
    minimum_bars: int,
    step: int,
    max_points: int,
):
    del fingerprint
    return replay_cached_auction_profile_history(
        cache_root,
        symbol=symbol,
        timeframe=timeframe,
        minimum_bars=minimum_bars,
        step=step,
        max_points=max_points,
    )


with st.sidebar:
    st.header("Auction / Volume Profile")
    cache_root_input = st.text_input("Parquet cache dizini", value=_default_cache_root())
    cache_root = str(Path(cache_root_input).expanduser().resolve(strict=False))
    timeframe = st.selectbox("Timeframe", ("30m", "1h", "2h", "4h", "1d"), index=1)
    symbols = discover_cached_symbols(cache_root, timeframes=(timeframe,))
    symbol = st.selectbox("Sembol", symbols) if symbols else ""
    max_points = st.slider("Replay noktası", 20, 300, 100, step=20)
    step = st.slider("Replay adımı", 1, 10, 1)
    minimum_bars = st.number_input("Minimum bar", min_value=1, value=20, step=1)

st.title("Auction · OHLCV Estimated Volume Profile")
st.caption(
    "Bu ekran gerçek exchange price-at-volume, tick profile, footprint veya bid/ask delta değildir. "
    "Candle toplam hacmi High-Low aralığına price-bin overlap ile dağıtılarak tahmini profil oluşturulur."
)
st.caption(
    "POC/VAH/VAL/HVN/LVN bu veri sınırı içinde yaklaşık auction geometrisidir; al/sat, hedef, stop veya "
    "Market Structure gerçeği üretmez."
)

if not symbol:
    st.info(f"{timeframe} cache bulunan bir sembol yok.")
    st.stop()

fingerprint = cache_fingerprint(cache_root, symbol=symbol, timeframes=(timeframe,))
try:
    replay = _cached_replay(
        cache_root,
        symbol,
        timeframe,
        fingerprint,
        int(minimum_bars),
        int(step),
        int(max_points),
    )
except Exception as error:
    st.error(f"Auction replay tamamlanamadı: {type(error).__name__}: {error}")
    st.stop()

latest = replay.latest
if latest is None:
    st.info("Replay noktası üretilemedi.")
    st.stop()

values = auction_profile_summary_values(latest)
metric_specs = (
    ("Kaynak", values["Source"]),
    ("POC", values["POC"]),
    ("VAH", values["VAH"]),
    ("VAL", values["VAL"]),
    ("Migration", values["Migration"]),
    ("Balance", values["Balance"]),
)
for column, (label, value) in zip(st.columns(6), metric_specs, strict=True):
    column.metric(label, value)

st.caption(
    f"{replay.symbol} · {replay.timeframe} · as-of: {latest.as_of} · quality={latest.data_quality.value} · "
    f"bars_used={latest.provenance.bars_used}/{latest.provenance.expected_lookback_bars} · "
    f"allocation_error={values['Allocation error %']}%"
)

if latest.data_quality.value == "LIMITED_HISTORY":
    st.warning("Profil çalışıyor ancak preset lookback penceresi tam dolu değil; history_fraction provenance alanında gösterilir.")
elif latest.data_quality.value != "OK":
    st.warning(f"Auction data quality: {latest.data_quality.value}")

history_tab, nodes_tab, provenance_tab = st.tabs(("Profile history", "HVN / LVN", "Provenance"))

with history_tab:
    st.subheader("Prefix-safe estimated profile replay")
    st.dataframe(auction_profile_replay_frame(replay), width="stretch", hide_index=True)

with nodes_tab:
    st.subheader("Estimated volume nodes")
    nodes = auction_profile_nodes_frame(latest)
    if nodes.empty:
        st.info("Current profile için HVN/LVN node yok.")
    else:
        st.dataframe(nodes, width="stretch", hide_index=True)

with provenance_tab:
    st.subheader("Data boundary / provenance")
    st.dataframe(auction_profile_provenance_frame(latest), width="stretch", hide_index=True)
    st.info(
        "is_true_price_at_volume=False, is_tick_profile=False ve is_footprint=False kalıcı authority sınırıdır."
    )
