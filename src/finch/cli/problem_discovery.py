"""Finch CLI：problem-discovery 子命令（Problem Inbox）。"""

import json
import sys
from pathlib import Path
from typing import cast

import typer

from finch.codex.runner import CodexRunner
from finch.llm.openai_compatible import create_runner
from finch.problem_discovery.discover import discover_problems
from finch.problem_discovery.importing import import_discussion
from finch.problem_discovery.models import Admission, Problem, has_unresolved_conflict
from finch.problem_discovery.presentation import ProblemPresentationRepository
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    filter_by_admission,
)
from finch.problem_discovery.repository import ProblemRepository as DiscoveredProblemRepository
from finch.problem_discovery.summary import build_summary
from finch.settings import load_settings
from finch.storage.workspace import Workspace

problem_discovery_app = typer.Typer(
    help="问题发现：从真实讨论提取问题卡片（Problem Inbox）"
)


def _render_problem_line(index: int, p: Problem) -> None:
    typer.echo(f"{index}. {p.title or '-'}  [{p.problem_id}]")
    meta = []
    if p.user_type:
        meta.append(f"用户={p.user_type}")
    meta.append(f"证据={len(p.evidence)} 条")
    if p.next_action:
        meta.append(f"下一步={p.next_action}")
    typer.echo("   " + " | ".join(meta))
    typer.echo(f"   查看：uv run finch problem-discovery show {p.problem_id}")


_ADMISSION_LABEL = {
    "source_checked": "引用已核对（不代表主张成立或已复现）",
    "accepted": "已接受",
    "quarantined": "隔离（无有效证据）",
    "draft": "草稿",
}


def _render_problem(p: Problem) -> None:
    typer.echo(f"问题：{p.title or '-'}  [{p.problem_id}]")
    typer.echo(f"  写入状态：{_ADMISSION_LABEL.get(p.admission.value, p.admission.value)}")
    if p.user_type:
        typer.echo(f"  用户类型：{p.user_type}")
    s = p.scenario
    if s.task or s.trigger or s.consequence:
        typer.echo(
            f"  场景：任务={s.task or '-'} | 触发={s.trigger or '-'} | 后果={s.consequence or '-'}"
        )
    if p.current_solutions:
        typer.echo(f"  现有方案：{'；'.join(p.current_solutions)}")
    typer.echo(f"  证据（{len(p.evidence)} 条）：")
    for e in p.evidence:
        typer.echo(f"    - [{e.evidence_level.value}] {e.claim or '-'}")
        if e.quote:
            typer.echo(f"      摘录：{e.quote}")
        if e.claim_support != "unknown":
            label = "支持" if e.claim_support == "supported" else "与摘录冲突"
            typer.echo(f"      主张核对：{label}（{e.claim_support_reason}）")
        if e.author:
            typer.echo(f"      作者：{e.author}")
        if e.url:
            typer.echo(f"      来源：{e.url}")
    if p.hypotheses:
        typer.echo(f"  假设：{'；'.join(p.hypotheses)}")
    if p.unknowns:
        typer.echo(f"  未知：{'；'.join(p.unknowns)}")
    if p.next_action:
        typer.echo(f"  下一步：{p.next_action}")


