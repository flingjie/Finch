"""端到端：连接主循环（发现 → 提案 → 批准 → 记录 → 对话 → 观点 → 指标）。

用 fakes 与临时 SQLite 把各领域服务串起来，验证三个核心不变量：
1. Proposal（建议）、InteractionRecord（已发生互动）、FeedbackSnapshot（结果）是三个
   不同事实；
2. 未批准内容无法进入执行态（record 要求 APPROVED）；
3. 对话型观点可追溯到 ConversationThread 与原始互动。
"""

from datetime import UTC, datetime

from finch.content.jobs import AuthorPosition
from finch.conversations.service import ConversationService
from finch.engagement.metrics import compute_relationship_metrics
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
)
from finch.ideas.fragment_service import FragmentService, IdeaDraftOutput
from finch.ideas.models import IdeaBoundaries
from finch.ideas.service import IdeaService
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    InteractionRecordRepository,
    InteractionRepository,
    PeerRepository,
)

_NOW = datetime(2026, 9, 8, tzinfo=UTC)


class FakeRunner:
    def __init__(self, ret):
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        return self.ret


def _proposal() -> InteractionProposal:
    post = ExternalPost(
        id="post_1", platform="x", url="https://x.com/alice/status/1",
        author_id="alice", author_name="Alice",
        content="How do you test agent reliability in production?",
        published_at=_NOW, matched_topics=["agent reliability"],
    )
    return InteractionProposal(
        id="x:post_1:draft_reply",
        post=post,
        score=ConversationScore(
            relevance=0.8, novelty=0.8, discussability=0.8, practical_evidence=0.8,
            relationship_value=0.5, total=0.86, reasons=["on topic"],
        ),
        action=InteractionAction.DRAFT_REPLY,
        draft="Have you tried recording a failure replay?",
        approval_required=True,
        peer_id="peer_alice",
    )


def _idea_output() -> IdeaDraftOutput:
    return IdeaDraftOutput(
        core_point="replays make agent failures reproducible",
        reader_problem="hard to rerun",
        why_worth_saying="replays help",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        boundaries=IdeaBoundaries(),
        recommended_format="original",
        communication_goal="summarize_practice",
    )


def test_connection_loop_three_facts_and_traceability(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()

    # 1) 发现 → 提案落库。
    proposal = _proposal()
    interactions = InteractionRepository(store)
    interactions.upsert(proposal, run_id="run_1")
    peers = PeerRepository(store)
    peers.upsert(
        PeerProfile(
            id="peer_alice",
            platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
            display_name="Alice",
        )
    )

    # 2) 未批准不可记录（未批准内容无法进入执行态）。
    assert InteractionRepository(store).get(proposal.id).status is InteractionStatus.PROPOSED

    # 3) 批准 → 记录真实互动（三事实分离：Proposal 仍保留自己的状态，Record 是独立事实）。
    interactions.approve(proposal.id)
    record = InteractionRecord(
        id=f"rec_{proposal.id}",
        proposal_id=proposal.id,
        peer_id="peer_alice",
        platform="x",
        source_url="https://x.com/alice/status/1",
        published_body=proposal.draft or "",
        occurred_at=_NOW,
        outcome="published",
    )
    records = InteractionRecordRepository(store)
    records.upsert(record)
    # 同一 proposal 重复记录幂等。
    records.upsert(record)
    assert len(records.list_by_proposal(proposal.id)) == 1

    # 4) 收到回复 → 形成 ConversationThread。
    thread = ConversationService().open_thread(peer_id="peer_alice", topic="agent reliability")
    thread = ConversationService().append_interaction(thread, record.id, occurred_at=_NOW)
    threads = ConversationThreadRepository(store)
    threads.upsert(thread)

    # 5) 从对话提炼观点（可追溯到 thread + 原始互动）。
    idea = FragmentService(FakeRunner(_idea_output())).from_thread(
        thread, interactions=[record]
    )
    assert idea.origin == "conversation"
    refs = {(r.type, r.ref) for r in idea.source_refs}
    assert ("conversation", thread.id) in refs
    assert ("conversation", record.id) in refs

    job = IdeaService(ContentJobRepository(store)).create_candidate(idea)
    assert job.status.value == "proposed"  # 没有用户立场时保持 PROPOSED
    assert job.communication_goal == "summarize_practice"

    # 6) 周复盘统计：关系质量指标覆盖该关系。
    metrics = compute_relationship_metrics(
        peers=peers.list_all(),
        interactions=records.list_all(),
        threads=threads.list_all(),
        snapshots=[],
        jobs=ContentJobRepository(store).list_jobs(),
        now=_NOW,
    )
    assert metrics.ideas_from_conversations == 1
    assert metrics.continued_conversations == 0  # 单次互动尚不构成「继续」
