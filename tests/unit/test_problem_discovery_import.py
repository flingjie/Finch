"""问题发现导入单测：GitHub repo → artifact_id；URL/文本 → discussion_id。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.problem_discovery.ids import discussion_id_for
from finch.problem_discovery.importing import import_discussion
from finch.problem_discovery.repository import DiscussionRepository
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def _art(artifact_id: str, text: str) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        source=Source.GITHUB,
        source_type="issue",
        source_id="1",
        canonical_url="https://github.com/a/b/issues/1",
        author_identity=AuthorIdentity(platform="github", handle="u"),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="gh",
    )


def test_import_repo_returns_artifact_ids(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()

    class _Conn:
        def fetch_repo_feedback(self, owner_repo: str) -> list[RawArtifact]:
            return [_art("github:issue:1", "crashes on import")]

    refs = import_discussion(ws, repo="a/b", connector=_Conn())
    assert refs == ["github:issue:1"]
    assert ArtifactRepository(ws).get("github:issue:1") is not None


def test_import_url_returns_discussion_id(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    refs = import_discussion(ws, url="https://x.com/a/1", text="body")
    assert refs == [discussion_id_for("https://x.com/a/1", "body")]
    d = DiscussionRepository(ws).get(refs[0])
    assert d.kind == "url"
    assert d.url == "https://x.com/a/1"
