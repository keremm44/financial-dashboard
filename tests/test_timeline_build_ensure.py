from __future__ import annotations

from types import SimpleNamespace

import pytest

import financial_dashboard.decision.timeline_build as timeline_build
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss


class _StubLoad:
    cache_status = "HIT_EXACT_CACHE_ONLY"

    def __init__(self, snapshots: int = 5) -> None:
        self.replay = SimpleNamespace(sshots=None)
        self.replay = SimpleNamespace(snapshots=tuple(range(snapshots)))


def _miss(*args, **kwargs):
    raise DecisionTimelineCacheMiss("stub miss")


def test_ensure_returns_existing_cache_without_building(monkeypatch):
    stub = _StubLoad()
    monkeypatch.setattr(timeline_build, "load_frozen_decision_timeline", lambda *a, **k: stub)
    builds: list[tuple] = []

    def fail_build(*args, **kwargs):
        builds.append(args)
        raise AssertionError("build must not run on cache hit")

    monkeypatch.setattr(timeline_build, "build_timeline_once", fail_build)

    report = timeline_build.ensure_frozen_decision_timeline(
        None, "ASELS", config=None, progress=lambda message: None
    )
    assert report.built is False
    assert report.load is stub
    assert report.build_seconds == 0.0
    assert builds == []


def test_ensure_builds_then_verifies_on_miss(monkeypatch):
    stub = _StubLoad(snapshots=7)
    calls = {"load": 0, "build": 0}
    messages: list[str] = []

    def flaky_load(*args, **kwargs):
        calls["load"] += 1
        if calls["load"] == 1:
            raise DecisionTimelineCacheMiss("stub miss")
        return stub

    def fake_build(store, *, symbol, config, run_with=None, progress=timeline_build._default_progress):
        calls["build"] += 1
        assert symbol == "ASELS"
        return SimpleNamespace(last_status="ok"), SimpleNamespace(snapshots=stub.replay.snapshots)

    monkeypatch.setattr(timeline_build, "load_frozen_decision_timeline", flaky_load)
    monkeypatch.setattr(timeline_build, "build_timeline_once", fake_build)

    report = timeline_build.ensure_frozen_decision_timeline(
        None, "asels", config=None, progress=messages.append
    )
    assert report.built is True
    assert report.load is stub
    assert calls == {"load": 2, "build": 1}
    assert report.snapshots_built == 7
    assert any("MISS_BUILDING" in message for message in messages)
    assert any("BUILD_SECONDS" in message for message in messages)
    assert any("VERIFY_STATUS" in message for message in messages)


def test_ensure_raises_when_verification_still_misses(monkeypatch):
    monkeypatch.setattr(timeline_build, "load_frozen_decision_timeline", _miss)

    def fake_build(*args, **kwargs):
        return SimpleNamespace(), SimpleNamespace(snapshots=())

    monkeypatch.setattr(timeline_build, "build_timeline_once", fake_build)

    with pytest.raises(RuntimeError, match="exact cache verification failed"):
        timeline_build.ensure_frozen_decision_timeline(
            None, "ASELS", config=None, progress=lambda message: None
        )


def test_build_timeline_once_recovers_from_checkpoint_alignment_error(monkeypatch):
    monkeypatch.setattr(timeline_build, "decision_prefix_exists", lambda *a, **k: True)

    class _FlakyReplayRunner:
        """First created runner fails on alignment; later (recovery) runners succeed."""

        fail_first_replay = False

        def __init__(self, store):
            self.should_fail = _FlakyReplayRunner.fail_first_replay
            _FlakyReplayRunner.fail_first_replay = False

        def replay(self, symbol, config=None):
            if self.should_fail:
                raise RuntimeError(
                    "native checkpoint delta is not aligned with the persisted decision prefix"
                )
            return SimpleNamespace(snapshots=(1, 2, 3))

    runners: list[_FlakyReplayRunner] = []
    _FlakyReplayRunner.fail_first_replay = True

    def fake_runner(store):
        runner = _FlakyReplayRunner(store)
        runners.append(runner)
        return runner

    monkeypatch.setattr(timeline_build, "HistoricalDecisionInputReplayRunner", fake_runner)

    messages: list[str] = []
    runner, built = timeline_build.build_timeline_once(
        None, symbol="ASELS", config=None, progress=messages.append
    )
    assert built.snapshots == (1, 2, 3)
    assert runner is runners[1]  # cold recovery used a fresh runner
    assert any("CANONICAL_INCREMENTAL_OR_EXACT" in message for message in messages)
    assert any("CANONICAL_COLD_DOMAIN_ONCE" in message for message in messages)


def test_cold_domain_checkpoint_scope_restores_versions(monkeypatch):
    import financial_dashboard.decision.history_incremental as incremental_history
    import financial_dashboard.decision.history_native_timeline as native_history

    native_before = native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION
    supporting_before = incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION

    with timeline_build.cold_domain_checkpoint_scope():
        assert native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION != native_before
        assert incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION != supporting_before

    assert native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION == native_before
    assert incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION == supporting_before
