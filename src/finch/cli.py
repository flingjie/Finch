"""Finch CLI（spec 10）。"""

import difflib
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import typer
import yaml
from pydantic import ValidationError

from .codex.runner import CodexRunner
from .codex.structured_output import StructuredOutputError
from .content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from .content.models import Draft
from .content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    propose_voice_updates,
    save_voice_profile,
)
from .content.writer import rewrite_with_instruction
from .conversations.service import ConversationService
from .drafts.service import DraftCreateResult, DraftService
from .engagement.flow import EngagementRunResult, run_discovery_engagement_flow
from .engagement.metrics import compute_relationship_metrics
from .engagement.models import InteractionRecord, InteractionStatus
from .engagement.proposals import generate_proposals
from .engagement.scoring import rank_candidates, score_posts
from .engagement.search import fetch_post_by_url
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.gh_client import GhClient
from .github.local_repo import resolve_commit_repo
from .ideas.commit_service import CommitService
from .ideas.fragment_service import FragmentService
from .ideas.service import IdeaService
from .inbox.models import DecisionAction, InboxTrack
from .inbox.service import InboxDecisionService, list_items
from .learn.models import Feedback, OutcomeAssessment
from .learn.reflection import WeeklyReflectionService, render_reflection
from .learn.weekly import weekly_analysis
from .llm.openai_compatible import create_runner
from .peers.service import PeerService
from .practice.service import PracticeService
from .projections import (
    TodayFocus,
    build_daily_context,
    build_pending_actions,
    build_today_focus,
)
from .reddit.opencli_client import RedditOpenCliClient
from .settings import Settings, load_settings
from .storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    FeedbackRepository,
    FeedbackSnapshotRepository,
    InteractionRecordRepository,
    InteractionRepository,
    PeerRepository,
    PracticeSessionRepository,
    PublicationIntentRepository,
)
from .storage.workspace import Workspace
from .style.models import StyleReport
from .style.service import WritingStyleService
from .style.source_resolver import SourceResolver
from .twitter.normalizer import normalize_tweets
from .twitter.opencli_client import OpenCliClient
from .twitter.query_builder import QueryBuilder
from .webfetch.fetcher import WebFetcher

app = typer.Typer(help="Finch: 同行连接与个人表达系统。")

github_app = typer.Typer(help="GitHub 读取与工程事件提取")
app.add_typer(github_app, name="github")

twitter_app = typer.Typer(help="Twitter 搜索与读取")
app.add_typer(twitter_app, name="twitter")

voice_app = typer.Typer(help="Manage the author voice profile (local, no auto-publish)")
app.add_typer(voice_app, name="voice")

ideas_app = typer.Typer(help="Idea 候选流（commit / 用户片段 / 对话提炼 + 状态转换）")
app.add_typer(ideas_app, name="ideas")

drafts_app = typer.Typer(help="Draft 生成（已确认 idea → 草稿，不自动发布）")
app.add_typer(drafts_app, name="drafts")

review_app = typer.Typer(help="Review original drafts (accept/revise/skip, no auto-publish)")
app.add_typer(review_app, name="review")

connect_app = typer.Typer(help="连接主循环：daily / prepare / approve / reject / edit / record")
app.add_typer(connect_app, name="connect")

peers_app = typer.Typer(help="同行档案与关系上下文")
app.add_typer(peers_app, name="peers")

conversations_app = typer.Typer(help="对话线索与跟进")
app.add_typer(conversations_app, name="conversations")

practice_app = typer.Typer(help="表达练习")
app.add_typer(practice_app, name="practice")

style_app = typer.Typer(help="分析一段文本/链接的写作特点")
app.add_typer(style_app, name="style")


def _idea_meta_line(job: ContentJob) -> str:
    """一行摘要：稳定、可扫描（tab 分隔；机器解析请用 --json，不用此文本输出）。"""
    return "\t".join(
        [
            job.id,
            job.status.value,
            job.origin or "-",
            job.intent or "stance",
            job.core_message,
        ]
    )


def _render_idea_list(jobs: list[ContentJob]) -> str:
    """人类可读的候选列表：带表头，一候选一行（机器解析请用 --json）。"""
    lines = ["id\tstatus\torigin\tintent\tcore_message"]
    lines.extend(_idea_meta_line(job) for job in jobs)
    focus = next((job for job in jobs if job.status == ContentJobStatus.PROPOSED), None)
    if focus is None and jobs:
        focus = jobs[0]
    if focus is not None:
        lines += _render_next_steps(focus)
    return "\n".join(lines)


_IDEA_CARD_LIMIT = 6

_FORMAT_LABELS = {
    "reply": "回复",
    "quote": "引用",
    "short_post": "短帖",
    "thread": "长帖",
    "dm": "私信",
    "do_not_publish": "不发布",
}


def _format_label(fmt: str) -> str:
    return _FORMAT_LABELS.get(fmt, fmt)


def _render_idea_card(job: ContentJob) -> str:
    """决策卡素材：可写观点 / 为什么值得写 / 适合形式；确认命令带真实 id。"""
    lines = [
        f"可写观点: {job.core_message or '-'}",
        *(
            [f"为什么值得写: {job.why_now}"]
            if (job.why_now or "").strip()
            else []
        ),
        f"适合形式: {_format_label(job.recommended_format.value)}",
    ]
    if job.status == ContentJobStatus.PROPOSED:
        lines.append(f"uv run finch ideas confirm {job.id}")
    elif job.status == ContentJobStatus.CONFIRMED:
        lines.append(f"uv run finch drafts create {job.id}")
    return "\n".join(lines)


def _render_idea_cards(jobs: list[ContentJob], *, limit: int = _IDEA_CARD_LIMIT) -> str:
    """本次结果的决策卡列表；超出 limit 时指向 `ideas list`。"""
    if not jobs:
        return "no idea candidates"
    shown = jobs[:limit]
    parts: list[str] = [
        "\n\n".join(_render_idea_card(job) for job in shown)
    ]
    if len(jobs) > limit:
        parts.append(
            f"共 {len(jobs)} 个候选，以上 {len(shown)} 个。其余：uv run finch ideas list"
        )
    first = shown[0]
    if first.status == ContentJobStatus.PROPOSED:
        parts.append(f"uv run finch ideas skip {first.id} --reason ...")
    return "\n\n".join(parts)


def _idea_next_steps(job: ContentJob) -> list[str]:
    """按状态给出可复制的下一步 CLI 命令。"""
    if job.status == ContentJobStatus.PROPOSED:
        return [
            f"uv run finch ideas confirm {job.id}",
            f"uv run finch ideas skip {job.id} --reason ...",
        ]
    if job.status == ContentJobStatus.CONFIRMED:
        return [f"uv run finch drafts create {job.id}"]
    if job.status == ContentJobStatus.DRAFTED:
        return ["uv run finch review list"]
    return []


