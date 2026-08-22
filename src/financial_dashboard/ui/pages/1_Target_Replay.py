from __future__ import annotations

import os
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.ui.chart_layers.targeting import add_targeting_layers
from financial_dashboard.ui.runtime import (
    cache_fingerprint,
    discover_cached_symbols,
    inspect_symbol_cache,
    runnable_timeframes,
)
from financial_dashboard.ui.targeting_replay_runtime import replay_cached_targeting_history
from financial_dashboard.ui.targeting_replay_view_models import (
    arrival_context_frame,
    cluster_anatomy_frame,
    cluster_stability_values,
    confirmations_frame,
    distance_bands_frame,
    objectives_frame,
    reaction_zones_frame,
    replay_points_frame,
    semantic_targeting_summary_values,
    semantic_transitions_frame,
    shadow_comparison_frame,
)
from financial_dashboard.ui.targeting_view_models import (
    target_clusters_frame,
    targeting_summary_values,
)


st.set_page_config(page_title="Target Replay", page_icon="◎", layout="wide")


def _default_cache_root() -> str:
    configured = os.environ.get("FINANCIAL_DASHBOARD_CACHE")
    if configured:
        return configured
    live_smoke = Path.cwd() / ".cache" / "live-smoke"
    if live_smoke.exists():
        return str(live_smoke)
    return str(Path.cwd() / "data" / "cache")


def _replay_signature(
    cache_root: str,
    symbol: str,
    timeframes: tuple[str, ...],
    reference_timeframe: str,
    minimum_bars: int,
    step: int,
    max_points: int,
):
    return (
        cache_root,
        symbol,
        timeframes,
        reference_timeframe,
        minimum_bars,
        step,
        max_points,
        cache_fingerprint(cache_root, symbol=symbol, timeframes=timeframes),
    )


def _causal_figure(cache_root: str, symbol: str, point, cluster) -> go.Figure:
    timeframe = point.snapshot.reference_timeframe
    store = ParquetOHLCVStore(Path(cache_root))
    batch = prepare_engine_input(store.load(symbol, timeframe))
    frame = batch.frame
    frame = frame[frame["timestamp"] <= point.reference_timestamp].tail(300)
    figure = go.Figure(
        data=[
            go.Candlestick(
                x=frame["timestamp"],
                open=frame["open"],
                high=frame["high"],
                low=frame["low"],
                close=frame["close"],
                name=f"{symbol} {timeframe}",
            )
        ]
    )
    add_targeting_layers(
        figure,
        point.snapshot,
        show_nearest=True,
        show_all_clusters=False,
    )
    if cluster is not None:
        figure.add_hrect(
            y0=cluster.envelope_low,
            y1=cluster.envelope_high,
            line_width=2,
            annotation_text="Selected cluster",
            annotation_position="top left",
        )
    figure.add_hline(
        y=point.snapshot.current_price,
        line_dash="dash",
        annotation_text=f"Current {point.snapshot.current_price:.2f}",
        annotation_position="bottom left",
    )
    figure.update_layout(
        title=f"{symbol} · causal replay · {point.available_at}",
        xaxis_rangeslider_visible=False,
        height=650,
    )
    return figure


def _render_arrival_context(context, label: str) -> None:
    if context is None:
        st.info(f"Aktif {label} objective yok.")
        return
    st.caption(
        f"State={context.state.value} · "
        f"independent relevant reaction origins={context.independent_reaction_origins}"
    )
    frame = arrival_context_frame(context)
    if frame.empty:
        st.info(f"{label.capitalize()} objective için reaction context yok.")
        return

    relevant = frame[frame["Context"] != "DOWNSTREAM"]
    downstream = frame[frame["Context"] == "DOWNSTREAM"]

    st.subheader("Relevant arrival context")
    if relevant.empty:
        st.info("CURRENT / AHEAD / AT_OBJECTIVE reaction yok.")
    else:
        st.dataframe(relevant, width="stretch", hide_index=True)

    with st.expander(f"Downstream context · {len(downstream)} zone", expanded=False):
        st.caption(
            "BEYOND bölgeleri arrival state üretmez. Hepsi audit için korunur; "
            "objective'a ATR mesafesine göre en yakınlar üstte gösterilir."
        )
        if downstream.empty:
            st.info("Downstream reaction zone yok.")
        else:
            st.dataframe(
                downstream.sort_values("Objective distance ATR").head(12),
                width="stretch",
                hide_index=True,
            )


