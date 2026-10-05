"""ContentSummary Workspace persistence."""

from finch.content_summary.models import ContentSummary, EvidencePoint
from finch.content_summary.repository import ContentSummaryRepository
from finch.storage.workspace import Workspace


def _summary(**kw) -> ContentSummary:
    base = dict(
        id="summary_abc",
        source_type="text",
        source_ref=None,
        content_hash="hash1",
        main_point="一句话主旨",
        key_points=["要点一", "要点二"],
        evidence=[EvidencePoint(source="作者", content="依据")],
        conditions=["原文未说明适用范围"],
    )
    base.update(kw)
    return ContentSummary(**base)


def test_upsert_and_get(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    repo.upsert(_summary())
    got = repo.get("summary_abc")
    assert got is not None
    assert got.content_hash == "hash1"
    assert got.main_point == "一句话主旨"
    assert got.evidence[0].source == "作者"


def test_upsert_overwrites_same_id(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    repo.upsert(_summary(content_hash="h1"))
    repo.upsert(_summary(content_hash="h2"))
    assert repo.get("summary_abc").content_hash == "h2"


def test_get_missing_returns_none(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    assert repo.get("missing") is None
