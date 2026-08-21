from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from financial_dashboard.engines.market_structure_state import EVENT_BOS, EVENT_CHOCH
from financial_dashboard.engines.pattern_compression_core import PROFILE_VALUES
from financial_dashboard.engines.three_domain_observer import FOUNDATION_OBSERVER_TIMEFRAMES
from financial_dashboard.ui.charts import make_market_figure
from financial_dashboard.ui.runtime import (
    cache_fingerprint,
    discover_cached_symbols,
    inspect_symbol_cache,
    replay_cached_observer,
    runnable_timeframes,
)
from financial_dashboard.ui.view_models import (
    cache_status_frame,
    confluence_frame,
    event_zone_links_frame,
    location_outcomes_frame,
    mtf_matrix_frame,
    observer_facts_frame,
    opposing_conflicts_frame,
    overview_values,
    structure_events_frame,
    structure_history_frame,
    zones_frame,
)


st.set_page_config(
    page_title="Financial Dashboard · Three-Domain Observer",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
      [data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.22);
        border-radius: .65rem;
        padding: .65rem .8rem;
      }
      [data-testid="stSidebar"] hr {margin: .85rem 0;}
      .fd-subtle {color: #6e7781; font-size: .9rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def _cached_symbols(cache_root: str, epoch: int) -> tuple[str, ...]:
    del epoch
    return discover_cached_symbols(cache_root)


@st.cache_data(show_spinner=False)
def _cached_inspection(
    cache_root: str,
    symbol: str,
    fingerprint: tuple[tuple[str, int, int], ...],
    epoch: int,
):
    del fingerprint, epoch
    return inspect_symbol_cache(cache_root, symbol=symbol)


@st.cache_data(show_spinner="Kapalı ve tamamlanmış mumlar yeniden oynatılıyor…")
def _cached_replay(
    cache_root: str,
    symbol: str,
    timeframes: tuple[str, ...],
    profile: str,
    fingerprint: tuple[tuple[str, int, int], ...],
    epoch: int,
):
    del fingerprint, epoch
    return replay_cached_observer(
        cache_root,
        symbol=symbol,
        timeframes=timeframes,
        pattern_profile=profile,
    )


def _default_cache_root() -> str:
    configured = os.environ.get("FINANCIAL_DASHBOARD_CACHE")
    if configured:
        return configured
    return str(Path.cwd() / "data" / "cache")


def _show_empty_state(cache_root: str) -> None:
    st.info(
        "Bu dizinde temel zaman dilimlerine ait Parquet dosyası bulunamadı. "
        "Dosya biçimi `SEMBOL__zaman_dilimi.parquet` olmalıdır."
    )
    st.code(
        f"{cache_root}/BTC-USD__1d.parquet\n"
        f"{cache_root}/BTC-USD__4h.parquet\n"
        f"{cache_root}/BTC-USD__2h.parquet\n"
        f"{cache_root}/BTC-USD__1h.parquet\n"
        f"{cache_root}/BTC-USD__30m.parquet",
        language="text",
    )


def main() -> None:
    if "cache_epoch" not in st.session_state:
        st.session_state.cache_epoch = 0

    with st.sidebar:
        st.header("Yerel inceleme")
        cache_root_input = st.text_input(
            "Parquet cache dizini",
            value=_default_cache_root(),
            help="Provider çağrısı yapılmaz; yalnızca var olan yerel cache okunur.",
        )
        cache_root = str(Path(cache_root_input).expanduser().resolve(strict=False))
        if st.button("Cache'i yeniden tara", use_container_width=True):
            st.session_state.cache_epoch += 1
            st.cache_data.clear()

        symbols = _cached_symbols(cache_root, st.session_state.cache_epoch)
        if symbols:
            symbol = st.selectbox("Sembol", symbols)
        else:
            symbol = ""
            st.caption("Keşfedilmiş sembol yok")

        profile = st.selectbox(
            "Pattern/Compression profili",
            PROFILE_VALUES,
            index=1,
            help="Bu ayar yalnızca deterministik Pattern/Compression motoruna gider.",
        )
        st.divider()
        st.caption("Temel zaman dilimleri")
        st.code(" · ".join(FOUNDATION_OBSERVER_TIMEFRAMES), language="text")
        st.caption(
            "Otomatik veri çekme yoktur. Açık veya eksik mumlar analitik durumu ilerletmez."
        )

    st.title("Three-Domain Market Observer")
    st.markdown(
        "<div class='fd-subtle'>MTF baskı bağlamı, Market Structure ilerlemesi ve "
        "Support/Resistance konumu — birbirinden ayrı, sürekli ve paralel.</div>",
        unsafe_allow_html=True,
    )
    st.warning(
        "Inspection/debug v0.1 · Bu arayüz al/sat sinyali, öneri, stop veya hedef üretmez.",
        icon="🔎",
    )

    if not symbol:
        _show_empty_state(cache_root)
        st.stop()

    fingerprint = cache_fingerprint(cache_root, symbol=symbol)
    statuses = _cached_inspection(
        cache_root,
        symbol,
        fingerprint,
        st.session_state.cache_epoch,
    )
    runnable = runnable_timeframes(statuses)

    if not runnable:
        st.error(
            "Bu sembol için yeniden oynatılabilir kapalı + tamamlanmış mum bulunamadı."
        )
        st.subheader("Veri kalitesi")
        st.dataframe(cache_status_frame(statuses), use_container_width=True, hide_index=True)
        st.stop()

    missing = tuple(tf for tf in FOUNDATION_OBSERVER_TIMEFRAMES if tf not in runnable)
    if missing:
        st.warning(
            "Eksik veya geçersiz zaman dilimleri devre dışı bırakılmadı; "
            f"matriste açıkça MISSING/INVALID gösterilir: {', '.join(missing)}"
        )

    try:
        result = _cached_replay(
            cache_root,
            symbol,
            runnable,
            profile,
            fingerprint,
            st.session_state.cache_epoch,
        )
    except Exception as error:  # Streamlit boundary: retain quality diagnostics.
        st.error(f"Deterministik replay tamamlanamadı: {type(error).__name__}: {error}")
        st.subheader("Veri kalitesi")
        st.dataframe(cache_status_frame(statuses), use_container_width=True, hide_index=True)
        st.stop()

    st.caption(
        f"{symbol} · replay: {', '.join(result.timeframes)} · as-of: "
        f"{result.observation.as_of}"
    )
    boundary_active = tuple(
        diagnostic.timeframe
        for diagnostic in result.structure_history
        if diagnostic.current_progression_uses_initial_structure
    )
    if boundary_active:
        st.warning(
            "Market Structure sol-sınır bağımlılığı: "
            f"{', '.join(boundary_active)}. Bu zaman dilimlerindeki güncel ilerleme, "
            "cache içinde nötr başlangıçtan kurulan ilk yöne dayanıyor. Cache öncesi "
            "yapı gözlenmediği için önceki bearish/bullish bağlam veya ARGENT ile "
            "CHoCH eşliği bu pencereyle kanıtlanamaz.",
            icon="⚠️",
        )
    values = overview_values(result)
    card_columns = st.columns(6)
    for column, label in zip(
        card_columns,
        (
            "MTF pressure",
            "Recovery evidence",
            "Up structure",
            "Down structure",
            "Location",
            "Combined state",
        ),
        strict=True,
    ):
        column.metric(label, values[label])

    overview_tab, chart_tab, structure_tab, location_tab, quality_tab = st.tabs(
        (
            "Genel görünüm",
            "Grafik",
            "Market Structure",
            "Zones & location",
            "Data quality",
        )
    )

    with overview_tab:
        st.subheader("Bağımsız MTF matrisi")
        st.caption(
            "Beş temel zaman dilimi her zaman görünür. Eksik veri, nötr görüş gibi yorumlanmaz."
        )
        st.dataframe(
            mtf_matrix_frame(result, statuses),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Birleşik betimleyici gerçekler")
        facts = observer_facts_frame(result)
        if facts.empty:
            st.info("Bu replay kesitinde ek gerilim veya açıklama gerçeği yok.")
        else:
            st.dataframe(facts, use_container_width=True, hide_index=True)

    with chart_tab:
        left, middle, right = st.columns((1.1, 2.2, 1.2))
        with left:
            chart_timeframe = st.selectbox(
                "Grafik zaman dilimi", result.timeframes, key="chart_timeframe"
            )
        with middle:
            chart_zone_timeframes = st.multiselect(
                "Gösterilecek zone zaman dilimleri",
                result.timeframes,
                default=(chart_timeframe,),
            )
        with right:
            bar_limit = st.slider("Mum sayısı", 50, 1000, 300, step=50)
        option_columns = st.columns(3)
        show_events = option_columns[0].checkbox("BOS/CHoCH", value=True)
        show_confluence = option_columns[1].checkbox("Confluence", value=True)
        show_conflicts = option_columns[2].checkbox("Opposing conflicts", value=True)
        figure = make_market_figure(
            result,
            timeframe=chart_timeframe,
            zone_timeframes=chart_zone_timeframes,
            bar_limit=bar_limit,
            show_events=show_events,
            show_confluence=show_confluence,
            show_conflicts=show_conflicts,
        )
        st.plotly_chart(figure, use_container_width=True)

    with structure_tab:
        st.subheader("History boundary & warm-up")
        st.caption(
            "Bu tablo keyfi bir minimum-bar eşiği uygulamaz; replay'in gerçekten "
            "gördüğü ilk/son kapalı mumu ve teyitli external olay kronolojisini raporlar."
        )
        st.dataframe(
            structure_history_frame(result),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("BOS / CHoCH event ledger")
        event_frame = structure_events_frame(result)
        filter_columns = st.columns(3)
        scopes = filter_columns[0].multiselect(
            "Scope", ("EXTERNAL", "INTERNAL"), default=("EXTERNAL", "INTERNAL")
        )
        event_types = filter_columns[1].multiselect(
            "Event", (EVENT_BOS, EVENT_CHOCH), default=(EVENT_BOS, EVENT_CHOCH)
        )
        event_timeframes = filter_columns[2].multiselect(
            "Timeframe", result.timeframes, default=result.timeframes
        )
        filtered_events = event_frame[
            event_frame["Scope"].isin(scopes)
            & event_frame["Event"].isin(event_types)
            & event_frame["Timeframe"].isin(event_timeframes)
        ]
        st.caption(
            "Causal available at, eventin ilgili zaman diliminde ancak hangi anda "
            "diğer domainlerce bilinebildiğini gösterir."
        )
        st.dataframe(filtered_events, use_container_width=True, hide_index=True)

    with location_tab:
        zone_table = zones_frame(result)
        zone_filter_columns = st.columns(3)
        zone_sides = zone_filter_columns[0].multiselect(
            "Zone side", ("SUPPORT", "RESISTANCE"), default=("SUPPORT", "RESISTANCE")
        )
        zone_lifecycles = tuple(zone_table["Lifecycle"].dropna().unique())
        selected_lifecycles = zone_filter_columns[1].multiselect(
            "Lifecycle", zone_lifecycles, default=zone_lifecycles
        )
        selected_zone_tfs = zone_filter_columns[2].multiselect(
            "Zone timeframe", result.timeframes, default=result.timeframes
        )
        filtered_zones = zone_table[
            zone_table["Side"].isin(zone_sides)
            & zone_table["Lifecycle"].isin(selected_lifecycles)
            & zone_table["Timeframe"].isin(selected_zone_tfs)
        ]
        st.subheader("Typed zones")
        st.dataframe(filtered_zones, use_container_width=True, hide_index=True)

        confluence_view, conflict_view, outcomes_view, links_view = st.tabs(
            ("Confluence", "Opposing conflicts", "Causal outcomes", "Event-zone links")
        )
        with confluence_view:
            st.dataframe(
                confluence_frame(result), use_container_width=True, hide_index=True
            )
        with conflict_view:
            st.dataframe(
                opposing_conflicts_frame(result),
                use_container_width=True,
                hide_index=True,
            )
        with outcomes_view:
            st.dataframe(
                location_outcomes_frame(result),
                use_container_width=True,
                hide_index=True,
            )
        with links_view:
            st.dataframe(
                event_zone_links_frame(result),
                use_container_width=True,
                hide_index=True,
            )

    with quality_tab:
        st.subheader("Cache freshness & source quality")
        st.dataframe(
            cache_status_frame(statuses), use_container_width=True, hide_index=True
        )
        st.caption(
            "Replay girdisi yalnızca `is_closed=True` ve `is_complete=True` satırlardır. "
            "Cache dosyasındaki açık/eksik satırlar sayılır fakat motor durumunu ilerletmez."
        )
        st.subheader("Usable replay range & structural warm-up")
        st.dataframe(
            structure_history_frame(result),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "LEFT_BOUNDARY_ACTIVE, güncel yapının cache içindeki ilk yön kurulumuna "
            "dayandığını söyler; daha fazla barın otomatik olarak yeterli olduğunu veya "
            "cache öncesinde kesin bir CHoCH bulunduğunu söylemez."
        )


main()
