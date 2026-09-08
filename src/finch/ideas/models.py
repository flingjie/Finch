"""Idea 候选流领域模型（Skill 架构领域核心）。

统一 `IdeaCandidate` 契约：Skill 层（commit-to-idea / search-to-idea）产出同一个
`IdeaCandidate`，领域服务再把它持久化到 ``ContentJob``（不新增 Idea 表）。

契约不变量（plan §3）：
- ``core_point`` 是单一中心主张；
- ``source_refs`` 可追溯来源；
- 自动生成立场一律 ``proposed``，只有用户输入或命中已批准 fingerprint 才是 ``confirmed``；
- Search 来源不得写成亲历；
- ``boundaries.known/inferred/unknown`` 传递到 Draft 校验。
"""

from typing import Literal

from pydantic import BaseModel, Field

from finch.content.jobs import AuthorPosition


class SourceRef(BaseModel):
    """来源引用：类型 + 引用标识 + 一句话摘要（保证可追溯）。"""

    type: Literal["commit", "pr", "issue", "test", "post", "paper", "conversation"]
    ref: str
    summary: str


class IdeaBoundaries(BaseModel):
    """证据边界：已知 / 推断 / 未知，传递到 Draft 校验（不得越界陈述）。"""

    known: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


class IdeaGenerator(BaseModel):
    """生成器元数据：哪个 Skill 与版本产出了该候选。"""

    skill: str
    version: str


class IdeaCandidate(BaseModel):
    """Idea 候选：Skill 层产出的统一输入契约。"""

    id: str
    origin: Literal["commit", "search", "user", "conversation"]
    core_point: str
    observation: str = ""
    reader_problem: str
    why_worth_saying: str
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    author_position: AuthorPosition
    source_refs: list[SourceRef]
    boundaries: IdeaBoundaries
    recommended_format: Literal["original", "reply", "thread"]
    generator: IdeaGenerator
