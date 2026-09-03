"""Finch CLI（spec 10）。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
import yaml

from .codex.runner import CodexRunner
from .content.checkers.base import CheckResult
from .content.jobs import AuthorPosition, ContentJobStatus
from .content.models import DailyBrief, Draft
from .content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader
from .github.gh_client import GhClient
from .github.models import CommitDetail
from .graph.context import parse_items
from .graph.daily import daily_nodes
from .graph.replay import replay
from .graph.runtime import GraphRuntime
from .review.feedback import FeedbackService
from .review.models import OutcomeAssessment, ReviewAction, SkipReason
from .review.service import ReviewService
from .review.weekly import render_weekly, weekly_analysis
from .settings import load_settings
from .storage.database import Store
from .storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DraftRepository,
    DraftVersionRepository,
    FeedbackRepository,
    ReviewRepository,
)
from .twitter.normalizer import normalize_tweets
from .twitter.opencli_client import OpenCliClient
from .twitter.query_builder import QueryBuilder

app = typer.Typer(help="Finch: evidence-driven builder companion.")

github_app = typer.Typer(help="GitHub 读取与工程事件提取")
app.add_typer(github_app, name="github")

twitter_app = typer.Typer(help="Twitter 搜索与读取")
app.add_typer(twitter_app, name="twitter")

run_app = typer.Typer(help="Run graph pipelines")
app.add_typer(run_app, name="run")

review_app = typer.Typer(help="Review drafts")
app.add_typer(review_app, name="review")

jobs_app = typer.Typer(help="Inspect and answer Content Jobs (human-in-the-loop)")
app.add_typer(jobs_app, name="jobs")

voice_app = typer.Typer(help="Manage the author voice profile (local, no auto-publish)")
app.add_typer(voice_app, name="voice")


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


@github_app.command("sync")
def github_sync(repo: str = typer.Option("flingjie/FDE-Gym"), since: str | None = None) -> None:
    """增量读取仓库 Commit 并推进游标。"""
    reader = CommitReader(GhClient(), repo=repo)
    commits = reader.sync(since=_since_iso(since))
    typer.echo(f"synced {len(commits)} commits for {repo}")


@github_app.command("reflect")
def github_reflect(repo: str = typer.Option("flingjie/FDE-Gym"),
                   since: str = typer.Option("7d")) -> None:
    """读取最近 Commit，提取工程事件并输出 Evidence Cards。"""
    gh = GhClient()
    summaries = gh.list_commits(repo, since=_since_iso(since))
    details = [gh.commit_detail(repo, c.sha) for c in summaries]
    reader = CommitReader(gh, repo)
    details = reader.filter_noise(details)
    events = Extractor(CodexRunner()).extract(details, repo=repo)
    cards = build_cards(events)
    typer.echo(f"# Finch reflect: {repo}\n")
    for ev in events:
        typer.echo(f"## {ev.id}\n- problem: {ev.problem.statement} [{ev.problem.confidence.value}]")
        typer.echo(f"- decision: {ev.decision.statement} [{ev.decision.confidence.value}]")
        typer.echo(f"- result: {ev.result.statement} [{ev.result.confidence.value}]")
    typer.echo(f"\n{len(cards)} evidence cards")


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


@run_app.command("daily")
def run_daily() -> None:
    """运行每日 Graph：同步 commit → 提取证据卡 → 收集推文 → 匹配证据 → 撰写与审查草稿。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    gh = GhClient()
    opencli = OpenCliClient()

    commits_by_repo: dict[str, list[CommitDetail]] = {}
    known_commit_urls: set[str] = set()
    repo_is_private: dict[str, bool] = {}
    for repo in settings.repositories:
        info = gh.repo_view(repo)
        repo_is_private[repo] = info.is_private
        summaries = gh.list_commits(repo)
        details = [gh.commit_detail(repo, c.sha) for c in summaries]
        details = CommitReader(gh, repo).filter_noise(details)
        commits_by_repo[repo] = details
        for detail in details:
            known_commit_urls.add(f"https://github.com/{repo}/commit/{detail.sha}")

    nodes = daily_nodes(
        settings=settings,
        store=store,
        gh=gh,
        opencli=opencli,
        extractor=Extractor(CodexRunner()),
        runner=CodexRunner(),
        commits_by_repo=commits_by_repo,
        known_commit_urls=known_commit_urls,
        repo_is_private=repo_is_private,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
    )
    run = GraphRuntime(store, nodes).run()
    typer.echo(run.state)
    critique_record = store.find_node(run.id, "critique", "default")
    if critique_record is not None and critique_record.output_json:
        persist_critique_reports(store, critique_record.output_json)
    draft_record = critique_record
    if draft_record is None:
        draft_record = store.find_node(run.id, "draft", "default")
    if draft_record is not None and draft_record.output_json:
        drafts = parse_items(json.loads(draft_record.output_json), Draft)
        draft_repo = DraftRepository(store)
        for draft in drafts:
            draft_repo.upsert_draft(draft)
    brief_record = store.find_node(run.id, "brief", "default")
    if brief_record is not None:
        briefs = parse_items(json.loads(brief_record.output_json), DailyBrief)
        if briefs:
            typer.echo(briefs[0].body)


