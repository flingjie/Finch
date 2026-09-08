"""OpportunityService：帖子 → Opportunity（LLM 精判 + 确定性预过滤）。"""

from finch.ideas.opportunity import OpportunityDraft, OpportunityService
from finch.twitter.models import Tweet

POST_URL = "https://x.com/acme/status/1"


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _post(text, id="1"):
    return Tweet(id=id, author="acme", text=text, url=POST_URL)


def _draft(is_opportunity=True):
    return OpportunityDraft(
        is_opportunity=is_opportunity,
        shared_tension="tension",
        why_relevant="relevant",
        response_angles=["ask_mechanism"],
        knowledge_gap="gap",
        relationship_value="value",
    )


def test_opportunity_yields_one():
    svc = OpportunityService(FakeRunner(_draft()))
    opps = svc.to_opportunities(
        [_post("The scheduler has a nasty race condition bug.")], topic="sched"
    )
    assert len(opps) == 1
    assert opps[0].source_post.url == POST_URL
    assert opps[0].id.startswith("opp_")


def test_noise_skipped_without_llm():
    runner = FakeRunner(_draft())
    svc = OpportunityService(runner)
    opps = svc.to_opportunities([_post("Acme launches v2 of their framework.")], topic="agents")
    assert opps == []
    assert runner.calls == 0


def test_llm_rejects_skips():
    svc = OpportunityService(FakeRunner(_draft(is_opportunity=False)))
    opps = svc.to_opportunities(
        [_post("The scheduler has a nasty race condition bug.")], topic="sched"
    )
    assert opps == []


def test_dedup():
    svc = OpportunityService(FakeRunner(_draft()))
    posts = [_post("Our agent keeps failing on long context.", id="1"),
             _post("Our agent keeps failing on long context.", id="2")]
    opps = svc.to_opportunities(posts, topic="agents")
    assert len(opps) == 1
