"""ResponsivenessChecker：回应型草稿是否为对方留下回应空间（连接优先 Phase 4）。

只对 reply / quote / dm 场景生效；原创 / 短帖 / thread / do_not_publish 不检查。
确定性启发式：正文含问句或明确邀请则通过；否则提示改写为「抛回一个问题 / 邀请反例」。
对应执行计划「内容 Critic 检查……是否为对方留下回应空间」。
"""

from finch.content.checkers.base import CheckContext, Checker, CheckResult
from finch.content.models import RecommendedFormat

# 需要「为对方留下回应空间」的场景（回应型内容）。
_RESPONSE_FORMATS = frozenset({
    RecommendedFormat.REPLY,
    RecommendedFormat.QUOTE,
    RecommendedFormat.DM,
})

# 明确邀请 / 提问的短语（英文 + 中文；问号单独判断）。
_INVITE_PHRASES = (
    "what do you think",
    "your take",
    "thoughts",
    "agree",
    "disagree",
    "counterexample",
    "how about",
    "would love",
    "happy to hear",
    "怎么样",
    "你怎么看",
    "欢迎",
    "请问",
    "你会",
    "如何",
)


def _leaves_room_for_response(body: str) -> bool:
    """确定性判断：正文是否含问句或明确邀请。"""
    if "?" in body or "？" in body:
        return True
    lowered = body.lower()
    return any(phrase in lowered for phrase in _INVITE_PHRASES)


class ResponsivenessChecker(Checker):
    """检查回应型草稿是否为对方留下回应空间。"""

    name: str = "responsiveness"

    def check(self, ctx: CheckContext) -> CheckResult:
        fmt = ctx.job.recommended_format if ctx.job is not None else None
        if fmt not in _RESPONSE_FORMATS:
            # 非回应型内容不检查「回应空间」。
            return CheckResult(checker=self.name, passed=True, severity="low")

        body = ctx.draft.body.strip()
        if not body or _leaves_room_for_response(body):
            return CheckResult(checker=self.name, passed=True, severity="low")

        return CheckResult(
            checker=self.name,
            passed=False,
            severity="medium",
            locations=["body"],
            issues=["reply does not leave room for a response"],
            rewrite_instructions=[
                "end the reply with a question or an explicit invitation to respond"
            ],
        )
