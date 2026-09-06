"""Gate 交互层渲染：确认卡、证据列表与立场 diff（纯函数，无 IO）。"""

import difflib

from finch.evidence.models import EvidenceCard

from .models import InputRequest, ProposedPosition


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


def render_input_request(request: InputRequest, cards: list[EvidenceCard]) -> str:
    """渲染作者立场确认卡。"""
    pos = request.proposed_position
    lines = [
        "Finch 需要你确认一个作者立场",
        "",
        "主题",
        request.topic or "(none)",
        "",
        "为什么值得现在写",
        request.why_now or "(none)",
        "",
        "建议立场",
        f"判断：{pos.claim or '(未填)'}",
        f"决策：{pos.decision or '(未填)'}",
        f"取舍：{pos.tradeoff or '(未填)'}",
    ]
    if pos.change_mind_if:
        lines.append(f"什么会改变判断：{pos.change_mind_if}")
    if request.questions:
        lines.append("")
        lines.append("待回答问题")
        lines.extend(f"- {q}" for q in request.questions)
    lines.extend(["", f"证据：{len(cards)} 张 Evidence Card", "", "请选择："])
    lines.extend(
        [
            "1. 确认并继续",
            "2. 修改后继续",
            "3. 跳过这个主题，尝试下一个",
            "4. 今天不写",
            "5. 查看完整证据",
        ]
    )
    return "\n".join(lines)
