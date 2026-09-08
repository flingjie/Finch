"""FragmentService：user / conversation 来源 → IdeaCandidate。"""

from datetime import datetime

from finch.content.jobs import AuthorPosition
from finch.content.models import RecommendedFormat
from finch.conversations.models import ConversationThread
from finch.engagement.models import ConversationEvidence, InteractionRecord
from finch.ideas.fragment_service import FragmentService, IdeaDraftOutput
from finch.ideas.models import IdeaBoundaries


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _out() -> IdeaDraftOutput:
    return IdeaDraftOutput(
        core_point="中心主张",
        observation="观察",
        reader_problem="读者问题",
        why_worth_saying="为什么",
        intent="exploration",
        open_question="问题",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        boundaries=IdeaBoundaries(known=[], inferred=[], unknown=[]),
        recommended_format=RecommendedFormat.SHORT_POST,
    )


def test_from_text_origin_user_no_source_refs():
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_text("我最近有个模糊想法")
    assert idea.origin == "user"
    assert idea.source_refs == []
    assert idea.generator.skill == "idea-discovery"
    assert idea.intent == "exploration"


def test_from_conversation_origin_and_source_ref():
    evidence = ConversationEvidence(
        id="ev_1", interaction_id="i1", post_id="p1", kind="question",
        statement="某个机制到底怎么工作", verified=True,
    )
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_conversation(evidence)
    assert idea.origin == "conversation"
    assert idea.source_refs[0].type == "conversation"
    assert idea.source_refs[0].ref == "ev_1"
    assert idea.source_refs[0].summary == "某个机制到底怎么工作"


def test_from_conversation_unverified_rejected():
    evidence = ConversationEvidence(
        id="ev_1", interaction_id="i1", post_id="p1", kind="question",
        statement="某个机制到底怎么工作", verified=False,
    )
    svc = FragmentService(FakeRunner(_out()))
    try:
        svc.from_conversation(evidence)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unverified evidence")


def test_from_thread_traces_to_thread_and_interactions():
    thread = ConversationThread(
        id="thread_1", peer_id="peer_abc", topic="agent evals",
        open_questions=["how to reproduce?"],
        agreements=["replays help"],
        possible_experiments=["diff failure replays"],
    )
    interaction = InteractionRecord(
        id="rec_1", proposal_id="p1", peer_id="peer_abc", platform="x",
        source_url="https://x.com/alice/status/1", occurred_at=datetime(2026, 9, 1),
    )
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_thread(thread, interactions=[interaction])
    assert idea.origin == "conversation"
    refs = {(r.type, r.ref) for r in idea.source_refs}
    assert ("conversation", "thread_1") in refs
    assert ("conversation", "rec_1") in refs


def test_from_thread_preserves_communication_goal():
    thread = ConversationThread(id="thread_1", peer_id="p", topic="t")
    out = _out().model_copy(update={"communication_goal": "invite_counterexample"})
    svc = FragmentService(FakeRunner(out))
    idea = svc.from_thread(thread)
    assert idea.communication_goal == "invite_counterexample"

