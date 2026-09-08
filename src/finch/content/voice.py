"""作者声音画像（Task 6）：正反样本 + 风格偏好，供 VoiceChecker 与 ``finch voice`` CLI 管理。

VoiceProfile 是纯本地配置（YAML 文件），不参与发布流程。缺省文件时加载空画像；
CLI 只做本地 profile 管理（idempotent，不自动发布）。
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ApprovedExample(BaseModel):
    """已人工批准、可作为声音参照的样例。

    ``text`` 是最终文本（用户亲写或人工修订后的最终版本）；``source`` 标记来源
    （``draft`` = 从草稿批准，``user_text`` = 用户亲写）；``original_draft`` 保存模型
    初稿、``diff`` 保存初稿 → 最终文本的 unified diff，供提取稳定偏好候选与审计样本来源。
    """

    id: str
    text: str
    source: str = "draft"
    original_draft: str | None = None
    diff: str | None = None


class RejectedExample(BaseModel):
    """被人工拒绝的样例（id=草稿 id，reason=拒绝理由）。"""

    id: str
    reason: str


class VoiceProfile(BaseModel):
    """作者声音画像。所有字段默认空：空画像意味着「无可检查项」，VoiceChecker 直接通过。"""

    preferred_patterns: list[str] = Field(default_factory=list)
    avoid_phrases: list[str] = Field(default_factory=list)
    rhythm_rules: list[str] = Field(default_factory=list)
    approved_examples: list[ApprovedExample] = Field(default_factory=list)
    rejected_examples: list[RejectedExample] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """画像是否为空（没有任何声音约束）。"""
        return not (
            self.preferred_patterns
            or self.avoid_phrases
            or self.rhythm_rules
            or self.approved_examples
            or self.rejected_examples
        )


def load_voice_profile(path: Path | str) -> VoiceProfile:
    """从 YAML 加载画像；文件不存在或为空时返回默认空画像。"""
    target = Path(path)
    if not target.exists():
        return VoiceProfile()
    data = yaml.safe_load(target.read_text()) or {}
    if not isinstance(data, dict):
        return VoiceProfile()
    return VoiceProfile(**data)


def save_voice_profile(profile: VoiceProfile, path: Path | str) -> None:
    """把画像写回 YAML（幂等，覆盖写）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(
            profile.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )
    )


class VoiceUpdateProposal(BaseModel):
    """从样例 diff 提取的稳定偏好候选（只读，不写画像；由用户确认后才写入）。"""

    avoid_phrases: list[str] = Field(default_factory=list)      # 用户删掉/改掉的表达
    preferred_patterns: list[str] = Field(default_factory=list)  # 用户改向的表达


def propose_voice_updates(profile: VoiceProfile) -> VoiceUpdateProposal:
    """从 approved examples 的 diff 提取稳定偏好候选（确定性，无 LLM）。

    把 unified diff 中被删除的行（``- ``）视为「用户删掉/改掉的表达」候选、被添加的行
    （``+ ``）视为「用户偏好」候选，去重排序后返回。仅作候选，不写画像。
    """
    avoid: set[str] = set()
    prefer: set[str] = set()
    for example in profile.approved_examples:
        if not example.diff:
            continue
        for line in example.diff.splitlines():
            if line.startswith(("---", "+++")):
                continue
            if line.startswith("-"):
                stripped = line[1:].strip()
                if stripped:
                    avoid.add(stripped)
            elif line.startswith("+"):
                stripped = line[1:].strip()
                if stripped:
                    prefer.add(stripped)
    return VoiceUpdateProposal(
        avoid_phrases=sorted(avoid),
        preferred_patterns=sorted(prefer),
    )