@run_app.command("resume")
def run_resume(run_id: str) -> None:
    """从 run-id 恢复：复用已完成节点，从 position_gate 继续（读取用户最新 job 编辑）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    gh = GhClient()
    opencli = OpenCliClient()

    # 只需重建节点依赖；已成功节点由 replay 复用，不会重新同步/收集/匹配。
    commits_by_repo: dict[str, list[CommitDetail]] = {
        repo: [] for repo in settings.repositories
    }
    known_commit_urls: set[str] = set()
    repo_is_private = {repo: False for repo in settings.repositories}

    nodes = daily_nodes(
        settings=settings,
        store=store,
        gh=gh,
        opencli=opencli,
        extractor=Extractor(CodexRunner()),
        runner=CodexRunner(),
        commits_by_repo=commits_by_repo,
        known_commit_urls=known_commit_urls,
        repo_is_private=repo_is_private,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
    )
    run = replay(store, nodes, run_id)
    typer.echo(run.state)
    brief_record = store.find_node(run_id, "brief", "default")
    if brief_record is not None and brief_record.output_json:
        briefs = parse_items(json.loads(brief_record.output_json), DailyBrief)
        if briefs:
            typer.echo(briefs[0].body)


@run_app.command("weekly")
def run_weekly() -> None:
    """周复盘：汇总最近 7 天的批准率、修改/跳过原因、内容效果指标与已发布候选。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    since = datetime.now(UTC) - timedelta(days=7)
    report = weekly_analysis(
        DraftRepository(store),
        ReviewRepository(store),
        FeedbackRepository(store),
        ContentJobRepository(store),
        CriticReportRepository(store),
        since=since,
    )
    typer.echo(render_weekly(report))


def _review_service() -> ReviewService:
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    return ReviewService(DraftRepository(store), ReviewRepository(store))


def _feedback_service() -> FeedbackService:
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    return FeedbackService(FeedbackRepository(store))


def _jobs_repo() -> ContentJobRepository:
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    return ContentJobRepository(store)


def _voice_profile_path() -> Path:
    settings = load_settings()
    return settings.paths.voice_profile_path


@review_app.command("list")
def review_list() -> None:
    """列出 pending 草稿（id + kind + 正文前 80 字符）。"""
    service = _review_service()
    drafts = service.list_pending()
    if not drafts:
        typer.echo("no pending drafts")
        return
    for draft in drafts:
        typer.echo(f"{draft.id}\t{draft.kind.value}\t{draft.body[:80]}")


@review_app.command("show")
def review_show(draft_id: str) -> None:
    """打印草稿全文。"""
    draft = _review_service().show(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    typer.echo(draft.body)


@review_app.command("approve")
def review_approve(draft_id: str) -> None:
    """批准草稿（可重放）。"""
    service = _review_service()
    try:
        decision = service.approve(draft_id)
    except KeyError:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"approved {decision.draft_id}")


