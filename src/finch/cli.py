"""Finch CLI（spec 10）。"""

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
    save_voice_profile,
)
from .content.writer import rewrite_with_instruction
from .conversations.service import ConversationService
from .drafts.service import DraftCreateResult, DraftService
from .engagement.flow import EngagementRunResult, run_discovery_engagement_flow
from .engagement.models import InteractionRecord, InteractionStatus
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.gh_client import GhClient
from .ideas.commit_service import CommitService
from .ideas.fragment_service import FragmentService
from .ideas.opportunity import Opportunity
from .ideas.service import IdeaService
from .inbox.models import DecisionAction, InboxTrack
from .inbox.service import InboxDecisionService, list_items
from .learn.models import Feedback, OutcomeAssessment
from .learn.reflection import WeeklyReflectionService, render_reflection
from .learn.weekly import weekly_analysis
from .llm.openai_compatible import create_runner
from .practice.service import PracticeService
from .reddit.opencli_client import RedditOpenCliClient
from .settings import Settings, load_settings
from .storage.database import Store
from .storage.repositories import (
    ContentJobRepository,
    ConversationEvidenceRepository,
    ConversationThreadRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    FeedbackRepository,
    InteractionRecordRepository,
    InteractionRepository,
    OpportunityRepository,
    PeerRepository,
    PracticeSessionRepository,
    PublicationIntentRepository,
)
from .style.models import StyleReport
from .style.service import WritingStyleService
from .style.source_resolver import SourceResolver
from .twitter.normalizer import normalize_tweets
from .twitter.opencli_client import OpenCliClient
from .twitter.query_builder import QueryBuilder
from .webfetch.fetcher import WebFetcher

app = typer.Typer(help="Finch: evidence-driven builder companion.")

github_app = typer.Typer(help="GitHub 读取与工程事件提取")
app.add_typer(github_app, name="github")

twitter_app = typer.Typer(help="Twitter 搜索与读取")
app.add_typer(twitter_app, name="twitter")

voice_app = typer.Typer(help="Manage the author voice profile (local, no auto-publish)")
app.add_typer(voice_app, name="voice")

ideas_app = typer.Typer(help="Idea 候选流（commit/search 提炼 + 状态转换）")
app.add_typer(ideas_app, name="ideas")

drafts_app = typer.Typer(help="Draft 生成（已确认 idea → 草稿，不自动发布）")
app.add_typer(drafts_app, name="drafts")

review_app = typer.Typer(help="Review original drafts (accept/revise/skip, no auto-publish)")
app.add_typer(review_app, name="review")

connect_app = typer.Typer(help="连接主循环：daily / prepare / approve / reject / record")
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
    return "\n".join(lines)


def _idea_next_steps(job: ContentJob) -> list[str]:
    """按状态给出下一步动作（不含 CLI 命令）。"""
    if job.status == ContentJobStatus.PROPOSED:
        return ["- 确认立场", "- 修改立场", "- 跳过"]
    if job.status == ContentJobStatus.CONFIRMED:
        return ["- 生成草稿", "- 表达练习"]
    if job.status == ContentJobStatus.DRAFTED:
        return ["- 查看草稿"]
    return []


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
    steps = _idea_next_steps(job)
    if steps:
        lines += ["", "下一步:"] + steps
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


def _render_opportunity_list(opps: list[Opportunity]) -> str:
    """交流机会列表：带表头，仍保持一机会一行。"""
    lines = ["id\turl\tshared_tension"]
    lines.extend(f"{opp.id}\t{opp.source_post.url}\t{opp.shared_tension}" for opp in opps)
    return "\n".join(lines)


def _render_opportunity_detail(opp: Opportunity) -> str:
    """单个交流机会的完整可读视图。"""
    return "\n".join(
        [
            f"id: {opp.id}",
            f"url: {opp.source_post.url}",
            f"author: @{opp.source_post.author}",
            f"source_text: {opp.source_post.text}",
            "",
            f"shared_tension: {opp.shared_tension}",
            f"why_relevant: {opp.why_relevant}",
            f"response_angles: {', '.join(opp.response_angles)}",
            f"knowledge_gap: {opp.knowledge_gap}",
            f"relationship_value: {opp.relationship_value}",
            "",
            "下一步:",
            "- 转成 idea",
        ]
    )


def _since_iso(since: str | None) -> str | None:
    if since is None:
        return None
    if since.endswith("h"):
        return (datetime.now(UTC) - timedelta(hours=int(since[:-1]))).isoformat()
    if since.endswith("d"):
        return (datetime.now(UTC) - timedelta(days=int(since[:-1]))).isoformat()
    return since


