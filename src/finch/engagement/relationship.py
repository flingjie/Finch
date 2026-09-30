"""关系评分的确定性模型：同行价值六维特征 + 汇总 total（total 只在代码里算）。

旧的关系评分管线（peer_aggregation / rank_peers / compute_peer_value）已由 people-first 的
``peers/scoring.py`` 取代；本模块只保留 daily 发现链路仍用的 ``PeerValue`` 模型。
"""

from pydantic import BaseModel, Field


class PeerValue(BaseModel):
    """确定性的同行价值：六维特征 + 汇总 total（total 只在代码里算）。"""

    topic_overlap: float = Field(ge=0, le=1)
    practical_depth: float = Field(ge=0, le=1)
    contribution_space: float = Field(ge=0, le=1)
    continuity_potential: float = Field(ge=0, le=1)
    repetition_penalty: float = Field(ge=0, le=1)
    promotion_risk: float = Field(ge=0, le=1)
    total: float
    reasons: list[str]
