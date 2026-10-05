"""ActiveProblem 领域模型：活跃问题（≤3 open，学习闭环的脊柱）。"""

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ActiveProblem(BaseModel):
    """一个正在研究的活跃问题。open 数量 ≤ 3，在 ``ProblemService.add`` 强制。"""

    id: str
    title: str
    why_it_matters: str = ""
    status: Literal["open", "closed"] = "open"
    attempt_ids: list[str] = Field(default_factory=list)
    closed_reason: str = ""
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


def problem_id_for(title: str) -> str:
    """内容寻址 id：相同 title → 相同 id（幂等）。"""
    return f"problem_{hashlib.sha256(title.strip().encode('utf-8')).hexdigest()[:8]}"