def _render_next_steps(job: ContentJob) -> list[str]:
    steps = _idea_next_steps(job)
    if not steps:
        return []
    return ["", "下一步:"] + steps


def _render_idea_detail(job: ContentJob) -> str:
    """单个候选的完整可读视图。"""
    lines = [
        f"id: {job.id}",
        f"status: {job.status.value}",
        f"origin: {job.origin or '-'}",
        f"intent: {job.intent or 'stance'}",
        f"recommended_format: {job.recommended_format.value}",
        "",
        f"核心主张: {job.core_message or '-'}",
    ]
    if job.observation:
        lines += ["", f"观察: {job.observation}"]
    if job.reader_problem:
        lines += ["", f"读者问题: {job.reader_problem}"]
    if job.why_now:
        lines += ["", f"为什么现在说: {job.why_now}"]
    if job.author_position:
        pos = job.author_position
        lines += [
            "",
            "作者立场 (proposed):",
            f"- claim: {pos.claim}",
            f"- decision: {pos.decision}",
            f"- tradeoff: {pos.tradeoff}",
        ]
        if pos.change_mind_if:
            lines.append(f"- change_mind_if: {pos.change_mind_if}")
    if job.open_question:
        lines += ["", f"开放问题: {job.open_question}"]
    if job.reject_reason:
        lines += ["", f"跳过理由: {job.reject_reason}"]
    lines += _render_next_steps(job)
    return "\n".join(lines)


def _render_draft(draft: Draft) -> str:
    """单个草稿的完整可读视图。"""
    lines = [
        f"id: {draft.id}",
        f"kind: {draft.kind.value}",
    ]
    if draft.content_job_id:
        lines.append(f"content_job_id: {draft.content_job_id}")
    if draft.position_statement:
        lines.append(f"position_statement: {draft.position_statement}")
    lines += ["", draft.body, "", "下一步:"]
    lines += [
        "- 采用并进入发布意图",
        "- 继续修改",
        "- 放弃草稿",
    ]
    return "\n".join(lines)


def _render_critic_reports(reports: list[dict]) -> str:
    """把 Critic 报告从原始 JSON 转成逐项可读视图。"""
    if not reports:
        return ""
    lines: list[str] = []
    for idx, report in enumerate(reports, start=1):
        lines += [f"--- critic {idx} ---", f"outcome: {report.get('outcome', '-')}"]
        for check in report.get("checks", []):
            passed = bool(check.get("passed"))
            severity = check.get("severity", "-")
            status = "pass" if passed else f"fail ({severity})"
            detail = "; ".join(check.get("issues", []))
            line = f"- {check.get('checker', '?')}: {status}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
    return "\n".join(lines)


def _since_iso(since: str | None) -> str | None:
    if since is None:
        return None
    if since.endswith("h"):
        return (datetime.now(UTC) - timedelta(hours=int(since[:-1]))).isoformat()
    if since.endswith("d"):
        return (datetime.now(UTC) - timedelta(days=int(since[:-1]))).isoformat()
    return since


