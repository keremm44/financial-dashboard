from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.ui.runtime import cache_fingerprint, discover_cached_symbols
from financial_dashboard.ui.volatility_view_models import volatility_lag_frame, volatility_latest_frame
from financial_dashboard.volatility_mtf_replay import VOLATILITY_TIMEFRAMES, VolatilityMTFReplayRunner


st.set_page_config(page_title="Financial Dashboard · Volatility", page_icon="≈", layout="wide")


def _default_cache_root() -> str:
    configured = os.environ.get("FINANCIAL_DASHBOARD_CACHE")
    return configured if configured else str(Path.cwd() / "data" / "cache")


@st.cache_data(show_spinner="Volatility replay çalıştırılıyor…")
def _cached_replay(cache_root: str, symbol: str, fingerprint, profile: str):
    del fingerprint
    return VolatilityMTFReplayRunner(ParquetOHLCVStore(cache_root)).replay(
        symbol,
        timeframes=VOLATILITY_TIMEFRAMES,
        profile=profile,
    )


with st.sidebar:
    st.header("Volatility / Bands / Fib")
    cache_root_input = st.text_input("Parquet cache dizini", value=_default_cache_root())
    cache_root = str(Path(cache_root_input).expanduser().resolve(strict=False))
    symbols = discover_cached_symbols(cache_root, timeframes=VOLATILITY_TIMEFRAMES)
    symbol = st.selectbox("Sembol", symbols) if symbols else ""
    profile = st.selectbox("Profil", ("Hassas", "Dengeli", "Seçici"), index=1)

st.title("Volatility · Direction Transition")
st.caption(
    "EARLY_UP / EARLY_DOWN yalnız erken yön değişimi kanıtıdır. Confirmed volatility, swing, "
    "structural break ve Fibonacci teyitleri kendi gecikmeli kurallarını korur."
)
st.caption("Bu ekran al/sat, trend dönüşü veya olasılık üretmez.")

if not symbol:
    st.info("1d/4h/2h cache bulunan bir sembol yok.")
    st.stop()

fingerprint = cache_fingerprint(cache_root, symbol=symbol, timeframes=VOLATILITY_TIMEFRAMES)
try:
    replay = _cached_replay(cache_root, symbol, fingerprint, profile)
except Exception as error:
    st.error(f"Volatility replay tamamlanamadı: {type(error).__name__}: {error}")
    st.stop()

st.subheader("Güncel MTF durum")
st.dataframe(volatility_latest_frame(replay), use_container_width=True, hide_index=True)

st.subheader("Erken yön → teyit gecikmesi")
lag = volatility_lag_frame(replay)
if lag.empty:
    st.info("Bu replay aralığında early transition kaydı yok.")
else:
    st.dataframe(lag, use_container_width=True, hide_index=True)

st.caption(
    "Lag satırları EARLY event'ten mevcut volatility candidate/confirmed state'e kadar bar farkını ölçer; "
    "Fib/structure teyidini erkene çekmez."
)
