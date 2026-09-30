"""ResponsivenessChecker：回应型草稿是否为对方留下回应空间（连接优先 Phase 4）。

只对 reply / quote / dm 场景生效；原创 / 短帖 / thread / do_not_publish 不检查。
确定性启发式：正文必须含一个**实质性**问句（不是补一句「你怎么看」式的机械邀请）才通过；
问号 / 邀请短语只是提示，不作为「能引发交流」的通过标准。
对应执行计划「内容 Critic 检查……是否为对方留下回应空间」。
"""

import re

from finch.content.checkers.base import CheckContext, Checker, CheckResult
from finch.content.models import RecommendedFormat

# 需要「为对方留下回应空间」的场景（回应型内容）。
_RESPONSE_FORMATS = frozenset({
    RecommendedFormat.REPLY,
    RecommendedFormat.QUOTE,
    RecommendedFormat.DM,
})

# 仅剩「你怎么看 / what do you think」这类机械邀请的短语，不算实质回应空间。
_GENERIC_INVITES = (
    "what do you think",
    "what would you do",
    "your take",
    "any thoughts",
    "thoughts",
    "agree",
    "disagree",
    "怎么样",
    "你怎么看",
    "你觉得呢",
    "同意吗",
    "如何",
    "欢迎",
)


def _question_clauses(body: str) -> list[str]:
    """按问号切出问句，去掉空段。"""
    clauses: list[str] = []
    for match in re.finditer(r"([^?？]*)[?？]", body):
        clause = match.group(1).strip()
        if clause:
            clauses.append(clause)
    return clauses


def _is_substantive_question(clause: str) -> bool:
    """问句是否实质：不是只补一句机械邀请。"""
    core = clause.strip().lower()
    # 「Replays helped here — what do you think?」中真正的问题部分是后半句。
    for sep in ("—", "–", ":", ";", ","):
        if sep in core:
            tail = core.rsplit(sep, 1)[-1].strip()
            if tail and any(tail == g or tail.startswith(g) for g in _GENERIC_INVITES):
                core = tail
                break
    if any(core == g for g in _GENERIC_INVITES):
        return False
    return len(core) >= 3


def _leaves_room_for_response(body: str) -> bool:
    """确定性判断：正文是否含实质性问句（机械邀请不算）。"""
    clauses = _question_clauses(body)
    return any(_is_substantive_question(c) for c in clauses)


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
