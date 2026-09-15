"""Build-in-Public P1 loop: note + URL → outline | 暂不回复 → record → follow-up.

Fake runner / temp workspace only — no network, no live LLM.
"""

from datetime import UTC, datetime

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import RecommendedFormat
from finch.conversations.service import ConversationService
from finch.engagement.contribution import assess_contribution, assess_job_contribution
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
)
from finch.engagement.proposals import (
    ProposalBatchOutput,
    ProposalItem,
    blocks_fabricated_experience,
    generate_proposals,
    ready_gate_blocks,
)
from finch.engagement.scoring import ScoredPost
from finch.ideas.fragment_service import FragmentService, IdeaDraftOutput
from finch.ideas.models import IdeaBoundaries
from finch.ideas.service import IdeaService
from finch.settings import EngagementSettings
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    InteractionRecordRepository,
    InteractionRepository,
)
from finch.storage.workspace import Workspace

_NOW = datetime(2026, 9, 15, tzinfo=UTC)


class FakeRunner:
    def __init__(self, ret):
        self.ret = ret
        self.calls = 0
        self.last_prompt = ""

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        if isinstance(self.ret, list):
            # Cycle through returns for multi-step flows.
            idx = min(self.calls - 1, len(self.ret) - 1)
            return self.ret[idx]
        return self.ret


def _post(**overrides) -> ExternalPost:
    data = dict(
        id="post_rel",
        platform="x",
        url="https://x.com/alice/status/1",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability in production with replays?",
        published_at=_NOW,
        matched_topics=["agent reliability", "replays"],
    )
    data.update(overrides)
    return ExternalPost(**data)


def _score(**overrides) -> ConversationScore:
    data = dict(
        relevance=0.9,
        novelty=0.9,
        discussability=0.9,
        practical_evidence=0.8,
        relationship_value=0.5,
        total=0.9,
        reasons=["on topic"],
    )
    data.update(overrides)
    return ConversationScore(**data)


def _idea_out(**overrides) -> IdeaDraftOutput:
    data = dict(
        core_point="failure replays make agent bugs reproducible",
        observation="recorded a failure replay and diffed two runs",
        reader_problem="hard to rerun agent failures",
        why_worth_saying="replays beat log archaeology",
        intent="stance",
        author_position=AuthorPosition(
            claim="use replays", decision="record failures", tradeoff="storage"
        ),
        boundaries=IdeaBoundaries(known=["replay captured"], inferred=[], unknown=[]),
        recommended_format=RecommendedFormat.REPLY,
        facts=["recorded failure replay for agent timeout"],
        interpretation="replays are the right tool for reliability debugging",
        evidence_status="observed",
        source_kind="note",
        limitations="only one timeout case",
    )
    data.update(overrides)
    return IdeaDraftOutput(**data)


def test_from_text_fills_facts_and_evidence_status_no_commit(tmp_path):
    """Case 3: no commit — note still becomes a ContentJob with facts split."""
    ws = Workspace(tmp_path / "var")
    ws.ensure()
    idea = FragmentService(FakeRunner(_idea_out())).from_text(
        "I recorded a failure replay for agent timeout and it helped."
    )
    assert idea.source_kind == "note"
    assert idea.evidence_status == "observed"
    assert idea.facts
    job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
    assert job.facts == idea.facts
    assert job.evidence_status == "observed"
    assert job.source_card_ids == []


def test_external_article_not_observed():
    """Case 2: external article → externally_reported, not observed."""
    out = _idea_out(
        core_point="paper claims 40% latency cut",
        observation="blog describes an A/B test",
        facts=["blog reported 40% latency improvement"],
        interpretation="interesting but I did not run it",
        evidence_status="externally_reported",
        source_kind="post",
    )
    idea = FragmentService(FakeRunner(out)).from_text(
        "This article says they cut latency 40% with caching."
    )
    assert idea.evidence_status == "externally_reported"
    assert idea.evidence_status != "observed"