st.title("Target Replay · causal diagnostics")
st.caption(
    "Yeni semantic model shadow mode'da çalışır: Liquidity objective; OB/FVG/S-R reaction; "
    "Engulfing confirmation. Eski TargetCluster çıktısı migration karşılaştırması için korunur. "
    "Bu sayfa işlem veya take-profit önerisi üretmez."
)

cache_root = str(
    Path(
        st.sidebar.text_input("Parquet cache dizini", value=_default_cache_root())
    ).expanduser().resolve(strict=False)
)
symbols = discover_cached_symbols(cache_root)
if not symbols:
    st.info("Bu cache dizininde analiz sembolü bulunamadı.")
    st.stop()

symbol = st.sidebar.selectbox("Sembol", symbols)
statuses = inspect_symbol_cache(cache_root, symbol=symbol)
runnable = runnable_timeframes(statuses)
if not runnable:
    st.error("Replay için kapalı + tamamlanmış timeframe bulunamadı.")
    st.stop()

selected_timeframes = tuple(
    st.sidebar.multiselect(
        "Replay timeframes",
        ANALYSIS_TIMEFRAMES,
        default=runnable,
    )
)
if not selected_timeframes:
    st.warning("En az bir timeframe seçilmeli.")
    st.stop()

reference_timeframe = st.sidebar.selectbox(
    "Reference timeframe",
    selected_timeframes,
    index=(selected_timeframes.index("1h") if "1h" in selected_timeframes else 0),
)
minimum_bars = st.sidebar.number_input(
    "Minimum causal bars / TF", min_value=5, max_value=500, value=20, step=5
)
step = st.sidebar.number_input("Replay step", min_value=1, max_value=50, value=1, step=1)
max_points = st.sidebar.number_input(
    "Max replay points", min_value=2, max_value=200, value=10, step=5
)

signature = _replay_signature(
    cache_root,
    symbol,
    selected_timeframes,
    reference_timeframe,
    int(minimum_bars),
    int(step),
    int(max_points),
)

if st.sidebar.button("Replay'i çalıştır", type="primary", width="stretch"):
    progress_bar = st.progress(0.0, text="Replay hazırlanıyor…")

    def progress(position, total, cutoff, state) -> None:
        if total <= 0:
            return
        value = min(max(position / total, 0.0), 1.0)
        status_label = {
            "start": "hesaplanıyor",
            "done": "tamamlandı",
            "skipped": "atlandı",
        }.get(state, state)
        progress_bar.progress(value, text=f"[{position}/{total}] {cutoff} · {status_label}")

    try:
        replay = replay_cached_targeting_history(
            cache_root,
            symbol=symbol,
            timeframes=selected_timeframes,
            reference_timeframe=reference_timeframe,
            minimum_bars_per_timeframe=int(minimum_bars),
            step=int(step),
            max_points=int(max_points),
            progress=progress,
        )
    except Exception as error:
        progress_bar.empty()
        st.error(f"Replay başarısız: {type(error).__name__}: {error}")
    else:
        progress_bar.progress(1.0, text="Replay tamamlandı")
        st.session_state["target_replay"] = replay
        st.session_state["target_replay_signature"] = signature

replay = st.session_state.get("target_replay")
replay_signature = st.session_state.get("target_replay_signature")
if replay is None or replay_signature != signature:
    st.info("Bu ayarlar için replay henüz çalıştırılmadı.")
    st.stop()
if not replay.points:
    st.warning("Seçilen ayarlarda yeterli causal replay noktası oluşmadı.")
    st.stop()

st.subheader("Replay zaman çizgisi")
st.dataframe(replay_points_frame(replay), width="stretch", hide_index=True)
point_index = st.slider(
    "Replay noktası",
    min_value=0,
    max_value=len(replay.points) - 1,
    value=len(replay.points) - 1,
    format="%d",
)
point = replay.points[point_index]
snapshot = point.snapshot
semantic_snapshot = point.semantic_snapshot
st.caption(
    f"Point {point_index + 1}/{len(replay.points)} · available={point.available_at} · "
    f"price={snapshot.current_price:.4f} · ATR={snapshot.reference_atr:.4f}"
)

st.header("Semantic shadow model")
if semantic_snapshot is None:
    st.warning("Bu replay noktasında semantic shadow snapshot üretilemedi.")
