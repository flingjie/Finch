"""FragmentService：user / conversation 来源 → IdeaCandidate。"""

from finch.content.jobs import AuthorPosition
from finch.engagement.models import ConversationEvidence
from finch.ideas.fragment_service import FragmentService, IdeaDraftOutput
from finch.ideas.models import IdeaBoundaries
from finch.ideas.opportunity import Opportunity, SourcePostRef


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
        recommended_format="original",
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


def test_from_opportunity_external_stays_external():
    opp = Opportunity(
        id="opp_1",
        source_post=SourcePostRef(
            url="https://x.com/a/status/9", author="a", text="I spent weeks debugging this"
        ),
        shared_tension="t", why_relevant="w", response_angles=["ask_mechanism"],
        knowledge_gap="g", relationship_value="v",
    )
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_opportunity(opp)
    assert idea.origin == "search"
    assert idea.boundaries.known == []  # 外部信号不归 known
    assert idea.source_refs[0].type == "post"
    assert idea.source_refs[0].summary == "I spent weeks debugging this"  # 原文保留在来源，可追溯


def test_from_opportunity_neutralizes_first_person():
    """LLM 返回第一人称时，确定性中性化兜底（外部帖 ≠ 个人证据）。"""
    opp = Opportunity(
        id="opp_2",
        source_post=SourcePostRef(
            url="https://x.com/a/status/10", author="a", text="I spent weeks debugging this"
        ),
        shared_tension="t", why_relevant="w", response_angles=["ask_mechanism"],
        knowledge_gap="g", relationship_value="v",
    )
    first_person = IdeaDraftOutput(
        core_point="I think our agent is broken",
        observation="I spent weeks debugging it",
        reader_problem="we can't reproduce the crash",
        why_worth_saying="worth saying",
        intent="stance",
        open_question="why does it crash",
        author_position=AuthorPosition(
            claim="I saw the failure", decision="I will fix it", tradeoff="I lose time"
        ),
        boundaries=IdeaBoundaries(known=["I know this"], inferred=[], unknown=[]),
        recommended_format="original",
    )
    svc = FragmentService(FakeRunner(first_person))
    idea = svc.from_opportunity(opp)
    assert "I think" not in idea.core_point
    assert "I spent" not in idea.observation
    assert "we" not in idea.reader_problem.lower()
    assert "I saw" not in idea.author_position.claim
    assert idea.boundaries.known == []  # LLM 的 known 被强制清空
    assert idea.source_refs[0].summary == "I spent weeks debugging this"  # 原文保留在来源


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

