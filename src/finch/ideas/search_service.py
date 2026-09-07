"""SearchService：把公开讨论中的真实问题 / 反例 / 工程缺口提炼为 IdeaCandidate。

（Skill 架构 Step 3）

复用 twitter 适配器的产物（QueryBuilder 查、OpenCliClient.search 召回、normalize_tweets
规范化）——本服务只做**机会提炼**：判断一条公开讨论是否是「读者值得知道」的真实问题 /
反例 / 工程缺口，是就产出**一个** ``IdeaCandidate``；新闻 / 融资 / 纯情绪 / 重复内容跳过。

字段映射（确定性，无 LLM 重述）：
- ``core_point`` ← ``话题 + 外部信号``（第三人口径，不写成作者亲历）；
- ``reader_problem`` ← 外部帖子的原始问题表述（可追溯到 ``source_refs``）；
- ``author_position``：``claim`` 是「公开讨论暴露真实缺口」这一可验证主张（帖子 URL 可查证）、
  ``decision`` 是「值得调研/回应」的建议决策、``tradeoff`` 是「不写则错失公共信号」。
- ``boundaries``：外部信号未经作者一手验证 → 全部归入 ``inferred``（``known``/``unknown`` 为空）。

本模块是纯领域逻辑：不访问 DB、不调用 opencli、不做重试。搜索与落库由 CLI 层完成，
``IdeaService.create_candidate`` 负责后续幂等落库为 ``ContentJob``。

``opencli`` 与 ``builder`` 注入仅为 API 对称（与 ``CommitService`` 一致），并保留给
未来「Skill 内部自行召回」的演进空间；``to_ideas`` 只消费调用方传入的 ``posts``。
"""

import hashlib
import re

from finch.content.jobs import AuthorPosition
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    SourceRef,
)
from finch.twitter.models import Tweet
from finch.twitter.opencli_client import OpenCliClient
from finch.twitter.query_builder import QueryBuilder

_GENERATOR_SKILL = "search-to-idea"
_GENERATOR_VERSION = "1.0.0"

# 机会信号：真实问题 / 反例 / 工程缺口（轻量启发式，英文 + 中文）。
# 命中任一关键词即视为「读者值得知道的真实问题」，产出 IdeaCandidate。
_OPPORTUNITY_SIGNALS = frozenset({
    "doesn't work", "does not work", "not working", "broken", "broke", "breaks",
    "bug", "fails", "failed", "failing", "failure", "error", "crash", "crashing",
    "problem", "issue", "struggling", "struggle", "painful", "frustrating",
    "hard to", "too hard", "can't", "cannot", "won't", "too slow",
    "workaround", "hack", "missing", "lacking", "gap", "no one", "nobody",
    "unexpected", "in practice", "turns out",
    "不行", "坏了", "崩溃", "问题", "坑", "踩坑", "反例", "缺口", "翻车",
})

# 噪音信号：新闻 / 融资 / 纯情绪（命中即跳过）。机会信号优先于噪音信号，
# 所以「融资了但产品仍一堆 bug」仍按机会处理。
_NOISE_SIGNALS = frozenset({
    "announces", "announced", "launches", "launched", "release", "released",
    "shipping", "shipped", "new version",
    "raised", "raises", "funding", "series a", "series b", "seed", "valuation",
    "acquires", "acquired", "ipo", "investment",
    "amazing", "awesome", "incredible", "wow",
    "新闻", "融资", "发布", "上线", "估值", "收购",
})


# 第一人称代词（英文 + 中文）：外部作者亲历不得被采纳为作者亲历，提炼时去掉。
_FIRST_PERSON_RE = re.compile(
    r"\b(i|i'm|i've|i'd|i'll|we|we're|we've|we'd|we'll|my|our|mine|ours|me|us|"
    r"myself|ourselves)\b",
    re.IGNORECASE,
)
_CN_FIRST_PERSON_RE = re.compile(r"(我|我们|我的|我们的|咱|咱们)")


def _signal_text(post: Tweet) -> str:
    """折叠空白后的原文信号（可追溯到 source_refs）。"""
    return " ".join(post.text.split())


def _neutralize(signal: str) -> str:
    """去掉第一人称代词，得到中性的问题陈述（不采纳外部亲历）。"""
    neutral = _FIRST_PERSON_RE.sub(" ", signal)
    neutral = _CN_FIRST_PERSON_RE.sub(" ", neutral)
    return " ".join(neutral.split())


def _classify(text: str) -> str:
    """确定性分类：opportunity（真实问题/反例/缺口）| noise（新闻/融资/情绪）| other。

    机会信号优先；无任何信号一律归 other（fail-closed：不产出）。
    """
    lowered = text.lower()
    if any(k in lowered for k in _OPPORTUNITY_SIGNALS):
        return "opportunity"
    if any(k in lowered for k in _NOISE_SIGNALS):
        return "noise"
    return "other"


def _core_point(topic: str, neutral: str) -> str:
    """中心主张：话题 + 外部信号（已中性化，第三人口径，不写成作者亲历）。"""
    return f"{topic} 相关公开讨论暴露工程缺口：{neutral}"


def _idea_for(post: Tweet, signal: str, topic: str) -> IdeaCandidate:
    """单条机会信号 → 单个 IdeaCandidate（外部来源保持外部，立场恒 proposed）。

    原文 ``signal`` 只保留在 ``source_refs.summary``（来源摘要，可追溯）；进入作者
    内容字段（``core_point``/``reader_problem``/``boundaries``）的一律是中性化后的表述。
    """
    neutral = _neutralize(signal)
    return IdeaCandidate(
        # 与 IdeaService 的 ContentJob.id 方案一致：sha256(core_point)[:8]。
        id=f"idea_{hashlib.sha256(_core_point(topic, neutral).encode('utf-8')).hexdigest()[:8]}",
        origin="search",
        core_point=_core_point(topic, neutral),
        reader_problem=neutral,
        why_worth_saying="公开讨论中出现的真实问题/缺口，值得写",
        author_position=AuthorPosition(
            claim=f"{topic} 相关公开讨论暴露真实工程缺口",
            decision="值得调研或回应这个缺口",
            tradeoff="不写则错失这个公共信号",
        ),
        source_refs=[SourceRef(type="post", ref=post.url, summary=signal)],
        boundaries=IdeaBoundaries(known=[], inferred=[neutral], unknown=[]),
        recommended_format="original",
        generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_GENERATOR_VERSION),
    )


class SearchService:
    """把一组公开讨论帖子提炼为 IdeaCandidate 列表（纯领域逻辑）。"""

    def __init__(self, opencli: OpenCliClient, builder: QueryBuilder) -> None:
        self.opencli = opencli
        self.builder = builder

    def to_ideas(self, posts: list[Tweet], *, topic: str) -> list[IdeaCandidate]:
        """posts → IdeaCandidate；新闻/融资/纯情绪/重复内容 → 空结果。

        去重：按规范化信号（小写 + 折叠空白）去重，同一条已表达内容只产出一次。
        """
        ideas: list[IdeaCandidate] = []
        seen: set[str] = set()
        for post in posts:
            signal = _signal_text(post)
            if not signal:
                continue
            key = signal.lower()
            if key in seen:
                continue
            seen.add(key)
            if _classify(signal) != "opportunity":
                continue
            ideas.append(_idea_for(post, signal, topic))
        return ideas
