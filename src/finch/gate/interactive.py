"""Gate 交互层：确认选择器与逐字段立场编辑（有 TTY 时调用）。"""

import typer

from finch.evidence.models import EvidenceCard

from .models import InputAction, InputRequest, ProposedPosition
from .render import render_confirm_card, render_evidence


def edit_position_inline(proposed: ProposedPosition) -> ProposedPosition:
    """逐字段编辑立场；直接回车（空输入）保留原值；不完整时提示补全并重新询问。"""
    typer.echo("编辑你的立场（直接回车表示保留原内容）")
    edited = _collect_position(proposed)
    while not edited.complete():
        typer.echo("立场不完整，请补充主张/方案/取舍")
        edited = _collect_position(edited)
    return edited


def _collect_position(current: ProposedPosition) -> ProposedPosition:
    claim = typer.prompt("主张", default=current.claim or "") or current.claim
    decision = typer.prompt("方案", default=current.decision or "") or current.decision
    tradeoff = typer.prompt("取舍", default=current.tradeoff or "") or current.tradeoff
    change_mind_if = (
        typer.prompt("改变决定的条件", default=current.change_mind_if or "")
        or current.change_mind_if
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
