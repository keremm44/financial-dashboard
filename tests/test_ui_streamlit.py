from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from _ui_test_data import make_ui_store


APP_PATH = Path(__file__).parents[1] / "src" / "financial_dashboard" / "ui" / "app.py"
STABIL_PAGE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "financial_dashboard"
    / "ui"
    / "pages"
    / "2_Stabil_Support.py"
)


def test_streamlit_app_smoke_renders_workspace_without_decision_actions(
    tmp_path, monkeypatch
) -> None:
    make_ui_store(tmp_path)
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(APP_PATH), default_timeout=120).run()

    assert not app.exception
    assert [title.value for title in app.title] == [
        "Financial Dashboard · Market Analysis Workspace"
    ]
    assert [metric.label for metric in app.metric] == [
        "MTF pressure",
        "Recovery evidence",
        "Up structure",
        "Down structure",
        "Location",
        "Observer state",
    ]
    assert app.metric[-1].value == "DOMAINS_REPORTED"
    assert {tab.label for tab in app.tabs} >= {
        "Genel görünüm",
        "Grafik",
        "Market Structure",
        "Zones & location",
        "Ham evidence",
        "Volume Participation",
        "Targeting",
        "Diagnostics",
    }

    overview_tab = next(tab for tab in app.tabs if tab.label == "Genel görünüm")
    assert len(overview_tab.dataframe) >= 2
    domain_health = overview_tab.dataframe[0].value
    assert tuple(domain_health["Domain"]) == (
        "Observer foundation",
        "Ham evidence",
        "Volume Participation",
        "Stabil Support Lifecycle",
        "Liquidity",
        "Order Block",
        "FVG / Engulfing",
        "Targeting",
    )
    assert set(domain_health["Status"]) == {"READY"}

    confluence_toggle = next(
        checkbox for checkbox in app.checkbox if checkbox.label == "Confluence"
    )
    conflict_toggle = next(
        checkbox for checkbox in app.checkbox if checkbox.label == "Opposing conflicts"
    )
    nearest_targets = next(
        checkbox for checkbox in app.checkbox if checkbox.label == "Nearest targets"
    )
    all_target_clusters = next(
        checkbox for checkbox in app.checkbox if checkbox.label == "All target clusters"
    )
    assert not confluence_toggle.value
    assert not conflict_toggle.value
    assert nearest_targets.value
    assert not all_target_clusters.value

    targeting_tab = next(tab for tab in app.tabs if tab.label == "Targeting")
    assert not targeting_tab.metric
    assert not targeting_tab.button
    assert targeting_tab.json

    ham_tab = next(tab for tab in app.tabs if tab.label == "Ham evidence")
    assert not ham_tab.metric
    assert not ham_tab.button
    assert len(ham_tab.dataframe) == 3
    assert len(ham_tab.dataframe[0].value) == 5
    assert len(ham_tab.dataframe[1].value) == 10
    assert len(ham_tab.dataframe[2].value) == 100
    assert "Source warnings" in ham_tab.dataframe[0].value.columns
    assert "Final confidence" not in ham_tab.dataframe[0].value.columns

    volume_tab = next(tab for tab in app.tabs if tab.label == "Volume Participation")
    assert not volume_tab.metric
    assert not volume_tab.button
    assert len(volume_tab.dataframe) == 8
    assert len(volume_tab.dataframe[0].value) == 5
    assert len(volume_tab.dataframe[1].value) == 40
    assert len(volume_tab.dataframe[5].value) == 100
    assert not volume_tab.dataframe[0].value["Raw volume summed"].any()
    assert not volume_tab.dataframe[1].value["Lower-TF confirms target"].any()
    assert volume_tab.dataframe[7].value.iloc[0]["Independent vote cap"] == 1
    assert "Action" not in volume_tab.dataframe[0].value.columns
    assert "Recommendation" not in volume_tab.dataframe[1].value.columns

    rendered_text = " ".join(warning.value for warning in app.warning).lower()
    assert "al/sat" in rendered_text
    assert "öneri" in rendered_text
    assert "take-profit" in rendered_text

    all_history = next(
        checkbox for checkbox in ham_tab.checkbox if checkbox.label == "Tüm geçmiş"
    )
    app = all_history.check().run(timeout=120)
    assert not app.exception
    ham_tab = next(tab for tab in app.tabs if tab.label == "Ham evidence")
    assert len(ham_tab.dataframe[2].value) == 160
    assert any("160 / 160" in caption.value for caption in ham_tab.caption)

    volume_tab = next(tab for tab in app.tabs if tab.label == "Volume Participation")
    all_volume_history = next(
        checkbox
        for checkbox in volume_tab.checkbox
        if checkbox.label == "Tüm Volume geçmişi"
    )
    app = all_volume_history.check().run(timeout=120)
    assert not app.exception
    volume_tab = next(tab for tab in app.tabs if tab.label == "Volume Participation")
    assert len(volume_tab.dataframe[5].value) == 160
    assert any("160 / 160" in caption.value for caption in volume_tab.caption)


def test_stabil_support_page_renders_typed_lifecycle_without_trading_authority(
    tmp_path, monkeypatch
) -> None:
    make_ui_store(tmp_path)
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(STABIL_PAGE_PATH), default_timeout=120).run()

    assert not app.exception
    assert [title.value for title in app.title] == [
        "Stabil · Günlük Yapısal Destek Yaşam Döngüsü"
    ]
    labels = [metric.label for metric in app.metric]
    assert labels == [
        "Durum",
        "Günlük destek",
        "Mesafe %",
        "Mesafe ATR",
        "Altında bar",
        "Progression",
    ]
    assert {tab.label for tab in app.tabs} == {
        "Lifecycle timeline",
        "Event ledger",
        "Breach / reclaim",
        "Test / hold",
        "Rebase",
        "Provenance",
    }
    rendered = " ".join(
        [caption.value for caption in app.caption]
        + [warning.value for warning in app.warning]
        + [info.value for info in app.info]
    ).lower()
    assert "ana trend dönüşü" in rendered
    assert "7–8" in rendered or "7-8" in rendered
    assert "al/sat" in rendered
    assert not app.button


def test_streamlit_app_explains_empty_cache_instead_of_failing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()

    assert not app.exception
    assert any("Parquet" in info.value for info in app.info)