else:
    semantic_summary = semantic_targeting_summary_values(semantic_snapshot)
    summary_columns = st.columns(len(semantic_summary))
    for column, (label, value) in zip(summary_columns, semantic_summary.items(), strict=True):
        column.metric(label, value)

    up_tab, down_tab, evidence_tab, shadow_tab = st.tabs(
        ("Upside arrival", "Downside arrival", "Semantic evidence", "Legacy vs semantic")
    )
    with up_tab:
        _render_arrival_context(semantic_snapshot.upside_arrival, "upside")
    with down_tab:
        _render_arrival_context(semantic_snapshot.downside_arrival, "downside")
    with evidence_tab:
        st.subheader("Objectives · Liquidity only in Phase 1")
        st.dataframe(objectives_frame(semantic_snapshot), width="stretch", hide_index=True)
        st.subheader("Reaction zones · OB / FVG / S-R")
        st.dataframe(reaction_zones_frame(semantic_snapshot), width="stretch", hide_index=True)
        st.subheader("Confirmations · Engulfing")
        confirmations = confirmations_frame(semantic_snapshot)
        if confirmations.empty:
            st.info("Aktif confirmation yok.")
        else:
            st.dataframe(confirmations, width="stretch", hide_index=True)
    with shadow_tab:
        st.caption(
            "Legacy Technical Zone sayısı ile yeni ReactionZone sayısı aynı kavram değildir; "
            "bu tablo migration davranışını yan yana izlemek içindir."
        )
        st.dataframe(shadow_comparison_frame(replay), width="stretch", hide_index=True)

st.divider()
st.header("Legacy TargetCluster diagnostics")
st.caption(
    "Bu bölüm backward-compatibility ve migration audit içindir. Yeni semantic modelde OB/FVG/S-R target değildir."
)
summary = targeting_summary_values(snapshot)
for column, (label, value) in zip(st.columns(4), summary.items(), strict=True):
    column.metric(label, value)

left, right = st.columns((1.2, 1.0))
with left:
    st.subheader("Distance bands")
    st.dataframe(
        distance_bands_frame(snapshot.clusters),
        width="stretch",
        hide_index=True,
    )
with right:
    st.subheader("Legacy semantic transitions")
    semantic = semantic_transitions_frame(replay)
    if semantic.empty:
        st.info("Legacy semantic target transition yok.")
    else:
        st.dataframe(semantic, width="stretch", hide_index=True)

st.subheader("Legacy cluster seçimi")
clusters = target_clusters_frame(snapshot)
if clusters.empty:
    st.info("Bu replay noktasında legacy target cluster yok.")
    st.stop()

cluster_by_id = {cluster.identity: cluster for cluster in snapshot.clusters}
selected_cluster_id = st.selectbox(
    "Cluster",
    tuple(cluster_by_id),
    format_func=lambda identity: (
        f"{identity} · {cluster_by_id[identity].kind.value} · "
        f"{cluster_by_id[identity].side.value} · "
        f"{cluster_by_id[identity].distance_atr:.2f} ATR · "
        f"origins={cluster_by_id[identity].independent_origin_count} · "
        f"families={cluster_by_id[identity].independent_family_count}"
    ),
)
selected_cluster = cluster_by_id[selected_cluster_id]

st.plotly_chart(
    _causal_figure(cache_root, symbol, point, selected_cluster),
    width="stretch",
)

st.subheader("Legacy cluster stability")
st.json(
    cluster_stability_values(
        replay,
        point_index=point_index,
        cluster=selected_cluster,
    )
)

st.subheader("Legacy cluster anatomy")
st.caption(
    f"Envelope={selected_cluster.envelope_low:.4f}–{selected_cluster.envelope_high:.4f} · "
    f"core={selected_cluster.core_low}–{selected_cluster.core_high} · "
    f"liquidity anchor={selected_cluster.liquidity_anchor} · "
    f"raw={selected_cluster.raw_source_count} · "
    f"origins={selected_cluster.independent_origin_count} · "
    f"families={selected_cluster.independent_family_count} · "
    f"TF={', '.join(selected_cluster.timeframes_present)}"
)
st.dataframe(
    cluster_anatomy_frame(selected_cluster),
    width="stretch",
    hide_index=True,
)

st.subheader("Bu snapshot'taki tüm legacy cluster'lar")
st.dataframe(clusters, width="stretch", hide_index=True)