@review_app.command("revise")
def review_revise(
    draft_id: str,
    path: Path = typer.Option(..., "--file", help="修订后的 Markdown 文件"),  # noqa: B008
) -> None:
    """保存修订正文与 diff（不自动发布）。"""
    service = _review_service()
    try:
        decision = service.revise(draft_id, path.read_text())
    except KeyError:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"revised {decision.draft_id}")
    if decision.diff:
        typer.echo(decision.diff)


@review_app.command("skip")
def review_skip(
    draft_id: str,
    reason: SkipReason = typer.Option(..., "--reason", help="跳过理由"),  # noqa: B008
) -> None:
    """跳过草稿并记录理由。"""
    service = _review_service()
    try:
        decision = service.skip(draft_id, reason)
    except KeyError:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"skipped {decision.draft_id} ({decision.reason})")


@review_app.command("confirm-position")
def review_confirm_position(
    draft_id: str,
    voice_match: int = typer.Option(..., "--voice-match", help="语气匹配度 0-5"),  # noqa: B008
    position_correct: bool | None = typer.Option(  # noqa: B008
        None, "--position-correct", help="立场是否正确"
    ),
    job_clear: bool | None = typer.Option(  # noqa: B008
        None, "--job-clear", help="job 是否清晰"
    ),
) -> None:
    """记录立场确认与 voice_match（独立于 approve/skip）。"""
    if not 0 <= voice_match <= 5:
        typer.echo(f"voice_match must be 0-5: {voice_match}")
        raise typer.Exit(code=1)
    service = _review_service()
    try:
        decision = service.confirm_position(
            draft_id,
            voice_match=voice_match,
            position_correct=position_correct,
            job_clear=job_clear,
        )
    except KeyError:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1) from None
    typer.echo(
        f"position confirmed {decision.draft_id} (voice_match={decision.voice_match})"
    )


@review_app.command("feedback")
def review_feedback(
    draft_id: str,
    url: str | None = typer.Option(None, "--url", help="发布链接"),
    metrics: str | None = typer.Option(None, "--metrics", help="互动数据 JSON"),
    outcome: str | None = typer.Option(
        None, "--outcome", help="结果评估 JSON（OutcomeAssessment）"
    ),
) -> None:
    """登记发布链接、互动数据与结果评估（outcome）。"""
    metrics_dict: dict | None = None
    if metrics:
        metrics_dict = json.loads(metrics)
    outcome_obj: OutcomeAssessment | None = None
    if outcome:
        outcome_obj = OutcomeAssessment.model_validate_json(outcome)
    feedback = _feedback_service().record(
        draft_id, published_url=url, metrics=metrics_dict, outcome=outcome_obj
    )
    typer.echo(f"feedback recorded: {feedback.draft_id}")


@jobs_app.command("list")
def jobs_list(
    status: ContentJobStatus | None = typer.Option(None, "--status", help="按状态过滤"),  # noqa: B008
) -> None:
    """列出 Content Jobs（可按状态过滤）。"""
    jobs = _jobs_repo().list_jobs()
    if status is not None:
        jobs = [job for job in jobs if job.status == status]
    if not jobs:
        typer.echo("no jobs")
        return
    for job in jobs:
        typer.echo(f"{job.id}\t{job.status.value}\t{job.reader_problem[:60]}")


@jobs_app.command("show")
def jobs_show(job_id: str) -> None:
    """打印单个 Content Job 详情。"""
    job = _jobs_repo().get_job(job_id)
    if job is None:
        typer.echo(f"job not found: {job_id}")
        raise typer.Exit(code=1)
    effect = job.intended_effect
    typer.echo(f"id: {job.id}")
    typer.echo(f"status: {job.status.value}")
    typer.echo(f"reader_problem: {job.reader_problem}")
    typer.echo(f"audience: {job.audience}")
    typer.echo(
        f"intended_effect: understand={effect.understand}; "
        f"believe={effect.believe or ''}; action={effect.action or ''}"
    )
    typer.echo(
        f"author_position: "
        f"{job.author_position.model_dump_json() if job.author_position else 'none'}"
    )
    typer.echo(f"missing_questions: {json.dumps(job.missing_questions)}")
    typer.echo(f"source_card_ids: {json.dumps(job.source_card_ids)}")
    if job.reject_reason:
        typer.echo(f"reject_reason: {job.reject_reason}")


