"""MethodCardChecker：方法卡完整性检查（规范 §7.2「方法卡：输入、步骤、输出、限制完整」）。

确定性结构检查：正文须覆盖四个必备要素（输入 / 步骤 / 输出 / 限制），缺一即 medium
失败并给出补齐指令。不要求长篇论证，也不强制绑定个人项目。
"""

from finch.content.checkers.base import CheckContext, Checker, CheckResult

# 四个必备要素 → 命中标记（大小写不敏感子串匹配）。
_REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "输入": ("输入", "前置", "需要准备", "input", "prerequisit"),
    "步骤": ("步骤", "step", "做法", "how to"),
    "输出": ("输出", "结果", "如何判断", "output", "result"),
    "限制": ("限制", "局限", "不适用", "边界", "limitation", "caveat"),
}


def _missing_sections(body: str) -> list[str]:
    lowered = body.lower()
    missing: list[str] = []
    for name, markers in _REQUIRED_SECTIONS.items():
        if not any(marker.lower() in lowered for marker in markers):
            missing.append(name)
    return missing


class MethodCardChecker(Checker):
    """检测方法卡是否覆盖输入 / 步骤 / 输出 / 限制四个必备要素。"""

    name: str = "method_card"

    def check(self, ctx: CheckContext) -> CheckResult:
        missing = _missing_sections(ctx.draft.body)
        if not missing:
            return CheckResult(checker=self.name, passed=True, severity="low")
        return CheckResult(
            checker=self.name,
            passed=False,
            severity="medium",
            locations=["body"],
            issues=[f"missing method card section: {name}" for name in missing],
            rewrite_instructions=[
                "add the missing method card sections: " + "、".join(missing)
            ],
        )
