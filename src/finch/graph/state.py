"""Graph 状态机：spec 6.1。"""

from enum import StrEnum


class GraphState(StrEnum):
    # 主链（spec 6.1）
    CREATED = "CREATED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    COMMITS_SYNCED = "COMMITS_SYNCED"
    EVENTS_EXTRACTED = "EVENTS_EXTRACTED"
    TWEETS_COLLECTED = "TWEETS_COLLECTED"
    CANDIDATES_RANKED = "CANDIDATES_RANKED"
    EVIDENCE_MATCHED = "EVIDENCE_MATCHED"
    DRAFTED = "DRAFTED"
    CRITIQUED = "CRITIQUED"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    APPROVED = "APPROVED"
    SKIPPED = "SKIPPED"
    PUBLISHED = "PUBLISHED"
    MEASURED = "MEASURED"
    COMPLETED = "COMPLETED"
    # 异常状态
    NEEDS_INPUT = "NEEDS_INPUT"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {GraphState.COMPLETED, GraphState.FAILED, GraphState.BLOCKED}

    @property
    def is_abnormal(self) -> bool:
        return self in {
            GraphState.NEEDS_INPUT,
            GraphState.PARTIALLY_COMPLETED,
            GraphState.BLOCKED,
            GraphState.FAILED,
        }


_MAIN_CHAIN: list[GraphState] = [
    GraphState.CREATED,
    GraphState.PREFLIGHT_PASSED,
    GraphState.COMMITS_SYNCED,
    GraphState.EVENTS_EXTRACTED,
    GraphState.TWEETS_COLLECTED,
    GraphState.CANDIDATES_RANKED,
    GraphState.EVIDENCE_MATCHED,
    GraphState.DRAFTED,
    GraphState.CRITIQUED,
    GraphState.WAITING_FOR_REVIEW,
    GraphState.APPROVED,
    GraphState.PUBLISHED,
    GraphState.MEASURED,
    GraphState.COMPLETED,
]


def can_transition(src: GraphState, dst: GraphState) -> bool:
    """异常状态可从任意非终态迁入；其余按主链或审核分叉前进。"""
    if src.is_terminal:
        return False
    if dst.is_abnormal:
        return True
    if src == GraphState.WAITING_FOR_REVIEW and dst in {GraphState.APPROVED, GraphState.SKIPPED}:
        return True
    return advance(src) == dst


def advance(state: GraphState) -> GraphState:
    """沿主链前进一步；终态/异常态原样返回。"""
    if state.is_terminal or state.is_abnormal:
        return state
    if state == GraphState.SKIPPED:
        return GraphState.PUBLISHED
    return _MAIN_CHAIN[_MAIN_CHAIN.index(state) + 1]
