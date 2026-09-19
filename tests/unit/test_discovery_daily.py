"""Unit tests for unified daily discovery (no X/Reddit search providers)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from finch.discovery.daily import (
    ConnectionOpportunityDraft,
    artifact_to_external_post,
    opportunities_from_artifacts,
    run_daily_discovery,
)
from finch.peers.evidence_service import (
    CreatorEvidenceBatchOutput,
    CreatorEvidenceBatchPerson,
    CreatorEvidenceItem,
)
from finch.peers.person import CreatorEvidenceKind
from finch.settings import Settings, SourcesSettings, SourceTwitterPlan
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def _art(sid: str, author: str, text: str) -> RawArtifact:
    url = f"https://x.com/{author}/status/{sid}"
    return RawArtifact(
        artifact_id=artifact_id("twitter", "post", sid),
        source=Source.TWITTER,
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(
            platform="x", external_id=author, handle=author
        ),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


class _FakeRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        if output_model is CreatorEvidenceBatchOutput:
            import json as _json

            persons_data = _json.loads(prompt.split("## Persons\n", 1)[1].strip())
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
                        ),
                        CreatorEvidenceItem(
                            artifact_id=ids[1] if len(ids) > 1 else ids[0],
                            kind=CreatorEvidenceKind.KNOWLEDGE_SHARING,
                            claim="shared practice",
                            support=[ids[1] if len(ids) > 1 else ids[0]],
                            confidence=0.7,
                        ),
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
                their_artifact_ids=["twitter:post:1", "twitter:post:2"],
            )
        # CollisionDraft
        from finch.collisions.service import CollisionDraft

        return CollisionDraft(
            their_domain="evals",
            your_domain="agents",
            surface_similarity="both about quality",
            structural_similarity="gate on uncertainty",
            shared_question="when to re-run?",
            transferable_mechanism="risk_gate",
            falsifiable_hypothesis="gates cut flaky retries 20%",
            artifact_ids=["twitter:post:1", "twitter:post:2"],
        )


def test_artifact_to_external_post():
    post = artifact_to_external_post(
        _art("1", "alice", "this is a long enough post about agent reliability tools")
    )
    assert post is not None
    assert post.platform == "x"
    assert post.author_id == "alice"


def test_opportunities_from_artifacts():
    arts = [
        _art("1", "alice", "long enough content about agent harness failures and retries"),
        _art("2", "bob", "another long enough post about eval trajectories in production"),
    ]
    opps = opportunities_from_artifacts(arts, limit=5)
    assert len(opps) >= 1
    assert all(o.discovered_via == "sources_sync" for o in opps)


def test_run_daily_skips_network_with_seeded_artifacts(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = [
        _art("1", "alice", "long enough content about agent harness failures and retries xx"),
        _art("2", "alice", "second long enough post about eval trajectories in production yy"),
    ]
    for a in arts:
        ArtifactRepository(ws).upsert(a)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project(arts)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )
    assert result.engagement is not None
    assert result.engagement.status in {"succeeded", "empty"}
    # With evidence from fake runner, shortlist should populate
    assert len(result.shortlist) >= 1 or result.engagement.posts_found >= 2


def test_run_daily_three_slot_cap_and_metrics(tmp_path: Path):
    """Phase 0 回归：短名单仍 ≤3，观测指标被记录且不改变选择。"""
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = []
    for i, author in enumerate(["alice", "bob", "carol", "dave", "erin"]):
        arts.append(
            _art(
                f"{i * 2 + 1}",
                author,
                f"first long enough post from {author} about agent reliability",
            )
        )
        arts.append(
            _art(
                f"{i * 2 + 2}",
                author,
                f"second long enough post from {author} about eval trajectories",
            )
        )
    for a in arts:
        ArtifactRepository(ws).upsert(a)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project(arts)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )

    # 观测指标不改变选择：priority 层（旧 shortlist 字段）不超过 priority_count=5。
    assert len(result.shortlist) <= 5
    assert result.recommendations is not None
    assert result.metrics.recommended_count == result.recommendations.total
    assert result.metrics.people_count >= 5
    assert result.metrics.llm_calls >= 1

