"""人际关系领域模型：人物任务理解 / 实践事件 / 交流记录 / 下一步 / 周度简报。

与旧 ``peers`` / ``opportunities`` 领域隔离：不 import 其模型，只共享 ``sources`` 的
原始工件与存储层。``attribution`` 区分四类内容（原始记录 / 人物明确表达 / 作者解释 /
系统推断）；所有「总分」只由 Python 确定性计算，LLM 永不写总分。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["x", "reddit", "github", "v2ex", "weixin", "xiaohongshu", "web"]


class Attribution(StrEnum):
    """内容归属：原始记录 / 人物明确表达 / 作者解释 / 系统推断（推断需附依据+待确认项）。"""

    RAW = "raw"
    PERSON_STATED = "person_stated"
    AUTHOR_INTERPRETATION = "author_interpretation"
    SYSTEM_INFERENCE = "system_inference"


class ExperienceLevel(StrEnum):
    """使用/经历层级：第一手 / 间接转述 / 未知（营销用独立 ``is_marketing`` 标记）。"""

    FIRST_HAND = "first_hand"
    SECOND_HAND = "second_hand"
    UNKNOWN = "unknown"


class PersonHandle(BaseModel):
    """某人在一个平台上的身份。"""

    platform: Platform
    external_id: str = ""
    handle: str = ""
    url: str | None = None


class PersonProfile(BaseModel):
    """一个人（人物记录）：已确认公开身份 + 角色 + 当前关注事项。轻量、不跨平台强合并。"""

    person_id: str  # person_<sha256[:12]>
    display_name: str = ""
    role: str = ""  # 角色：solo founder / 使用者 / 实施者 / 观察者
    handles: list[PersonHandle] = Field(default_factory=list)
    current_focus: str = ""  # 当前关注事项
    source_refs: list[str] = Field(default_factory=list)  # artifact_id 或 note_id
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvidenceQuote(BaseModel):
    """一条可追溯证据（原文或摘要）；默认 attribution=RAW。"""

    source_ref: str  # artifact_id 或 note_id
    url: str = ""
    quote: str = ""  # 原文或必要摘要
    author: str = ""
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    experience: ExperienceLevel = ExperienceLevel.UNKNOWN
    is_marketing: bool = False
    attribution: Attribution = Attribution.RAW


class TaskEvent(BaseModel):
    """一次实践事件（核心新实体）：人物的任务与判断，缺项留空。"""

    event_id: str = ""  # evt_<sha256[:12]>（由服务按内容派生）
    person_id: str
    task: str = ""  # 当前任务：最近一次做了什么
    expectation: str = ""  # 预期与判断标准
    trust_boundary: str = ""  # 信任边界：哪些交出去、哪些必须检查
    problem: str = ""  # 问题
    workaround: str = ""  # 补救/人工接管/绕过
    cognitive_change: str = ""  # 认知变化：哪次经历改变了看法
    author_contribution: str = ""  # 作者可贡献点（无材料则留空）
    occurred_at: datetime | None = None
    source_refs: list[str] = Field(default_factory=list)
    quotes: list[EvidenceQuote] = Field(default_factory=list)
    attribution: Attribution = Attribution.RAW
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommitmentStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


class Commitment(BaseModel):
    """一次约定/承诺（含约定时间）。"""

    text: str = ""
    due_at: datetime | None = None
    status: CommitmentStatus = CommitmentStatus.OPEN


class ContactDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Contact(BaseModel):
    """一条实际交流记录；``is_draft=True`` 表示草稿/模拟，不进入实际交流历史。"""

    contact_id: str = ""  # contact_<sha256[:12]>（由服务按内容派生）
    person_id: str
    occurred_at: datetime
    channel: str = ""  # 平台/渠道
    participants: list[str] = Field(default_factory=list)
    summary: str = ""  # 内容摘要
    feedback: str = ""  # 对方反馈
    commitments: list[Commitment] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    direction: ContactDirection = ContactDirection.OUTBOUND
    is_draft: bool = False  # 草稿/模拟 ≠ 实际交流
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NextStepStatus(StrEnum):
    PROPOSED = "proposed"
    DONE = "done"
    PAUSED = "paused"
    DROPPED = "dropped"


class NextStep(BaseModel):
    """下一步：观察/交流建议 + 触发条件 + 依据 + 状态（不把建议标成已执行）。"""

    next_id: str = ""  # next_<sha256[:12]>（由服务按内容派生）
    person_id: str
    suggestion: str = ""  # 观察/交流建议
    trigger: str = ""  # 触发条件
    basis_refs: list[str] = Field(default_factory=list)  # 依据（引用事件/交流/承诺）
    status: NextStepStatus = NextStepStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BriefingChannel(StrEnum):
    PUBLIC_REPLY = "public_reply"
    PRIVATE = "private"
    OBSERVE = "observe"


class BriefingEntry(BaseModel):
    """周度简报单条：一个人 + 一个切入点 + 依据 + 未知项。"""

    person_id: str
    entry_point: str = ""  # 一个主要切入点
    why_now: str = ""  # 为什么现在交流 + 依据
    contribution: str = ""  # 作者可提供的具体价值（无材料则空）
    question: str = ""  # 最想了解的一个问题
    channel: BriefingChannel = BriefingChannel.OBSERVE  # 公开回复/私下/观察
    facts_vs_guesses: str = ""  # 哪些是事实、哪些是猜测
    basis_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class Briefing(BaseModel):
    """一份周度简报（最多 3 人，不足时少报）。"""

    briefing_id: str = ""  # brief_<sha256[:12]>（由简报服务按内容派生）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fingerprint: str = ""  # 输入指纹（缓存/replay 用）
    entries: list[BriefingEntry] = Field(default_factory=list)
    insufficient: bool = False  # 无值得报告的人 → 空简报
    info_gaps: str = ""


class ImportedNote(BaseModel):
    """手工导入的原始材料（公开链接或经允许的交流摘要）。"""

    note_id: str = ""  # note_<sha256[:12]>（由导入服务按内容派生）
    kind: Literal["public_link", "summary"] = "public_link"
    url: str = ""
    title: str = ""
    text: str = ""  # 摘要或粘贴的正文
    author: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PersonTraceback(BaseModel):
    """一个人物的完整回溯投影：人物 → 事件 → 交流 → 下一步。"""

    person: PersonProfile
    events: list[TaskEvent] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)