@problem_discovery_app.command("add")
def problem_discovery_add(
    repo: str | None = typer.Option(None, "--repo", help="GitHub owner/repo"),
    url: str = typer.Option("", "--url", help="公开讨论链接"),
    text: str = typer.Option("", "--text", help="粘贴的讨论正文"),
    title: str = typer.Option("", "--title", help="标题"),
    author: str = typer.Option("", "--author", help="作者"),
) -> None:
    """导入一条公开讨论（GitHub repo 或 URL/文本），返回可引用的 source_ref。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    if not repo and not url and not text:
        typer.echo("需要 --repo 或 --url 或 --text")
        raise typer.Exit(code=1)
    refs = import_discussion(ws, repo=repo or "", url=url, text=text, title=title, author=author)
    for ref in refs:
        typer.echo(ref)


@problem_discovery_app.command("discover")
def problem_discovery_discover(
    refresh: bool = typer.Option(False, "--refresh", help="强制重抽；否则复用缓存"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    full: bool = typer.Option(False, "--full", help="显示完整问题卡片"),
) -> None:
    """从已导入来源抽取问题卡片并入库。

    ``--refresh``：若配置了 ``problem_discovery.sources`` / research_topics，先广域同步再重抽。
    """
    from finch.problem_discovery.sync import format_sync_summary, sync_problem_sources
    from finch.sources.store import ArtifactRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    pd = settings.problem_discovery
    if refresh:
        sync_results = sync_problem_sources(ws, settings)
        if sync_results:
            typer.echo(format_sync_summary(sync_results))
    runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
    problems = discover_problems(
        runner,
        problem_repo=DiscoveredProblemRepository(ws),
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        run_repo=DiscoveryRunRepository(ws),
        version=pd.version,
        force=refresh,
        lookback_hours=pd.lookback_hours,
        max_sources=pd.max_sources,
        max_chars_per_source=pd.max_chars_per_source,
        batch_size=pd.batch_size,
    )
    if problems is None:
        typer.echo("无已导入来源或无法抽取。")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(
            json.dumps([p.model_dump(mode="json") for p in problems], ensure_ascii=False, indent=2)
        )
        return
    typer.echo(f"问题卡片（{len(problems)}）：")
    for i, p in enumerate(problems, 1):
        if full:
            _render_problem(p)
        else:
            _render_problem_line(i, p)


@problem_discovery_app.command("list")
def problem_discovery_list(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    full: bool = typer.Option(False, "--full", help="显示完整问题卡片"),
    admission: str = typer.Option(
        "source_checked,accepted",
        "--admission",
        help=(
            "Filter by admission (default source_checked,accepted; "
            "legacy verified accepted; all for quarantine)"
        ),
    ),
) -> None:
    """列出问题卡片（默认 source_checked,accepted；冲突卡单列待复查；隔离卡用 --admission all）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    all_problems = DiscoveredProblemRepository(ws).list_all()
    problems = filter_by_admission(all_problems, admission)
    conflicted = [p for p in problems if has_unresolved_conflict(p)]
    problems = [p for p in problems if not has_unresolved_conflict(p)]
    if as_json:
        typer.echo(
            json.dumps([p.model_dump(mode="json") for p in problems], ensure_ascii=False, indent=2)
        )
        return
    if not problems and not conflicted:
        typer.echo("暂无问题。可用 `uv run finch problem-discovery add` 导入讨论，再 `discover`。")
        return
    problems.sort(key=lambda p: (p.title.casefold(), p.problem_id))
    typer.echo(f"问题（{len(problems)}）：")
    for i, p in enumerate(problems, 1):
        if full:
            _render_problem(p)
        else:
            _render_problem_line(i, p)
    if conflicted:
        typer.echo(f"待复查（主张与摘录冲突，不计入重点）：{len(conflicted)} 张")
        for p in conflicted:
            typer.echo(f"  - {p.title}  [{p.problem_id}]")


