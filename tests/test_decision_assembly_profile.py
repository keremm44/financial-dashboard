from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_decision_assembly_profiler_exposes_stage_and_batch_contract():
    source = Path("scripts/decision_assembly_profile.py").read_text(encoding="utf-8")
    for stage in (
        "views",
        "evidence",
        "dedup",
        "targeting",
        "semantic_targeting",
        "cross_domain",
        "decision_input",
    ):
        assert f'"{stage}"' in source
    assert "last_assembly_breakdown" in source
    assert "CACHE_WRITE_SECONDS" in source
    assert "SNAPSHOTS_PER_SECOND" in source
    assert 'parser.add_argument("symbols", nargs="+")' in source
    assert "DECISION_ASSEMBLY_PROFILE_BATCH_OK" in source


def test_decision_assembly_profiler_restores_persistence_method():
    source = Path("scripts/decision_assembly_profile.py").read_text(encoding="utf-8")
    assert "original = PersistentHistoricalDecisionInputReplayRunner._save_decision_checkpoints" in source
    assert "PersistentHistoricalDecisionInputReplayRunner._save_decision_checkpoints = wrapped" in source
    assert "PersistentHistoricalDecisionInputReplayRunner._save_decision_checkpoints = original" in source


def test_decision_assembly_profiler_help_starts_cleanly():
    completed = subprocess.run(
        [sys.executable, "scripts/decision_assembly_profile.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Profile post-domain DecisionInput assembly" in completed.stdout
