"""Finch CLI：relationships 子命令（人物 / 事件 / 交流 / 周度简报）。"""

import json
from datetime import UTC, datetime
from typing import cast

import typer

from finch.codex.runner import CodexRunner
from finch.llm.openai_compatible import create_runner
from finch.relationships.briefing import generate_briefing
from finch.relationships.ids import person_id_for
from finch.relationships.importing import import_public_link as rel_import_link
from finch.relationships.importing import import_summary as rel_import_summary
from finch.relationships.models import (
    Briefing,
    Contact,
    ContactDirection,
    NextStep,
    PersonProfile,
    PersonTraceback,
    TaskEvent,
)
from finch.relationships.repository import (
    BriefingRepository,
    ContactRepository,
    NextStepRepository,
    PersonRepository,
    TaskEventRepository,
)
from finch.relationships.service import RelationshipsService
from finch.settings import load_settings
from finch.storage.workspace import Workspace

relationships_app = typer.Typer(
    help="人际关系：从真实任务理解人、建立持续交流（人物 / 事件 / 交流 / 周度简报）"
)


def _parse_relationship_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_direction(value: str) -> ContactDirection:
    try:
        return ContactDirection(value)
    except ValueError:
        return ContactDirection.OUTBOUND


def _relationships_service(ws: Workspace) -> RelationshipsService:
    return RelationshipsService(
        people=PersonRepository(ws),
        events=TaskEventRepository(ws),
        contacts=ContactRepository(ws),
        next_steps=NextStepRepository(ws),
    )


def _render_traceback(tb: PersonTraceback) -> None:
    p = tb.person
    typer.echo(f"人物：{p.display_name or '-'}  [{p.person_id}]")
    if p.role:
        typer.echo(f"  角色：{p.role}")
    if p.current_focus:
        typer.echo(f"  当前关注：{p.current_focus}")
    if p.source_refs:
        typer.echo(f"  来源：{' '.join(p.source_refs)}")
    typer.echo(f"实践事件（{len(tb.events)}）：")
    for e in tb.events:
        typer.echo(f"  - 任务：{e.task or '-'}")
        if e.problem:
            typer.echo(f"    问题：{e.problem}")
        if e.workaround:
            typer.echo(f"    补救：{e.workaround}")
        if e.cognitive_change:
            typer.echo(f"    认知变化：{e.cognitive_change}")
        if e.source_refs:
            typer.echo(f"    来源：{' '.join(e.source_refs)}")
    typer.echo(f"实际交流（{len(tb.contacts)}）：")
    for c in tb.contacts:
        mark = "草稿" if c.is_draft else "已发生"
        typer.echo(f"  - [{mark}] {c.channel or '-'} {c.occurred_at.isoformat()}")
        if c.summary:
            typer.echo(f"    摘要：{c.summary}")
        if c.feedback:
            typer.echo(f"    反馈：{c.feedback}")
        for cm in c.commitments:
            due = f"（{cm.due_at.isoformat()}）" if cm.due_at else ""
            typer.echo(f"    承诺：{cm.text or '-'}{due} [{cm.status.value}]")
    typer.echo(f"下一步（{len(tb.next_steps)}）：")
    for n in tb.next_steps:
        typer.echo(f"  - [{n.status.value}] {n.suggestion or '-'}")
        if n.trigger:
            typer.echo(f"    触发：{n.trigger}")


def _render_briefing(briefing: Briefing, persons_by_id: dict[str, PersonProfile]) -> None:
    if briefing.insufficient:
        typer.echo("周度简报：暂无可报告的人（不足时少报）。")
        if briefing.info_gaps:
            typer.echo(f"  说明：{briefing.info_gaps}")
        return
    typer.echo(f"周度简报（{len(briefing.entries)} 人）：")
    for i, e in enumerate(briefing.entries, 1):
        p = persons_by_id.get(e.person_id)
        name = p.display_name if p else e.person_id
        typer.echo(f"{i}. {name}  [{e.person_id}]")
        typer.echo(f"   切入点：{e.entry_point or '-'}")
        if e.why_now:
            typer.echo(f"   为什么现在：{e.why_now}")
        if e.contribution:
            typer.echo(f"   可贡献：{e.contribution}")
        if e.question:
            typer.echo(f"   想了解：{e.question}")
        typer.echo(f"   方式：{e.channel.value}")
        if e.unknowns:
            typer.echo(f"   未知：{'；'.join(e.unknowns)}")
        if e.basis_refs:
            typer.echo(f"   依据：{' '.join(e.basis_refs)}")


@relationships_app.command("import")
def relationships_import(
    url: str | None = typer.Option(None, "--url", help="公开链接"),
    summary: str | None = typer.Option(None, "--summary", help="经允许的交流摘要"),
    title: str = typer.Option("", "--title", help="标题"),
    text: str = typer.Option("", "--text", help="粘贴文本/摘要正文"),
    author: str = typer.Option("", "--author", help="作者"),
) -> None:
    """手工导入公开链接或交流摘要，返回可引用的 note_id（source_ref）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    if summary is not None:
        note_id = rel_import_summary(ws, summary, url=url or "")
    elif url is not None:
        note_id = rel_import_link(ws, url, title=title, text=text, author=author)
    else:
        typer.echo("需要 --url 或 --summary")
        raise typer.Exit(code=1)
    typer.echo(note_id)


@relationships_app.command("add-person")
def relationships_add_person(
    person: str = typer.Option(..., "--person", help="稳定身份键（如 github.com/alice）"),
    name: str = typer.Option("", "--name", help="显示名"),
    role: str = typer.Option("", "--role", help="角色（solo founder / 使用者 / 实施者）"),
    focus: str = typer.Option("", "--focus", help="当前关注事项"),
    refs: list[str] = typer.Option([], "--ref", help="证据 ref（可多次）"),
) -> None:
    """建立/更新一个人物（幂等；缺省字段保留既有值）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = PersonRepository(ws)
    pid = person_id_for(person)
    existing = repo.get(pid)
    profile = PersonProfile(
        person_id=pid,
        display_name=name or (existing.display_name if existing else ""),
        role=role or (existing.role if existing else ""),
        current_focus=focus or (existing.current_focus if existing else ""),
        source_refs=list(dict.fromkeys((existing.source_refs if existing else []) + refs)),
    )
    repo.upsert(profile)
    typer.echo(pid)


