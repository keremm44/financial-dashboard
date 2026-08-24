from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.engines.market_structure_state import EVENT_BOS, EVENT_CHOCH
from financial_dashboard.engines.pattern_compression_core import PROFILE_VALUES
from financial_dashboard.ui.cross_domain_view_models import (
    cross_domain_context_frame,
    cross_domain_knowledge_frame,
    cross_domain_permission_frame,
    cross_domain_summary_values,
    cross_domain_zones_frame,
)
from financial_dashboard.ui.runtime import (
    cache_fingerprint,
    discover_cached_symbols,
    inspect_symbol_cache,
    replay_cached_observer,
    replay_cached_workspace,
    runnable_timeframes,
)
from financial_dashboard.ui.targeting_view_models import (
    target_clusters_frame,
    targeting_summary_values,
)
from financial_dashboard.ui.view_models import (
    cache_status_frame,
    ham_mtf_evidence_frame,
    mtf_matrix_frame,
    structure_events_frame,
    structure_history_frame,
    volume_mtf_matrix_frame,
    zones_frame,
)
from financial_dashboard.ui.workspace_view_models import workspace_domain_status_frame


st.set_page_config(
    page_title="Financial Dashboard",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
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


@st.cache_data(show_spinner="Market foundation hazırlanıyor…")
def _cached_observer(
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


@st.cache_data(show_spinner="Tam analiz workspace hazırlanıyor…")
def _cached_workspace(
    cache_root: str,
    symbol: str,
    timeframes: tuple[str, ...],
    profile: str,
    fingerprint: tuple[tuple[str, int, int], ...],
    epoch: int,
):
    del fingerprint, epoch
    return replay_cached_workspace(
        cache_root,
        symbol=symbol,
        timeframes=timeframes,
        pattern_profile=profile,
    )


def _default_cache_root() -> str:
    configured = os.environ.get("FINANCIAL_DASHBOARD_CACHE")
    if configured:
        return configured
    return str(Path.cwd() / ".cache" / "live-smoke-15m")


def _domain_error(domain) -> str:
    if domain.error_type is None:
        return "unknown workspace error"
    return f"{domain.error_type}: {domain.error_message}"


def _render_market(observer, *, targeting=None) -> None:
    structure_view, zones_view, targets_view = st.tabs(
        ("Market Structure", "Zones", "Targeting")
    )

    with structure_view:
        event_frame = structure_events_frame(observer)
        columns = st.columns(3)
        scopes = columns[0].multiselect(
            "Scope", ("EXTERNAL", "INTERNAL"), default=("EXTERNAL", "INTERNAL")
        )
        event_types = columns[1].multiselect(
            "Event", (EVENT_BOS, EVENT_CHOCH), default=(EVENT_BOS, EVENT_CHOCH)
        )
        event_tfs = columns[2].multiselect("TF", observer.timeframes, default=observer.timeframes)
        filtered = event_frame[
            event_frame["Scope"].isin(scopes)
            & event_frame["Event"].isin(event_types)
            & event_frame["Timeframe"].isin(event_tfs)
        ]
        st.dataframe(filtered, width="stretch", hide_index=True)

    with zones_view:
        st.dataframe(zones_frame(observer), width="stretch", hide_index=True)

    with targets_view:
        if targeting is None:
            st.info("Targeting hızlı başlangıçta çalıştırılmaz. Sidebar'dan 'Tam analiz' görünümünü açın.")
        else:
            st.json(targeting_summary_values(targeting))
            clusters = target_clusters_frame(targeting)
            if clusters.empty:
                st.info("Aktif target cluster yok.")
            else:
                st.dataframe(clusters, width="stretch", hide_index=True)


def _render_context(workspace) -> None:
    if not workspace.cross_domain.is_ready or workspace.cross_domain_result is None:
        st.error(f"Cross-domain context hazırlanamadı: {_domain_error(workspace.cross_domain)}")
        return
    result = workspace.cross_domain_result
    values = cross_domain_summary_values(result)
    metric_specs = (
        ("Main thesis", values["Structural thesis"]),
        ("Reaction", values["Reaction"]),
        ("Reversal", values["Reversal"]),
        ("Objective", values["Objective"]),
        ("Conflict", values["Conflict"]),
        ("Gate", values["Gate"]),
    )
    for column, (label, value) in zip(st.columns(6), metric_specs, strict=True):
        column.metric(label, value)
    permission_cols = st.columns(3)
    permission_cols[0].metric("Permission scope", values["Permission scope"])
    permission_cols[1].metric("Permitted side", values["Permitted side"])
    permission_cols[2].metric("Continuation", values["Continuation"])

    context_view, zone_view, permission_view, knowledge_view = st.tabs(
        ("Context axes", "Qualified zones", "Permission", "Knowledge boundary")
    )
    with context_view:
        st.dataframe(cross_domain_context_frame(result), width="stretch", hide_index=True)
    with zone_view:
        st.dataframe(cross_domain_zones_frame(result), width="stretch", hide_index=True)
    with permission_view:
        st.dataframe(cross_domain_permission_frame(result), width="stretch", hide_index=True)
    with knowledge_view:
        st.dataframe(cross_domain_knowledge_frame(result), width="stretch", hide_index=True)


def main() -> None:
    if "cache_epoch" not in st.session_state:
        st.session_state.cache_epoch = 0

    with st.sidebar:
        st.header("Financial Dashboard")
        cache_root_input = st.text_input("Parquet cache", value=_default_cache_root())
        cache_root = str(Path(cache_root_input).expanduser().resolve(strict=False))
        if st.button("Cache'i yeniden tara", width="stretch"):
            st.session_state.cache_epoch += 1
            st.cache_data.clear()

        symbols = _cached_symbols(cache_root, st.session_state.cache_epoch)
        symbol = st.selectbox("Sembol", symbols) if symbols else ""
        profile = st.selectbox("Pattern profili", PROFILE_VALUES, index=1)
        view = st.radio(
            "Çalışma görünümü",
            ("Market (hızlı)", "Tam analiz", "Evidence", "Diagnostics"),
            index=0,
            help="Market hızlı yolu yalnızca foundation observer çalıştırır. Diğer görünümler full workspace'i ihtiyaç halinde yükler.",
        )
        st.caption("Analiz TF: " + " · ".join(ANALYSIS_TIMEFRAMES))

    st.title("Financial Dashboard")
    st.caption("Hızlı başlangıç: Market foundation önce; ağır domainler ihtiyaç halinde. Action layer yok.")

    if not symbol:
        st.info("Bu cache dizininde analiz edilebilir sembol bulunamadı.")
        st.code(cache_root, language="text")
        st.stop()

    fingerprint = cache_fingerprint(cache_root, symbol=symbol)
    statuses = _cached_inspection(cache_root, symbol, fingerprint, st.session_state.cache_epoch)
    runnable = runnable_timeframes(statuses)
    if not runnable:
        st.error("Bu sembol için replay edilebilir kapalı + tamamlanmış mum bulunamadı.")
        st.dataframe(cache_status_frame(statuses), width="stretch", hide_index=True)
        st.stop()

    missing = tuple(tf for tf in ANALYSIS_TIMEFRAMES if tf not in runnable)
    if missing:
        st.warning(f"Eksik/invalid TF nötr sayılmaz: {', '.join(missing)}")

    if view == "Market (hızlı)":
        observer = _cached_observer(
            cache_root,
            symbol,
            runnable,
            profile,
            fingerprint,
            st.session_state.cache_epoch,
        )
        st.caption(
            f"{observer.symbol} · {', '.join(observer.timeframes)} · FAST FOUNDATION"
        )
        _render_market(observer)
        return

    workspace = _cached_workspace(
        cache_root,
        symbol,
        runnable,
        profile,
        fingerprint,
        st.session_state.cache_epoch,
    )
    observer = workspace.observer
    st.caption(
        f"{workspace.symbol} · {', '.join(workspace.timeframes)} · observer as-of: {observer.observation.as_of}"
    )

    if view == "Tam analiz":
        _render_context(workspace)
        st.divider()
        _render_market(observer, targeting=workspace.targeting_result)
    elif view == "Evidence":
        volume_view, ham_view, mtf_view = st.tabs(("Volume", "HAM", "MTF foundation"))
        with volume_view:
            if workspace.volume_result is None:
                st.info("Volume sonucu yok.")
            else:
                st.dataframe(
                    volume_mtf_matrix_frame(workspace.volume_result, statuses),
                    width="stretch",
                    hide_index=True,
                )
        with ham_view:
            if workspace.ham_result is None:
                st.info("HAM sonucu yok.")
            else:
                st.dataframe(
                    ham_mtf_evidence_frame(workspace.ham_result, statuses),
                    width="stretch",
                    hide_index=True,
                )
        with mtf_view:
            st.dataframe(mtf_matrix_frame(observer, statuses), width="stretch", hide_index=True)
    else:
        st.subheader("Domain health")
        st.dataframe(workspace_domain_status_frame(workspace), width="stretch", hide_index=True)
        st.subheader("Cache / source quality")
        st.dataframe(cache_status_frame(statuses), width="stretch", hide_index=True)
        st.subheader("Structure warm-up / replay range")
        st.dataframe(structure_history_frame(observer), width="stretch", hide_index=True)
        if workspace.cross_domain_result is not None:
            st.subheader("Cross-domain knowledge boundary")
            st.dataframe(
                cross_domain_knowledge_frame(workspace.cross_domain_result),
                width="stretch",
                hide_index=True,
            )


main()