def test_assess_contribution_zero_when_no_overlap():
    """Case 4: no contribution → 暂不回复 reason."""
    post = _post(content="knitting patterns for wool sweaters", matched_topics=["knitting"])
    job = ContentJob(
        id="idea_x",
        source_card_ids=[],
        reader_problem="agent evals",
        author_position=None,
        recommended_format=RecommendedFormat.REPLY,
        status=ContentJobStatus.PROPOSED,
        core_message="failure replays",
        facts=["recorded failure replay"],
        evidence_status="observed",
    )
    ok, reason = assess_job_contribution(post, job)
    assert ok is False
    assert "暂不回复" in reason or "无交集" in reason or "缺少" in reason


def test_assess_contribution_yes_with_shared_topic():
    """Case 1: evidenced reply — overlapping practice + discussion."""
    post = _post()
    ok, reason = assess_contribution(
        post,
        facts=["recorded failure replay for agent timeout"],
        core_message="failure replays make agent bugs reproducible",
        contribution_basis_refs=["idea_1"],
    )
    assert ok is True
    assert reason


def test_outline_default_no_draft_body():
    """Default generate_proposals(full_draft=False) leaves draft empty, fills outline."""
    scored = [ScoredPost(post=_post(), score=_score())]
    runner = FakeRunner(
        ProposalBatchOutput(
            items=[
                ProposalItem(
                    post_id="post_rel",
                    draft="",
                    outline="- peer asks about production reliability\n"
                    "- share replay capture approach\n"
                    "- ask what failure mode they hit first",
                    value_added="offer a concrete replay workflow",
                    source_summary="how to test agent reliability in production",
                    factual_risks=[],
                )
            ]
        )
    )
    out = generate_proposals(
        runner,
        scored,
        EngagementSettings(),
        full_draft=False,
        contribution_basis_refs=["idea_1"],
        facts=["recorded failure replay"],
        core_message="replays help",
    )
    assert len(out) == 1
    assert out[0].outline
    assert not (out[0].draft or "").strip()
    assert out[0].value_added


def test_full_draft_flag_still_produces_body():
    scored = [ScoredPost(post=_post(), score=_score())]
    runner = FakeRunner(
        ProposalBatchOutput(
            items=[
                ProposalItem(
                    post_id="post_rel",
                    draft="Have you tried recording a failure replay?",
                    intent="ask about replays",
                    source_summary="reliability in prod",
                    factual_risks=[],
                    outline="- ask about replays",
                    value_added="concrete question",
                )
            ]
        )
    )
    out = generate_proposals(
        runner, scored, EngagementSettings(), full_draft=True, contribution_basis_refs=["idea_1"]
    )
    assert out[0].draft
    assert "replay" in out[0].draft.lower()


def test_fabricated_experience_rewritten_without_observed_basis():
    """Case 2: 我测试过 without observed facts → rewritten."""
    assert blocks_fabricated_experience("我测试过 replay", has_basis=False) is True
    scored = [ScoredPost(post=_post(), score=_score())]
    runner = FakeRunner(
        ProposalBatchOutput(
            items=[
                ProposalItem(
                    post_id="post_rel",
                    draft="",
                    outline="我测试过 failure replay 在生产环境很好用",
                    value_added="share experience",
                    source_summary="reliability",
                    factual_risks=[],
                )
            ]
        )
    )
    job = ContentJob(
        id="idea_ext",
        source_card_ids=[],
        reader_problem="r",
        author_position=None,
        recommended_format=RecommendedFormat.REPLY,
        status=ContentJobStatus.PROPOSED,
        core_message="blog claim",
        facts=["blog said 40%"],
        evidence_status="externally_reported",
    )
    out = generate_proposals(
        runner,
        scored,
        EngagementSettings(),
        full_draft=False,
        job=job,
        contribution_basis_refs=[job.id],
    )
    assert out
    assert "我测试过" not in out[0].outline
    assert any("fabricated" in r for r in out[0].factual_risks)


