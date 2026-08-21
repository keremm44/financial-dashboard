from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from _ui_test_data import make_ui_store


APP_PATH = Path(__file__).parents[1] / "src" / "financial_dashboard" / "ui" / "app.py"


def test_streamlit_app_smoke_renders_observer_without_decision_actions(
    tmp_path, monkeypatch
) -> None:
    make_ui_store(tmp_path)
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(APP_PATH), default_timeout=120).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["Three-Domain Market Observer"]
    assert [metric.label for metric in app.metric] == [
        "MTF pressure",
        "Recovery evidence",
        "Up structure",
        "Down structure",
        "Location",
        "Combined state",
    ]
    assert app.metric[-1].value == "DOMAINS_REPORTED"
    assert {tab.label for tab in app.tabs} >= {
        "Genel görünüm",
        "Grafik",
        "Market Structure",
        "Zones & location",
        "Data quality",
    }
    rendered_text = " ".join(
        warning.value for warning in app.warning
    ).lower()
    assert "al/sat" in rendered_text
    assert "öneri" in rendered_text


def test_streamlit_app_explains_empty_cache_instead_of_failing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()

    assert not app.exception
    assert any("Parquet" in info.value for info in app.info)
