"""Unit tests for CreatorEvidenceService (batch)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.evidence_service import (
    CreatorEvidenceBatchOutput,
    CreatorEvidenceBatchPerson,
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


def _seed(ws: Workspace, authors: list[str] | None = None) -> list[str]:
    authors = authors or ["alice"]
    aids: list[str] = []
    for author in authors:
        for sid, text in [("1", "I built a harness"), ("2", "Ship log week 3")]:
            source_id = f"{author}-{sid}"
            url = f"https://x.com/{author}/status/{sid}"
            art = RawArtifact(
                artifact_id=artifact_id("twitter", "post", source_id),
                source=Source.TWITTER,
                source_type="post",
                source_id=source_id,
                canonical_url=url,
                author_identity=AuthorIdentity(
                    platform="x", external_id=author, handle=author
                ),
                text=text,
                retrieved_at=datetime.now(UTC),
                capture_method="adapter",
                content_fingerprint=content_fingerprint(text, url=url),
            )
            ArtifactRepository(ws).upsert(art)
            aids.append(art.artifact_id)
    ArtifactProjector(ws).project(
        ArtifactRepository(ws).list_all()
    )
    return aids


class _FakeRunner:
    def __init__(self, output: CreatorEvidenceBatchOutput) -> None:
        self.output = output
        self.calls = 0

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        self.calls += 1
        assert output_model is CreatorEvidenceBatchOutput
        return self.output


class _CountingRunner:
    """Returns one valid creation item per person parsed from the batch prompt."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        self.calls += 1
        assert output_model is CreatorEvidenceBatchOutput
        json_str = prompt.split("## Persons\n", 1)[1].strip()
        persons_data = json.loads(json_str)
        persons = []
        for p in persons_data:
            aids = [a["artifact_id"] for a in p["artifacts"]]
            items = []
            if aids:
                items = [
                    CreatorEvidenceItem(
                        artifact_id=aids[0],
                        kind=CreatorEvidenceKind.CREATION,
                        claim="built",
                        support=[aids[0]],
                        first_hand=True,
                        confidence=0.8,
                    )
                ]
            persons.append(CreatorEvidenceBatchPerson(person_id=p["person_id"], items=items))
        return CreatorEvidenceBatchOutput(persons=persons)


def _person_id(ws: Workspace) -> str:
    return ArtifactProjector(ws).peers.list_all()[0].person_id or ""


def test_assess_saves_validated_evidence(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    aids = _seed(ws)
    pid = _person_id(ws)
    output = CreatorEvidenceBatchOutput(
        persons=[
            CreatorEvidenceBatchPerson(
                person_id=pid,
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
                ],
            )
        ]
    )
    svc = CreatorEvidenceService(ws, runner=_FakeRunner(output))
    result = svc.assess()
    assert result.assessed_persons == 1
    assert result.saved == 2
    evs = CreatorEvidenceRepository(ws).list_for_person(pid)
    ids = {e.evidence_id for e in evs}
    assert evidence_id_for(pid, aids[0]) in ids


def test_rejects_unknown_artifact_id(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    aids = _seed(ws)
    pid = _person_id(ws)
    output = CreatorEvidenceBatchOutput(
        persons=[
            CreatorEvidenceBatchPerson(
                person_id=pid,
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
                ],
            )
        ]
    )
    svc = CreatorEvidenceService(ws, runner=_FakeRunner(output))
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


def test_batch_calls_capped_by_batch_size(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    # 12 people × 2 artifacts → ceil(12/5) = 3 batch calls.
    _seed(ws, authors=[f"a{i:02d}" for i in range(12)])
    runner = _CountingRunner()
    svc = CreatorEvidenceService(ws, runner=runner)
    result = svc.assess()
    assert runner.calls == 3
    assert result.assessed_persons == 12
    assert result.saved == 12


def test_unknown_person_id_ignored(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    aids = _seed(ws)
    output = CreatorEvidenceBatchOutput(
        persons=[
            CreatorEvidenceBatchPerson(
                person_id="person_not_in_batch",
                items=[
                    CreatorEvidenceItem(
                        artifact_id=aids[0],
                        kind=CreatorEvidenceKind.CREATION,
                        claim="should be ignored",
                        support=[aids[0]],
                    )
                ],
            )
        ]
    )
    svc = CreatorEvidenceService(ws, runner=_FakeRunner(output))
    result = svc.assess()
    assert result.saved == 0
