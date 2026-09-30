"""互动轨道的轻量结果模型：同行及其确定性 peer_value。

旧互动轨道流程（搜索 → 评分 → 机会 → 提案）已由 ``finch.opportunities`` 聚合取代；
本模块只保留 daily 发现链路仍需的 ``RankedPeer`` 与 ``EngagementRunResult``。
"""

from typing import Literal

from pydantic import BaseModel, Field

from finch.engagement.relationship import PeerValue
from finch.peers.models import PeerProfile


class RankedPeer(BaseModel):
    """一个同行及其确定性 peer_value。"""

    profile: PeerProfile
    value: PeerValue


class EngagementRunResult(BaseModel):
    """互动轨道单轮结果（发现快照 + 粗筛同行子集）。"""

    run_id: str
    posts_found: int
    peers: list[RankedPeer] = Field(default_factory=list)
    status: Literal["succeeded", "empty", "failed"]
    summary: str
    context_fingerprint: str = ""
    source_coverage: dict[str, object] = Field(default_factory=dict)
