"""Tests for opportunity funnel metrics and artifact status update."""

from datetime import UTC, datetime

from finch.engagement.models import (
    DiscoverySnapshot,
    FeedbackSnapshot,
    InteractionRecord,
    VerificationStatus,
)
from finch.opportunities.funnel import compute_opportunity_funnel, render_opportunity_funnel
from finch.opportunities.models import (
    Artifact,
    ArtifactKind,
    ContributionForm,
    ExecutionStatus,
    MaterialOrigin,
    Proposal,
)
from finch.opportunities.repository import ArtifactRepository, OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.storage.workspace import Workspace


def test_update_artifact_records_execution_status(tmp_path):
    ws = Workspace(tmp_path)
    service = OpportunityService(
        OpportunityRepository(ws), artifacts=ArtifactRepository(ws)
    )
    service.create(
        opportunity_id="opp_1",
        topic="t",
        proposal=Proposal(
            contribution="c",
            form=ContributionForm.DEMO,
            expected_output="o",
            scope="s",
        ),
    )
    service.select("opp_1")
    service.add_artifact(
        "opp_1",
        Artifact(
            id="art_1",
            kind=ArtifactKind.DEMO,
            material_origin=MaterialOrigin.SYNTHETIC,
            execution_status=ExecutionStatus.NOT_RUN,
        ),
    )
    art = service.update_artifact(
        "opp_1",
        "art_1",
        execution_status=ExecutionStatus.RAN_OK,
        material_origin=MaterialOrigin.REAL,
        author_note="用合成输入跑通时间依赖",
    )
    assert art.execution_status == ExecutionStatus.RAN_OK
    assert art.material_origin == MaterialOrigin.REAL
    assert art.author_note.startswith("用合成")
    events = [e.event_type for e in OpportunityRepository(ws).list_events("opp_1")]
    assert "artifact_updated" in events


def test_funnel_counts_preferred_selected_ready_and_meaningful(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = OpportunityRepository(ws)
    service = OpportunityService(repo, artifacts=ArtifactRepository(ws))
    service.create(
        opportunity_id="opp_a",
        topic="t",
        proposal=Proposal(
            contribution="c",
            form=ContributionForm.METHOD_CARD,
            expected_output="o",
            scope="s",
        ),
    )
    service.select("opp_a")
    service.mark_ready("opp_a")

    snap = DiscoverySnapshot(
        id="snap_1",
        created_at=datetime.now(UTC),
        context_fingerprint="ctx",
        preferred_opportunity_id="opp_a",
    )
    interactions = [
        InteractionRecord(
            id="out_1",
            opportunity_id="opp_a",
            peer_id="peer_1",
            platform="x",
            source_url="https://x.com/u/1",
            body="发出去了",
            occurred_at=datetime.now(UTC),
            direction="outbound",
            verification_status=VerificationStatus.USER_ATTESTED,
        ),
        InteractionRecord(
            id="in_1",
            opportunity_id="opp_a",
            peer_id="peer_1",
            platform="x",
            source_url="https://x.com/u/2",
            body="补充了边界",
            occurred_at=datetime.now(UTC),
            direction="inbound",
            outcome="meaningful",
            verification_status=VerificationStatus.USER_ATTESTED,
        ),
        InteractionRecord(
            id="in_2",
            opportunity_id="opp_a",
            peer_id="peer_1",
            platform="x",
            source_url="https://x.com/u/3",
            body="又追问了一步",
            occurred_at=datetime.now(UTC),
            direction="inbound",
            outcome="meaningful",
            verification_status=VerificationStatus.USER_ATTESTED,
        ),
    ]
    funnel = compute_opportunity_funnel(
        snapshots=[snap],
        opportunities=repo.list_all(),
        opportunity_repo=repo,
        interactions=interactions,
        feedbacks=[
            FeedbackSnapshot(
                id="fs_1",
                interaction_id="in_1",
                meaningful=True,
                captured_at=datetime.now(UTC),
            )
        ],
    )
    assert funnel.preferred_presented == 1
    assert funnel.selected == 1
    assert funnel.ready == 1
    assert funnel.outbound_participated == 1
    assert funnel.meaningful_replies == 2
    assert funnel.repeat_meaningful_peers == 1
    text = render_opportunity_funnel(funnel)
    assert "呈现首选" in text
    assert "实质回应" in text


def test_funnel_prefers_presentation_records_over_snapshot(tmp_path):
    from finch.engagement.models import PresentationRecord

    snap = DiscoverySnapshot(
        id="snap_1",
        created_at=datetime.now(UTC),
        context_fingerprint="ctx",
        preferred_opportunity_id="opp_never_shown",
    )
    presentations = [
        PresentationRecord(
            id="snap_1:opp_shown",
            snapshot_id="snap_1",
            opportunity_id="opp_shown",
            presented_at=datetime.now(UTC),
        )
    ]
    funnel = compute_opportunity_funnel(
        snapshots=[snap],
        opportunities=[],
        opportunity_repo=OpportunityRepository(Workspace(tmp_path)),
        interactions=[],
        feedbacks=[],
        presentations=presentations,
    )
    assert funnel.preferred_presented == 1
    assert funnel.preferred_ids == ["opp_shown"]
