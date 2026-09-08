"""conversation-scout 领域服务：从公开讨论提炼「交流机会」（Opportunity），非 IdeaCandidate。

与已删除的 SearchService 不同：本服务产出的是交流机会（由用户决定是否转成自己的 Idea），
且机会提炼本身是开放性判断，故注入 LLM runner；确定性去重 + 噪音预过滤仍走代码。

外部帖子只是信号，不是个人证据；Opportunity 不是 ContentJob、不是证据，是独立中转记录。
"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.twitter.models import Tweet

# 机会信号（与旧 search_service 相同的轻量启发式，用作 LLM 前的确定性预过滤）。
_OPPORTUNITY_SIGNALS = frozenset({
    "doesn't work", "does not work", "not working", "broken", "broke", "breaks",
    "bug", "fails", "failed", "failing", "failure", "error", "crash", "crashing",
    "problem", "issue", "struggling", "struggle", "painful", "frustrating",
    "hard to", "too hard", "can't", "cannot", "won't", "too slow",
    "workaround", "hack", "missing", "lacking", "gap", "no one", "nobody",
    "unexpected", "in practice", "turns out",
    "不行", "坏了", "崩溃", "问题", "坑", "踩坑", "反例", "缺口", "翻车",
})

# LLM 精判的并发上限（bounded pool，避免按帖子数无限开线程）。
_MAX_WORKERS = 4

_NOISE_SIGNALS = frozenset({
    "announces", "announced", "launches", "launched", "release", "released",
    "shipping", "shipped", "new version",
    "raised", "raises", "funding", "series a", "series b", "seed", "valuation",
    "acquires", "acquired", "ipo", "investment",
    "amazing", "awesome", "incredible", "wow",
    "新闻", "融资", "发布", "上线", "估值", "收购",
})


def _signal_text(post: Tweet) -> str:
    return " ".join(post.text.split())


def _classify(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in _OPPORTUNITY_SIGNALS):
        return "opportunity"
    if any(k in lowered for k in _NOISE_SIGNALS):
        return "noise"
    return "other"


_OPPORTUNITY_PROMPT = """\
You scout public technical discussion for genuine conversation opportunities.
Given a post, decide whether it exposes a real problem, disagreement, failure case,
or unresolved mechanism worth engaging — not merely a launch, funding, or sentiment.

Rules:
- Do not treat the author's first-person experience as your own or as a fact.
- Do not recommend merely because the post is popular.
- Prefer real problems, disagreements, failure cases, and unresolved mechanisms.

## Topic
{topic}

## Post
{post}

Respond with JSON matching the schema:
- is_opportunity: true only if genuinely worth engaging.
- shared_tension: the tension both sides share.
- why_relevant: why it matters to practitioners now.
- response_angles: 1-3 of share_experience / ask_mechanism / challenge_assumption.
- knowledge_gap: what is unresolved or under-explained.
- relationship_value: what a useful exchange would build.
"""


class OpportunityDraft(BaseModel):
    """LLM 结构化输出：机会判断 + 交流机会字段。"""

    is_opportunity: bool
    shared_tension: str = ""
    why_relevant: str = ""
    response_angles: list[
        Literal["share_experience", "ask_mechanism", "challenge_assumption"]
    ] = Field(default_factory=list)
    knowledge_gap: str = ""
    relationship_value: str = ""


class SourcePostRef(BaseModel):
    """机会的来源帖子引用（外部信号，可追溯）。"""

    url: str
    author: str
    text: str


class Opportunity(BaseModel):
    """交流机会：值得交流的人、问题和切入口。"""

    id: str
    source_post: SourcePostRef
    shared_tension: str
    why_relevant: str
    response_angles: list[str]
    knowledge_gap: str
    relationship_value: str


class OpportunityService:
    """把一组公开帖子提炼为 Opportunity 列表（LLM 精判 + 确定性预过滤）。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def to_opportunities(self, posts: list[Tweet], *, topic: str) -> list[Opportunity]:
        """posts → Opportunity；去重 + 噪音预过滤确定性串行，LLM 精判并行 + 逐帖容错。"""
        # 确定性预过滤（纯 CPU，串行）：去重 + 机会信号预筛，噪音帖不进 LLM。
        survivors: list[tuple[Tweet, str]] = []
        seen: set[str] = set()
        for post in posts:
            signal = _signal_text(post)
            if not signal:
                continue
            key = signal.lower()
            if key in seen:
                continue
            seen.add(key)
            if _classify(signal) == "opportunity":
                survivors.append((post, signal))

        # 独立 I/O 的 LLM 精判：≥2 帖时用有界 pool.map 并行，结果顺序与串行一致。
        if len(survivors) <= 1:
            results = [self._judge(signal, topic) for _, signal in survivors]
        else:
            workers = min(len(survivors), _MAX_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda ps: self._judge(ps[1], topic), survivors))

        opportunities: list[Opportunity] = []
        for (post, signal), out in zip(survivors, results, strict=True):
            if out is None or not out.is_opportunity:
                continue
            opportunities.append(
                Opportunity(
                    id=f"opp_{hashlib.sha256((post.url + signal).encode('utf-8')).hexdigest()[:8]}",
                    source_post=SourcePostRef(url=post.url, author=post.author, text=signal),
                    shared_tension=out.shared_tension,
                    why_relevant=out.why_relevant,
                    response_angles=[str(a) for a in out.response_angles],
                    knowledge_gap=out.knowledge_gap,
                    relationship_value=out.relationship_value,
                )
            )
        return opportunities

    def _judge(self, signal: str, topic: str) -> OpportunityDraft | None:
        """单帖 LLM 精判；失败（超时/子进程/校验）返回 None，跳过该帖而非中止整轮。"""
        try:
            return cast(
                OpportunityDraft,
                self.runner.run(
                    _OPPORTUNITY_PROMPT.format(topic=topic, post=signal),
                    OpportunityDraft,
                ),
            )
        except RuntimeError:
            return None
