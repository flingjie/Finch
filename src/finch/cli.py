"""Finch CLI（spec 10）。"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import typer
import yaml

from .author.reconcile import reconcile
from .author.sync import sync_posts, verify_account
from .codex.runner import CodexRunner
from .codex.structured_output import StructuredOutputError
from .content.checkers.aggregate import AggregateOutcome
from .content.checkers.base import CheckResult
from .content.models import Draft
from .content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from .dev.cli import dev_app
from .engagement.flow import run_discovery_engagement_flow
from .engagement.models import EngagementRunStats, InteractionCandidate
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.discovery import resolve_repositories
from .github.gh_client import GhClient
from .github.ingestion import Ingestor
from .graph.context import parse_items
from .graph.daily import daily_nodes
from .graph.dual_track import DualTrackResult, run_dual_track
from .graph.runtime import GraphRuntime
from .idea.models import IdeaAssessment
from .idea.service import (
    assess_idea,
    build_content_job,
    build_draft,
    critic_failure_reason,
    recent_author_posts,
    run_idea_critic,
    write_idea,
)
from .ideas.commit_service import CommitService
from .ideas.service import IdeaService
from .inbox.models import DecisionAction, DecisionRecord
from .inbox.render import render_daily_summary, state_label
from .inbox.service import InboxDecisionService, list_items, next_item
from .learn.models import OutcomeAssessment
from .learn.service import FeedbackService
from .learn.weekly import render_weekly, weekly_analysis
from .llm.openai_compatible import create_runner
from .reddit.opencli_client import RedditOpenCliClient
from .settings import load_settings
from .storage.database import Store
from .storage.repositories import (
    AuthorPostRepository,
    CommitIngestionRepository,
    ContentJobRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    DraftVersionRepository,
    EngagementRunStatsRepository,
    EvidenceRepository,
    FeedbackRepository,
    InteractionRepository,
    PublicationIntentRepository,
    RepoCursorRepository,
)
from .twitter.models import TwitterError
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

author_app = typer.Typer(help="Author account sync + publication reconcile (read-only)")
app.add_typer(author_app, name="author")

app.add_typer(dev_app, name="dev")

ideas_app = typer.Typer(help="Idea 候选流（commit/search 提炼 + 状态转换）")
app.add_typer(ideas_app, name="ideas")


@author_app.command("sync")
def author_sync(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """同步作者账号发帖并确定性匹配已批准草稿。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    client = OpenCliClient()
    total_synced = 0
    try:
        for cfg in settings.author_accounts:
            if not cfg.enabled:
                continue
            account = verify_account(cfg, client, store)
            total_synced += sync_posts(
                account, client, store, lookback_days=cfg.history_lookback_days
            )
        result = reconcile(store)
    except (KeyError, ValueError, TwitterError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    payload = {
        "synced": total_synced,
        "linked": [link.model_dump(mode="json") for link in result.linked],
        "needs_manual": result.needs_manual,
        "awaiting": result.awaiting,
    }
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(
            f"synced={total_synced} linked={len(result.linked)} "
            f"awaiting={len(result.awaiting)}"
        )


def _since_iso(since: str | None) -> str | None:
    if since is None:
        return None
    if since.endswith("h"):
        return (datetime.now(UTC) - timedelta(hours=int(since[:-1]))).isoformat()
    if since.endswith("d"):
        return (datetime.now(UTC) - timedelta(days=int(since[:-1]))).isoformat()
    return since


def persist_critique_reports(store: Store, output_json: str) -> None:
    """从 critique 节点输出持久化草稿版本与 Critic 报告（Task 7）。

    critique 节点保持无状态（不访问 DB），持久化在 CLI 层完成。每个 report 条目含
    draft_id / round / version / checks / outcome，分别写入 DraftVersionRepository
    与 CriticReportRepository。历史草稿（content_job_id=None）照常读回，
    不参与新指标（Task 8 在指标侧跳过）。
    """
    payload = json.loads(output_json)
    reports = payload.get("reports", [])
    if not reports:
        return
    version_repo = DraftVersionRepository(store)
    report_repo = CriticReportRepository(store)
    for report in reports:
        draft_id = report["draft_id"]
        round_no = report["round"]
        version = report.get("version")
        if version is not None:
            version_repo.upsert_version(draft_id, round_no, Draft.model_validate(version))
        checks = [CheckResult.model_validate(c) for c in report.get("checks", [])]
        report_repo.upsert_report(draft_id, round_no, checks, report["outcome"])


def _persist_run_outputs(store: Store, run_id: str) -> None:
    """把一次 run 的 Critic 报告与保留草稿持久化（Task 7 + F1）。

    write 节点输出既是 kept drafts 又是 report 的权威来源。run_daily 与 run_resume 共用，
    确保 resume 出来的草稿进入 review list、报告进入周复盘指标。
    """
    write_record = store.find_node(run_id, "write", "default")
    if write_record is not None and write_record.output_json:
        persist_critique_reports(store, write_record.output_json)
    if write_record is not None and write_record.output_json:
        drafts = parse_items(json.loads(write_record.output_json), Draft)
        draft_repo = DraftRepository(store)
        for draft in drafts:
            draft_repo.upsert_draft(draft)


def _echo_inbox_summary(store: Store) -> None:
    """非 json 输出末尾打印收件箱汇总（今天 N 条待决定）。"""
    items = list_items(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        interactions=InteractionRepository(store),
        cards=EvidenceRepository(store),
    )
    typer.echo(render_daily_summary(items))


def _echo_dual_track_result(result: DualTrackResult, store: Store) -> None:
    """汇总输出双轨结果：原创轨道 state，随后互动轨道 summary（不再打印 brief）。"""
    if result.original is not None:
        typer.echo(state_label(result.original.state))
        _persist_run_outputs(store, result.original.id)
    elif result.original_error is not None:
        typer.echo(
            f"original track error: {result.original_error.type}: {result.original_error.message}"
        )
    if result.engagement is not None:
        typer.echo(result.engagement.summary)
    elif result.engagement_error is not None:
        typer.echo(
            f"engagement track error: {result.engagement_error.type}: "
            f"{result.engagement_error.message}"
        )


def _persist_engagement_candidates(result: DualTrackResult, store: Store) -> None:
    """把互动轨道产出的候选写入审批队列（keyed by run_id）。

    无候选时不写入；``engagement.enabled`` 为 False 时调用方跳过，本函数也不处理。
    """
    engagement = result.engagement
    if engagement is None or not engagement.candidates:
        return
    repo = InteractionRepository(store)
    for candidate in engagement.candidates:
        repo.upsert(candidate, run_id=engagement.run_id)


def _daily_json_summary(store: Store, run_id: str, *, engagement_drafts: int) -> str:
    decided = {
        rec.job_id for rec in DecisionRecordRepository(store).list()
        if rec.action in {DecisionAction.ACCEPT, DecisionAction.SKIP}
    }
    drafts = [
        d for d in DraftRepository(store).list_drafts()
        if d.content_job_id and d.run_id == run_id
    ]
    n_review = sum(1 for d in drafts if d.content_job_id not in decided)
    return json.dumps(
        {
            "run_id": run_id,
            "status": "review_required" if n_review else "completed",
            "n_review": n_review,
            "n_engagement_drafts": engagement_drafts,
        },
        ensure_ascii=False,
        indent=2,
    )


def _persist_engagement_run_stats(
    result: DualTrackResult, store: Store, *, latency_ms: int
) -> None:
    """把互动轨道单轮运行级计数写入运行统计（Phase 7 可观测性）。

    ``posts_scanned`` 来自 ``EngagementRunResult.posts_found``，``candidates`` 为候选数，
    ``drafts`` 为含非空草稿的候选数。互动轨道未返回结果（异常被双轨调度隔离）时跳过，
    不写统计。空运行（``posts_scanned=0``）也写入，否则 ``no_evidence_runs`` 无法计数。
    """
    engagement = result.engagement
    if engagement is None:
        return
    drafts = sum(1 for c in engagement.candidates if c.draft is not None)
    EngagementRunStatsRepository(store).upsert(
        EngagementRunStats(
            run_id=engagement.run_id,
            posts_scanned=engagement.posts_found,
            candidates=len(engagement.candidates),
            drafts=drafts,
            latency_ms=latency_ms,
        )
    )


@app.command()
def init() -> None:
    """初始化 var/ 目录与数据库 schema。"""
    settings = load_settings()
    from .storage.database import Store

    store = Store(settings.paths.db_path)
    store.init()
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


@app.command("daily")
def run_daily(
    as_json: bool = typer.Option(False, "--json", help="输出结构化 JSON 摘要"),  # noqa: B008
) -> None:
    """运行每日 Graph：同步 commit → 提取证据卡 → 收集推文 → 匹配证据 → 撰写与审查草稿。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    gh = GhClient()
    opencli = OpenCliClient()
    reddit_opencli = RedditOpenCliClient()

    repos = resolve_repositories(settings, gh)

    ingestion_repo = CommitIngestionRepository(store)
    cursor_repo = RepoCursorRepository(store)
    existing_topics = {
        topic for card in EvidenceRepository(store).list_cards() for topic in card.topics
    }
    groups_by_repo = Ingestor(gh, settings, ingestion_repo, cursor_repo).ingest(
        repos, existing_topics=existing_topics
    )

    repo_is_private: dict[str, bool] = {}
    known_commit_urls: set[str] = set()
    for repo in repos:
        repo_is_private[repo] = gh.repo_view(repo).is_private
    for repo, groups in groups_by_repo.items():
        for group in groups:
            for commit in group:
                known_commit_urls.add(f"https://github.com/{repo}/commit/{commit.sha}")

    nodes = daily_nodes(
        settings=settings,
        store=store,
        gh=gh,
        opencli=opencli,
        extractor=Extractor(
            create_runner(settings.llm) or CodexRunner(),
            settings=settings.extraction,
            cache_path=settings.paths.cache_dir / "extraction_cache.json",
        ),
        runner=CodexRunner(),
        groups_by_repo=groups_by_repo,
        known_commit_urls=known_commit_urls,
        repo_is_private=repo_is_private,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        inference_runners={
            "match_evidence": create_runner(settings.llm, "match_evidence"),
            "plan_topics": create_runner(settings.llm, "plan_topics"),
            "expand_job": create_runner(settings.llm, "expand_job"),
            "critique": create_runner(settings.llm, "critique"),
        },
    )

    if settings.engagement.enabled:
        start = time.monotonic()
        result = run_dual_track(
            original_track=lambda rid: GraphRuntime(store, nodes).run(run_id=rid),
            engagement_track=lambda rid: run_discovery_engagement_flow(
                settings, opencli, CodexRunner(),
                run_id=rid, reddit_opencli=reddit_opencli,
            ),
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        _persist_engagement_candidates(result, store)
        _persist_engagement_run_stats(result, store, latency_ms=latency_ms)

        engagement = result.engagement
        engagement_drafts = sum(
            1 for c in (engagement.candidates if engagement else []) if c.draft is not None
        )

        if as_json:
            run_id = (
                result.original.id if result.original is not None else result.run_id
            )
            typer.echo(
                _daily_json_summary(store, run_id, engagement_drafts=engagement_drafts)
            )
            return

        _echo_dual_track_result(result, store)
        _echo_inbox_summary(store)
        return

    run = GraphRuntime(store, nodes).run()
    if as_json:
        typer.echo(_daily_json_summary(store, run.id, engagement_drafts=0))
        return
    typer.echo(state_label(run.state))
    _persist_run_outputs(store, run.id)
    _echo_inbox_summary(store)


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


@app.command("decide")
def decide(
    item_id: str = typer.Argument(..., help="待决策项 id（job_id 或 candidate id）"),
    action: str = typer.Option(..., "--action", help="accept|skip|revise"),
    reason: str = typer.Option(None, "--reason", help="--action skip 的拒绝理由"),
    instruction: str | None = typer.Option(None, "--instruction", help="--action revise 的指令"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """单一决策点：accept 确认立场+批准；skip 标记不写；revise 按指令重写。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    svc = InboxDecisionService(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        publication_intents=PublicationIntentRepository(store),
        interactions=InteractionRepository(store),
    )
    try:
        action_enum = DecisionAction(action)
    except ValueError as exc:
        typer.echo(f"invalid --action: {action}")
        raise typer.Exit(code=1) from exc
    result: DecisionRecord | InteractionCandidate | dict | None = None
    try:
        if action_enum is DecisionAction.ACCEPT:
            result = svc.accept(item_id)
        elif action_enum is DecisionAction.SKIP:
            if not reason:
                typer.echo("--action skip requires --reason")
                raise typer.Exit(code=1)
            result = svc.skip(item_id, reason)
        else:
            if not instruction:
                typer.echo("--action revise requires --instruction")
                raise typer.Exit(code=1)
            cards_by_id = {c.id: c for c in EvidenceRepository(store).list_cards()}
            runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
            try:
                result = svc.revise(item_id, instruction, runner=runner, cards_by_id=cards_by_id)
            except (RuntimeError, StructuredOutputError) as exc:
                typer.echo(
                    json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
                )
                raise typer.Exit(code=1) from exc
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    assert result is not None
    if as_json:
        if isinstance(result, dict):
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            typer.echo(result.model_dump_json(indent=2))
    else:
        if isinstance(result, dict):
            typer.echo(result["new_body"])
        elif isinstance(result, DecisionRecord):
            typer.echo(result.action.value)
        else:
            typer.echo(result.status.value)


@app.command("next")
def next_cmd(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """返回下一个待决策卡（原创 + 互动），无则 status=none。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    payload = next_item(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        interactions=InteractionRepository(store),
        cards=EvidenceRepository(store),
    )
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if payload.get("status") == "none":
            typer.echo("no pending items")
        else:
            typer.echo(payload.get("topic") or payload.get("id", ""))


@app.command("draft")
@app.command("idea", hidden=True)
def draft(
    text: str = typer.Argument(..., help="想法或片段"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),  # noqa: B008
) -> None:
    """判断一个想法能否发，能发时生成样稿进入收件箱。"""
    if not text.strip():
        typer.echo("idea text is empty")
        raise typer.Exit(code=1)

    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()

    cards = EvidenceRepository(store).list_cards()
    cards_by_id = {card.id: card for card in cards}
    recent_posts = recent_author_posts(AuthorPostRepository(store).list())

    assess_runner = create_runner(settings.llm, "assess_idea") or CodexRunner()
    write_runner = create_runner(settings.llm, "write_idea") or CodexRunner()
    critic_runner = create_runner(settings.llm, "critique") or CodexRunner()
    voice_profile = load_voice_profile(settings.paths.voice_profile_path)

    try:
        assessment = assess_idea(assess_runner, text, cards, recent_posts)
    except (RuntimeError, StructuredOutputError) as exc:
        _echo_idea_error(exc, as_json)
        raise typer.Exit(code=1) from exc

    if assessment.status != "ready":
        _echo_idea(assessment, as_json)
        return

    matched_cards = [
        cards_by_id[cid] for cid in assessment.matched_evidence_ids if cid in cards_by_id
    ]
    try:
        body = write_idea(write_runner, text, assessment, matched_cards)
        job = build_content_job(text, assessment)
        draft = build_draft(job, body)
        outcome, checks, final_draft = run_idea_critic(
            critic_runner,
            draft,
            job,
            matched_cards,
            settings.quality_gates.max_rewrite_rounds,
            voice_profile=voice_profile,
        )
    except (RuntimeError, StructuredOutputError) as exc:
        _echo_idea_error(exc, as_json)
        raise typer.Exit(code=1) from exc

    if outcome != AggregateOutcome.PASS:
        reason_code, reason = critic_failure_reason(checks)
        _echo_idea(
            IdeaAssessment(
                status="not_ready",
                reason_code=reason_code,
                reason=reason,
                core_point=assessment.core_point,
                matched_evidence_ids=assessment.matched_evidence_ids,
            ),
            as_json,
        )
        return

    ContentJobRepository(store).upsert_job(job)
    DraftRepository(store).upsert_draft(final_draft)
    CriticReportRepository(store).upsert_report(
        final_draft.id, 0, checks, AggregateOutcome.PASS
    )

    _echo_idea(
        IdeaAssessment(
            status="ready",
            reason_code=None,
            reason="观点明确，且通过 Critic",
            core_point=assessment.core_point,
            matched_evidence_ids=assessment.matched_evidence_ids,
            draft_id=final_draft.id,
            sample=final_draft.body,
        ),
        as_json,
    )


def _echo_idea(assessment: IdeaAssessment, as_json: bool) -> None:
    if as_json:
        typer.echo(assessment.model_dump_json(indent=2))
        return
    if assessment.status == "ready":
        typer.echo("适合发。")
        typer.echo(f"\n原因\n{assessment.reason}")
        if assessment.core_point:
            typer.echo(f"\n核心观点\n{assessment.core_point}")
        if assessment.sample:
            typer.echo(f"\n样稿\n{assessment.sample}")
        if assessment.draft_id:
            typer.echo(f"\n采用：finch decide {assessment.draft_id} --action accept")
            typer.echo(f"修改：finch decide {assessment.draft_id} --action revise")
            typer.echo(f"跳过：finch decide {assessment.draft_id} --action skip --reason not_now")
    else:
        typer.echo("暂时不适合发。")
        typer.echo(f"\n原因\n{assessment.reason}")
        if assessment.reason_code:
            typer.echo(f"（{assessment.reason_code}）")


def _echo_idea_error(exc: Exception, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
    else:
        typer.echo(f"idea failed: {exc}")


@app.command("learn")
def learn(
    draft_id: str = typer.Argument(..., help="草稿 id"),
    url: str | None = typer.Option(None, "--url", help="发布链接"),
    metrics: str | None = typer.Option(None, "--metrics", help="互动数据 JSON"),
    outcome: str | None = typer.Option(None, "--outcome", help="结果评估 JSON"),
    learning: str | None = typer.Option(None, "--learning", help="学习记录"),
) -> None:
    """登记发布链接、互动数据、结果评估与学习记录。"""
    metrics_dict: dict | None = json.loads(metrics) if metrics else None
    outcome_obj: OutcomeAssessment | None = (
        OutcomeAssessment.model_validate_json(outcome) if outcome else None
    )
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    feedback = FeedbackService(FeedbackRepository(store)).record(
        draft_id, published_url=url, metrics=metrics_dict,
        outcome=outcome_obj, learning=learning,
    )
    typer.echo(f"feedback recorded: {feedback.draft_id}")


if __name__ == "__main__":
    app()
