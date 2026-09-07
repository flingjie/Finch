"""DraftService：draft 幂等键 + 骨架（Skill 架构 Step 1 领域核心）。

把 idea-to-draft Skill 产出的统一 Draft 落库为幂等骨架。本模块是纯领域服务：
只依赖注入的 ``DraftRepository`` 与 ``CriticReportRepository``，不直接访问 DB、
不调用 LLM、不做自动重试。

幂等：``draft_generation_key`` 由 (idea 指纹, idea-to-draft 版本, 格式, voice 版本)
的 sha256 决定，同一 idea + 同一生成配置重复创建时命中同一 ``draft_id``，
返回已存在的 Draft。

本任务只落键与幂等骨架（``body`` 为空占位）；真正的正文生成与 Critic 由后续
idea-to-draft Skill（Step 4）完成。
"""

import hashlib

from finch.content.jobs import ContentJob
from finch.content.models import Draft, DraftKind
from finch.storage.repositories import CriticReportRepository, DraftRepository

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


class DraftService:
    """draft 领域服务：幂等创建 Draft 骨架。"""

    def __init__(
        self,
        drafts: DraftRepository,
        critic_reports: CriticReportRepository,
    ) -> None:
        self.drafts = drafts
        self.critic_reports = critic_reports

    def create(
        self,
        idea: ContentJob,
        *,
        version: str,
        format: str,
        voice_version: str,
    ) -> Draft:
        """按幂等键创建 Draft 骨架：命中已有 Draft 直接返回，否则落库空骨架。

        ``Draft`` 没有独立 ``generation_key`` 字段，故把键的截断值确定性写入 ``id``
        （``draft_<key[:16]>``），用 ``get_draft`` 命中即视为已存在、直接返回。

        本方法只落幂等键与骨架（``body=""`` 占位）；正文生成与 Critic 由后续
        idea-to-draft Skill（Step 4）完成。
        """
        fingerprint = _idea_fingerprint(idea)
        key = draft_generation_key(fingerprint, version, format, voice_version)
        draft_id = f"{_ID_PREFIX}{key[:16]}"
        existing = self.drafts.get_draft(draft_id)
        if existing is not None:
            return existing
        kind = (
            DraftKind.REPLY
            if idea.recommended_format == DraftKind.REPLY
            else DraftKind.ORIGINAL
        )
        draft = Draft(
            id=draft_id,
            kind=kind,
            candidate_id=idea.candidate_id,
            language="zh",
            body="",
            claims=[],
            content_job_id=idea.id,
            position_statement=(
                idea.author_position.decision if idea.author_position else ""
            ),
            run_id="idea",
        )
        self.drafts.upsert_draft(draft)
        return draft