@relationships_app.command("add-event")
def relationships_add_event(
    person: str = typer.Option(..., "--person", help="人物 person_id"),
    task: str = typer.Option("", "--task", help="当前任务"),
    expectation: str = typer.Option("", "--expectation", help="预期与判断标准"),
    trust_boundary: str = typer.Option("", "--trust-boundary", help="信任边界"),
    problem: str = typer.Option("", "--problem", help="问题"),
    workaround: str = typer.Option("", "--workaround", help="补救/接管"),
    cognitive_change: str = typer.Option("", "--cognitive-change", help="认知变化"),
    contribution: str = typer.Option("", "--contribution", help="作者可贡献点"),
    occurred_at: str | None = typer.Option(None, "--occurred-at", help="ISO 时间"),
    refs: list[str] = typer.Option([], "--ref", help="证据 ref（可多次）"),
) -> None:
    """记录一次实践事件（人物的任务与判断；缺项留空）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    event = _relationships_service(ws).upsert_event(
        TaskEvent(
            person_id=person,
            task=task,
            expectation=expectation,
            trust_boundary=trust_boundary,
            problem=problem,
            workaround=workaround,
            cognitive_change=cognitive_change,
            author_contribution=contribution,
            occurred_at=_parse_relationship_ts(occurred_at),
            source_refs=refs,
        )
    )
    typer.echo(event.event_id)


@relationships_app.command("record-contact")
def relationships_record_contact(
    person: str = typer.Option(..., "--person", help="人物 person_id"),
    occurred_at: str | None = typer.Option(None, "--occurred-at", help="ISO 时间"),
    channel: str = typer.Option("", "--channel", help="平台/渠道"),
    summary: str = typer.Option("", "--summary", help="内容摘要"),
    feedback: str = typer.Option("", "--feedback", help="对方反馈"),
    direction: str = typer.Option("outbound", "--direction", help="inbound|outbound"),
    sent: bool = typer.Option(False, "--sent", help="标记为已实际发送（否则为草稿）"),
    refs: list[str] = typer.Option([], "--ref", help="证据 ref（可多次）"),
) -> None:
    """记录一条实际交流；只有 --sent 才标记已发生，否则为草稿。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    contact = _relationships_service(ws).upsert_contact(
        Contact(
            person_id=person,
            occurred_at=_parse_relationship_ts(occurred_at) or datetime.now(UTC),
            channel=channel,
            summary=summary,
            feedback=feedback,
            direction=_coerce_direction(direction),
            source_refs=refs,
            is_draft=not sent,
        )
    )
    typer.echo(contact.contact_id)


@relationships_app.command("next")
def relationships_next(
    person: str = typer.Option(..., "--person", help="人物 person_id"),
    suggestion: str = typer.Option(..., "--suggestion", help="观察/交流建议"),
    trigger: str = typer.Option("", "--trigger", help="触发条件"),
    refs: list[str] = typer.Option([], "--ref", help="依据 ref（可多次）"),
) -> None:
    """记录一条下一步（观察/交流建议 + 触发条件 + 依据）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    step = _relationships_service(ws).upsert_next_step(
        NextStep(person_id=person, suggestion=suggestion, trigger=trigger, basis_refs=refs)
    )
    typer.echo(step.next_id)


@relationships_app.command("weekly")
def relationships_weekly(
    refresh: bool = typer.Option(False, "--refresh", help="重新生成；否则回放最新简报"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """周度交流简报（最多 3 人，每人一个切入点）；默认回放最新简报。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = BriefingRepository(ws)
    if refresh:
        runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
        briefing = generate_briefing(
            runner,
            people=PersonRepository(ws).list_all(),
            events=TaskEventRepository(ws).list_all(),
            contacts=ContactRepository(ws).list_all(),
            next_steps=NextStepRepository(ws).list_all(),
            repository=repo,
            version=settings.relationships.briefing_version,
            limit=settings.relationships.weekly_limit,
            force=True,
        )
        if briefing is None:
            typer.echo("简报未生成（无人物或无 LLM runner）。")
            raise typer.Exit(code=1)
    else:
        briefing = repo.latest()
        if briefing is None:
            typer.echo("暂无周度简报。可用 `uv run finch relationships weekly --refresh` 生成。")
            return
    if as_json:
        typer.echo(json.dumps(briefing.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    _render_briefing(briefing, {p.person_id: p for p in PersonRepository(ws).list_all()})


@relationships_app.command("show")
def relationships_show(
    person_id: str = typer.Argument(..., help="人物 person_id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看一个人物的完整回溯（人物 → 事件 → 交流 → 下一步）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    tb = _relationships_service(ws).traceback(person_id)
    if tb is None:
        typer.echo(f"person not found: {person_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(tb.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    _render_traceback(tb)
