"""Gate 交互层渲染：确认卡、摘要、紧凑结果与状态文案（纯函数，无 IO）。"""

import difflib

from finch.content.jobs import ContentJob
from finch.evidence.models import EvidenceCard

from .models import InputAction, InputRequest, ProposedPosition

_STATE_LABELS = {
    "COMPLETED": "已完成",
    "NEEDS_INPUT": "等待你的确认",
    "SKIPPED": "已跳过",
    "STOPPED": "已保存并退出",
    "FAILED": "运行失败",
    "BLOCKED": "运行失败",
}


def state_label(state: str) -> str:
    """把内部 GraphState 映射为面向用户的文案；未知名回退原值。"""
    return _STATE_LABELS.get(state, state)


def _position_lines(position: ProposedPosition) -> list[str]:
    return [
        f"claim: {position.claim}",
        f"decision: {position.decision}",
        f"tradeoff: {position.tradeoff}",
        f"change_mind_if: {position.change_mind_if or ''}",
    ]


def render_position_diff(before: ProposedPosition, after: ProposedPosition) -> str:
    """返回 before → after 的 unified diff 文本。"""
    return "\n".join(
        difflib.unified_diff(_position_lines(before), _position_lines(after), lineterm="")
    )


def render_evidence(cards: list[EvidenceCard]) -> str:
    """渲染证据卡清单（查看完整证据动作）。"""
    if not cards:
        return "（无证据）"
    return "\n".join(f"- {card.claim} [{card.confidence.value}]" for card in cards)


def render_confirm_card(request: InputRequest, cards: list[EvidenceCard]) -> str:
    """渲染作者立场确认卡（面向用户，隐藏内部概念）。"""
    pos = request.proposed_position
    lines = [
        "主题",
        request.topic or "(无主题)",
        "",
        "建议立场",
        f"主张：{pos.claim or '(未填)'}",
        f"方案：{pos.decision or '(未填)'}",
        f"取舍：{pos.tradeoff or '(未填)'}",
    ]
    if pos.change_mind_if:
        lines += ["", "什么情况会改变这个决定？", pos.change_mind_if]
    if request.questions:
        lines += ["", "待回答问题"]
        lines += [f"- {q}" for q in request.questions]
    lines += ["", f"依据：{len(cards)} 张证据卡"]
    return "\n".join(lines)


def render_daily_summary(
    *, posts_found: int | None, engagement_drafts: int, pending_original: int
) -> str:
    """Daily 决策前摘要：互动内容（可选）+ 原创内容。``posts_found`` 为 None 时跳过互动区块。"""
    lines = ["✓ 今日分析已完成", ""]
    if posts_found is not None:
        lines += [
            "互动内容",
            f"  扫描 {posts_found} 条帖子",
            f"  生成 {engagement_drafts} 条互动草稿，等待审核",
            "",
        ]
    lines += [
        "原创内容",
        f"  找到 {pending_original} 个值得展开的主题，需要确认你的立场",
    ]
    return "\n".join(lines)


def render_compact_resolve(request: InputRequest, *, engagement_drafts: int = 0) -> str:
    """非 TTY 紧凑结果：说明分析已完成 + 推荐动作 + 其他命令。"""
    lines = [
        "Daily 分析完成，现有 1 项需要确认。",
        "",
        f"主题：{request.topic or '(无主题)'}",
        "推荐：确认当前立场并继续生成草稿",
        "",
        "  uv run finch run resolve --confirm",
        "",
        "其他操作：",
        "  uv run finch run resolve --edit",
        '  uv run finch run resolve --skip --reason "..."',
        "  uv run finch run resolve --stop",
        "",
        "查看完整信息：",
        "  uv run finch run resolve --json",
    ]
    if engagement_drafts:
        lines += ["", f"另外有 {engagement_drafts} 条互动草稿等待人工审核。"]
    return "\n".join(lines)


def render_outcome(action: InputAction | None) -> str:
    """一次决策后的人性化结果行。"""
    if action is InputAction.CONFIRM:
        return "✓ 已确认你的立场"
    if action is InputAction.EDIT:
        return "✓ 已更新你的立场"
    if action is InputAction.SKIP:
        return "✓ 已跳过这个主题"
    if action is InputAction.STOP:
        return "✓ 已保存并退出"
    return "✓ 已保存进度并退出"


def render_produced(*, engagement_drafts: int, original_drafts: int) -> str:
    """决策 + 恢复完成后的今日产出块。"""
    return "\n".join([
        "✓ 原创草稿已生成",
        "",
        "今日产出",
        f"  互动草稿：{engagement_drafts} 条，等待审核",
        f"  原创草稿：{original_drafts} 条，等待审核",
    ])


def build_ask_reasons(job: ContentJob | None, critic_reports: list[dict]) -> list[str]:
    """must_ask 信号：position_conflict（change_mind_if 非空）或 safety_risk（safety 失败）。"""
    reasons: list[str] = []
    if (
        job is not None
        and job.author_position is not None
        and job.author_position.change_mind_if
    ):
        reasons.append("position_conflict")
    if critic_reports:
        for check in critic_reports[-1].get("checks", []):
            if check.get("checker") == "safety" and not check.get("passed", True):
                reasons.append("safety_risk")
                break
    return reasons


def build_risks(critic_reports: list[dict]) -> list[str]:
    """返回最后一轮 Critic 的失败检查项（checker: issues）。"""
    if not critic_reports:
        return []
    return [
        f"{c['checker']}: {c.get('issues') or 'failed'}"
        for c in critic_reports[-1].get("checks", [])
        if not c.get("passed", True)
    ]
