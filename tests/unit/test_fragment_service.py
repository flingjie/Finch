"""FragmentService：user / conversation 来源 → IdeaCandidate。"""

from finch.content.jobs import AuthorPosition
from finch.engagement.models import ConversationEvidence
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
