"""DraftService：idea-to-draft 领域服务（Skill 架构 Step 4）。

把已确认的 ``ContentJob``（idea 候选）生成统一 ``Draft``：加载 job → ``require_confirmed``
语义 → ``draft_generation_key`` 幂等命中 → 生成正文（只依据 job 语境，不搜索新来源、
不绑定证据卡）→ Critic（6 检查器，Safety 硬门禁）→ 有限 rewrite → 落库 ``Draft`` +
``CriticReport``。

本模块是纯领域服务：只依赖注入的仓储与 runner，不直接访问 DB、不做自动重试、
不自动发布。idea 候选没有证据卡，故复用 ``idea`` 流的 Critic 套件（``idea_checker_suite``，
去掉 EvidenceChecker），而不是证据绑定的 ``default_checker_suite``。

幂等：``draft_generation_key`` 由 (idea 指纹, idea-to-draft 版本, 格式, voice 版本)
的 sha256 决定，同一 idea + 同一生成配置重复创建时命中同一 ``draft_id``，返回已存在的
Draft，不重复调用 LLM。
"""

import hashlib

from finch.codex.runner import CodexRunner
from finch.content.checkers.aggregate import AggregateOutcome, aggregate_checks
from finch.content.checkers.base import CheckContext, CheckResult
from finch.content.critic import _run_checks
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft
from finch.content.voice import VoiceProfile
from finch.content.writer import write_original_from_job
from finch.idea.service import idea_checker_suite, rewrite_idea
from finch.settings import QualityGates
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DraftRepository,
)

# 稳定分隔符：ASCII unit separator，字段值几乎不可能包含该控制字符。
_SEP = "\x1f"

_ID_PREFIX = "draft_"


def draft_generation_key(
    idea_fingerprint: str,
    idea_to_draft_version: str,
    format: str,
    voice_profile_version: str,
) -> str:
    """由 idea 指纹 + 生成器版本 + 格式 + voice 版本确定幂等键（sha256 hex digest）。

    同一 (idea, idea-to-draft 版本, 格式, voice 版本) 的草稿重复产出时命中同一 key。
    """
    raw = _SEP.join(
        [idea_fingerprint, idea_to_draft_version, format, voice_profile_version]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _idea_fingerprint(idea: ContentJob) -> str:
    """idea 指纹：优先用已算好的 ``content_fingerprint``，否则回退到核心主张的 sha256。"""
    if idea.content_fingerprint:
        return idea.content_fingerprint
    return hashlib.sha256(idea.core_message.encode("utf-8")).hexdigest()


def _failed_issues(checks: list[CheckResult]) -> str:
    """把失败检查器折叠成一句人类可读原因（用于硬失败抛出）。"""
    parts: list[str] = []
    for check in checks:
        if not check.passed:
            detail = "; ".join(check.issues) if check.issues else "failed"
            parts.append(f"{check.checker}: {detail}")
    return " | ".join(parts) or "critic failed"


class DraftService:
    """idea-to-draft 领域服务：从已确认 idea 生成 Draft 并落库 Critic 报告。"""

    def __init__(
        self,
        drafts: DraftRepository,
        critic_reports: CriticReportRepository,
        jobs: ContentJobRepository,
        runner: CodexRunner,
        *,
        max_rewrite_rounds: int | None = None,
        voice_profile: VoiceProfile | None = None,
    ) -> None:
        self.drafts = drafts
        self.critic_reports = critic_reports
        self.jobs = jobs
        self.runner = runner
        self.max_rewrite_rounds = (
            max_rewrite_rounds
            if max_rewrite_rounds is not None
            else QualityGates().max_rewrite_rounds
        )
        self.voice_profile = voice_profile

    def create(
        self,
        idea_id: str,
        *,
        version: str,
        format: str,
        voice_version: str,
    ) -> Draft:
        """从已确认 idea 生成 Draft（幂等）：未确认抛 ValueError，命中已有 Draft 直接返回。

        生成正文只依据 job 语境（``write_original_from_job``，不搜索新来源、不绑定证据卡）；
        Critic 用 6 检查器套件（去掉 EvidenceChecker），有限 rewrite 至多
        ``max_rewrite_rounds`` 轮。``pass`` 或 rewrite 用尽 → 落库并返回最终 Draft；
        ``reject``（hard_fail）/``needs_input`` → 丢弃（抛 ValueError，fail-closed）。
        """
        job = self.jobs.get_job(idea_id)
        if job is None:
            raise KeyError(idea_id)
        if job.status != ContentJobStatus.CONFIRMED:
            raise ValueError(
                f"idea {idea_id} needs_confirmation (status={job.status.value})"
            )

        fingerprint = _idea_fingerprint(job)
        key = draft_generation_key(fingerprint, version, format, voice_version)
        draft_id = f"{_ID_PREFIX}{key[:16]}"
        existing = self.drafts.get_draft(draft_id)
        if existing is not None:
            return existing

        draft = self._generate(job, draft_id)
        return self._critic_loop(draft, job, draft_id)

    def _generate(self, job: ContentJob, draft_id: str) -> Draft:
        """生成首稿：只依据 job 语境写正文，并把幂等键确定性写入 ``id``。"""
        draft = write_original_from_job(self.runner, job)
        return draft.model_copy(update={"id": draft_id, "run_id": "idea"})

    def _critic_loop(self, draft: Draft, job: ContentJob, draft_id: str) -> Draft:
        """Critic + 有限 rewrite：逐轮写 CriticReport，pass/rewrite 用尽保留，硬失败丢弃。"""
        suite = idea_checker_suite(self.runner, self.voice_profile)
        current = draft
        for round_no in range(self.max_rewrite_rounds + 1):
            checks = _run_checks(suite, CheckContext(draft=current, cards=[], job=job))
            outcome = aggregate_checks(checks)
            self.critic_reports.upsert_report(draft_id, round_no, checks, outcome)

            if outcome == AggregateOutcome.PASS:
                self.drafts.upsert_draft(current)
                return current

            if outcome in (AggregateOutcome.REJECT, AggregateOutcome.NEEDS_INPUT):
                raise ValueError(
                    f"draft {draft_id} dropped by critic ({outcome}): "
                    f"{_failed_issues(checks)}"
                )

            # rewrite：只把失败检查器的指令交给 rewrite_idea（不改立场、不换来源）
            if round_no == self.max_rewrite_rounds:
                break
            failed = [check for check in checks if not check.passed]
            current = rewrite_idea(self.runner, current, failed, job)

        # rewrite 用尽仍不 pass：保留最后一版（人工审核仍可见 Critic 报告）。
        self.drafts.upsert_draft(current)
        return current
