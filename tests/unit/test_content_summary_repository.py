"""ContentSummary Workspace persistence."""

from finch.content_summary.models import ContentSummary, EvidencePoint
from finch.content_summary.repository import ContentSummaryRepository
from finch.storage.workspace import Workspace


def _summary(**kw) -> ContentSummary:
    base = dict(
        id="summary_abc",
        source_type="text",
        source_refs=[],
        content_hash="hash1",
        main_point="一句话主旨",
        key_points=["要点一", "要点二"],
        evidence=[EvidencePoint(source="作者", content="依据")],
        conditions=["仅用于内部测试"],
        coverage_gaps=[],
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


def test_upsert_merges_source_refs(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    repo.upsert(_summary(source_refs=["https://a/1"]))
    repo.upsert(_summary(source_refs=["https://b/2"]))
    got = repo.get("summary_abc")
    assert got is not None
    assert got.source_refs == ["https://a/1", "https://b/2"]


def test_upsert_returns_merged_summary(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    repo.upsert(_summary(source_refs=["https://a/1"]))
    merged = repo.upsert(_summary(source_refs=["https://b/2"]))
    assert merged.source_refs == ["https://a/1", "https://b/2"]


def test_list_ids(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    repo.upsert(_summary())
    repo.upsert(_summary(id="summary_def"))
    assert set(repo.list()) == {"summary_abc", "summary_def"}


def test_get_missing_returns_none(tmp_path):
    repo = ContentSummaryRepository(Workspace(tmp_path))
    assert repo.get("missing") is None
