"""Gate 交互层：确认选择器与逐字段立场编辑（有 TTY 时调用）。"""

import typer

from finch.evidence.models import EvidenceCard

from .models import InputAction, InputRequest, ProposedPosition
from .render import render_confirm_card, render_evidence


def edit_position_inline(proposed: ProposedPosition) -> ProposedPosition:
    """逐字段编辑立场；直接回车（空输入）保留原值。"""
    typer.echo("编辑你的立场（直接回车表示保留原内容）")
    claim = typer.prompt("主张", default=proposed.claim or "") or proposed.claim
    decision = typer.prompt("方案", default=proposed.decision or "") or proposed.decision
    tradeoff = typer.prompt("取舍", default=proposed.tradeoff or "") or proposed.tradeoff
    change_mind_if = (
        typer.prompt("改变决定的条件", default=proposed.change_mind_if or "")
        or proposed.change_mind_if
    )
    return ProposedPosition(
        claim=claim,
        decision=decision,
        tradeoff=tradeoff,
        change_mind_if=change_mind_if or None,
    )


def select_action(request: InputRequest, cards: list[EvidenceCard]) -> InputAction | None:
    """渲染确认卡 + 菜单并读取选择；``None`` 表示保存进度并退出（q）。"""
    typer.echo(render_confirm_card(request, cards))
    typer.echo("")
    typer.echo("如何处理？")
    typer.echo("")
    typer.echo("❯ [Enter] 确认立场并继续生成草稿")
    typer.echo("  [e]    编辑立场")
    typer.echo("  [s]    跳过这个主题")
    typer.echo("  [d]    查看完整依据")
    typer.echo("  [q]    保存进度并退出")
    while True:
        choice = typer.prompt("> ", default="").strip().lower()
        if choice == "":
            return InputAction.CONFIRM
        if choice == "e":
            return InputAction.EDIT
        if choice == "s":
            return InputAction.SKIP
        if choice == "d":
            typer.echo(render_evidence(cards))
            continue
        if choice == "q":
            return None
        typer.echo(f"无效选择：{choice}")
