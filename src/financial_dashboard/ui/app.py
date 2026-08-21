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
    replay_cached_ham,
    replay_cached_observer,
    replay_cached_volume,
    runnable_timeframes,
)
from financial_dashboard.ui.view_models import (
    cache_status_frame,
    confluence_frame,
    event_zone_links_frame,
    ham_history_frame,
    ham_indicator_evidence_frame,
    ham_mtf_evidence_frame,
    location_outcomes_frame,
    mtf_matrix_frame,
    observer_facts_frame,
    opposing_conflicts_frame,
    overview_values,
    structure_events_frame,
    structure_history_frame,
    volume_deduplication_frame,
    volume_diagnostics_frame,
    volume_event_links_frame,
    volume_history_frame,
    volume_mtf_matrix_frame,
    volume_propagations_frame,
    volume_risk_transitions_frame,
    volume_shocks_frame,
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


@st.cache_data(show_spinner="Ham MTF evidence geçmişi yeniden oynatılıyor…")
def _cached_ham_replay(
    cache_root: str,
    symbol: str,
    timeframes: tuple[str, ...],
    fingerprint: tuple[tuple[str, int, int], ...],
    epoch: int,
):
    del fingerprint, epoch
    return replay_cached_ham(
        cache_root,
        symbol=symbol,
        timeframes=timeframes,
    )


@st.cache_data(show_spinner="Volume Participation MTF geçmişi yeniden oynatılıyor…")
def _cached_volume_replay(
    cache_root: str,
    symbol: str,
    timeframes: tuple[str, ...],
    fingerprint: tuple[tuple[str, int, int], ...],
    epoch: int,
    _structure_replay,
):
    del fingerprint, epoch
    return replay_cached_volume(
        cache_root,
        symbol=symbol,
        timeframes=timeframes,
        structure_replay=_structure_replay,
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
        if st.button("Cache'i yeniden tara", width="stretch"):
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
        st.dataframe(cache_status_frame(statuses), width="stretch", hide_index=True)
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
        st.dataframe(cache_status_frame(statuses), width="stretch", hide_index=True)
        st.stop()

    ham_result = None
    ham_error: Exception | None = None
    try:
        ham_result = _cached_ham_replay(
            cache_root,
            symbol,
            runnable,
            fingerprint,
            st.session_state.cache_epoch,
        )
    except Exception as error:  # Ham inspection must not hide the other domains.
        ham_error = error

    volume_result = None
    volume_error: Exception | None = None
    try:
        volume_result = _cached_volume_replay(
            cache_root,
            symbol,
            runnable,
            fingerprint,
            st.session_state.cache_epoch,
            result.structure_location,
        )
    except Exception as error:  # Volume inspection must not hide the other domains.
        volume_error = error

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

    (
        overview_tab,
        chart_tab,
        structure_tab,
        location_tab,
        ham_tab,
        volume_tab,
        quality_tab,
    ) = st.tabs(
        (
            "Genel görünüm",
            "Grafik",
            "Market Structure",
            "Zones & location",
            "Ham evidence",
            "Volume Participation",
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
            width="stretch",
            hide_index=True,
        )
        st.subheader("Birleşik betimleyici gerçekler")
        facts = observer_facts_frame(result)
        if facts.empty:
            st.info("Bu replay kesitinde ek gerilim veya açıklama gerçeği yok.")
        else:
            st.dataframe(facts, width="stretch", hide_index=True)

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
        st.plotly_chart(figure, width="stretch")

    with structure_tab:
        st.subheader("History boundary & warm-up")
        st.caption(
            "Bu tablo keyfi bir minimum-bar eşiği uygulamaz; replay'in gerçekten "
            "gördüğü ilk/son kapalı mumu ve teyitli external olay kronolojisini raporlar."
        )
        st.dataframe(
            structure_history_frame(result),
            width="stretch",
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
        st.dataframe(filtered_events, width="stretch", hide_index=True)

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
        st.dataframe(filtered_zones, width="stretch", hide_index=True)

        confluence_view, conflict_view, outcomes_view, links_view = st.tabs(
            ("Confluence", "Opposing conflicts", "Causal outcomes", "Event-zone links")
        )
        with confluence_view:
            st.dataframe(
                confluence_frame(result), width="stretch", hide_index=True
            )
        with conflict_view:
            st.dataframe(
                opposing_conflicts_frame(result),
                width="stretch",
                hide_index=True,
            )
        with outcomes_view:
            st.dataframe(
                location_outcomes_frame(result),
                width="stretch",
                hide_index=True,
            )
        with links_view:
            st.dataframe(
                event_zone_links_frame(result),
                width="stretch",
                hide_index=True,
            )

    with ham_tab:
        st.subheader("Ham Indicator Dashboard v2.3.7 · nötr evidence")
        st.caption(
            "Bu görünüm karar üretmez. Ham system_state/system_bias kullanılmaz; "
            "yalnızca kapalı + tamamlanmış mumların Tur-1 bileşenleri ve "
            "PRICE/MOMENTUM/TIMING/FLOW özetleri gösterilir."
        )
        if ham_error is not None:
            st.error(
                "Ham evidence replay tamamlanamadı: "
                f"{type(ham_error).__name__}: {ham_error}"
            )
        elif ham_result is None:
            st.info("Ham evidence replay sonucu bulunmuyor.")
        else:
            st.dataframe(
                ham_mtf_evidence_frame(ham_result, statuses),
                width="stretch",
                hide_index=True,
            )
            st.info(
                "±5 Ham adaptörü yalnızca otoritatif core direction + confidence "
                "oluştuktan sonra çalışır. Three-domain pressure burada karar yönü "
                "yerine kullanılmaz.",
                icon="ℹ️",
            )
            detail_tab, history_tab = st.tabs(
                ("Latest indicator detail", "Confirmed history")
            )
            with detail_tab:
                detail_timeframe = st.selectbox(
                    "Ham detay zaman dilimi",
                    ham_result.timeframes,
                    key="ham_detail_timeframe",
                )
                detail_replay = ham_result.replay_for(detail_timeframe)
                st.caption(
                    f"{detail_timeframe} · profile={detail_replay.profile.value} · "
                    f"latest={detail_replay.latest_timestamp} · "
                    f"source={detail_replay.source_quality.status.value}"
                )
                st.dataframe(
                    ham_indicator_evidence_frame(
                        ham_result,
                        timeframe=detail_timeframe,
                    ),
                    width="stretch",
                    hide_index=True,
                )
            with history_tab:
                history_columns = st.columns((1.2, 1.0, 1.0))
                history_timeframe = history_columns[0].selectbox(
                    "Ham geçmiş zaman dilimi",
                    ham_result.timeframes,
                    key="ham_history_timeframe",
                )
                show_all_history = history_columns[1].checkbox(
                    "Tüm geçmiş",
                    value=False,
                    key="ham_history_all",
                    help="Kapalı ve tamamlanmış cache geçmişinin tamamını gösterir.",
                )
                recent_limit = history_columns[2].number_input(
                    "Son mum",
                    min_value=10,
                    max_value=1000,
                    value=100,
                    step=10,
                    disabled=show_all_history,
                    key="ham_history_limit",
                )
                history = ham_history_frame(
                    ham_result,
                    timeframe=history_timeframe,
                    limit=None if show_all_history else int(recent_limit),
                )
                history_total = ham_result.replay_for(history_timeframe).bar_count
                st.caption(
                    f"Gösterilen {len(history)} / {history_total} teyitli mum. "
                    "Varsayılan görünüm son 100 mumdur; Tüm geçmiş isteğe bağlıdır."
                )
                st.dataframe(history, width="stretch", hide_index=True)

    with volume_tab:
        st.subheader("Volume Participation · causal MTF inspection")
        st.caption(
            "Bu sekme yalnızca teyitli kapalı mum kanıtını, Structure bağlantılarını, "
            "domain risklerini ve tanıları gösterir. Al/sat, giriş, öneri veya karar "
            "otoritesi yoktur."
        )
        if volume_error is not None:
            st.error(
                "Volume Participation replay tamamlanamadı: "
                f"{type(volume_error).__name__}: {volume_error}"
            )
        elif volume_result is None:
            st.info("Volume Participation replay sonucu bulunmuyor.")
        else:
            pressure = volume_result.round2.pressure
            st.caption(
                f"MTF bağlam={pressure.state.value} · yönsel skor="
                f"{pressure.directional_score:.3f} · coverage="
                f"{pressure.evidence_coverage:.3f} · authority="
                f"{pressure.decision_authority}. Ham hacimler toplanmaz."
            )
            st.dataframe(
                volume_mtf_matrix_frame(volume_result, statuses),
                width="stretch",
                hide_index=True,
            )
            (
                volume_links_tab,
                volume_shock_tab,
                volume_progression_tab,
                volume_history_tab,
                volume_diagnostics_tab,
            ) = st.tabs(
                (
                    "Structure links & risks",
                    "Shock / fake / reclaim",
                    "Structure progression",
                    "Confirmed history",
                    "Diagnostics & dedup",
                )
            )
            with volume_links_tab:
                st.caption(
                    "Same-TF ilişki otoritatiftir. Lower-TF inflow yalnızca zenginleştirir, "
                    "karşı çıkar veya izler; hedef timeframe teyidi üretemez."
                )
                st.dataframe(
                    volume_event_links_frame(volume_result),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "Opposition weakening bir release değildir. Yalnız aligned recovery, "
                    "authoritative Structure supersession veya tamamlanmış fake/reclaim "
                    "resolution blok riskini serbest bırakabilir."
                )
                st.dataframe(
                    volume_risk_transitions_frame(volume_result),
                    width="stretch",
                    hide_index=True,
                )
            with volume_shock_tab:
                st.caption(
                    "Tek-mum hacim patlaması DETECTED_UNCONFIRMED başlar; aynı mumda "
                    "confirmation veya entry authority kazanmaz."
                )
                st.dataframe(
                    volume_shocks_frame(volume_result),
                    width="stretch",
                    hide_index=True,
                )
            with volume_progression_tab:
                st.caption(
                    "PARTICIPATION_WITHOUT_STRUCTURE originleri için yalnız doğrudan "
                    "teyit edilmiş i/eCHoCH ve i/eBOS dağılımı gösterilir; üst timeframe "
                    "Structure icat edilmez."
                )
                st.dataframe(
                    volume_propagations_frame(volume_result),
                    width="stretch",
                    hide_index=True,
                )
            with volume_history_tab:
                history_columns = st.columns((1.2, 1.0, 1.0))
                volume_history_timeframe = history_columns[0].selectbox(
                    "Volume geçmiş zaman dilimi",
                    volume_result.timeframes,
                    key="volume_history_timeframe",
                )
                show_all_volume_history = history_columns[1].checkbox(
                    "Tüm Volume geçmişi",
                    value=False,
                    key="volume_history_all",
                    help="Kapalı ve tamamlanmış Volume geçmişinin tamamını gösterir.",
                )
                volume_recent_limit = history_columns[2].number_input(
                    "Volume son mum",
                    min_value=10,
                    max_value=5000,
                    value=100,
                    step=10,
                    disabled=show_all_volume_history,
                    key="volume_history_limit",
                )
                volume_history = volume_history_frame(
                    volume_result,
                    timeframe=volume_history_timeframe,
                    limit=(
                        None
                        if show_all_volume_history
                        else int(volume_recent_limit)
                    ),
                )
                volume_history_total = volume_result.replay_for(
                    volume_history_timeframe
                ).bar_count
                st.caption(
                    f"Gösterilen {len(volume_history)} / {volume_history_total} teyitli "
                    "Volume mumu. Varsayılan görünüm son 100 mumdur; tüm geçmiş "
                    "isteğe bağlıdır."
                )
                st.dataframe(volume_history, width="stretch", hide_index=True)
            with volume_diagnostics_tab:
                st.dataframe(
                    volume_diagnostics_frame(volume_result),
                    width="stretch",
                    hide_index=True,
                )
                st.dataframe(
                    volume_deduplication_frame(volume_result),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "Ham FLOW, Volume Participation ve gelecekteki Auction aynı kaynak "
                    "hacim ailesindedir; bağımsız oy gibi üst üste bindirilemez."
                )

    with quality_tab:
        st.subheader("Cache freshness & source quality")
        st.dataframe(
            cache_status_frame(statuses), width="stretch", hide_index=True
        )
        st.caption(
            "Replay girdisi yalnızca `is_closed=True` ve `is_complete=True` satırlardır. "
            "Cache dosyasındaki açık/eksik satırlar sayılır fakat motor durumunu ilerletmez."
        )
        st.subheader("Usable replay range & structural warm-up")
        st.dataframe(
            structure_history_frame(result),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "LEFT_BOUNDARY_ACTIVE, güncel yapının cache içindeki ilk yön kurulumuna "
            "dayandığını söyler; daha fazla barın otomatik olarak yeterli olduğunu veya "
            "cache öncesinde kesin bir CHoCH bulunduğunu söylemez."
        )


main()