@jobs_app.command("answer")
def jobs_answer(
    job_id: str,
    path: Path = typer.Option(..., "--file", help="author_position 字段的 YAML 文件"),  # noqa: B008
) -> None:
    """从 YAML 读取 author_position 字段并写入 job（confirmed 保持 False）。"""
    repo = _jobs_repo()
    job = repo.get_job(job_id)
    if job is None:
        typer.echo(f"job not found: {job_id}")
        raise typer.Exit(code=1)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not data:
        typer.echo("answers file is empty or not a YAML mapping")
        raise typer.Exit(code=1)
    missing = [k for k in ("decision", "tradeoff", "claim") if not data.get(k)]
    if missing:
        typer.echo(f"answers missing required field(s): {', '.join(missing)}")
        raise typer.Exit(code=1)
    try:
        position = AuthorPosition(**data)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"invalid answers: {exc}")
        raise typer.Exit(code=1) from None
    position.confirmed = False
    repo.upsert_job(job.model_copy(update={"author_position": position}))
    typer.echo(f"answered {job_id}")


@jobs_app.command("confirm-position")
def jobs_confirm_position(job_id: str) -> None:
    """确认作者立场（独立于最终发布批准）。"""
    repo = _jobs_repo()
    job = repo.get_job(job_id)
    if job is None:
        typer.echo(f"job not found: {job_id}")
        raise typer.Exit(code=1)
    if job.author_position is None:
        typer.echo(f"job has no author_position: {job_id}")
        raise typer.Exit(code=1)
    position = job.author_position.model_copy(update={"confirmed": True})
    repo.upsert_job(job.model_copy(update={"author_position": position}))
    typer.echo(f"confirmed {job_id}")


@jobs_app.command("reject")
def jobs_reject(
    job_id: str,
    reason: str = typer.Option(..., "--reason", help="拒绝理由"),  # noqa: B008
) -> None:
    """标记 job 为 DO_NOT_WRITE 并记录理由。"""
    repo = _jobs_repo()
    job = repo.get_job(job_id)
    if job is None:
        typer.echo(f"job not found: {job_id}")
        raise typer.Exit(code=1)
    updated = job.model_copy(
        update={"status": ContentJobStatus.DO_NOT_WRITE, "reject_reason": reason}
    )
    repo.upsert_job(updated)
    typer.echo(f"rejected {job_id}")


@voice_app.command("show")
def voice_show() -> None:
    """加载并打印声音画像。"""
    profile = load_voice_profile(_voice_profile_path())
    typer.echo(
        yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )


_VOICE_MATCH_THRESHOLD = 4


@voice_app.command("approve-example")
def voice_approve_example(draft_id: str) -> None:
    """把草稿追加为 approved example（人工修改文本优先于原始 AI 草稿，按 id 去重）。

    仅当草稿已被人工 APPROVE 且 voice_match 达标时允许进入 approved examples。
    """
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    draft = DraftRepository(store).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    decision = ReviewRepository(store).get_review(draft_id)
    if decision is None or decision.action != ReviewAction.APPROVE:
        typer.echo(f"not approved: {draft_id}")
        raise typer.Exit(code=1)
    position = ReviewRepository(store).get_position_review(draft_id)
    if position is None or position.voice_match is None:
        typer.echo(f"voice_match not recorded: {draft_id}")
        raise typer.Exit(code=1)
    if position.voice_match < _VOICE_MATCH_THRESHOLD:
        typer.echo(f"voice_match below threshold: {draft_id}")
        raise typer.Exit(code=1)
    text = decision.revised_body if decision.revised_body else draft.body
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


if __name__ == "__main__":
    app()
