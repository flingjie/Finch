"""确定性聚合器（C7）：把逐项 CheckResult 汇总成单一决策。

决策完全由规则计算，绝不信任模型输出的 ``passed``。四种结果字符串稳定：
"pass" | "rewrite" | "reject" | "needs_input"。
"""

from enum import StrEnum

from finch.content.checkers.base import CheckResult


class AggregateOutcome(StrEnum):
    """聚合结果。成员即字符串值，可直接用于 JSON 与比较。"""

    PASS = "pass"
    REWRITE = "rewrite"
    REJECT = "reject"
    NEEDS_INPUT = "needs_input"


def aggregate_checks(checks: list[CheckResult]) -> str:
    """按优先级从高到低聚合：

    1. 任一 ``hard_fail`` 且未通过 → ``reject``（不可被平均分掩盖）。
    2. 任一 ``high`` 且 ``requires_human_input`` 且未通过 → ``needs_input``。
    3. 任一未通过 → ``rewrite``。
    4. 全部通过 → ``pass``。
    """
    if any(c.severity == "hard_fail" and not c.passed for c in checks):
        return AggregateOutcome.REJECT
    if any(
        c.severity == "high" and c.requires_human_input and not c.passed for c in checks
    ):
        return AggregateOutcome.NEEDS_INPUT
    if any(not c.passed for c in checks):
        return AggregateOutcome.REWRITE
    return AggregateOutcome.PASS