@problem_discovery_app.command("show")
def problem_discovery_show(
    problem_id: str = typer.Argument(..., help="问题 problem_id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看单个问题卡片及其证据。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    p = DiscoveredProblemRepository(ws).get(problem_id)
    if p is None:
        typer.echo(f"problem not found: {problem_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(p.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    _render_problem(p)


@problem_discovery_app.command("summary")
def problem_discovery_summary(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    admission: str = typer.Option(
        "source_checked,accepted",
        "--admission",
        help=(
            "Filter by admission (default source_checked,accepted; "
            "legacy verified accepted; all for quarantine)"
        ),
    ),
) -> None:
    """确定性概览：卡片数 / 来源范围 / 缺失字段（供 Skill 呈现，不含重点选择）。"""
    from finch.sources.store import ArtifactRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    summary = build_summary(
        problem_repo=DiscoveredProblemRepository(ws),
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        run_repo=DiscoveryRunRepository(ws),
        admission=admission,
    )
    if as_json:
        typer.echo(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(f"问题卡片（{summary.total_cards}）：")
    typer.echo(f"  来源范围：{summary.source_scope}（{summary.total_sources} 个来源）")
    if summary.source_urls:
        typer.echo(f"  来源链接：{', '.join(summary.source_urls)}")
    if summary.cards_without_evidence:
        typer.echo(f"  缺证据卡片：{len(summary.cards_without_evidence)} 张")
    if summary.cards_without_role:
        typer.echo(f"  缺角色卡片：{len(summary.cards_without_role)} 张")
    if summary.cards_needing_review:
        typer.echo(f"  待复查卡片：{len(summary.cards_needing_review)} 张")


@problem_discovery_app.command("admit")
def problem_discovery_admit(
    problem_id: str = typer.Argument(..., help="问题 problem_id"),
    accepted: bool = typer.Option(False, "--accepted", help="标为 accepted（必填）"),
    force: bool = typer.Option(
        False, "--force", help="预留：降级时需要；设置 accepted 不依赖此标志"
    ),
    shadow: bool = typer.Option(False, "--shadow", help="写入 shadow 命名空间"),
) -> None:
    """将卡片标为 accepted（admission_reason=human_accepted）。

    只升级到 accepted（draft / quarantined / source_checked 均可）。--accepted 必填。
    已是 accepted 时成功返回且不改写入。--force 仅为后续降级预留。
    """
    if not accepted:
        raise typer.BadParameter("admit requires --accepted")
    _ = force  # forward-compat; this command does not demote
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = DiscoveredProblemRepository(ws, shadow=shadow)
    problem = repo.get(problem_id)
    if problem is None:
        typer.echo(f"problem not found: {problem_id}")
        raise typer.Exit(code=1)
    if problem.admission != Admission.ACCEPTED:
        problem.admission = Admission.ACCEPTED
        problem.admission_reason = "human_accepted"
        repo.upsert(problem)
    typer.echo(f"{problem.problem_id} admission={problem.admission.value}")


@problem_discovery_app.command("present")
def problem_discovery_present(
    problem_ids: list[str] = typer.Argument(None, help="本轮展示的有序 problem_id 列表"),
    batch: str = typer.Option("", "--batch", help="批次标识（run_id 或日期）"),
) -> None:
    """记录一次展示顺序（供「展开第二个」等自然语言引用跨会话恢复）。"""
    ids = problem_ids or []
    if not ids:
        typer.echo("需要至少一个 problem_id")
        raise typer.Exit(code=1)
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    record = ProblemPresentationRepository(ws).record_shown(ids, batch=batch)
    typer.echo(record.id)


@problem_discovery_app.command("last-presented")
def problem_discovery_last_presented(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """读取最近一次展示顺序（序号 → problem_id）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    record = ProblemPresentationRepository(ws).latest()
    if record is None:
        typer.echo("暂无展示记录。")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    for i, pid in enumerate(record.problem_ids, 1):
        typer.echo(f"{i}. {pid}")


@problem_discovery_app.command("candidates")
def problem_discovery_candidates(
    as_json: bool = typer.Option(True, "--json/--text", help="输出 JSON（默认）"),
) -> None:
    """导出与 discover 同源的候选池（fingerprint + budget_stats）；供影子 Skill 使用。"""
    from finch.problem_discovery.shadow import export_candidates
    from finch.sources.store import ArtifactRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    pd = settings.problem_discovery
    export = export_candidates(
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        version=pd.version,
        lookback_hours=pd.lookback_hours,
        max_sources=pd.max_sources,
        max_chars_per_source=pd.max_chars_per_source,
    )
    if as_json:
        typer.echo(json.dumps(export.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"candidates={export.budget_stats.get('source_count', 0)} "
        f"fingerprint={export.fingerprint or '(empty)'}"
    )


@problem_discovery_app.command("sync")
def problem_discovery_sync(
    source: str | None = typer.Option(
        None, "--source", help="twitter|reddit|github|hackernews|…"
    ),
    query: list[str] | None = typer.Option(None, "--query", "-q", help="覆盖/追加查询"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """按 problem_discovery.sources + research_topics 同步（非 peer settings.sources）。"""
    from finch.problem_discovery.shadow import JournalEntry, journal_append
    from finch.problem_discovery.sync import format_sync_summary, sync_problem_sources
    from finch.sources.orchestrator import sync_result_to_dict

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    results = sync_problem_sources(
        ws, settings, source=source, cli_queries=list(query or []) or None
    )
    journal_append(
        ws,
        JournalEntry(
            kind="sync",
            shadow=True,
            detail={
                "source": source or "all",
                "queries": list(query or []),
                "states": {r.source.value: r.kind.value if r.kind else "" for r in results},
            },
        ),
        shadow=True,
    )
    if as_json:
        typer.echo(
            json.dumps([sync_result_to_dict(r) for r in results], ensure_ascii=False, indent=2)
        )
        return
    typer.echo(format_sync_summary(results))


@problem_discovery_app.command("validate-upsert")
def problem_discovery_validate_upsert(
    draft: str = typer.Option("-", "--draft", help="草稿 JSON 路径；- 表示 stdin"),
    shadow: bool = typer.Option(False, "--shadow", help="写入 shadow 命名空间"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """对宿主草稿跑 build_problem 门禁并入库；--shadow 不污染 control inbox。"""
    from finch.problem_discovery.shadow import (
        JournalEntry,
        journal_append,
        validate_upsert,
    )
    from finch.sources.store import ArtifactRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    pd = settings.problem_discovery
    if draft == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(draft).read_text(encoding="utf-8")
    result = validate_upsert(
        raw,
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        problem_repo=DiscoveredProblemRepository(ws, shadow=shadow),
        run_repo=DiscoveryRunRepository(ws, shadow=shadow),
        version=pd.version,
        lookback_hours=pd.lookback_hours,
        max_sources=pd.max_sources,
        max_chars_per_source=pd.max_chars_per_source,
    )
    journal_append(
        ws,
        JournalEntry(
            kind="validate",
            shadow=shadow,
            detail={
                "draft_count": result.draft_count,
                "kept_count": result.kept_count,
                "dropped": result.dropped,
                "run_id": result.run_id,
                "fingerprint": result.fingerprint,
            },
        ),
        shadow=shadow or True,
    )
    if as_json:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"kept={result.kept_count}/{result.draft_count} "
        f"run={result.run_id or '-'} fingerprint={result.fingerprint or '(empty)'}"
    )
    for d in result.dropped:
        typer.echo(f"  dropped: {d}")


@problem_discovery_app.command("journal")
def problem_discovery_journal(
    action: str = typer.Argument("show", help="show | append"),
    shadow: bool = typer.Option(True, "--shadow/--control", help="默认 shadow journal"),
    kind: str = typer.Option("note", "--kind", help="append 时的事件类型"),
    detail: str = typer.Option("{}", "--detail", help="append 时 JSON detail"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """影子实验运行日志：show 最近事件；append 追加一条。"""
    from finch.problem_discovery.shadow import JournalEntry, journal_append, journal_show

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    if action == "append":
        journal_append(
            ws,
            JournalEntry(kind=kind, shadow=shadow, detail=json.loads(detail)),
            shadow=shadow,
        )
        typer.echo("ok")
        return
    if action != "show":
        typer.echo("action must be show|append")
        raise typer.Exit(code=1)
    entries = journal_show(ws, shadow=shadow)
    if as_json:
        typer.echo(
            json.dumps([e.model_dump(mode="json") for e in entries], ensure_ascii=False, indent=2)
        )
        return
    if not entries:
        typer.echo("journal empty")
        return
    for e in entries:
        typer.echo(f"{e.at.isoformat()} {e.kind} {json.dumps(e.detail, ensure_ascii=False)}")


@problem_discovery_app.command("compare")
def problem_discovery_compare(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """对比最新 control vs shadow DiscoveryRun。"""
    from finch.problem_discovery.shadow import JournalEntry, compare_runs, journal_append

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = compare_runs(
        control_run_repo=DiscoveryRunRepository(ws, shadow=False),
        shadow_run_repo=DiscoveryRunRepository(ws, shadow=True),
        control_problem_repo=DiscoveredProblemRepository(ws, shadow=False),
        shadow_problem_repo=DiscoveredProblemRepository(ws, shadow=True),
    )
    journal_append(
        ws,
        JournalEntry(kind="compare", shadow=True, detail=result.model_dump(mode="json")),
        shadow=True,
    )
    if as_json:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"control={result.control_run_id or '-'} shadow={result.shadow_run_id or '-'} "
        f"fp_match={result.fingerprint_match} overlap={len(result.overlap_ids)}"
    )
    if result.only_control:
        typer.echo(f"  only_control: {', '.join(result.only_control)}")
    if result.only_shadow:
        typer.echo(f"  only_shadow: {', '.join(result.only_shadow)}")
    for note in result.notes:
        typer.echo(f"  note: {note}")
