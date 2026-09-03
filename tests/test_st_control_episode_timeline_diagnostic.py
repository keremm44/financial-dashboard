from __future__ import annotations

import importlib.util
from pathlib import Path


def test_st_control_episode_timeline_diagnostic_imports() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "st_control_episode_timeline_diagnostic.py"
    spec = importlib.util.spec_from_file_location("st_control_episode_timeline_diagnostic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
