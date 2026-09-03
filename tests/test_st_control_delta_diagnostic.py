from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "st_control_delta_diagnostic.py"


def test_st_control_delta_diagnostic_help_imports_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ST control change-point audit" in result.stdout
    assert "--json-out" in result.stdout
    assert "--max-print-changes" in result.stdout
