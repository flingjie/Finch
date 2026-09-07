"""Tests for SearchService（search → IdeaCandidate，Skill 架构 Step 3 Task 1）。"""

from finch.ideas.models import IdeaGenerator, SourceRef
from finch.ideas.search_service import SearchService
from finch.twitter.models import Tweet

POST_URL = "https://x.com/acme/status/1234567890"


class FakeOpenCliClient:
    """轻量 double for OpenCliClient（本服务不调用它，仅构造注入）。"""


class FakeQueryBuilder:
    """轻量 double for QueryBuilder（本服务不调用它，仅构造注入）。"""


def _service() -> SearchService:
    return SearchService(FakeOpenCliClient(), FakeQueryBuilder())


def _post(text: str, *, url: str = POST_URL, id: str = "123") -> Tweet:
    return Tweet(id=id, author="acme", text=text, url=url)


# ---- 真实问题 / 反例 / 工程缺口 → 1 个 Idea ----

def test_real_problem_yields_one_idea():
    svc = _service()
    posts = [_post("Our agent keeps failing on long context, it's broken.")]
    ideas = svc.to_ideas(posts, topic="agent evals")
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.origin == "search"
    assert "agent evals" in idea.core_point
    assert "broken" in idea.core_point
    assert idea.generator == IdeaGenerator(skill="search-to-idea", version="1.0.0")
    assert idea.recommended_format == "original"


def test_counterexample_yields_one_idea():
    svc = _service()
    posts = [_post("In practice the vector DB falls over under load, huge gap.")]
    ideas = svc.to_ideas(posts, topic="vector search")
    assert len(ideas) == 1
    assert ideas[0].origin == "search"


def test_source_ref_uses_post_url():
    svc = _service()
    posts = [_post("The scheduler has a nasty race condition bug.")]
    idea = svc.to_ideas(posts, topic="scheduling")[0]
    assert idea.source_refs == [
        SourceRef(
            type="post",
            ref=POST_URL,
            summary="The scheduler has a nasty race condition bug.",
        )
    ]


# ---- 新闻 / 融资 / 纯情绪 → 空 ----

def test_news_yields_nothing():
    svc = _service()
    posts = [_post("Acme launches v2 of their agent framework today.")]
    assert svc.to_ideas(posts, topic="agents") == []


def test_funding_yields_nothing():
    svc = _service()
    posts = [_post("Acme raised a $50M series A at a $1B valuation.")]
    assert svc.to_ideas(posts, topic="agents") == []


def test_pure_sentiment_yields_nothing():
    svc = _service()
    posts = [_post("This new framework is absolutely amazing and incredible.")]
    assert svc.to_ideas(posts, topic="agents") == []


def test_neutral_post_yields_nothing():
    svc = _service()
    posts = [_post("Went for a walk today, the weather was nice.")]
    assert svc.to_ideas(posts, topic="agents") == []


# ---- 外部亲历不写成作者亲历 ----

def test_external_experience_stays_external():
    svc = _service()
    # 外部作者的第一人称亲历（I spent / our agent）不得被采纳为作者亲历。
    posts = [_post("I spent 3 weeks debugging our agent and it still crashes.")]
    idea = svc.to_ideas(posts, topic="agents")[0]
    # 进入作者内容字段的一律中性化（第三人口径），不出现第一人称。
    assert "I spent" not in idea.core_point
    assert "I spent" not in idea.reader_problem
    assert "our" not in idea.reader_problem
    assert "I spent" not in idea.author_position.claim
    assert "我" not in idea.author_position.claim
    # 原文只保留在来源摘要（外部来源，可追溯），不被改写为用户亲历。
    assert idea.source_refs[0].type == "post"
    assert idea.source_refs[0].ref == POST_URL
    assert idea.source_refs[0].summary == (
        "I spent 3 weeks debugging our agent and it still crashes."
    )
    # 外部信号未经作者验证，只能归入 inferred，不得进 known。
    assert idea.boundaries.known == []
    assert idea.boundaries.inferred
    assert "I spent" not in idea.boundaries.inferred[0]


# ---- 立场恒 proposed ----

def test_position_always_proposed():
    svc = _service()
    posts = [_post("Rate limiting on their API is a real problem for us.")]
    idea = svc.to_ideas(posts, topic="api")[0]
    assert idea.author_position.decision
    assert idea.author_position.claim


# ---- 最近已表达 → 去重 ----

def test_duplicate_post_deduped():
    svc = _service()
    posts = [
        _post("Our agent keeps failing on long context, it's broken.", id="1"),
        _post("Our agent keeps failing on long context, it's broken.", id="2"),
    ]
    ideas = svc.to_ideas(posts, topic="agent evals")
    assert len(ideas) == 1


# ---- 相同输入 → 相同结果（确定性）----

def test_same_input_yields_same_output():
    svc = _service()
    posts = [_post("The scheduler has a nasty race condition bug.")]
    first = svc.to_ideas(posts, topic="scheduling")
    second = svc.to_ideas(posts, topic="scheduling")
    assert first == second
    assert first[0].id == second[0].id
