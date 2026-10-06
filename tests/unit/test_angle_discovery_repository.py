"""AngleBrief Workspace 持久化。"""

from finch.angle_discovery.models import AngleBrief, AngleCard, SourceSummary
from finch.angle_discovery.repository import AngleBriefRepository
from finch.storage.workspace import Workspace


def _brief(**kw) -> AngleBrief:
    base = dict(
        id="angle_abc",
        source_type="text",
        content_hash="hash1",
        source_summary=SourceSummary(main_point="一句话主旨"),
        angles=[AngleCard(title="t", thesis="中心", incremental_value="增量")],
    )
    base.update(kw)
    return AngleBrief(**base)


def test_upsert_and_get(tmp_path):
    repo = AngleBriefRepository(Workspace(tmp_path))
    repo.upsert(_brief())
    got = repo.get("angle_abc")
    assert got is not None
    assert got.content_hash == "hash1"
    assert got.source_summary.main_point == "一句话主旨"


def test_upsert_overwrites_same_id(tmp_path):
    repo = AngleBriefRepository(Workspace(tmp_path))
    repo.upsert(_brief(content_hash="h1"))
    repo.upsert(_brief(content_hash="h2"))
    assert repo.get("angle_abc").content_hash == "h2"


def test_list_ids(tmp_path):
    repo = AngleBriefRepository(Workspace(tmp_path))
    repo.upsert(_brief())
    repo.upsert(_brief(id="angle_def"))
    assert set(repo.list()) == {"angle_abc", "angle_def"}


def test_get_missing_returns_none(tmp_path):
    repo = AngleBriefRepository(Workspace(tmp_path))
    assert repo.get("missing") is None
