"""问题发现展示记录单测：幂等 + latest。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.problem_discovery.presentation import ProblemPresentationRepository
from finch.storage.workspace import Workspace


def _t(hour: int) -> datetime:
    return datetime(2026, 10, 30, hour, tzinfo=UTC)


def test_record_shown_idempotent_and_latest(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ProblemPresentationRepository(ws)

    r1 = repo.record_shown(["prob_a", "prob_b"], batch="run_1", now=_t(1))
    r2 = repo.record_shown(["prob_a", "prob_b"], batch="run_1", now=_t(2))
    assert r1.id == r2.id  # 同批同序幂等

    r3 = repo.record_shown(["prob_c"], batch="run_2", now=_t(3))
    assert repo.latest().id == r3.id
    assert repo.latest().problem_ids == ["prob_c"]


def test_latest_none_when_empty(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    assert ProblemPresentationRepository(ws).latest() is None