@app.command()
def init(
    prune: bool = typer.Option(False, "--prune", help="删除数据库里已废弃模型遗留的孤儿表"),
) -> None:
    """初始化 var/ 目录与数据库 schema；--prune 时额外清理 schema 漂移。"""
    settings = load_settings()
    from .storage.database import Store

    store = Store(settings.paths.db_path)
    store.init()
    if prune:
        dropped = store.prune_orphan_tables()
        typer.echo(
            f"pruned orphan tables: {', '.join(dropped)}" if dropped
            else "no orphan tables to prune"
        )
        legacy = store.prune_legacy_content_jobs()
        typer.echo(
            f"pruned legacy content jobs: {', '.join(legacy)}" if legacy
            else "no legacy content jobs to prune"
        )
    typer.echo(f"initialized: {settings.paths.db_path}")


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
    repo: str = typer.Option(None, "--repo", help="仓库（默认 settings.repositories[0]）"),
    since: str = typer.Option("7d", "--since", help="起始时间（如 7d / 24h / ISO 时间）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从最近 Commit 提炼 idea 候选并幂等落库（不生成草稿）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    gh = GhClient()
    if repo is None:
        if not settings.repositories:
            typer.echo("--repo is required (no repositories configured)")
            raise typer.Exit(code=1)
        repo = settings.repositories[0]
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
    idea_service = IdeaService(ContentJobRepository(store))
    jobs = [idea_service.create_candidate(idea) for idea in ideas]
    if as_json:
        payload = [
            {
                "id": job.id,
                "origin": job.origin,
                "core_point": job.core_message,
                "status": job.status.value,
                "generation_key": job.generation_key,
            }
            for job in jobs
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_idea_list(jobs))


@ideas_app.command("create")
def ideas_create(
    text: str = typer.Option(None, "--text", help="用户输入的一句话/片段"),
    conversation: str = typer.Option(None, "--conversation", help="对话线索 id"),
    opportunity: str = typer.Option(None, "--opportunity", help="交流机会 id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """把用户片段 / 交流证据 / 交流机会结构化为 idea 候选并落库。"""
    provided = sum(x is not None for x in (text, conversation, opportunity))
    if provided != 1:
        typer.echo("exactly one of --text / --conversation / --opportunity is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    try:
        if text is not None:
            idea = service.from_text(text)
        elif conversation is not None:
            thread = ConversationThreadRepository(store).get(conversation)
            if thread is None:
                typer.echo(f"conversation not found: {conversation}")
                raise typer.Exit(code=1)
            interactions = InteractionRecordRepository(store).list_by_peer(thread.peer_id)
            idea = service.from_thread(thread, interactions=interactions)
        else:
            opp = OpportunityRepository(store).get(opportunity)
            if opp is None:
                typer.echo(f"opportunity not found: {opportunity}")
                raise typer.Exit(code=1)
            idea = service.from_opportunity(opp)
        job = IdeaService(ContentJobRepository(store)).create_candidate(idea)
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


@ideas_app.command("list")
def ideas_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出全部 idea 候选，一行一个；旧行在系统警告中提示。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
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
    store = Store(settings.paths.db_path)
    store.init()
    job = ContentJobRepository(store).get_job(idea_id)
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
    store = Store(settings.paths.db_path)
    store.init()
    service = IdeaService(ContentJobRepository(store))
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
        typer.echo("下一步：生成草稿")


@ideas_app.command("revise-position")
def ideas_revise_position(
    idea_id: str = typer.Argument(..., help="idea id"),
    position_file: str = typer.Option(..., "--file", help="作者立场 YAML 文件路径"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从 YAML 读取作者立场并更新（不改状态）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    try:
        position = AuthorPosition.model_validate(
            yaml.safe_load(Path(position_file).read_text())
        )
    except (OSError, yaml.YAMLError, ValueError) as exc:
        typer.echo(f"invalid position file: {exc}")
        raise typer.Exit(code=1) from exc
    service = IdeaService(ContentJobRepository(store))
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
    store = Store(settings.paths.db_path)
    store.init()
    service = IdeaService(ContentJobRepository(store))
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
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = DraftService(
        DraftRepository(store),
        CriticReportRepository(store),
        ContentJobRepository(store),
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
    store = Store(settings.paths.db_path)
    store.init()
    draft = DraftRepository(store).get_draft(draft_id)
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
    store = Store(settings.paths.db_path)
    store.init()
    draft_repo = DraftRepository(store)
    draft = draft_repo.get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    job = None
    if draft.content_job_id:
        job = ContentJobRepository(store).get_job(draft.content_job_id)
    cards_by_id = {c.id: c for c in EvidenceRepository(store).list_cards()}
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
    store = Store(settings.paths.db_path)
    store.init()
    if DraftRepository(store).get_draft(draft_id) is None:
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
    FeedbackRepository(store).save_feedback(feedback)
    if as_json:
        typer.echo(feedback.model_dump_json(indent=2))
    else:
        typer.echo(f"recorded feedback for {draft_id}")


@app.command("weekly")
def run_weekly(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """周复盘：确定性指标 + LLM 定性解读（一个训练重点）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    since = datetime.now(UTC) - timedelta(days=7)
    report = weekly_analysis(
        DraftRepository(store),
        DecisionRecordRepository(store),
        FeedbackRepository(store),
        ContentJobRepository(store),
        CriticReportRepository(store),
        since=since,
    )
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    # 反馈按 recorded_at 对齐 7 天窗口（与 weekly_analysis 的 since 一致），避免复盘
    # 输入随库无界膨胀。ConversationEvidence 无时间戳字段，无法按窗过滤，仍取全量
    # （其量级受 verified/promote 门限约束）。
    window_feedbacks = [
        fb for fb in FeedbackRepository(store).list_feedbacks() if fb.recorded_at >= since
    ]
    try:
        reflection = WeeklyReflectionService(runner).reflect(
            report,
            feedbacks=window_feedbacks,
            conversation_evidence=ConversationEvidenceRepository(store).list_all(),
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


@voice_app.command("approve-example")
def voice_approve_example(draft_id: str) -> None:
    """把草稿追加为 approved example（按 id 去重；已接受即入库）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    draft = DraftRepository(store).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    decisions = {d.draft_id: d for d in DecisionRecordRepository(store).list()}
    decision = decisions.get(draft_id)
    if decision is None or decision.action != DecisionAction.ACCEPT:
        typer.echo(f"not accepted: {draft_id}")
        raise typer.Exit(code=1)
    text = decision.revised_body or draft.body
    path = settings.paths.voice_profile_path
    profile = load_voice_profile(path)
    if any(ex.id == draft_id for ex in profile.approved_examples):
        typer.echo(f"already approved: {draft_id}")
        return
    profile.rejected_examples = [
        ex for ex in profile.rejected_examples if ex.id != draft_id
    ]
    profile.approved_examples.append(ApprovedExample(id=draft_id, text=text))
    save_voice_profile(profile, path)
    typer.echo(f"approved example: {draft_id}")


@voice_app.command("reject-example")
def voice_reject_example(
    draft_id: str,
    reason: str = typer.Option(..., "--reason", help="拒绝理由"),  # noqa: B008
) -> None:
    """把草稿追加为 rejected example（按 id 去重）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    draft = DraftRepository(store).get_draft(draft_id)
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


def _decision_service(store: Store) -> InboxDecisionService:
    return InboxDecisionService(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        publication_intents=PublicationIntentRepository(store),
        interactions=InteractionRepository(store),
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
    store = Store(settings.paths.db_path)
    store.init()
    items = list_items(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        interactions=InteractionRepository(store),
        cards=EvidenceRepository(store),
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
    store = Store(settings.paths.db_path)
    store.init()
    draft = DraftRepository(store).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    reports = CriticReportRepository(store).list_reports(draft_id)
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
    store = Store(settings.paths.db_path)
    store.init()
    drafts = DraftRepository(store)
    try:
        job_id = _draft_job_id(drafts, draft_id)
    except KeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    try:
        result = _decision_service(store).accept(job_id)
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
    store = Store(settings.paths.db_path)
    store.init()
    drafts = DraftRepository(store)
    try:
        job_id = _draft_job_id(drafts, draft_id)
    except KeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    cards_by_id = {c.id: c for c in EvidenceRepository(store).list_cards()}
    runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
    try:
        result = _decision_service(store).revise(
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
    store = Store(settings.paths.db_path)
    store.init()
    drafts = DraftRepository(store)
    try:
        job_id = _draft_job_id(drafts, draft_id)
    except KeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    try:
        result = _decision_service(store).skip(job_id, reason)
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


def _persist_discovery(store: Store, result: EngagementRunResult) -> None:
    """把发现结果的同行与提案落库（只读流程的落库，供 peers show / connect approve 进入）。"""
    peers = PeerRepository(store)
    interactions = InteractionRepository(store)
    for ranked in result.peers:
        peers.upsert(ranked.profile)
    for candidate in result.candidates:
        interactions.upsert(candidate, run_id=result.run_id)


def _render_daily(needs_follow_up, peers, contributions, idea_candidates) -> str:
    lines = ["## 需要继续的对话"]
    if needs_follow_up:
        for t in needs_follow_up:
            lines.append(f"- {t.id}\t{t.topic}\t{t.status.value}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## 今天最值得连接的同行")
    if peers:
        for rp in peers:
            name = rp.profile.display_name or rp.profile.id
            lines.append(f"- {name}\tpeer_value={rp.value.total:.2f}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## 可贡献的具体内容")
    if contributions:
        for c in contributions:
            snippet = " ".join(c.post.content.split())[:60]
            lines.append(f"- {c.id}\t[{c.action.value}]\t{snippet}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## 从近期交流产生的观点候选")
    if idea_candidates:
        for j in idea_candidates:
            lines.append(f"- {j.id}\t{j.core_message}")
    else:
        lines.append("- (none)")
    return "\n".join(lines)


@connect_app.command("daily")
def connect_daily(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """连接主循环每日入口：需要继续的对话 → 最值得连接的同行 → 可贡献内容 → 观点候选。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    result = _run_discovery(settings)
    _persist_discovery(store, result)

    now = datetime.now(UTC)
    threads = ConversationThreadRepository(store).list_all()
    needs_follow_up = [
        t for t in threads if ConversationService().needs_follow_up(t, now=now)
    ]
    idea_candidates = [
        j for j in ContentJobRepository(store).list_jobs()
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
    typer.echo(_render_daily(needs_follow_up, result.peers, result.candidates, idea_candidates))


@connect_app.command("prepare")
def connect_prepare(
    opportunity_id: str = typer.Argument(None, help="可选：已存 opportunity id（保留给定向准备）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """为发现结果准备互动提案并落库（只读发现 + 落库，不做审批/执行）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    result = _run_discovery(settings)
    _persist_discovery(store, result)
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


@connect_app.command("approve")
def connect_approve(proposal_id: str = typer.Argument(..., help="proposal id")) -> None:
    """批准提案（PROPOSED→APPROVED，幂等；批准只创建发布意图，不等于已发布）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    try:
        InteractionRepository(store).approve(proposal_id)
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
    store = Store(settings.paths.db_path)
    store.init()
    try:
        InteractionRepository(store).reject(proposal_id, reason)
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
    store = Store(settings.paths.db_path)
    store.init()
    repo = InteractionRepository(store)
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
    store = Store(settings.paths.db_path)
    store.init()
    proposal = InteractionRepository(store).get(proposal_id)
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
    InteractionRecordRepository(store).upsert(record)
    if as_json:
        typer.echo(record.model_dump_json(indent=2))
    else:
        typer.echo(f"recorded {record.id}")


@peers_app.command("list")
def peers_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出全部同行档案（按 peer id 稳定排序）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    peers = PeerRepository(store).list_all()
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
    store = Store(settings.paths.db_path)
    store.init()
    peer = PeerRepository(store).get(peer_id)
    if peer is None:
        typer.echo(f"peer not found: {peer_id}")
        raise typer.Exit(code=1)
    if as_json:
        payload = peer.model_dump(mode="json")
        payload["interactions"] = [
            r.model_dump(mode="json")
            for r in InteractionRecordRepository(store).list_by_peer(peer_id)
        ]
        payload["threads"] = [
            t.model_dump(mode="json")
            for t in ConversationThreadRepository(store).list_by_peer(peer_id)
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


@conversations_app.command("list")
def conversations_list(
    needs_follow_up: bool = typer.Option(False, "--needs-follow-up", help="只列需要跟进的对话"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """列出对话线索（可只列需要跟进的）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    threads = ConversationThreadRepository(store).list_all()
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
    store = Store(settings.paths.db_path)
    store.init()
    thread = ConversationThreadRepository(store).get(conversation_id)
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


@conversations_app.command("follow-up")
def conversations_follow_up(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """恢复对话上下文并提出下一步（确定性恢复；语义建议由 conversation-follow-up 补充）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    thread = ConversationThreadRepository(store).get(conversation_id)
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
    opportunity: str = typer.Option(None, "--opportunity", help="关联 opportunity id"),
    attempt: str = typer.Option(..., "--attempt", help="用户首稿"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """开始一次表达练习（记 idea/opportunity + 首稿）。"""
    if (idea is None) == (opportunity is None):
        typer.echo("exactly one of --idea / --opportunity is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    session = PracticeService(PracticeSessionRepository(store), runner).start(
        idea_id=idea, opportunity_id=opportunity, initial_attempt=attempt
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
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(store), runner).diagnose(
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
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(store), runner).save_revision(
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
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(store), runner).finish(
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
    store = Store(settings.paths.db_path)
    store.init()
    session = PracticeSessionRepository(store).get(session_id)
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
    Store(settings.paths.db_path).init()
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
