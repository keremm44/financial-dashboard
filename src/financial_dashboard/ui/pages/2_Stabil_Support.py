from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from financial_dashboard.ui.runtime import cache_fingerprint, discover_cached_symbols
from financial_dashboard.ui.stabil_support_runtime import replay_cached_stabil_support_history
from financial_dashboard.ui.stabil_support_view_models import (
    stabil_support_event_counts_frame,
    stabil_support_events_frame,
    stabil_support_rebase_frame,
    stabil_support_reclaim_frame,
    stabil_support_replay_frame,
    stabil_support_retest_frame,
    stabil_support_summary_values,
)


st.set_page_config(
    page_title="Financial Dashboard · Stabil Support",
    page_icon="▱",
    layout="wide",
)


def _default_cache_root() -> str:
    configured = os.environ.get("FINANCIAL_DASHBOARD_CACHE")
    if configured:
        return configured
    return str(Path.cwd() / "data" / "cache")


@st.cache_data(show_spinner="Stabil support replay çalıştırılıyor…")
def _cached_replay(
    cache_root: str,
    symbol: str,
    fingerprint: tuple[tuple[str, int, int], ...],
    minimum_bars: int,
    step: int,
    max_points: int,
):
    del fingerprint
    return replay_cached_stabil_support_history(
        cache_root,
        symbol=symbol,
        minimum_bars=minimum_bars,
        step=step,
        max_points=max_points,
    )


with st.sidebar:
    st.header("Stabil Support")
    cache_root_input = st.text_input("Parquet cache dizini", value=_default_cache_root())
    cache_root = str(Path(cache_root_input).expanduser().resolve(strict=False))
    symbols = discover_cached_symbols(cache_root, timeframes=("1d",))
    symbol = st.selectbox("Sembol", symbols) if symbols else ""
    max_points = st.slider("Replay noktası", 20, 300, 100, step=20)
    step = st.slider("Replay adımı (gün)", 1, 10, 1)
    minimum_bars = st.number_input("Minimum günlük bar", min_value=1, value=1, step=1)

st.title("Stabil · Günlük Yapısal Destek Yaşam Döngüsü")
st.caption(
    "Bu domain yalnızca mevcut Stabil günlük yapısal destek stepline'ına göre fiyat davranışını "
    "gözlemler. Ana trend dönüşü, al/sat, hedef, stop veya olasılık üretmez."
)
st.caption(
    "Araştırma notu: %5/%10/%20 genişleme ve 7–8 bar altında kalma gözlemleri yalnız replay "
    "hipotezidir; hiçbirisi sabit threshold veya failure kuralı değildir."
)

if not symbol:
    st.info("1d cache bulunan bir sembol yok.")
    st.stop()

fingerprint = cache_fingerprint(cache_root, symbol=symbol, timeframes=("1d",))
try:
    replay = _cached_replay(
        cache_root,
        symbol,
        fingerprint,
        int(minimum_bars),
        int(step),
        int(max_points),
    )
except Exception as error:
    st.error(f"Stabil support replay tamamlanamadı: {type(error).__name__}: {error}")
    st.stop()

latest = replay.latest
if latest is None:
    st.info("Replay noktası üretilemedi.")
    st.stop()

values = stabil_support_summary_values(latest)
metric_specs = (
    ("Durum", values["State"]),
    ("Günlük destek", values["Support"]),
    ("Mesafe %", values["Distance %"]),
    ("Mesafe ATR", values["Distance ATR"]),
    ("Altında bar", values["Bars below"]),
    ("Progression", values["Progression"]),
)
for column, (label, value) in zip(st.columns(6), metric_specs, strict=True):
    column.metric(label, value)

st.caption(
    f"{replay.symbol} · 1d · as-of: {latest.as_of} · validity={latest.validity.value} · "
    f"dynamics={latest.dynamics.value} · reclaims={latest.reclaim_count}"
)

if latest.close_below_support:
    st.warning(
        f"Fiyat günlük destek altında kapanmış durumda · bars_below={latest.bars_below_support}. "
        "Bu sayaç 7–8 bar hipotezini ölçmek içindir; kendisi sabit bir failure kuralı değildir."
    )
elif latest.intrabar_below_support:
    st.info("Mum desteğin altına intrabar sarkmış ancak kapanış destek altında değildir.")

(
    timeline_tab,
    events_tab,
    reclaim_tab,
    retest_tab,
    rebase_tab,
    provenance_tab,
) = st.tabs(
    (
        "Lifecycle timeline",
        "Event ledger",
        "Breach / reclaim",
        "Test / hold",
        "Rebase",
        "Provenance",
    )
)

with timeline_tab:
    st.subheader("Prefix-safe replay")
    st.caption(
        "Her satır yalnız o güne kadar causally available olan destek bilgisiyle yeniden oluşturulur."
    )
    st.dataframe(stabil_support_replay_frame(replay), width="stretch", hide_index=True)

with events_tab:
    left, right = st.columns((2.2, 1.0))
    with left:
        st.subheader("Append-only lifecycle event ledger")
        st.dataframe(stabil_support_events_frame(latest), width="stretch", hide_index=True)
    with right:
        st.subheader("Event counts")
        st.dataframe(stabil_support_event_counts_frame(latest), width="stretch", hide_index=True)

with reclaim_tab:
    st.subheader("Breach / floor break / reclaim / loss")
    st.caption(
        "Bars below factual tutulur. Reclaim window için sabit 7/8 bar kuralı uygulanmaz."
    )
    frame = stabil_support_reclaim_frame(latest)
    if frame.empty:
        st.info("Bu replay penceresinde breach/reclaim olayı yok.")
    else:
        st.dataframe(frame, width="stretch", hide_index=True)

with retest_tab:
    st.subheader("Support test / held events")
    frame = stabil_support_retest_frame(latest)
    if frame.empty:
        st.info("Bu replay penceresinde support test/held olayı yok.")
    else:
        st.dataframe(frame, width="stretch", hide_index=True)

with rebase_tab:
    st.subheader("Structural support progression")
    st.caption("Rebase, moving-average yönü değil; yeni confirmed Stabil support basamağıdır.")
    frame = stabil_support_rebase_frame(latest)
    if frame.empty:
        st.info("Bu replay penceresinde support rebase olayı yok.")
    else:
        st.dataframe(frame, width="stretch", hide_index=True)

with provenance_tab:
    st.subheader("Current support provenance")
    st.dataframe(
        {
            "Alan": [
                "support_origin_at",
                "support_confirmed_at",
                "support_available_at",
                "support_level",
                "support_floor",
                "bars_since_support",
                "bars_above_support",
                "bars_below_support",
                "reclaim_count",
            ],
            "Değer": [
                latest.support_origin_at,
                latest.support_confirmed_at,
                latest.support_available_at,
                latest.support_level,
                latest.support_floor,
                latest.bars_since_support,
                latest.bars_above_support,
                latest.bars_below_support,
                latest.reclaim_count,
            ],
        },
        width="stretch",
        hide_index=True,
    )
