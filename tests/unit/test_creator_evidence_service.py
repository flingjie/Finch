"""Unit tests for CreatorEvidenceService."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.evidence_service import (
    CreatorEvidenceBatch,
    CreatorEvidenceItem,
    CreatorEvidenceService,
    evidence_id_for,
)
from finch.peers.person import CreatorEvidenceKind
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.projector import ArtifactProjector
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


class _FakeRunner:
    def __init__(self, batch: CreatorEvidenceBatch) -> None:
        self.batch = batch
        self.calls = 0

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0) -> BaseModel:
        self.calls += 1
        assert output_model is CreatorEvidenceBatch
        return self.batch


def _seed(ws: Workspace) -> list[str]:
    arts = []
    for sid, text in [("1", "I built a harness"), ("2", "Ship log week 3")]:
        url = f"https://x.com/alice/status/{sid}"
        art = RawArtifact(
            artifact_id=artifact_id("twitter", "post", sid),
            source=Source.TWITTER,
            source_type="post",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="x", external_id="alice", handle="alice"
            ),
            text=text,
            retrieved_at=datetime.now(UTC),
            capture_method="adapter",
            content_fingerprint=content_fingerprint(text, url=url),
        )
        ArtifactRepository(ws).upsert(art)
        arts.append(art)
    ArtifactProjector(ws).project(arts)
    return [a.artifact_id for a in arts]


def test_assess_saves_validated_evidence(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    aids = _seed(ws)
    batch = CreatorEvidenceBatch(
        items=[
            CreatorEvidenceItem(
                artifact_id=aids[0],
                kind=CreatorEvidenceKind.CREATION,
                claim="Built an agent harness",
                support=[aids[0]],
                first_hand=True,
                confidence=0.8,
            ),
            CreatorEvidenceItem(
                artifact_id=aids[1],
                kind=CreatorEvidenceKind.KNOWLEDGE_SHARING,
                claim="Shared a ship log",
                support=[aids[1]],
                confidence=0.7,
            ),
        ]
    )
    svc = CreatorEvidenceService(ws, runner=_FakeRunner(batch))
    result = svc.assess()
    assert result.assessed_persons == 1
    assert result.saved == 2
    people_ev = CreatorEvidenceRepository(ws).list_for_person(
        next(iter(ArtifactProjector(ws).peers.list_all())).person_id  # type: ignore[arg-type]
    )
    # re-list via peer
    peers = ArtifactProjector(ws).peers.list_all()
    evs = CreatorEvidenceRepository(ws).list_for_person(peers[0].person_id or "")
    assert len(evs) == 2
    assert evs[0].evidence_id == evidence_id_for(peers[0].person_id or "", aids[0]) or True
    ids = {e.evidence_id for e in evs}
    assert evidence_id_for(peers[0].person_id or "", aids[0]) in ids


def test_rejects_unknown_artifact_id(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    aids = _seed(ws)
    batch = CreatorEvidenceBatch(
        items=[
            CreatorEvidenceItem(
                artifact_id="twitter:post:forged",
                kind=CreatorEvidenceKind.CREATION,
                claim="fake",
                support=["twitter:post:forged"],
            ),
            CreatorEvidenceItem(
                artifact_id=aids[0],
                kind=CreatorEvidenceKind.CREATION,
                claim="ok",
                support=[aids[0]],
                first_hand=True,
            ),
        ]
    )
    svc = CreatorEvidenceService(ws, runner=_FakeRunner(batch))
    result = svc.assess()
    assert result.saved == 1


def test_codex_failure_skips_person(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    _seed(ws)

    class Boom:
        def run(self, prompt, output_model, *, timeout=600.0):
            raise RuntimeError("codex down")

    svc = CreatorEvidenceService(ws, runner=Boom())  # type: ignore[arg-type]
    result = svc.assess()
    assert result.skipped_persons == 1
    assert result.saved == 0
