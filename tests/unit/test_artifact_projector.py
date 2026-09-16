"""Unit tests for ArtifactProjector."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from finch.peers.person_service import PersonRepository
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.projector import ArtifactProjector
from finch.storage.repositories import PeerRepository
from finch.storage.workspace import Workspace


def _art(
    *,
    sid: str,
    author: str,
    text: str = "hello",
    platform: str = "x",
    source: Source = Source.TWITTER,
    source_type: str = "post",
) -> RawArtifact:
    url = f"https://x.com/{author}/status/{sid}"
    return RawArtifact(
        artifact_id=artifact_id(source.value, source_type, sid),
        source=source,
        source_type=source_type,
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(
            platform=platform, external_id=author, handle=author
        ),
        title=None,
        text=text,
        published_at=None,
        metrics={},
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


def test_project_groups_peers_and_backfills_person_id(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    projector = ArtifactProjector(ws)
    arts = [
        _art(sid="1", author="alice", text="one"),
        _art(sid="2", author="alice", text="two"),
        _art(sid="3", author="bob", text="three"),
    ]
    result = projector.project(arts)
    assert result.projected_peers == 2
    assert result.created_peers == 2

    peers = PeerRepository(ws).list_all()
    assert len(peers) == 2
    alice = next(p for p in peers if p.display_name == "alice")
    bob = next(p for p in peers if p.display_name == "bob")
    assert alice.person_id
    assert bob.person_id
    assert alice.person_id != bob.person_id
    assert len(alice.source_refs) == 2
    assert all(r.startswith("twitter:post:") for r in alice.source_refs)

    people = PersonRepository(ws).list_all()
    assert len(people) == 2
    assert all(p.last_evidence_at is not None for p in people)


def test_project_idempotent(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    projector = ArtifactProjector(ws)
    arts = [
        _art(sid="1", author="alice", text="one"),
        _art(sid="2", author="alice", text="two"),
    ]
    r1 = projector.project(arts)
    r2 = projector.project(arts)
    assert r1.created_peers == 2 or r1.created_peers == 1
    assert r2.created_peers == 0
    assert r2.updated_peers == 1
    peers = PeerRepository(ws).list_all()
    assert len(peers) == 1
    assert len(peers[0].source_refs) == 2


def test_skips_missing_author(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    art = _art(sid="1", author="alice")
    art = art.model_copy(
        update={"author_identity": AuthorIdentity(platform="x", external_id="", handle="")}
    )
    result = ArtifactProjector(ws).project([art])
    assert result.skipped == 1
    assert result.projected_peers == 0
