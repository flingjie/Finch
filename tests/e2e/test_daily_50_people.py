"""E2E (offline) for the daily 50-person pipeline: fixtures only, no live credentials.

Seeds a deterministic workspace of 60 people × 2 artifacts across 6 platforms, runs the
full pipeline with a fake batch runner, and asserts the 5/15/30 tiering, per-platform cap,
distinct identities, and replay stability.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from finch.discovery.candidate_pool import build_pool
from finch.discovery.daily import (
    ConnectionOpportunityDraft,
    build_shortlist_candidates,
    run_daily_discovery,
)
from finch.peers.evidence_service import (
    CreatorEvidenceBatchOutput,
    CreatorEvidenceBatchPerson,
    CreatorEvidenceItem,
)
from finch.peers.person import CreatorEvidenceKind
from finch.peers.recommendations import select_daily_recommendations
from finch.settings import Settings
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.projector import ArtifactProjector
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace

_PLATFORM_TO_SOURCE = {
    "x": (Source.TWITTER, "post"),
    "reddit": (Source.REDDIT, "post"),
    "github": (Source.GITHUB, "commit"),
    "v2ex": (Source.V2EX, "topic"),
    "weixin": (Source.WEIXIN, "article"),
    "xiaohongshu": (Source.XIAOHONGSHU, "note"),
}
_PLATFORMS = list(_PLATFORM_TO_SOURCE)


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        self.calls += 1
        if output_model is CreatorEvidenceBatchOutput:
            persons_data = json.loads(prompt.split("## Persons\n", 1)[1].strip())
            persons = []
            for p in persons_data:
                ids = [a["artifact_id"] for a in p["artifacts"]]
                items = []
                if ids:
                    items = [
                        CreatorEvidenceItem(
                            artifact_id=ids[0],
                            kind=CreatorEvidenceKind.CREATION,
                            claim="built something",
                            support=[ids[0]],
                            first_hand=True,
                            confidence=0.8,
                        )
                    ]
                persons.append(
                    CreatorEvidenceBatchPerson(person_id=p["person_id"], items=items)
                )
            return CreatorEvidenceBatchOutput(persons=persons)
        if output_model is ConnectionOpportunityDraft:
            from finch.connections.service import ConnectionDecision

            return ConnectionOpportunityDraft(
                their_problem="flaky evals",
                user_contribution="we use trajectory diffs",
                why_now="recent post",
                decision=ConnectionDecision.CONNECT,
                user_evidence_refs=["practice:1"],
                their_artifact_ids=["twitter:post:x"],
            )
        raise AssertionError(f"unexpected output_model: {output_model}")


def _seed(ws: Workspace, n_people: int = 60) -> list[RawArtifact]:
    arts: list[RawArtifact] = []
    for i in range(n_people):
        platform = _PLATFORMS[i % len(_PLATFORMS)]
        source, source_type = _PLATFORM_TO_SOURCE[platform]
        author = f"author_{i:03d}"
        for j in range(2):
            sid = f"{i}-{j}"
            text = f"long enough original content from {author} about agent reliability #{sid}"
            url = f"https://{platform}.example/{author}/{sid}"
            art = RawArtifact(
                artifact_id=artifact_id(source.value, source_type, sid),
                source=source,
                source_type=source_type,
                source_id=sid,
                canonical_url=url,
                author_identity=AuthorIdentity(
                    platform=platform, external_id=author, handle=author
                ),
                text=text,
                retrieved_at=datetime.now(UTC),
                capture_method="adapter",
                content_fingerprint=content_fingerprint(text, url=url),
            )
            ArtifactRepository(ws).upsert(art)
            arts.append(art)
    ArtifactProjector(ws).project(arts)
    return arts


def _settings(tmp_path: Path) -> Settings:
    s = Settings(paths={"var_dir": tmp_path})  # type: ignore[arg-type]
    s.paths.var_dir = tmp_path
    return s


def test_daily_50_people_e2e(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    _seed(ws, n_people=60)

    settings = _settings(tmp_path)

    def fake_gateway(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_gateway),
        skip_sync=True,
        generate_collision=False,
    )

    recs = result.recommendations
    assert recs is not None
    assert recs.total == 50
    assert len(recs.priority) == 5
    assert len(recs.summary) == 15
    assert len(recs.browse) == 30
    ids = [r.person_id for r in recs.all]
    assert len(set(ids)) == 50

    from collections import Counter

    platform_counts = Counter(r.candidate.platform for r in recs.all)
    assert all(v <= 20 for v in platform_counts.values())


def test_replay_stable(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    _seed(ws, n_people=30)
    settings = _settings(tmp_path)

    candidates, _ = build_shortlist_candidates(ws)
    pool = build_pool(candidates, settings=settings, max_size=100)
    now = datetime.now(UTC)

    first = select_daily_recommendations(pool.candidates, settings=settings, now=now)
    second = select_daily_recommendations(pool.candidates, settings=settings, now=now)

    assert [r.person_id for r in first.all] == [r.person_id for r in second.all]
    assert [(r.tier, r.rank) for r in first.all] == [(r.tier, r.rank) for r in second.all]
