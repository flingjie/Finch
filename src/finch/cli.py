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
from .content.jobs import AuthorPosition
from .content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from .content.writer import rewrite_with_instruction
from .drafts.service import DraftCreateResult, DraftService
from .engagement.metrics import (
    compute_metrics,
    render_metrics,
    render_run_stats,
    summarize_run_stats,
)
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.gh_client import GhClient
from .ideas.commit_service import CommitService
from .ideas.search_service import SearchService
from .ideas.service import IdeaService
from .inbox.models import DecisionAction, InboxTrack
from .inbox.service import InboxDecisionService, list_items
from .learn.models import Feedback, OutcomeAssessment
from .learn.weekly import render_weekly, weekly_analysis
from .llm.openai_compatible import create_runner
from .settings import load_settings
from .storage.database import Store
from .storage.repositories import (
    ContentJobRepository,
    ConversationEvidenceRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    EngagementRunStatsRepository,
    EvidenceRepository,
    FeedbackRepository,
    FeedbackSnapshotRepository,
    InteractionRepository,
    PublicationIntentRepository,
)
from .twitter.normalizer import normalize_tweets
from .twitter.opencli_client import OpenCliClient
from .twitter.query_builder import QueryBuilder

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

engagement_app = typer.Typer(help="Review engagement candidates (human-in-the-loop)")
app.add_typer(engagement_app, name="engagement")


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
    """读取最近 Commit，提取工程事件并输出 Evidence Cards。"""
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
    """从最近 Commit 提炼 Idea 候选并幂等落库为 ContentJob（不生成草稿）。"""
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
        for job in jobs:
            typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")


