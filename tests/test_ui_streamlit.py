from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from _ui_test_data import make_ui_store


APP_PATH = Path(__file__).parents[1] / "src" / "financial_dashboard" / "ui" / "app.py"


def test_streamlit_app_smoke_renders_cross_domain_without_action_layer(
    tmp_path, monkeypatch
) -> None:
    make_ui_store(tmp_path)
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(APP_PATH), default_timeout=120).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["Financial Dashboard"]

    metric_labels = [metric.label for metric in app.metric]
    for label in (
        "Main thesis",
        "Reaction",
        "Reversal",
        "Objective",
        "Conflict",
        "Gate",
        "Permission scope",
        "Permitted side",
        "Continuation",
    ):
        assert label in metric_labels

    tab_labels = {tab.label for tab in app.tabs}
    assert {
        "Context axes",
        "Qualified zones",
        "Permission",
        "Knowledge boundary",
        "Market",
        "Evidence",
        "Diagnostics",
        "Chart",
        "Market Structure",
        "Zones",
        "Targeting",
        "Volume",
        "HAM",
        "MTF foundation",
    }.issubset(tab_labels)

    diagnostics_tab = next(tab for tab in app.tabs if tab.label == "Diagnostics")
    assert diagnostics_tab.dataframe
    domain_health = diagnostics_tab.dataframe[0].value
    assert tuple(domain_health["Domain"])[-1] == "Cross-Domain Context"
    assert set(domain_health["Status"]) == {"READY"}

    context_tab = next(tab for tab in app.tabs if tab.label == "Context axes")
    context_frame = context_tab.dataframe[0].value
    assert "Structural thesis" in set(context_frame["Axis"])
    assert "Reaction" in set(context_frame["Axis"])
    assert "Reversal" in set(context_frame["Axis"])

    permission_tab = next(tab for tab in app.tabs if tab.label == "Permission")
    permission_frame = permission_tab.dataframe[0].value
    assert set(permission_frame["Field"]) >= {"Scope", "Permitted side", "Gate"}
    assert "BUY" not in " ".join(permission_frame["Value"].astype(str)).upper()
    assert "SELL" not in " ".join(permission_frame["Value"].astype(str)).upper()

    knowledge_tab = next(tab for tab in app.tabs if tab.label == "Knowledge boundary")
    knowledge_frame = knowledge_tab.dataframe[0].value
    assert set(knowledge_frame["Boundary"]) >= {
        "as_of",
        "eligible facts",
        "future facts excluded",
        "unconfirmed facts",
    }

    confluence = next(item for item in app.checkbox if item.label == "Confluence")
    conflicts = next(item for item in app.checkbox if item.label == "Conflicts")
    targets = next(item for item in app.checkbox if item.label == "Targets")
    assert not confluence.value
    assert not conflicts.value
    assert targets.value

    rendered = " ".join(
        [caption.value for caption in app.caption]
        + [markdown.value for markdown in app.markdown]
    ).lower()
    assert "buy/sell" in rendered
    assert "action layer" in rendered
    assert "position sizing" in rendered


def test_removed_debug_pages_are_not_part_of_default_streamlit_navigation() -> None:
    pages = APP_PATH.parent / "pages"
    assert not (pages / "1_Target_Replay.py").exists()
    assert not (pages / "2_Stabil_Support.py").exists()
    assert not (pages / "3_Volatility.py").exists()


def test_streamlit_app_explains_empty_cache_instead_of_failing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FINANCIAL_DASHBOARD_CACHE", str(tmp_path))

    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()

    assert not app.exception
    assert any("cache" in info.value.lower() for info in app.info)
