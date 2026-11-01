"""问题发现领域模型：Problem / ProblemEvidence / ProblemScenario。

与旧 ``problems``（个人 ActiveProblem）/ ``opportunities`` / ``products`` / ``relationships``
领域隔离：不 import 其模型，只共享 ``sources`` 的原始工件与存储层。事实（``evidence``）与
假设（``hypotheses``）/未知（``unknowns``）在结构上分开；LLM 只做语义抽取，不写任何总分。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EvidenceLevel(StrEnum):
    """证据分级：E0 自己推测 / E1 单条公开讨论 / E2 多个独立用户 / E3 真实访谈 / E4 已验证。"""

    E0 = "e0"
    E1 = "e1"
    E2 = "e2"
    E3 = "e3"
    E4 = "e4"


class ExperienceLevel(StrEnum):
    """使用/经历层级：第一手 / 间接转述 / 未知（营销用独立 ``is_marketing`` 标记）。"""

    FIRST_HAND = "first_hand"
    SECOND_HAND = "second_hand"
    UNKNOWN = "unknown"


class ProblemStatus(StrEnum):
    """问题状态机（P0 只写 NEW，其余留 P1+）。"""

    NEW = "new"
    EVIDENCE_GATHERING = "evidence_gathering"
    INVESTIGATING = "investigating"
    INTERVIEWING = "interviewing"
    EXPERIMENT_READY = "experiment_ready"
    TESTING = "testing"
    VALIDATED = "validated"
    PARKED = "parked"
    REJECTED = "rejected"


class Admission(StrEnum):
    """写入策略，与 ProblemStatus 并行：草稿 / 隔离 / 已核实 / 已接受。"""

    DRAFT = "draft"
    QUARANTINED = "quarantined"
    VERIFIED = "verified"
    ACCEPTED = "accepted"


class ProblemScenario(BaseModel):
    """问题发生场景：任务 / 触发 / 后果。"""

    task: str = ""
    trigger: str = ""
    consequence: str = ""


class ProblemEvidence(BaseModel):
    """一条支撑问题的证据（引用原始工件/导入讨论；摘录必须可回溯）。"""

    source_ref: str = ""  # artifact_id 或 discussion_id
    url: str = ""
    quote: str = ""  # 原文摘录
    claim: str = ""  # 用户主张
    evidence_level: EvidenceLevel = EvidenceLevel.E1
    experience: ExperienceLevel = ExperienceLevel.UNKNOWN
    is_marketing: bool = False
    author: str = ""
    author_id: str = ""
    author_identity_confidence: Literal["unknown", "stated", "verified"] = "unknown"


class Problem(BaseModel):
    """一个问题卡片（Problem Inbox 主对象）。"""

    problem_id: str = ""  # prob_<sha256[:12]>，由标题内容派生
    title: str = ""
    user_type: str = ""
    scenario: ProblemScenario = Field(default_factory=ProblemScenario)
    current_solutions: list[str] = Field(default_factory=list)
    evidence: list[ProblemEvidence] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)  # 假设（无来源）
    unknowns: list[str] = Field(default_factory=list)  # 未知/缺口
    next_action: str = ""  # interview / observe（P0 只存不执行）
    source_refs: list[str] = Field(default_factory=list)  # evidence 的 source_ref 并集
    status: ProblemStatus = ProblemStatus.NEW
    admission: Admission = Admission.QUARANTINED
    admission_reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def _legacy_admission(cls, data: object) -> object:
        """旧载荷没有 admission 时，按 evidence 是否非空推断写入策略。

        新鲜构造未传入 evidence（也未显式给 admission）时保持字段默认：
        quarantined，且 admission_reason 为空。
        """
        if not isinstance(data, dict):
            return data
        if "admission" in data and data["admission"] not in (None, ""):
            return data
        # 构造函数只传 problem_id/title 时没有 evidence 键；不要写成 legacy 推断。
        if "evidence" not in data and "admission" not in data:
            return data
        evidence = data.get("evidence") or []
        data = {**data, "admission": "verified" if evidence else "quarantined"}
        if not data.get("admission_reason"):
            data["admission_reason"] = "has_valid_evidence" if evidence else "no_valid_evidence"
        return data


class ImportedDiscussion(BaseModel):
    """手工导入的原始材料（GitHub 走 RawArtifact，手工走此模型）。"""

    discussion_id: str = ""  # disc_<sha256[:12]>（由 url+text 内容派生）
    kind: Literal["url", "repo", "text"] = "url"
    url: str = ""
    title: str = ""
    text: str = ""
    author: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiscoveryRun(BaseModel):
    """一次问题发现运行的持久化投影（指纹缓存/replay 用）。"""

    run_id: str = ""  # run_<YYYYMMDDHHMMSS>
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fingerprint: str = ""  # 输入指纹（来源集合 + 版本）
    problem_ids: list[str] = Field(default_factory=list)