@ideas_app.command("search")
def ideas_search(
    topic: str = typer.Option(None, "--topic", help="搜索话题（默认 settings.twitter.queries[0]）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从公开讨论搜索提炼 Idea 候选并幂等落库为 ContentJob（不生成草稿）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    builder = QueryBuilder(
        settings.twitter.queries, per_query_limit=settings.twitter.per_query_limit
    )
    opencli = OpenCliClient()
    if topic is None:
        if not builder.configs:
            typer.echo("--topic is required (no twitter queries configured)")
            raise typer.Exit(code=1)
        topic = builder.configs[0].text
    tweets = opencli.search(topic, product="top", limit=builder.per_query_limit)
    posts = normalize_tweets(tweets)
    ideas = SearchService(opencli, builder).to_ideas(posts, topic=topic)
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
        for job in jobs:
            typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")


@ideas_app.command("list")
def ideas_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出全部 idea 候选（ContentJob），一行一个；旧行在系统警告中提示。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    jobs = sorted(repo.list_jobs(), key=lambda j: j.id)
    failures = repo.list_job_parse_failures()
    if as_json:
        payload = [
            {"id": job.id, "status": job.status.value, "core_point": job.core_message}
            for job in jobs
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for job in jobs:
        typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")
    if failures:
        preview = ", ".join(failures[:5]) + ("…" if len(failures) > 5 else "")
        typer.echo(
            f"\n系统警告：检测到 {len(failures)} 条旧版 job 记录无法解析（{preview}），"
            "已跳过。建议运行 `finch init --prune` 清理。"
        )


@ideas_app.command("show")
def ideas_show(
    idea_id: str = typer.Argument(..., help="idea id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示单个 idea 候选（--json 输出完整 ContentJob）。"""
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
        typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")


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
        typer.echo(f"{job.id}\t{job.status.value}")


@ideas_app.command("revise-position")
def ideas_revise_position(
    idea_id: str = typer.Argument(..., help="idea id"),
    position_file: str = typer.Option(..., "--file", help="AuthorPosition YAML 文件路径"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从 YAML 读取 AuthorPosition 并更新立场（不改状态）。"""
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
        typer.echo(f"{job.id}\t{job.status.value}")


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
        typer.echo(f"{job.id}\t{job.status.value}")


def _render_draft_result(result: DraftCreateResult) -> str:
    draft = result.draft
    passed = result.outcome == "pass"
    verdict = "通过" if passed else f"未通过（重写 {result.critic_rounds} 轮后仍未满足）"
    lines = [
        "草稿已生成并通过质量检查，当前等待你的审核。" if passed
        else "草稿已生成，但质量检查未完全通过，请人工判读。",
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
        f"- 采用并进入发布意图：finch review approve {draft.id}",
        f"- 继续修改：finch drafts revise {draft.id} --instruction \"…\"",
        f"- 放弃草稿：finch review skip {draft.id} --reason \"…\"",
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
    """从已确认 idea 生成草稿并落库 Draft + CriticReport（不自动发布）。"""
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
        typer.echo(draft.id)
        typer.echo(draft.body)


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
def run_weekly() -> None:
    """周复盘：汇总最近 7 天的批准率、修改/跳过原因、内容效果指标与已发布候选。"""
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
    typer.echo(render_weekly(report))


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
    for item in original:
        snippet = " ".join(item.draft.split())[:80]
        typer.echo(f"{item.draft_id}\t{item.content_type}\t{snippet}")


@review_app.command("show")
def review_show(
    draft_id: str = typer.Argument(..., help="draft id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示草稿正文与（若存在）critic 报告。"""
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
        typer.echo(draft.body)
        if reports:
            typer.echo("\n--- critic ---")
            typer.echo(json.dumps(reports, ensure_ascii=False, indent=2))


@review_app.command("approve")
def review_approve(
    draft_id: str = typer.Argument(..., help="draft id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """采用草稿（写 DecisionRecord + PublicationIntent，不自动发布）。"""
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
        typer.echo(f"approved {draft_id}")


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
        typer.echo(f"skipped {draft_id}")


@engagement_app.command("list")
def engagement_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出 pending 互动候选（id + action + 帖子 url/摘要）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    candidates = InteractionRepository(store).list_pending()
    if as_json:
        typer.echo(
            json.dumps(
                [c.model_dump(mode="json") for c in candidates],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not candidates:
        typer.echo("no pending candidates")
        return
    for candidate in candidates:
        snippet = " ".join(candidate.post.content.split())[:60]
        typer.echo(f"{candidate.id}\t{candidate.action.value}\t{candidate.post.url} — {snippet}")


@engagement_app.command("show")
def engagement_show(
    candidate_id: str = typer.Argument(..., help="candidate id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """打印候选全文：原帖 + 作者 + 五维评分与理由 + 动作 + 草稿 + 事实风险。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    candidate = InteractionRepository(store).get(candidate_id)
    if candidate is None:
        typer.echo(f"candidate not found: {candidate_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(candidate.model_dump_json(indent=2))
        return
    score = candidate.score
    typer.echo(f"id: {candidate.id}")
    typer.echo(f"status: {candidate.status.value}")
    typer.echo(f"action: {candidate.action.value}")
    typer.echo(f"approval_required: {candidate.approval_required}")
    typer.echo(f"post: {candidate.post.url}")
    typer.echo(f"author: @{candidate.post.author_name} ({candidate.post.author_id})")
    typer.echo(f"post_content: {candidate.post.content}")
    typer.echo(
        f"score: relevance={score.relevance:.3f} novelty={score.novelty:.3f} "
        f"discussability={score.discussability:.3f} "
        f"practical_evidence={score.practical_evidence:.3f} "
        f"relationship_value={score.relationship_value:.3f} total={score.total:.3f}"
    )
    typer.echo(f"score_reasons: {', '.join(score.reasons)}")
    typer.echo(f"draft: {candidate.draft or '(none)'}")
    if candidate.revised_draft:
        typer.echo(f"revised_draft: {candidate.revised_draft}")
    typer.echo(f"intent: {candidate.intent or '(none)'}")
    typer.echo(f"source_summary: {candidate.source_summary or '(none)'}")
    typer.echo(f"factual_risks: {json.dumps(candidate.factual_risks)}")
    if candidate.reject_reason:
        typer.echo(f"reject_reason: {candidate.reject_reason}")


@engagement_app.command("approve")
def engagement_approve(candidate_id: str = typer.Argument(..., help="candidate id")) -> None:
    """批准候选（PROPOSED→APPROVED，幂等，不自动发布）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    try:
        InteractionRepository(store).approve(candidate_id)
    except KeyError:
        typer.echo(f"candidate not found: {candidate_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"approved {candidate_id}")


@engagement_app.command("reject")
def engagement_reject(
    candidate_id: str = typer.Argument(..., help="candidate id"),
    reason: str = typer.Option(..., "--reason", help="拒绝理由"),
) -> None:
    """拒绝候选并记录理由（→ REJECTED）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    try:
        InteractionRepository(store).reject(candidate_id, reason)
    except KeyError:
        typer.echo(f"candidate not found: {candidate_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"rejected {candidate_id}")


@engagement_app.command("edit")
def engagement_edit(
    candidate_id: str = typer.Argument(..., help="candidate id"),
    path: str = typer.Option(..., "--file", help="人工修订后的草稿文件"),
) -> None:
    """保存人工修订草稿到 revised_draft（不自动批准、不改变发布权限）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    repo = InteractionRepository(store)
    if repo.get(candidate_id) is None:
        typer.echo(f"candidate not found: {candidate_id}")
        raise typer.Exit(code=1)
    try:
        revised = Path(path).read_text()
    except OSError as exc:
        typer.echo(f"cannot read file: {exc}")
        raise typer.Exit(code=1) from exc
    repo.edit(candidate_id, revised)
    typer.echo(f"edited {candidate_id}")


@engagement_app.command("metrics")
def engagement_metrics() -> None:
    """汇总互动质量指标与运行级计数（质量优先，不优化互动数量）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    metrics = compute_metrics(
        InteractionRepository(store).list_all(),
        FeedbackSnapshotRepository(store).list_all(),
        ConversationEvidenceRepository(store).list_all(),
    )
    typer.echo(render_metrics(metrics))
    stats = EngagementRunStatsRepository(store).list_all()
    typer.echo(render_run_stats(summarize_run_stats(stats)))


if __name__ == "__main__":
    app()
