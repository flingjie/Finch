"""Finch CLI（spec 10）。"""

import json
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
from .content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from .content.writer import rewrite_with_instruction
from .dev.cli import dev_app
from .drafts.service import DraftService
from .engagement.models import InteractionCandidate
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.gh_client import GhClient
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
from .ideas.models import IdeaPosition
from .ideas.search_service import SearchService
from .ideas.service import IdeaService
from .inbox.models import DecisionAction, DecisionRecord
from .inbox.service import InboxDecisionService, next_item
from .learn.models import OutcomeAssessment
from .learn.service import FeedbackService
from .learn.weekly import render_weekly, weekly_analysis
from .llm.openai_compatible import create_runner
from .settings import load_settings
from .storage.database import Store
from .storage.repositories import (
    AuthorPostRepository,
    ContentJobRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    FeedbackRepository,
    InteractionRepository,
    PublicationIntentRepository,
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

drafts_app = typer.Typer(help="Draft 生成（已确认 idea → 草稿，不自动发布）")
app.add_typer(drafts_app, name="drafts")


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
    """列出全部 idea 候选（ContentJob），一行一个。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    jobs = sorted(ContentJobRepository(store).list_jobs(), key=lambda j: j.id)
    if as_json:
        payload = [
            {"id": job.id, "status": job.status.value, "core_point": job.core_message}
            for job in jobs
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for job in jobs:
            typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")


@ideas_app.command("show")
def ideas_show(
    idea_id: str = typer.Argument(..., help="idea id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示单个 idea 候选（--json 输出 ContentJob 或嵌入的 IdeaCandidate）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    job = ContentJobRepository(store).get_job(idea_id)
    if job is None:
        typer.echo(f"idea not found: {idea_id}")
        raise typer.Exit(code=1)
    if as_json:
        if job.idea_candidate_json is not None:
            typer.echo(job.idea_candidate_json)
        else:
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
    position_file: str = typer.Option(..., "--file", help="IdeaPosition YAML 文件路径"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从 YAML 读取 IdeaPosition 并更新立场（不改状态）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    try:
        position = IdeaPosition.model_validate(
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


@drafts_app.command("create")
def drafts_create(
    idea_id: str = typer.Argument(..., help="已确认的 idea id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),  # noqa: B008
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
        draft = service.create(
            idea_id, version="1.0.0", format="original", voice_version="1.0.0"
        )
    except (KeyError, ValueError, RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        payload = {"draft_id": draft.id, "status": "drafted", "body": draft.body}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{draft.id}\tdrafted")
        typer.echo(draft.body)


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
