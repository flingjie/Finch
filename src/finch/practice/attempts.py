"""PracticeAttempt 领域模型：实践尝试原始素材（问题/尝试/观察/未知/下一步/结果）。

独立于表达训练的 ``PracticeSession`` 与证据库 ``PracticeItem``；CLI 用 ``finch attempts``。
本文件只放模型，不 import repository（避免与 ``storage/repositories.py`` 循环导入）。
"""

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PracticeAttempt(BaseModel):
    """一次实践尝试：问题 → 尝试 → 观察 → 未知 → 下一步；有结果时在 verify 回填。

    ``observation`` 是实际观察（可 ``observed``）；``unknown`` / ``next_step`` 明确是
    待验证（``unverified``）——这条证据边界是 idea 提炼时「不把读到的方法写成亲历」的依据。
    """

    id: str
    problem_id: str | None = None
    problem: str
    attempt: str
    observation: str
    unknown: str = ""
    next_step: str = ""
    result: str = ""
    status: Literal["open", "verified", "closed"] = "open"
    source_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def attempt_id_for(problem: str, attempt: str, observation: str) -> str:
    """内容寻址 id：相同 (problem+attempt+observation) → 相同 id（幂等）。"""
    raw = "\n".join([problem, attempt, observation])
    return f"attempt_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"
