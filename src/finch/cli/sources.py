"""Finch CLI：sources / twitter / repos 子命令与根命令 init、diagnose。"""

import json
from pathlib import Path

import typer

from finch.github.gh_client import GhClient
from finch.settings import load_settings
from finch.storage.workspace import Workspace
from finch.twitter.normalizer import normalize_tweets
from finch.twitter.opencli_client import OpenCliClient
from finch.twitter.query_builder import QueryBuilder

twitter_app = typer.Typer(help="Twitter 搜索与读取")

sources_app = typer.Typer(help="跨平台 OpenCLI 采集源（只读）")

repos_app = typer.Typer(help="X 分享的 GitHub 仓库发现与热度榜（只读）")


def init() -> None:
    """初始化工作区目录树（幂等）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    typer.echo(f"initialized: {settings.paths.var_dir}")


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


@sources_app.command("doctor")
def sources_doctor(
    smoke: bool = typer.Option(True, "--smoke/--no-smoke", help="对各源跑最小只读探测"),
) -> None:
    """跨平台 OpenCLI 环境自检（只读；不写 Cookie/Token）。"""
    from finch.sources.doctor import format_doctor_report, run_doctor

    settings = load_settings()
    report = run_doctor(
        profile=settings.opencli.profile,
        run_smoke=smoke,
    )
    typer.echo(format_doctor_report(report))
    if not report.opencli_ok:
        raise typer.Exit(code=1)
    bad = [s for s in report.sources if s.status.value == "UNAVAILABLE"]
    if len(bad) == len(report.sources):
        raise typer.Exit(code=1)


@sources_app.command("sync")
def sources_sync(
    source: str | None = typer.Option(
        None, "--source", help="twitter|reddit|github|hackernews|v2ex|weixin|xiaohongshu"
    ),
    all_sources: bool = typer.Option(False, "--all", help="同步全部已注册源"),
    query: list[str] = typer.Option([], "--query", "-q", help="查询词（可重复）"),
    url: list[str] = typer.Option([], "--url", help="URL 导入（公众号/笔记等）"),
    limit: int = typer.Option(20, "--limit", help="每查询条数上限"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """只读同步：raw 落盘 → RawArtifact → Peer/Person 投影（单源失败不阻塞）。"""
    from finch.sources.models import Source
    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import (
        DiscoveryOrchestrator,
        aggregate_state,
        sync_result_to_dict,
    )
    from finch.sources.query_plan import build_context_by_source

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    gateway = OpenCliGateway(profile=settings.opencli.profile)
    orch = DiscoveryOrchestrator(ws, gateway=gateway)

    src: Source | None = None
    if source is not None and not all_sources:
        try:
            src = Source(source)
        except ValueError as exc:
            typer.echo(f"unknown source: {source}")
            raise typer.Exit(code=1) from exc

    contexts = build_context_by_source(
        settings,
        cli_queries=list(query),
        cli_urls=list(url),
        source=src,
        limit=limit,
        all_sources=all_sources or source is None,
    )

    if all_sources or source is None:
        results = orch.sync_all(context_by_source=contexts)
        all_targets = list(Source)
    else:
        assert src is not None
        if src not in contexts:
            results = []  # 禁用源：不实际同步，直接报 not_executed
        else:
            results = [orch.sync_source(src, contexts.get(src))]
        all_targets = [src]

    if as_json:
        rows = []
        by_source = {r.source: r for r in results}
        for s in all_targets:
            r = by_source.get(s)
            if r is None:
                rows.append({"source": s.value, "state": "not_executed", "status": "disabled"})
            else:
                rows.append({**sync_result_to_dict(r), "state": aggregate_state(r)})
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    by_source = {r.source: r for r in results}
    for s in all_targets:
        r = by_source.get(s)
        if r is None:
            typer.echo(f"{s.value}: not_executed [disabled]")
            continue
        state = aggregate_state(r)
        typer.echo(
            f"{r.source.value}: {state} [{r.mode or '-'}] "
            f"started={r.started_at or '-'} raw={r.raw_count} "
            f"norm={r.normalized_count} new={r.created_count} "
            f"projected={r.projected_count}"
            + (f" ({r.detail})" if r.detail else "")
            + (f" projection_failed={r.projection_error}" if r.projection_failed else "")
        )
        for o in r.per_query:
            if o.outcome == "skipped":
                typer.echo(f"  - skipped (mode={r.mode}): {o.query}")
            elif o.outcome == "failed":
                typer.echo(f"  - {o.query}: failed ({o.kind.value if o.kind else 'error'})"
                           + (f" — {o.detail}" if o.detail else ""))
            elif o.outcome == "empty":
                typer.echo(f"  - {o.query}: empty (0 raw)")
            else:
                suffix = " (partial)" if o.partial else ""
                typer.echo(f"  - {o.query}: ran ({o.raw_count} raw){suffix}")


@repos_app.command("discover")
def repos_discover(
    lookback_hours: int | None = typer.Option(
        None, "--lookback-hours", help="回溯小时数（默认读配置）"
    ),
    resume: str | None = typer.Option(None, "--resume", help="续跑未完成的 run_id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """抓取窗口内 X 上分享的 GitHub 仓库，去重并生成热度榜（全量，无 Top-N）。"""
    from finch.repos.repository import RepoDiscoveryRepository
    from finch.repos.service import RepoDiscoveryService
    from finch.twitter.opencli_client import OpenCliClient

    settings = load_settings()
    cfg = settings.repo_discovery
    if not cfg.enabled and resume is None:
        typer.echo("repo_discovery.enabled is false")
        raise typer.Exit(code=1)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = RepoDiscoveryService(
        RepoDiscoveryRepository(ws),
        OpenCliClient(),
        gh=GhClient(),
    )
    run = svc.discover(
        queries=list(cfg.queries),
        lookback_hours=lookback_hours or cfg.lookback_hours,
        timezone=cfg.timezone,
        resume_run_id=resume,
        per_query_limit=cfg.execution.per_query_limit,
        per_slice_budget_seconds=cfg.execution.per_slice_budget_seconds,
        agent_keywords=list(cfg.agent_tags.keywords),
        agent_tags_enabled=cfg.agent_tags.enabled,
        likes_weight=cfg.ranking.likes_weight,
        fetch_github=True,
        expand_shorts=cfg.execution.expand_short_urls,
    )
    if as_json:
        typer.echo(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))
        if run.status.value == "failed":
            raise typer.Exit(code=1)
        return
    typer.echo(f"run_id: {run.run_id}")
    typer.echo(f"status: {run.status.value}")
    typer.echo(
        f"window: {run.window_start.isoformat()} → {run.window_end.isoformat()} "
        f"({run.timezone})"
    )
    typer.echo(f"formula: {run.formula_version} | {run.effective_formula}")
    if run.unavailable_metrics:
        typer.echo("unavailable metrics: " + ", ".join(run.unavailable_metrics))
    typer.echo(f"tweets: {run.tweet_count}  repos: {run.repo_count}")
    for q in run.queries:
        note = f" ({q.coverage_note})" if q.coverage_note else ""
        err = f" err={q.error}" if q.error else ""
        typer.echo(f"  query {q.query_id}: {q.status.value} seen={q.tweets_seen}{note}{err}")
    if run.errors:
        typer.echo("errors:")
        for e in run.errors[:10]:
            typer.echo(f"  - {e}")
    if run.status.value == "partial":
        typer.echo(f"resume: uv run finch repos discover --resume {run.run_id}")
    if run.status.value == "failed":
        raise typer.Exit(code=1)


@repos_app.command("list")
def repos_list(
    run: str = typer.Option(..., "--run", help="discover 产出的 run_id"),
    sort: str = typer.Option("x_heat", "--sort", help="x_heat|mentions|github_stars|latest"),
    topic: str = typer.Option("all", "--topic", help="all|agent"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int | None = typer.Option(None, "--page-size"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """分页展示某次运行的仓库榜（分页只影响渲染，不截断数据）。"""
    from finch.repos.models import SortKey
    from finch.repos.repository import RepoDiscoveryRepository
    from finch.repos.service import RepoDiscoveryService
    from finch.twitter.opencli_client import OpenCliClient

    settings = load_settings()
    cfg = settings.repo_discovery
    try:
        sort_key = SortKey(sort)
    except ValueError as exc:
        typer.echo(f"invalid --sort: {sort}")
        raise typer.Exit(code=1) from exc
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = RepoDiscoveryService(RepoDiscoveryRepository(ws), OpenCliClient())
    try:
        snap, page_entries, total = svc.list_page(
            run,
            sort=sort_key,
            topic=topic,
            page=page,
            page_size=page_size or cfg.page_size,
            likes_weight=cfg.ranking.likes_weight,
        )
    except KeyError as exc:
        typer.echo(f"run not found: {run}")
        raise typer.Exit(code=1) from exc
    pages = max(1, (total + (page_size or cfg.page_size) - 1) // (page_size or cfg.page_size))
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "run_id": run,
                    "sort": sort,
                    "topic": topic,
                    "page": page,
                    "page_size": page_size or cfg.page_size,
                    "total": total,
                    "pages": pages,
                    "formula_version": snap.formula_version,
                    "effective_formula": snap.effective_formula,
                    "entries": [e.model_dump(mode="json") for e in page_entries],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    run_obj = RepoDiscoveryRepository(ws).get_run(run)
    status = run_obj.status.value if run_obj else "?"
    typer.echo(
        f"run={run} status={status} sort={sort} topic={topic} "
        f"第 {page} 页，共 {total} 条（{pages} 页）"
    )
    typer.echo(f"formula: {snap.effective_formula}")
    typer.echo("禁止把本页当作完整结果；导出：finch repos export --run …")
    for e in page_entries:
        desc = e.description if e.description not in (None, "") else "暂无简介"
        heat = "—" if e.x_heat is None else str(e.x_heat)
        stars = "—" if e.github_stars is None else str(e.github_stars)
        partial = " [partial]" if e.metrics_partial else ""
        typer.echo(
            f"{e.rank}. {e.repo_key}  tag={e.topic_tag.value}  "
            f"heat={heat}  mentions={e.mentions}  stars={stars}{partial}"
        )
        typer.echo(f"   {desc}  |  {e.url}")


@repos_app.command("export")
def repos_export(
    run: str = typer.Option(..., "--run"),
    fmt: str = typer.Option("json", "--format", help="json|csv"),
    output: str = typer.Option(..., "--output", "-o"),
    sort: str = typer.Option("x_heat", "--sort"),
    topic: str = typer.Option("all", "--topic"),
) -> None:
    """导出完整榜单（JSON 含推文关联；CSV 每仓库一行）。"""
    import csv

    from finch.repos.models import SortKey
    from finch.repos.repository import RepoDiscoveryRepository
    from finch.repos.service import RepoDiscoveryService
    from finch.twitter.opencli_client import OpenCliClient

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        sort_key = SortKey(sort)
    except ValueError as exc:
        typer.echo(f"invalid --sort: {sort}")
        raise typer.Exit(code=1) from exc
    svc = RepoDiscoveryService(RepoDiscoveryRepository(ws), OpenCliClient())
    try:
        snap, entries, total = svc.list_page(
            run,
            sort=sort_key,
            topic=topic,
            page=1,
            page_size=10**9,
            likes_weight=settings.repo_discovery.ranking.likes_weight,
        )
    except KeyError as exc:
        typer.echo(f"run not found: {run}")
        raise typer.Exit(code=1) from exc
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        tweets = RepoDiscoveryRepository(ws).list_tweets(run)
        payload = {
            "run_id": run,
            "total": total,
            "formula_version": snap.formula_version,
            "effective_formula": snap.effective_formula,
            "entries": [e.model_dump(mode="json") for e in entries],
            "tweets": [t.model_dump(mode="json") for t in tweets],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=[
                    "rank",
                    "repo_key",
                    "url",
                    "description",
                    "topic_tag",
                    "x_heat",
                    "mentions",
                    "github_stars",
                    "github_forks",
                    "latest_share_at",
                    "source_urls",
                    "metrics_partial",
                ],
            )
            w.writeheader()
            for e in entries:
                w.writerow(
                    {
                        "rank": e.rank,
                        "repo_key": e.repo_key,
                        "url": e.url,
                        "description": e.description or "",
                        "topic_tag": e.topic_tag.value,
                        "x_heat": "" if e.x_heat is None else e.x_heat,
                        "mentions": e.mentions,
                        "github_stars": "" if e.github_stars is None else e.github_stars,
                        "github_forks": "" if e.github_forks is None else e.github_forks,
                        "latest_share_at": (
                            e.latest_share_at.isoformat() if e.latest_share_at else ""
                        ),
                        "source_urls": " ".join(e.source_urls),
                        "metrics_partial": e.metrics_partial,
                    }
                )
    else:
        typer.echo("format must be json or csv")
        raise typer.Exit(code=1)
    typer.echo(f"wrote {total} repos → {path}")


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