def test_idempotent_generation_key_same_url(tmp_path):
    """Same URL / peer / action → same generation_key; repo upsert does not duplicate."""
    ws = Workspace(tmp_path / "var")
    ws.ensure()
    scored = [ScoredPost(post=_post(), score=_score())]
    item = ProposalItem(
        post_id="post_rel",
        outline="- ask about replays\n- share capture steps\n- next: try one failure",
        value_added="replay workflow",
        source_summary="reliability",
        factual_risks=[],
    )
    runner = FakeRunner(ProposalBatchOutput(items=[item]))
    a = generate_proposals(
        runner, scored, EngagementSettings(), full_draft=False, contribution_basis_refs=["idea_1"]
    )[0]
    runner2 = FakeRunner(ProposalBatchOutput(items=[item]))
    b = generate_proposals(
        runner2, scored, EngagementSettings(), full_draft=False, contribution_basis_refs=["idea_1"]
    )[0]
    assert a.generation_key == b.generation_key
    repo = InteractionRepository(ws)
    repo.upsert(a, run_id="create")
    repo.upsert(b, run_id="create")
    assert len(repo.list_all()) == 1


def test_record_opens_thread_follow_up(tmp_path):
    """Case 5: after publish record, thread exists and follow-up context restores."""
    ws = Workspace(tmp_path / "var")
    ws.ensure()
    post = _post()
    proposal = InteractionProposal(
        id="x:post_rel:draft_reply",
        post=post,
        score=_score(),
        action=InteractionAction.DRAFT_REPLY,
        outline="- share replay\n- ask failure mode",
        value_added="replay workflow",
        draft=None,
        approval_required=True,
        status=InteractionStatus.APPROVED,
        peer_id="peer_alice",
        contribution_basis_refs=["idea_1"],
    )
    InteractionRepository(ws).upsert(proposal, run_id="t")
    record = InteractionRecord(
        id=f"rec_{proposal.id}",
        proposal_id=proposal.id,
        peer_id="peer_alice",
        platform="x",
        source_url="https://x.com/me/status/9",
        published_body="Have you tried failure replays?",
        occurred_at=_NOW,
        outcome="published",
        direction="outbound",
    )
    InteractionRecordRepository(ws).upsert(record)
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_alice", topic="agent reliability")
    thread = svc.append_interaction(thread, record.id, occurred_at=_NOW)
    ConversationThreadRepository(ws).upsert(thread)

    loaded = ConversationThreadRepository(ws).get(thread.id)
    assert loaded is not None
    assert record.id in loaded.interaction_ids
    # Duplicate record id does not create a second file.
    InteractionRecordRepository(ws).upsert(record)
    assert len(InteractionRecordRepository(ws).list_all()) == 1


def test_edit_invalidates_approval(tmp_path):
    """P2: revising body bumps revision and returns status to proposed."""
    ws = Workspace(tmp_path / "var")
    ws.ensure()
    proposal = InteractionProposal(
        id="x:post_rel:draft_reply",
        post=_post(),
        score=_score(),
        action=InteractionAction.DRAFT_REPLY,
        draft="first",
        outline="- a",
        approval_required=True,
        status=InteractionStatus.PROPOSED,
        peer_id="peer_alice",
        revision=1,
    )
    repo = InteractionRepository(ws)
    repo.upsert(proposal, run_id="t")
    repo.approve(proposal.id)
    approved = repo.get(proposal.id)
    assert approved is not None
    assert approved.status is InteractionStatus.APPROVED
    assert approved.approval_revision == 1
    repo.edit(proposal.id, "second body")
    edited = repo.get(proposal.id)
    assert edited is not None
    assert edited.status is InteractionStatus.PROPOSED
    assert edited.revision == 2
    assert edited.approval_revision is None
    assert edited.revised_draft == "second body"


def test_ready_gate_blocks_secrets_and_lived_claims():
    job = ContentJob(
        id="idea_u",
        source_card_ids=[],
        reader_problem="r",
        author_position=None,
        recommended_format=RecommendedFormat.REPLY,
        status=ContentJobStatus.PROPOSED,
        evidence_status="unverified",
        facts=[],
    )
    secret_hits = ready_gate_blocks(body="key=AKIAIOSFODNN7EXAMPLE", job=job)
    assert "secret_detected" in secret_hits
    assert "lived_claim_without_observed_facts" in ready_gate_blocks(
        body="我测试过 this in prod", job=job
    )