@app.command()
def init() -> None:
    """初始化工作区目录树（幂等）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    typer.echo(f"initialized: {settings.paths.var_dir}")


@app.command()
def context(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """生成当日上下文投影并写入 projections/（可重建，非事实源）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    daily = build_daily_context(ws)
    pending = build_pending_actions(ws)
    proj = ws.dir("projections")
    ws.atomic_write(proj / "daily-context.json", json.dumps(daily, ensure_ascii=False, indent=2))
    ws.atomic_write(
        proj / "pending-actions.json", json.dumps(pending, ensure_ascii=False, indent=2)
    )
    if as_json:
        typer.echo(json.dumps({"daily": daily, "pending": pending}, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"projections written to {proj}")


@app.command()
def diagnose() -> None:
    """分别报告 gh 与 opencli 的可用状态（spec 5.1）。"""
    gh = GhClient()
    opencli = OpenCliClient()

    gh_ver = gh.version()
    gh_auth = gh.auth_status()
    opencli_ver = opencli.version()
    opencli_doctor = opencli.doctor()

    typer.echo("gh:")
    typer.echo(f"  version: {gh_ver or 'unavailable'}")
    typer.echo(f"  auth: {gh_auth}")
    typer.echo("opencli:")
    typer.echo(f"  version: {opencli_ver or 'unavailable'}")
    typer.echo(f"  doctor: {opencli_doctor}")


@github_app.command("reflect")
def github_reflect(repo: str = typer.Option("flingjie/FDE-Gym"),
                   since: str = typer.Option("7d")) -> None:
    """读取最近 Commit，提取工程事件并输出证据卡。"""
    gh = GhClient()
    settings = load_settings()
    details = load_commit_details(
        repo, gh, local_dirs=settings.paths.local_repos_dirs, since=_since_iso(since)
    )
    reader = CommitReader(gh, repo)
    details = reader.filter_noise(details)
    events = Extractor(
        create_runner(settings.llm) or CodexRunner(),
        settings=settings.extraction,
        cache_path=settings.paths.cache_dir / "extraction_cache.json",
    ).extract(details, repo=repo)
    cards = build_cards(events)
    typer.echo(f"# Finch reflect: {repo}\n")
    for ev in events:
        typer.echo(f"## {ev.id}\n- problem: {ev.problem.statement} [{ev.problem.confidence.value}]")
        typer.echo(f"- decision: {ev.decision.statement} [{ev.decision.confidence.value}]")
        typer.echo(f"- result: {ev.result.statement} [{ev.result.confidence.value}]")
    typer.echo(f"\n{len(cards)} evidence cards")


@ideas_app.command("commit")
def ideas_commit(
    repo: str | None = typer.Option(
        None, "--repo", help="仓库（默认当前 checkout 的 origin，否则 settings.repositories[0]）"
    ),
    since: str = typer.Option("7d", "--since", help="起始时间（如 7d / 24h / ISO 时间）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从最近 Commit 提炼 idea 候选并幂等落库（不生成草稿）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    gh = GhClient()
    repo = resolve_commit_repo(repo, settings.repositories)
    if repo is None:
        typer.echo(
            "--repo is required (no current checkout origin and no repositories configured)"
        )
        raise typer.Exit(code=1)
    details = load_commit_details(
        repo, gh, local_dirs=settings.paths.local_repos_dirs, since=_since_iso(since)
    )
    reader = CommitReader(gh, repo)
    extractor = Extractor(
        create_runner(settings.llm) or CodexRunner(),
        settings=settings.extraction,
        cache_path=settings.paths.cache_dir / "extraction_cache.json",
    )
    ideas = CommitService(reader, extractor).to_ideas(details, repo=repo)
    idea_service = IdeaService(ContentJobRepository(ws))
    jobs = [idea_service.create_candidate(idea) for idea in ideas]
    if as_json:
        payload = [
            {
                "id": job.id,
                "origin": job.origin,
                "core_point": job.core_message,
                "reader_problem": job.reader_problem,
                "why_now": job.why_now,
                "recommended_format": job.recommended_format.value,
                "status": job.status.value,
                "generation_key": job.generation_key,
            }
            for job in jobs
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_idea_cards(jobs))


@ideas_app.command("create")
def ideas_create(
    text: str = typer.Option(None, "--text", help="用户输入的一句话/片段"),
    conversation: str = typer.Option(None, "--conversation", help="对话线索 id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """把用户片段 / 对话线索结构化为 idea 候选并落库。"""
    provided = sum(x is not None for x in (text, conversation))
    if provided != 1:
        typer.echo("exactly one of --text / --conversation is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    try:
        if text is not None:
            idea = service.from_text(text)
        else:
            thread = ConversationThreadRepository(ws).get(conversation)
            if thread is None:
                typer.echo(f"conversation not found: {conversation}")
                raise typer.Exit(code=1)
            interactions = InteractionRecordRepository(ws).list_by_peer(thread.peer_id)
            idea = service.from_thread(thread, interactions=interactions)
        job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
    except (RuntimeError, StructuredOutputError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps(
            {"id": job.id, "origin": job.origin, "status": job.status.value},
            ensure_ascii=False, indent=2,
        ))
    else:
        typer.echo(_render_idea_detail(job))


@ideas_app.command("signals")
def ideas_signals(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """聚合社区信号（同行共同主题 + 未解问题/分歧）为一个 idea 候选并落库。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    try:
        idea = service.from_signals(
            peers=PeerRepository(ws).list_all(),
            threads=ConversationThreadRepository(ws).list_all(),
        )
        if idea is not None:
            job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
    except (RuntimeError, StructuredOutputError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    if idea is None:
        typer.echo("no community signal worth an idea")
        raise typer.Exit(code=0)
    if as_json:
        typer.echo(json.dumps(
            {"id": job.id, "origin": job.origin, "status": job.status.value},
            ensure_ascii=False, indent=2,
        ))
    else:
        typer.echo(_render_idea_detail(job))


@ideas_app.command("list")
def ideas_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出全部 idea 候选，一行一个；旧行在系统警告中提示。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ContentJobRepository(ws)
    jobs = sorted(repo.list_jobs(), key=lambda j: j.id)
    if as_json:
        payload = [
            {"id": job.id, "status": job.status.value, "core_point": job.core_message}
            for job in jobs
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    failures = repo.list_job_parse_failures()
    typer.echo(_render_idea_list(jobs))
    if failures:
        preview = ", ".join(failures[:5]) + ("…" if len(failures) > 5 else "")
        typer.echo(
            f"\n系统警告：检测到 {len(failures)} 条旧版 job 记录无法解析（{preview}），"
            "已跳过。"
        )


@ideas_app.command("show")
def ideas_show(
    idea_id: str = typer.Argument(..., help="idea id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示单个 idea 候选（--json 输出完整记录）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    job = ContentJobRepository(ws).get_job(idea_id)
    if job is None:
        typer.echo(f"idea not found: {idea_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(job.model_dump_json(indent=2))
    else:
        typer.echo(_render_idea_detail(job))


@ideas_app.command("confirm")
def ideas_confirm(
    idea_id: str = typer.Argument(..., help="idea id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """确认立场：proposed → confirmed。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    service = IdeaService(ContentJobRepository(ws))
    try:
        job = service.confirm_position(idea_id)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(
            json.dumps({"id": job.id, "status": job.status.value}, ensure_ascii=False, indent=2)
        )
    else:
        typer.echo(f"{job.id} -> {job.status.value}")
        for line in _render_next_steps(job):
            if line:
                typer.echo(line)


@ideas_app.command("revise-position")
def ideas_revise_position(
    idea_id: str = typer.Argument(..., help="idea id"),
    position_file: str = typer.Option(..., "--file", help="作者立场 YAML 文件路径"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从 YAML 读取作者立场并更新（不改状态）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        position = AuthorPosition.model_validate(
            yaml.safe_load(Path(position_file).read_text())
        )
    except (OSError, yaml.YAMLError, ValueError) as exc:
        typer.echo(f"invalid position file: {exc}")
        raise typer.Exit(code=1) from exc
    service = IdeaService(ContentJobRepository(ws))
    try:
        job = service.revise_position(idea_id, position)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(
            json.dumps({"id": job.id, "status": job.status.value}, ensure_ascii=False, indent=2)
        )
    else:
        typer.echo(f"{job.id} -> position updated ({job.status.value})")


@ideas_app.command("skip")
def ideas_skip(
    idea_id: str = typer.Argument(..., help="idea id"),
    reason: str = typer.Option(..., "--reason", help="跳过理由"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """跳过 idea：proposed/confirmed → skipped。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    service = IdeaService(ContentJobRepository(ws))
    try:
        job = service.skip(idea_id, reason)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        payload = {"id": job.id, "status": job.status.value, "reason": reason}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{job.id} -> {job.status.value} (reason: {reason})")


def _render_draft_result(result: DraftCreateResult) -> str:
    draft = result.draft
    if result.outcome == "pass":
        header = "草稿已生成并通过质量检查，当前等待你的审核。"
        verdict = "通过"
    elif result.outcome == "unknown":
        header = "草稿已生成。"
        verdict = "未知（无 Critic 报告）"
    else:
        header = "草稿已生成，但质量检查未完全通过，请人工判读。"
        verdict = f"未通过（经过 {result.critic_rounds} 轮检查后仍未满足）"
    lines = [
        header,
        "",
        f"> {draft.body}",
        "",
        f"质量检查：{verdict}",
    ]
    if result.adjustments:
        lines.append(f"主要调整：{'；'.join(result.adjustments)}")
    lines += [
        "状态：未发布",
        "",
        "下一步：",
        "- 采用并进入发布意图",
        "- 继续修改",
        "- 放弃草稿",
    ]
    return "\n".join(lines)


def _render_run_details(result: DraftCreateResult) -> str:
    draft = result.draft
    return "\n".join(
        [
            "",
            "运行详情：",
            f"- draft_id: {draft.id}",
            f"- idea_id: {draft.content_job_id}",
            f"- critic_rounds: {result.critic_rounds}",
            f"- outcome: {result.outcome}",
        ]
    )


@drafts_app.command("create")
def drafts_create(
    idea_id: str = typer.Argument(..., help="已确认的 idea id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),  # noqa: B008
    verbose: bool = typer.Option(False, "--verbose", help="附带运行详情"),
) -> None:
    """从已确认 idea 生成草稿并记录质检报告（不自动发布）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = DraftService(
        DraftRepository(ws),
        CriticReportRepository(ws),
        ContentJobRepository(ws),
        runner,
        max_rewrite_rounds=settings.quality_gates.max_rewrite_rounds,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
    )
    try:
        result = service.create_result(
            idea_id, version="1.0.0", format="original", voice_version="1.0.0"
        )
    except (KeyError, ValueError, RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        payload = {
            "draft_id": result.draft.id,
            "status": "drafted",
            "body": result.draft.body,
            "critic_rounds": result.critic_rounds,
            "outcome": result.outcome,
            "adjustments": result.adjustments,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_draft_result(result))
        if verbose:
            typer.echo(_render_run_details(result))


@drafts_app.command("show")
def drafts_show(
    draft_id: str = typer.Argument(..., help="draft id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示单个草稿。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    draft = DraftRepository(ws).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(draft.model_dump_json(indent=2))
    else:
        typer.echo(_render_draft(draft))


@drafts_app.command("revise")
def drafts_revise(
    draft_id: str = typer.Argument(..., help="draft id"),
    instruction: str = typer.Option(..., "--instruction", help="重写指令"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """按自然语言指令重写草稿正文并落库（不自动发布）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    draft_repo = DraftRepository(ws)
    draft = draft_repo.get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    job = None
    if draft.content_job_id:
        job = ContentJobRepository(ws).get_job(draft.content_job_id)
    cards_by_id = {c.id: c for c in EvidenceRepository(ws).list_cards()}
    runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
    try:
        revised = rewrite_with_instruction(runner, draft, instruction, cards_by_id, job)
    except (RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    draft_repo.upsert_draft(revised)
    if as_json:
        payload = {"draft_id": revised.id, "body": revised.body}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"已更新草稿 {revised.id}:")
        typer.echo(revised.body)


@twitter_app.command("search")
def twitter_search(
    query_set: str = typer.Option("agent_evals", help="Query set ID from finch.yaml"),
    product: str = typer.Option("top", help="Search product: top, live, photos, videos"),
    limit: int = typer.Option(20, help="Max tweets per query"),
) -> None:
    """运行配置的 Twitter 查询集."""
    settings = load_settings()
    client = OpenCliClient()
    builder = QueryBuilder(settings.twitter.queries, per_query_limit=limit)

    total = 0
    for cfg, _argv in builder.build_all():
        if query_set != "all" and cfg.id != query_set:
            continue
        tweets = client.search(cfg.text, product=product, limit=limit)
        normalized = normalize_tweets(tweets)
        typer.echo(f"[{cfg.id}] {len(normalized)} tweets (raw={len(tweets)})")
        for t in normalized[:3]:
            typer.echo(f"  @{t.author}: {t.text[:80]}...")
        total += len(normalized)
    typer.echo(f"\ntotal: {total} tweets")


@twitter_app.command("import-bookmarks")
def twitter_import_bookmarks(limit: int = typer.Option(50, help="Max bookmarks to import")) -> None:
    """导入 Twitter 书签."""
    client = OpenCliClient()
    tweets = client.bookmarks(limit=limit)
    normalized = normalize_tweets(tweets)
    typer.echo(f"imported {len(normalized)} bookmarks (raw={len(tweets)})")
    for t in normalized[:5]:
        typer.echo(f"  @{t.author}: {t.text[:80]}...")


@twitter_app.command("diagnose")
def twitter_diagnose() -> None:
    """报告 opencli Twitter 状态."""
    client = OpenCliClient()
    ver = client.version()
    doctor = client.doctor()
    typer.echo(f"opencli version: {ver or 'unavailable'}")
    typer.echo(f"doctor: {doctor}")
    try:
        tweets = client.search("test", limit=1)
        typer.echo(f"search probe: ok ({len(tweets)} tweets)")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"search probe: failed ({type(exc).__name__}: {exc})")


@app.command("learn")
def learn(
    draft_id: str = typer.Argument(..., help="已发布草稿 id"),
    url: str = typer.Option(None, "--url", help="发布后的 URL"),
    metrics: str = typer.Option(None, "--metrics", help="互动指标 JSON 对象（如 {\"likes\":3}）"),
    outcome: str = typer.Option(None, "--outcome", help="结果评估 JSON（OutcomeAssessment）"),
    learning: str = typer.Option(None, "--learning", help="这次实际学到了什么（自由文本）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """记录一条已发布草稿的反馈（URL / 互动指标 / 结果评估 / 学习），供 weekly 汇总。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    if DraftRepository(ws).get_draft(draft_id) is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)

    parsed_metrics: dict = {}
    if metrics:
        try:
            parsed_metrics = json.loads(metrics)
        except json.JSONDecodeError as exc:
            typer.echo(f"invalid --metrics JSON: {exc}")
            raise typer.Exit(code=1) from exc
        if not isinstance(parsed_metrics, dict):
            typer.echo("--metrics must be a JSON object")
            raise typer.Exit(code=1)

    parsed_outcome: OutcomeAssessment | None = None
    if outcome:
        try:
            parsed_outcome = OutcomeAssessment.model_validate_json(outcome)
        except ValidationError as exc:
            typer.echo(f"invalid --outcome JSON: {exc}")
            raise typer.Exit(code=1) from exc

    feedback = Feedback(
        draft_id=draft_id,
        published_url=url,
        interaction_metrics=parsed_metrics,
        recorded_at=datetime.now(UTC),
        outcome=parsed_outcome,
        learning=learning,
    )
    FeedbackRepository(ws).save_feedback(feedback)
    if as_json:
        typer.echo(feedback.model_dump_json(indent=2))
    else:
        typer.echo(f"recorded feedback for {draft_id}")


@app.command("weekly")
def run_weekly(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """周复盘：关系质量指标与周报指标由代码算，LLM 定性解读（五个关系问题）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    since = datetime.now(UTC) - timedelta(days=7)
    report = weekly_analysis(
        DraftRepository(ws),
        DecisionRecordRepository(ws),
        FeedbackRepository(ws),
        ContentJobRepository(ws),
        CriticReportRepository(ws),
        since=since,
    )
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    # 反馈按 recorded_at 对齐 7 天窗口（与 weekly_analysis 的 since 一致），避免复盘
    # 输入随库无界膨胀。
    window_feedbacks = [
        fb for fb in FeedbackRepository(ws).list_feedbacks() if fb.recorded_at >= since
    ]
    rel_metrics = compute_relationship_metrics(
        peers=PeerRepository(ws).list_all(),
        interactions=InteractionRecordRepository(ws).list_all(),
        threads=ConversationThreadRepository(ws).list_all(),
        snapshots=FeedbackSnapshotRepository(ws).list_all(),
        jobs=ContentJobRepository(ws).list_jobs(),
        now=datetime.now(UTC),
    )
    try:
        reflection = WeeklyReflectionService(runner).reflect(
            report,
            relationship_metrics=rel_metrics,
            feedbacks=window_feedbacks,
            threads=ConversationThreadRepository(ws).list_all(),
            voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        )
    except (RuntimeError, StructuredOutputError) as exc:
        typer.echo(f"weekly reflection failed: {exc}")
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(reflection.model_dump_json(indent=2))
    else:
        typer.echo(render_reflection(reflection))


def _voice_profile_path() -> Path:
    settings = load_settings()
    return settings.paths.voice_profile_path


@voice_app.command("show")
def voice_show() -> None:
    """加载并打印声音画像。"""
    profile = load_voice_profile(_voice_profile_path())
    typer.echo(
        yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )


def _diff_text(before: str, after: str) -> str:
    """模型初稿 → 最终文本的 unified diff。"""
    return "\n".join(
        difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    )


@voice_app.command("approve-example")
def voice_approve_example(
    draft_id: str = typer.Argument(None, help="草稿 id（从已批准草稿采纳）"),
    text: str = typer.Option(None, "--text", help="用户亲写文本"),
) -> None:
    """把已批准草稿或用户亲写文本追加为 approved example（按 id 去重）。

    只接受用户明确批准的最终文本：从草稿采纳时要求 DecisionRecord=ACCEPT 且优先用人工
    修订版本；用户亲写文本经 --text 直接采纳。记录模型初稿 → 最终文本的 diff。
    """
    if (draft_id is None) == (text is None):
        typer.echo("exactly one of draft_id / --text is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    path = settings.paths.voice_profile_path
    profile = load_voice_profile(path)

    if text is not None:
        example_id = f"text_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"
        example = ApprovedExample(id=example_id, text=text, source="user_text")
    else:
        ws = Workspace(settings.paths.var_dir)
        ws.ensure()
        draft = DraftRepository(ws).get_draft(draft_id)
        if draft is None:
            typer.echo(f"draft not found: {draft_id}")
            raise typer.Exit(code=1)
        decisions = {d.draft_id: d for d in DecisionRecordRepository(ws).list()}
        decision = decisions.get(draft_id)
        if decision is None or decision.action != DecisionAction.ACCEPT:
            typer.echo(f"not accepted: {draft_id}")
            raise typer.Exit(code=1)
        final_text = decision.revised_body or draft.body
        example = ApprovedExample(
            id=draft_id,
            text=final_text,
            source="draft",
            original_draft=draft.body,
            diff=_diff_text(draft.body, final_text) if draft.body != final_text else None,
        )

    if any(ex.id == example.id for ex in profile.approved_examples):
        typer.echo(f"already approved: {example.id}")
        return
    profile.rejected_examples = [
        ex for ex in profile.rejected_examples if ex.id != example.id
    ]
    profile.approved_examples.append(example)
    save_voice_profile(profile, path)
    typer.echo(f"approved example: {example.id}")


@voice_app.command("revoke-example")
def voice_revoke_example(example_id: str = typer.Argument(..., help="样例 id")) -> None:
    """撤销一个错误样例并从画像移除（移除后画像重新计算偏好）。"""
    settings = load_settings()
    path = settings.paths.voice_profile_path
    profile = load_voice_profile(path)
    before = len(profile.approved_examples) + len(profile.rejected_examples)
    profile.approved_examples = [
        ex for ex in profile.approved_examples if ex.id != example_id
    ]
    profile.rejected_examples = [
        ex for ex in profile.rejected_examples if ex.id != example_id
    ]
    after = len(profile.approved_examples) + len(profile.rejected_examples)
    if before == after:
        typer.echo(f"example not found: {example_id}")
        raise typer.Exit(code=1)
    save_voice_profile(profile, path)
    typer.echo(f"revoked example: {example_id}")


@voice_app.command("propose")
def voice_propose(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """从已批准样例的 diff 提取稳定偏好候选（只读，不写画像；由用户确认后写入）。"""
    settings = load_settings()
    profile = load_voice_profile(settings.paths.voice_profile_path)
    proposal = propose_voice_updates(profile)
    if as_json:
        typer.echo(proposal.model_dump_json(indent=2))
        return
    typer.echo("## 建议避免的表达（用户曾删掉/改掉）")
    for phrase in proposal.avoid_phrases:
        typer.echo(f"- {phrase}")
    typer.echo("## 建议偏好的表达（用户曾改向）")
    for phrase in proposal.preferred_patterns:
        typer.echo(f"- {phrase}")


@voice_app.command("reject-example")
def voice_reject_example(
    draft_id: str,
    reason: str = typer.Option(..., "--reason", help="拒绝理由"),  # noqa: B008
) -> None:
    """把草稿追加为 rejected example（按 id 去重）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    draft = DraftRepository(ws).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    path = settings.paths.voice_profile_path
    profile = load_voice_profile(path)
    if any(ex.id == draft_id for ex in profile.rejected_examples):
        typer.echo(f"already rejected: {draft_id}")
        return
    profile.approved_examples = [
        ex for ex in profile.approved_examples if ex.id != draft_id
    ]
    profile.rejected_examples.append(RejectedExample(id=draft_id, reason=reason))
    save_voice_profile(profile, path)
    typer.echo(f"rejected example: {draft_id}")


def _decision_service(ws: Workspace) -> InboxDecisionService:
    return InboxDecisionService(
        jobs=ContentJobRepository(ws),
        drafts=DraftRepository(ws),
        decisions=DecisionRecordRepository(ws),
        publication_intents=PublicationIntentRepository(ws),
        interactions=InteractionRepository(ws),
    )


def _draft_job_id(drafts: DraftRepository, draft_id: str) -> str:
    """把 review 的 draft_id 映射到 InboxDecisionService 的 job_id。

    ``InboxDecisionService`` 以 job_id 为键；idea 草稿均带 ``content_job_id``，
    故先按 draft_id 读草稿再取其 job。无草稿或无 job 时抛 KeyError。
    """
    draft = drafts.get_draft(draft_id)
    if draft is None:
        raise KeyError(f"draft not found: {draft_id}")
    if draft.content_job_id is None:
        raise KeyError(f"draft has no content job: {draft_id}")
    return draft.content_job_id


@review_app.command("list")
def review_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出待决策的原创草稿（未被 accept/skip 决策覆盖，按 next 的确定性顺序）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    items = list_items(
        jobs=ContentJobRepository(ws),
        drafts=DraftRepository(ws),
        decisions=DecisionRecordRepository(ws),
        interactions=InteractionRepository(ws),
        cards=EvidenceRepository(ws),
    )
    original = [i for i in items if i.track == InboxTrack.ORIGINAL]
    if as_json:
        payload = [
            {
                "draft_id": i.draft_id,
                "job_id": i.id,
                "content_type": i.content_type,
                "score": i.score,
                "draft": i.draft,
            }
            for i in original
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not original:
        typer.echo("no pending drafts")
        return
    typer.echo("draft_id\tcontent_type\tpreview")
    for item in original:
        snippet = " ".join(item.draft.split())[:80]
        typer.echo(f"{item.draft_id}\t{item.content_type}\t{snippet}")


@review_app.command("show")
def review_show(
    draft_id: str = typer.Argument(..., help="draft id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示草稿正文与（若存在）质检报告。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    draft = DraftRepository(ws).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    reports = CriticReportRepository(ws).list_reports(draft_id)
    if as_json:
        typer.echo(
            json.dumps(
                {"draft": draft.model_dump(mode="json"), "critic_reports": reports},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        typer.echo(f"id: {draft.id}")
        typer.echo(f"kind: {draft.kind.value}")
        typer.echo("")
        typer.echo(draft.body)
        critic = _render_critic_reports(reports)
        if critic:
            typer.echo("")
            typer.echo(critic)
        typer.echo("")
        typer.echo("下一步:")
        typer.echo("- 采用")
        typer.echo("- 修改")
        typer.echo("- 跳过")


@review_app.command("approve")
def review_approve(
    draft_id: str = typer.Argument(..., help="draft id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """采用草稿（记录决策与发布意图，不自动发布）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    drafts = DraftRepository(ws)
    try:
        job_id = _draft_job_id(drafts, draft_id)
    except KeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    try:
        result = _decision_service(ws).accept(job_id)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(f"approved {draft_id} (publication intent recorded)")


@review_app.command("revise")
def review_revise(
    draft_id: str = typer.Argument(..., help="draft id"),
    instruction: str = typer.Option(..., "--instruction", help="重写指令"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """按自然语言指令重写草稿正文（经 critic 校验，不自动发布）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    drafts = DraftRepository(ws)
    try:
        job_id = _draft_job_id(drafts, draft_id)
    except KeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    cards_by_id = {c.id: c for c in EvidenceRepository(ws).list_cards()}
    runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
    try:
        result = _decision_service(ws).revise(
            job_id, instruction, runner=runner, cards_by_id=cards_by_id
        )
    except (RuntimeError, StructuredOutputError) as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        raise typer.Exit(code=1) from exc
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo(result["new_body"])


@review_app.command("skip")
def review_skip(
    draft_id: str = typer.Argument(..., help="draft id"),
    reason: str = typer.Option(..., "--reason", help="跳过理由"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """跳过草稿并记录理由（不写）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    drafts = DraftRepository(ws)
    try:
        job_id = _draft_job_id(drafts, draft_id)
    except KeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    try:
        result = _decision_service(ws).skip(job_id, reason)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(f"skipped {draft_id} (reason: {reason})")


def _run_discovery(settings: Settings) -> EngagementRunResult:
    """执行一次只读发现流程（搜索 → 同行聚合 → 关系评分 → 提案）。"""
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    return run_discovery_engagement_flow(
        settings,
        OpenCliClient(),
        runner,
        reddit_opencli=RedditOpenCliClient(),
        run_id=f"daily_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
    )


def _persist_discovery(ws: Workspace, result: EngagementRunResult) -> None:
    """把发现结果的同行与提案落库（只读流程的落库，供 peers show / connect approve 进入）。

    同行已存在时只并入新平台身份，不覆写已积累的关系字段（relationship_stage / why_relevant 等）。
    """
    peers = PeerRepository(ws)
    interactions = InteractionRepository(ws)
    peer_svc = PeerService()
    for ranked in result.peers:
        merged = peer_svc.merge_discovered(peers.get(ranked.profile.id), ranked.profile)
        peers.upsert(merged)
    for candidate in result.candidates:
        interactions.upsert(candidate, run_id=result.run_id)


def _render_daily(focus: TodayFocus) -> str:
    def _section(title, items, total, render):
        lines = [f"## {title}"]
        if not items:
            lines.append("- (none)")
        else:
            for i in items:
                lines.append(render(i))
        if items and total > len(items):
            lines.append(f"… 还有 {total - len(items)} 个")
        return "\n".join(lines)

    conv = focus["conversations"]
    peer = focus["peers"]
    contrib = focus["contributions"]
    ideas = focus["ideas"]
    return "\n\n".join([
        _section("需要继续的对话", conv["items"], conv["total"],
                 lambda t: f"- {t.id}\t{t.topic}\t{t.status.value}"),
        _section("今天最值得连接的同行", peer["items"], peer["total"],
                 lambda rp: f"- {rp.profile.display_name or rp.profile.id}\t"
                            f"peer_value={rp.value.total:.2f}"),
        _section("可贡献的具体内容", contrib["items"], contrib["total"],
                 lambda c: f"- {c.id}\t[{c.action.value}]\t"
                           f"{' '.join(c.post.content.split())[:60]}"),
        _section("从近期交流产生的观点候选", ideas["items"], ideas["total"],
                 lambda j: f"- {j.id}\t{j.core_message}"),
    ])


@connect_app.command("daily")
def connect_daily(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """连接主循环每日入口：需要继续的对话 → 最值得连接的同行 → 可贡献内容 → 观点候选。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = _run_discovery(settings)
    _persist_discovery(ws, result)

    now = datetime.now(UTC)
    threads = ConversationThreadRepository(ws).list_all()
    needs_follow_up = [
        t for t in threads if ConversationService().needs_follow_up(t, now=now)
    ]
    idea_candidates = [
        j for j in ContentJobRepository(ws).list_jobs()
        if j.status == ContentJobStatus.PROPOSED
    ]

    if as_json:
        typer.echo(json.dumps({
            "run_id": result.run_id,
            "posts_found": result.posts_found,
            "failures": [
                {"platform": f.platform, "query": f.query, "reason": f.reason}
                for f in result.failures
            ],
            "conversations_needing_follow_up": [
                t.model_dump(mode="json") for t in needs_follow_up
            ],
            "peers": [rp.profile.model_dump(mode="json") for rp in result.peers],
            "contributions": [c.model_dump(mode="json") for c in result.candidates],
            "idea_candidates": [j.model_dump(mode="json") for j in idea_candidates],
        }, ensure_ascii=False, indent=2))
        return
    focus = build_today_focus(
        peers=result.peers,
        contributions=result.candidates,
        threads=needs_follow_up,
        ideas=idea_candidates,
        now=now,
    )
    typer.echo(_render_daily(focus))


@connect_app.command("prepare")
def connect_prepare(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """为发现结果准备互动提案并落库（只读发现 + 落库，不做审批/执行）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = _run_discovery(settings)
    _persist_discovery(ws, result)
    if as_json:
        typer.echo(json.dumps(
            [c.model_dump(mode="json") for c in result.candidates],
            ensure_ascii=False, indent=2,
        ))
        return
    if not result.candidates:
        typer.echo("no interaction proposals")
        return
    for c in result.candidates:
        snippet = " ".join(c.post.content.split())[:60]
        typer.echo(f"{c.id}\t{c.action.value}\t{c.peer_id or '-'}\t{snippet}")


@connect_app.command("create")
def connect_create(
    input_url: str = typer.Option(..., "--input", help="帖子 URL"),
    topic: str = typer.Option("", "--topic", help="帖子主题（可选）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """为一个具体帖子 URL 生成互动提案（抓取 → 评分 → 选动作 → 草稿 → 存为 PROPOSED）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    post = fetch_post_by_url(
        input_url, opencli=OpenCliClient(), reddit_opencli=RedditOpenCliClient(), topic=topic
    )
    if post is None:
        typer.echo(f"could not fetch post: {input_url}")
        raise typer.Exit(code=1)
    engagement = settings.engagement
    scored = score_posts(runner, [post], engagement.weights, relationship_by_peer={})
    ranked = rank_candidates(scored, min_candidate_score=engagement.min_candidate_score)
    candidates = generate_proposals(runner, ranked, engagement)
    if not candidates:
        typer.echo("no proposal above threshold")
        return
    candidate = candidates[0]
    InteractionRepository(ws).upsert(candidate, run_id="create")
    if as_json:
        typer.echo(candidate.model_dump_json(indent=2))
    else:
        typer.echo(f"{candidate.id}\t{candidate.action.value}\t{candidate.draft or ''}")


@connect_app.command("approve")
def connect_approve(proposal_id: str = typer.Argument(..., help="proposal id")) -> None:
    """批准提案（PROPOSED→APPROVED，幂等；批准只创建发布意图，不等于已发布）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        InteractionRepository(ws).approve(proposal_id)
    except KeyError:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"approved {proposal_id}")


@connect_app.command("reject")
def connect_reject(
    proposal_id: str = typer.Argument(..., help="proposal id"),
    reason: str = typer.Option(..., "--reason", help="拒绝理由"),
) -> None:
    """拒绝提案并记录理由（→ REJECTED）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        InteractionRepository(ws).reject(proposal_id, reason)
    except KeyError:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"rejected {proposal_id}")


@connect_app.command("edit")
def connect_edit(
    proposal_id: str = typer.Argument(..., help="proposal id"),
    path: str = typer.Option(..., "--file", help="人工修订后的草稿文件"),
) -> None:
    """保存人工修订草稿到 revised_draft（不自动批准、不改变发布权限）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = InteractionRepository(ws)
    if repo.get(proposal_id) is None:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1)
    try:
        revised = Path(path).read_text()
    except OSError as exc:
        typer.echo(f"cannot read file: {exc}")
        raise typer.Exit(code=1) from exc
    repo.edit(proposal_id, revised)
    typer.echo(f"edited {proposal_id}")


@connect_app.command("record")
def connect_record(
    proposal_id: str = typer.Argument(..., help="proposal id"),
    url: str = typer.Option(..., "--url", help="实际发布/互动的 URL"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """记录一次真实互动为 InteractionRecord（需先批准；同一 proposal 幂等，不重复计两次）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    proposal = InteractionRepository(ws).get(proposal_id)
    if proposal is None:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1)
    if proposal.status != InteractionStatus.APPROVED:
        typer.echo(f"proposal not approved (approve first): {proposal_id}")
        raise typer.Exit(code=1)
    record = InteractionRecord(
        id=f"rec_{proposal_id}",
        proposal_id=proposal_id,
        peer_id=proposal.peer_id or "",
        platform=proposal.post.platform,
        source_url=url,
        published_body=proposal.revised_draft or proposal.draft or "",
        occurred_at=datetime.now(UTC),
        outcome="published",
    )
    InteractionRecordRepository(ws).upsert(record)
    if proposal.peer_id:
        topic = (
            proposal.post.matched_topics[0]
            if proposal.post.matched_topics
            else (proposal.contribution_type.value if proposal.contribution_type else "general")
        )
        svc = ConversationService()
        opened = svc.open_thread(peer_id=proposal.peer_id, topic=topic)
        thread = ConversationThreadRepository(ws).get(opened.id) or opened
        thread = svc.append_interaction(thread, record.id, occurred_at=record.occurred_at)
        ConversationThreadRepository(ws).upsert(thread)
    if as_json:
        typer.echo(record.model_dump_json(indent=2))
    else:
        typer.echo(f"recorded {record.id}")


@peers_app.command("list")
def peers_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出全部同行档案（按 peer id 稳定排序）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peers = PeerRepository(ws).list_all()
    if as_json:
        typer.echo(json.dumps(
            [p.model_dump(mode="json") for p in peers], ensure_ascii=False, indent=2
        ))
        return
    if not peers:
        typer.echo("no peers")
        return
    typer.echo("id\tdisplay_name\tstage\tshared_topics")
    for p in peers:
        topics = ",".join(p.shared_topics)
        typer.echo(f"{p.id}\t{p.display_name or '-'}\t{p.relationship_stage.value}\t{topics}")


@peers_app.command("show")
def peers_show(
    peer_id: str = typer.Argument(..., help="peer id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示同行档案：身份、共同主题、互动历史与下一步。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peer = PeerRepository(ws).get(peer_id)
    if peer is None:
        typer.echo(f"peer not found: {peer_id}")
        raise typer.Exit(code=1)
    if as_json:
        payload = peer.model_dump(mode="json")
        payload["interactions"] = [
            r.model_dump(mode="json")
            for r in InteractionRecordRepository(ws).list_by_peer(peer_id)
        ]
        payload["threads"] = [
            t.model_dump(mode="json")
            for t in ConversationThreadRepository(ws).list_by_peer(peer_id)
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(f"id: {peer.id}")
    typer.echo(f"display_name: {peer.display_name or '-'}")
    typer.echo(f"stage: {peer.relationship_stage.value}")
    typer.echo(f"expertise_topics: {', '.join(peer.expertise_topics) or '-'}")
    typer.echo(f"shared_topics: {', '.join(peer.shared_topics) or '-'}")
    typer.echo(f"why_relevant: {peer.why_relevant or '-'}")
    typer.echo(f"next_context: {peer.next_context or '-'}")


@peers_app.command("get")
def peers_get(peer_id: str = typer.Argument(..., help="peer id")) -> None:
    """Agent 用确定性读取：等同 peers show --json。"""
    peers_show(peer_id, as_json=True)


@conversations_app.command("list")
def conversations_list(
    needs_follow_up: bool = typer.Option(False, "--needs-follow-up", help="只列需要跟进的对话"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """列出对话线索（可只列需要跟进的）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    threads = ConversationThreadRepository(ws).list_all()
    if needs_follow_up:
        now = datetime.now(UTC)
        threads = [t for t in threads if ConversationService().needs_follow_up(t, now=now)]
    if as_json:
        typer.echo(json.dumps(
            [t.model_dump(mode="json") for t in threads], ensure_ascii=False, indent=2
        ))
        return
    if not threads:
        typer.echo("no conversations")
        return
    typer.echo("id\tpeer_id\ttopic\tstatus")
    for t in threads:
        typer.echo(f"{t.id}\t{t.peer_id}\t{t.topic}\t{t.status.value}")


@conversations_app.command("show")
def conversations_show(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示对话线索全文：open questions / agreements / disagreements / experiments。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    thread = ConversationThreadRepository(ws).get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(thread.model_dump_json(indent=2))
        return
    typer.echo(f"id: {thread.id}")
    typer.echo(f"peer_id: {thread.peer_id}")
    typer.echo(f"topic: {thread.topic}")
    typer.echo(f"status: {thread.status.value}")
    typer.echo(f"interactions: {', '.join(thread.interaction_ids) or '-'}")
    typer.echo(f"open_questions: {', '.join(thread.open_questions) or '-'}")
    typer.echo(f"agreements: {', '.join(thread.agreements) or '-'}")
    typer.echo(f"disagreements: {', '.join(thread.disagreements) or '-'}")
    typer.echo(f"possible_experiments: {', '.join(thread.possible_experiments) or '-'}")


@conversations_app.command("get")
def conversations_get(conversation_id: str = typer.Argument(..., help="conversation id")) -> None:
    """Agent 用确定性读取：等同 conversations show --json。"""
    conversations_show(conversation_id, as_json=True)


@conversations_app.command("follow-up")
def conversations_follow_up(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """恢复对话上下文并提出下一步（确定性恢复；语义建议由 conversation-follow-up 补充）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    thread = ConversationThreadRepository(ws).get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    open_questions = thread.open_questions
    next_step = (
        "回答未解问题或提出实验"
        if open_questions
        else "已无未解问题；确认是否关闭或延续新主题"
    )
    if as_json:
        typer.echo(json.dumps({
            "conversation_id": thread.id,
            "topic": thread.topic,
            "open_questions": open_questions,
            "next_step": next_step,
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(f"conversation: {thread.id} ({thread.topic})")
    typer.echo(f"open_questions: {', '.join(open_questions) or '-'}")
    typer.echo(f"next_step: {next_step}")


@practice_app.command("start")
def practice_start(
    idea: str = typer.Option(None, "--idea", help="关联 idea id"),
    attempt: str = typer.Option(..., "--attempt", help="用户首稿"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """开始一次表达练习（可选关联 idea + 首稿）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    session = PracticeService(PracticeSessionRepository(ws), runner).start(
        idea_id=idea, initial_attempt=attempt
    )
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"id: {session.id}")
        typer.echo("下一步：诊断本次表达")


@practice_app.command("diagnose")
def practice_diagnose(
    session_id: str = typer.Argument(..., help="session id"),
    context: str = typer.Option("", "--context", help="可选 idea 语境"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """诊断最大问题 + 追问一个问题（LLM）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(ws), runner).diagnose(
            session_id, context=context
        )
    except KeyError:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1) from None
    except (ValueError, RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"diagnosis: {session.diagnosis}")
        typer.echo(f"question: {session.questions_asked[-1]}")
        typer.echo("下一步：保存最终版")


@practice_app.command("save")
def practice_save(
    session_id: str = typer.Argument(..., help="session id"),
    revision: str = typer.Option(..., "--revision", help="修订后的表达"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """追加一次修订。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(ws), runner).save_revision(
            session_id, revision
        )
    except KeyError:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1) from None
    except (ValueError, RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"revisions: {len(session.revisions)}")
        typer.echo("下一步：诊断本次表达")


@practice_app.command("finish")
def practice_finish(
    session_id: str = typer.Argument(..., help="session id"),
    final: str = typer.Option(..., "--final", help="最终表达"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """记最终版 + LLM 经验总结，置 finished。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(ws), runner).finish(
            session_id, final
        )
    except KeyError:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1) from None
    except (ValueError, RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"lesson: {session.lesson}")
        typer.echo("下一步：查看会话")


@practice_app.command("show")
def practice_show(
    session_id: str = typer.Argument(..., help="session id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示会话（首稿 / 诊断 / 追问 / 修订 / 最终版 / lesson）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    session = PracticeSessionRepository(ws).get(session_id)
    if session is None:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"id: {session.id}")
        typer.echo(f"status: {session.status}")
        typer.echo(f"initial_attempt: {session.initial_attempt}")
        typer.echo(f"diagnosis: {session.diagnosis}")
        typer.echo(f"questions_asked: {json.dumps(session.questions_asked, ensure_ascii=False)}")
        typer.echo(f"revisions: {json.dumps(session.revisions, ensure_ascii=False)}")
        typer.echo(f"final_expression: {session.final_expression}")
        typer.echo(f"lesson: {session.lesson}")


@style_app.command("analyze")
def style_analyze(
    text: str = typer.Option(None, "--text", help="要分析的文本"),
    file: str = typer.Option(None, "--file", help="文本文件（可用 --- 分隔多篇）"),
    url: str = typer.Option(None, "--url", help="要分析的链接（X/Reddit/普通网页）"),
    compare_voice: bool = typer.Option(False, "--compare-voice", help="追加对比我的画像"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """分析写作风格，产出风格报告（可选与个人声音画像比较）。"""
    provided = sum(x is not None for x in (text, file, url))
    if provided != 1:
        typer.echo("exactly one of --text / --file / --url is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    resolver = SourceResolver(OpenCliClient(), RedditOpenCliClient(), WebFetcher())
    try:
        if text is not None:
            source = resolver.resolve_text(text)
        elif file is not None:
            source = resolver.resolve_file(file)
        else:
            source = resolver.resolve_url(url)
        runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
        report = WritingStyleService(runner).analyze(source)
    except (RuntimeError, StructuredOutputError, OSError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if compare_voice:
        try:
            runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
            voice = load_voice_profile(settings.paths.voice_profile_path)
            comparison = WritingStyleService(runner).compare(report, voice)
        except (RuntimeError, StructuredOutputError) as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        if as_json:
            typer.echo(json.dumps(
                {"report": report.model_dump(mode="json"),
                 "comparison": comparison.model_dump(mode="json")},
                ensure_ascii=False, indent=2,
            ))
        else:
            typer.echo(json.dumps(comparison.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        typer.echo(_render_report(report))


def _render_report(report: StyleReport) -> str:
    lines = [f"# 写作风格分析（{report.scope}，{report.overall_confidence}）"]
    for name in ("opening", "structure", "rhythm", "word_choice", "stance",
                 "concreteness", "reader_relationship", "rhetorical_patterns"):
        for ev in getattr(report, name):
            lines.append(f"- [{name}] {ev.observation}")
    if report.transferable_techniques:
        lines.append("\n可借鉴：")
        lines += [f"- {t}" for t in report.transferable_techniques]
    if report.experiments_for_me:
        lines.append("\n可实验：")
        lines += [f"- {e}" for e in report.experiments_for_me]
    return "\n".join(lines)


if __name__ == "__main__":
    app()
