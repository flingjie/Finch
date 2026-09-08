"""CommitService：把 GitHub Commit 提炼为 IdeaCandidate（Skill 架构 Step 2 Task 1）。

复用现有适配器（不重写，只迁移调用）：
- ``CommitReader.filter_noise`` 过滤机械/无操作 commit；
- ``Extractor.extract`` 提取 ``EngineeringEvent``（LLM 调用已由注入的 Extractor 完成）；
- ``build_cards`` 生成 ``EvidenceCard``；
- ``scan_cards`` 安全扫描（私密仓库内容 / 密钥 / 不存在 commit）。

每个「有明确工程决策」的 EngineeringEvent 产出**一个** ``IdeaCandidate``；
机械变化、私密仓库内容、无法提炼明确决策的事件一律跳过（不产出）。

字段映射（确定性，无 LLM 重述）：
- ``core_point`` ← ``decision.statement``；``reader_problem`` ← ``problem.statement``；
  ``why_worth_saying`` ← ``result.statement``。
- ``author_position``：``claim`` ← ``result.statement``（可验证的主张）、
  ``decision`` ← ``decision.statement``（主张的决策）、
  ``tradeoff`` ← ``problem.statement``（被放弃的旧问题状态）。
- ``boundaries``：按置信度分桶，VERIFIED/SUPPORTED/USER_CONFIRMED → ``known``、
  INFERRED → ``inferred``、UNKNOWN → ``unknown``。

本模块是纯领域逻辑：不访问 DB、不调用 LLM、不做重试。
``IdeaService.create_candidate`` 负责后续幂等落库为 ``ContentJob``。
"""

import hashlib

from finch.content.jobs import AuthorPosition
from finch.content.models import RecommendedFormat
from finch.evidence.extractor import Extractor, build_cards
from finch.evidence.models import ClaimConfidence, EngineeringEvent
from finch.evidence.safety import scan_cards
from finch.github.commit_reader import CommitReader
from finch.github.models import CommitDetail
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    SourceRef,
)

_GENERATOR_SKILL = "idea-discovery"
_GENERATOR_VERSION = "1.0.0"

# ClaimConfidence → IdeaBoundaries 桶。USER_CONFIRMED 与 VERIFIED/SUPPORTED 同属
# 可断言（assertable），归入 known；extractor 已把它降级为 SUPPORTED，此处仅作兜底。
_CONFIDENCE_TO_BOUNDARY = {
    ClaimConfidence.VERIFIED: "known",
    ClaimConfidence.SUPPORTED: "known",
    ClaimConfidence.USER_CONFIRMED: "known",
    ClaimConfidence.INFERRED: "inferred",
    ClaimConfidence.UNKNOWN: "unknown",
}


def _has_clear_decision(event: EngineeringEvent) -> bool:
    """decision 语句非空且置信度非 UNKNOWN 才视为有可提炼的明确决策。"""
    statement = (event.decision.statement or "").strip()
    if not statement:
        return False
    return event.decision.confidence is not ClaimConfidence.UNKNOWN


def _boundaries_from_event(event: EngineeringEvent) -> IdeaBoundaries:
    """把 problem/decision/result 的置信度映射到 known/inferred/unknown 桶（去重保序）。"""
    buckets: dict[str, list[str]] = {"known": [], "inferred": [], "unknown": []}
    for claim in (event.problem, event.decision, event.result):
        statement = (claim.statement or "").strip()
        if not statement:
            continue
        bucket = _CONFIDENCE_TO_BOUNDARY[claim.confidence]
        if statement not in buckets[bucket]:
            buckets[bucket].append(statement)
    return IdeaBoundaries(
        known=buckets["known"],
        inferred=buckets["inferred"],
        unknown=buckets["unknown"],
    )


def _known_commit_urls(commits: list[CommitDetail], repo: str) -> set[str]:
    """安全扫描用的已知 commit URL 集合（html_url 与构造 URL 两种形式）。"""
    urls: set[str] = set()
    for c in commits:
        if c.html_url:
            urls.add(c.html_url)
        urls.add(f"https://github.com/{repo}/commit/{c.sha}")
    return urls


def _event_to_idea(
    event: EngineeringEvent,
    commits: list[CommitDetail],
) -> IdeaCandidate | None:
    """单个事件 → 单个 IdeaCandidate；无来源 commit 时返回 None（不可追溯）。"""
    sha_to_commit = {c.sha: c for c in commits}
    source_refs: list[SourceRef] = []
    for sha in event.commits:
        commit = sha_to_commit.get(sha)
        if commit is not None and commit.html_url:
            url = commit.html_url
        else:
            url = f"https://github.com/{event.repository}/commit/{sha}"
        summary = commit.message if commit is not None else ""
        source_refs.append(SourceRef(type="commit", ref=url, summary=summary))
    if not source_refs:
        return None

    core_point = event.decision.statement
    return IdeaCandidate(
        # 与 IdeaService 的 ContentJob.id 方案一致：sha256(core_point)[:8]。
        id=f"idea_{hashlib.sha256(core_point.encode('utf-8')).hexdigest()[:8]}",
        origin="commit",
        core_point=core_point,
        reader_problem=event.problem.statement,
        why_worth_saying=event.result.statement,
        author_position=AuthorPosition(
            claim=event.result.statement,
            decision=event.decision.statement,
            tradeoff=event.problem.statement,
        ),
        source_refs=source_refs,
        boundaries=_boundaries_from_event(event),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_GENERATOR_VERSION),
    )


class CommitService:
    """把一组 Commit 提炼为 IdeaCandidate 列表（纯领域逻辑）。"""

    def __init__(self, reader: CommitReader, extractor: Extractor) -> None:
        self.reader = reader
        self.extractor = extractor

    def to_ideas(
        self,
        commits: list[CommitDetail],
        *,
        repo: str,
        repo_is_private: bool = False,
    ) -> list[IdeaCandidate]:
        """commits → IdeaCandidate；机械/私密/无可提炼观点 → 空结果。"""
        filtered = self.reader.filter_noise(commits)
        if not filtered:
            return []

        events = self.extractor.extract(filtered, repo)

        # 安全扫描（复用 evidence/safety.py）：私密内容 / 密钥 / 不存在 commit。
        cards = build_cards(events)
        if repo_is_private:
            cards = [c.model_copy(update={"publishable": False}) for c in cards]
        report = scan_cards(
            cards,
            repo_is_private={repo: repo_is_private},
            known_commit_urls=_known_commit_urls(filtered, repo),
        )
        blocked_card_ids = {hit.card_id for hit in report.hits if hit.card_id}
        blocked_event_ids = {
            card.event_id for card in cards if card.id in blocked_card_ids
        }

        ideas: list[IdeaCandidate] = []
        for event in events:
            if event.id in blocked_event_ids:
                continue
            if not _has_clear_decision(event):
                continue
            idea = _event_to_idea(event, filtered)
            if idea is not None:
                ideas.append(idea)
        return ideas
