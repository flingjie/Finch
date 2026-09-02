"""Finch CLI（spec 10）。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from .codex.runner import CodexRunner
from .content.models import DailyBrief, Draft
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader
from .github.gh_client import GhClient
from .github.models import CommitDetail
from .graph.context import parse_items
from .graph.daily import daily_nodes
from .graph.runtime import GraphRuntime
from .review.feedback import FeedbackService
from .review.models import SkipReason
from .review.service import ReviewService
from .settings import load_settings
from .storage.database import Store
from .storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository
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
    )
    run = GraphRuntime(store, nodes).run()
    typer.echo(run.state)
    draft_record = store.find_node(run.id, "critique", "default")
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


@review_app.command("feedback")
def review_feedback(
    draft_id: str,
    url: str | None = typer.Option(None, "--url", help="发布链接"),
    metrics: str | None = typer.Option(None, "--metrics", help="互动数据 JSON"),
) -> None:
    """登记发布链接与互动数据。"""
    metrics_dict: dict | None = None
    if metrics:
        metrics_dict = json.loads(metrics)
    feedback = _feedback_service().record(draft_id, published_url=url, metrics=metrics_dict)
    typer.echo(f"feedback recorded: {feedback.draft_id}")


if __name__ == "__main__":
    app()
