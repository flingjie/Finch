"""Finch CLI（spec 10）。"""

import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import typer

from finch.cli import frozen
from finch.codex.runner import CodexRunner
from finch.github.gh_client import GhClient
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
from finch.products.discovery import DiscoveryRunReport, run_product_discovery
from finch.products.explorer import explore_product
from finch.products.feedback import research_feedback
from finch.products.models import FeedbackReport, Product, Solution
from finch.products.presentation import ProductPresentationRepository
from finch.products.repository import FeedbackRepository as ProductFeedbackRepository
from finch.products.repository import (
    ProductRepository,
    ProductSnapshotRepository,
    SolutionRepository,
)
from finch.products.urls import product_url_for_key
from finch.products.urls import profile_url_for as product_profile_url_for
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
from finch.twitter.normalizer import normalize_tweets
from finch.twitter.opencli_client import OpenCliClient
from finch.twitter.query_builder import QueryBuilder

app = typer.Typer(help="Finch: AI 产品发现与创业者连接 Copilot。")

# 冻结（hidden=True）：旧使命子命令从默认 `finch --help` 面移除，但命令仍可调用、
# 代码与测试保留；验证新方向后再决定删除。恢复只需删掉 hidden=True。
app.add_typer(frozen.github_app, name="github", hidden=True)

twitter_app = typer.Typer(help="Twitter 搜索与读取")
app.add_typer(twitter_app, name="twitter")

sources_app = typer.Typer(help="跨平台 OpenCLI 采集源（只读）")
app.add_typer(sources_app, name="sources")

repos_app = typer.Typer(help="X 分享的 GitHub 仓库发现与热度榜（只读）")
app.add_typer(repos_app, name="repos")

app.add_typer(frozen.voice_app, name="voice", hidden=True)

app.add_typer(frozen.profile_app, name="profile", hidden=True)

app.add_typer(frozen.ideas_app, name="ideas", hidden=True)

app.add_typer(frozen.drafts_app, name="drafts", hidden=True)

app.add_typer(frozen.review_app, name="review", hidden=True)

app.add_typer(frozen.connect_app, name="connect", hidden=True)

app.add_typer(frozen.peers_app, name="peers", hidden=True)

app.add_typer(frozen.people_app, name="people", hidden=True)

app.add_typer(frozen.connections_app, name="connections", hidden=True)

app.add_typer(frozen.collisions_app, name="collisions", hidden=True)

app.add_typer(frozen.experiments_app, name="experiments", hidden=True)

app.add_typer(frozen.inspirations_app, name="inspirations", hidden=True)

app.add_typer(frozen.conversations_app, name="conversations", hidden=True)

app.add_typer(frozen.practice_app, name="practice", hidden=True)

app.add_typer(frozen.problems_app, name="problems", hidden=True)

app.add_typer(frozen.attempts_app, name="attempts", hidden=True)

app.add_typer(frozen.grounded_app, name="grounded", hidden=True)

app.add_typer(frozen.knowledge_app, name="knowledge", hidden=True)

app.add_typer(frozen.angles_app, name="angles", hidden=True)

app.add_typer(frozen.article_app, name="article", hidden=True)

app.add_typer(frozen.methods_app, name="methods", hidden=True)

app.add_typer(frozen.community_app, name="community", hidden=True)

app.add_typer(frozen.dialogue_app, name="dialogue", hidden=True)

app.add_typer(frozen.summaries_app, name="summaries", hidden=True)

app.add_typer(frozen.materials_app, name="materials", hidden=True)

products_app = typer.Typer(help="AI 产品发现 + 创业者连接（产品候选 / 方案分析）")
app.add_typer(products_app, name="products")

relationships_app = typer.Typer(
    help="人际关系：从真实任务理解人、建立持续交流（人物 / 事件 / 交流 / 周度简报）"
)
app.add_typer(relationships_app, name="relationships")

problem_discovery_app = typer.Typer(
    help="问题发现：从真实讨论提取问题卡片（Problem Inbox）"
)
app.add_typer(problem_discovery_app, name="problem-discovery")


@app.command()
def init() -> None:
    """初始化工作区目录树（幂等）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    typer.echo(f"initialized: {settings.paths.var_dir}")


app.command(hidden=True)(frozen.context)


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


app.command("learn", hidden=True)(frozen.learn)
app.command("weekly", hidden=True)(frozen.weekly)
app.command("summarize", hidden=True)(frozen.summarize)


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        url = (url or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _product_primary_url(p: Product) -> str:
    return (p.product_url or product_url_for_key(p.key) or "").strip()


def _product_founder_urls(p: Product) -> list[str]:
    if p.founder is None:
        return []
    urls = []
    for handle in p.founder.handles:
        url = handle.url or product_profile_url_for(
            handle.platform, handle=handle.handle, external_id=handle.external_id
        )
        if url:
            urls.append(url)
    return urls


def _product_link_summary(p: Product) -> str:
    parts: list[str] = []
    primary = _product_primary_url(p)
    if primary:
        parts.append(f"产品 {primary}")
    source_urls = _dedupe_urls([e.url for e in p.evidence])
    if source_urls:
        parts.append("来源 " + " ".join(source_urls))
    founder_urls = _dedupe_urls(_product_founder_urls(p))
    if founder_urls:
        parts.append("创始人 " + " ".join(founder_urls))
    return " | ".join(parts)


def _render_product_line(index: int, p: Product) -> None:
    founder = p.founder.display_name if p.founder else ""
    typer.echo(f"{index}. {p.name or p.key}  [{p.key}]")
    if p.tagline:
        typer.echo(f"   {p.tagline}")
    meta = []
    if founder:
        meta.append(f"founder={founder}")
    if p.target_user:
        meta.append(f"用户={p.target_user}")
    if meta:
        typer.echo("   " + "  ".join(meta))
    if p.why_worth_attention:
        typer.echo(f"   值得关注：{p.why_worth_attention}")
    links = _product_link_summary(p)
    if links:
        typer.echo(f"   链接：{links}")
    typer.echo(f"   深入：uv run finch products explore {p.id}")


def _render_product_full(p: Product) -> None:
    founder = p.founder.display_name if p.founder else ""
    typer.echo(f"id: {p.id}")
    typer.echo(f"key: {p.key}")
    typer.echo(f"name: {p.name or '-'}")
    typer.echo(f"tagline: {p.tagline or '-'}")
    typer.echo(f"founder: {founder or '-'}")
    typer.echo(f"product_url: {_product_primary_url(p) or '-'}")
    source_urls = _dedupe_urls([e.url for e in p.evidence])
    typer.echo(f"source_urls: {' '.join(source_urls) or '-'}")
    founder_urls = _dedupe_urls(_product_founder_urls(p))
    typer.echo(f"founder_urls: {' '.join(founder_urls) or '-'}")
    typer.echo(f"target_user: {p.target_user or '-'}")
    typer.echo(f"problem_solved: {p.problem_solved or '-'}")
    typer.echo(f"core_solution: {p.core_solution or '-'}")
    typer.echo(f"differentiation: {p.differentiation or '-'}")
    typer.echo(f"progress: {p.progress or '-'}")
    typer.echo(f"why_worth_attention: {p.why_worth_attention or '-'}")
    typer.echo(f"score: {p.score_total:.2f}")
    typer.echo(f"evidence: {len(p.evidence)} 条")


def _render_solution(s: Solution, product: Product | None = None) -> None:
    verdict_labels = {
        "worth_trial": "值得试用",
        "worth_outreach": "值得联系创业者",
        "watch": "继续观察",
        "skip": "跳过",
    }
    typer.echo("8 点理解：")
    typer.echo(f"1) 为谁解决什么问题：{s.who_problem or '-'}")
    typer.echo(f"2) 原来如何解决：{s.prior_approach or '-'}")
    typer.echo(f"3) 新方案：{s.new_solution or '-'}")
    typer.echo(f"4) 为什么可能更有价值：{s.why_more_valuable or '-'}")
    typer.echo("5) 价值证据 vs 假设：")
    if not s.value_evidence:
        typer.echo("   （无显式证据，均视为未验证假设）")
    for ev in s.value_evidence:
        origin = {"founder": "创业者主张", "feedback": "用户反馈", "inference": "推断"}.get(
            ev.origin, ev.origin
        )
        typer.echo(f"   [{origin}/{ev.status.value}] {ev.claim or '-'}")
    typer.echo(f"6) 值得学习的设计思路：{s.design_thinking or '-'}")
    typer.echo(f"7) 谁在构建：{s.builders or '-'}")
    typer.echo(f"8) 下一步：{verdict_labels.get(s.verdict.value, s.verdict.value)}")
    if s.feedback_opportunities:
        typer.echo("用户反馈揭示的机会：")
        for opp in s.feedback_opportunities:
            typer.echo(f"   [{opp.kind.value}] {opp.claim or '-'}")
    if product is not None:
        links = _product_link_summary(product)
        if links:
            typer.echo(f"相关链接：{links}")


def _render_feedback_report(report: FeedbackReport) -> None:
    if report.insufficient_feedback:
        typer.echo("用户反馈研究：证据不足。")
        if report.info_gaps:
            typer.echo(f"  说明：{report.info_gaps}")
        return
    typer.echo(f"用户反馈（{len(report.feedback)} 条）：")
    for i, f in enumerate(report.feedback, 1):
        summary = f.user_problem or f.requested_outcome or f.user_scenario or "-"
        typer.echo(f"{i}. [{f.feedback_type.value}] {summary}")
        if f.original_excerpt:
            if len(f.original_excerpt) <= 160:
                excerpt = f.original_excerpt
            else:
                excerpt = f.original_excerpt[:159] + "…"
            typer.echo(f"   摘录：{excerpt}")
        if f.source_url:
            typer.echo(f"   来源：{f.source_url}")
    for label, points in (
        ("高频痛点", report.recurring_problems),
        ("未满足需求", report.unmet_needs),
        ("用户意见矛盾", report.contradictions),
        ("可向创业者提问", report.questions_for_founder),
    ):
        if not points:
            continue
        typer.echo(f"{label}：")
        for p in points:
            typer.echo(f"  - {p.claim or '-'}" + (f"（{p.count} 条）" if p.count else ""))
    if report.info_gaps:
        typer.echo(f"信息不足：{report.info_gaps}")


def _render_report_line(report: DiscoveryRunReport) -> str:
    states = ", ".join(f"{s}:{st}" for s, st in sorted(report.per_source_state.items()))
    return (
        f"raw={report.raw_count} norm={report.normalized_count} "
        f"filtered={report.filtered_count} products={report.product_count} "
        f"cards={report.card_fill_success}ok/{report.card_fill_failed}fail "
        f"{report.elapsed_seconds}s [{states}]"
    )


@products_app.command("daily")
def products_daily(
    refresh: bool = typer.Option(False, "--refresh", help="重新跑发现；否则回放最新快照"),
    view: str = typer.Option("home", "--view", help="home|browse"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    lookback_hours: int | None = typer.Option(None, "--lookback-hours", help="回溯小时数"),
    question: str | None = typer.Option(None, "--question", help="发现意图/问题"),
) -> None:
    """每日产品候选（5–10 个）；--refresh 跑发现，否则回放最新快照。"""
    from dataclasses import asdict

    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import aggregate_state

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ProductRepository(ws)
    snap_repo = ProductSnapshotRepository(ws)
    presentation = ProductPresentationRepository(ws)

    report = None
    sync_results = []
    if refresh:
        runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
        gateway = OpenCliGateway(profile=settings.opencli.profile)
        result = run_product_discovery(
            settings,
            runner=runner,
            gateway=gateway,
            lookback_hours=lookback_hours,
            question=question,
        )
        snapshot = result.snapshot
        products = result.products
        report = result.report
        sync_results = result.sync_results
        if result.detail:
            typer.echo(f"note: {result.detail}")
    else:
        snapshot = snap_repo.latest()
        products = []
        if snapshot is not None:
            for pid in snapshot.product_ids:
                p = repo.get(pid)
                if p is not None:
                    products.append(p)

    if view == "browse":
        products = sorted(repo.list_all(), key=lambda p: (-p.score_total, p.id))
    elif snapshot is not None:
        for p in products:
            presentation.record_shown(product_id=p.id, surface="home", snapshot_id=snapshot.id)

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "snapshot_id": snapshot.id if snapshot else "",
                    "products": [p.model_dump(mode="json") for p in products],
                    "report": asdict(report) if report else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if report is not None:
        typer.echo(_render_report_line(report))

    if not products:
        if sync_results:
            typer.echo("今日无产品候选（空轮保持空）。来源状态：")
            for sr in sync_results:
                typer.echo(
                    f"  {sr.source.value}: {aggregate_state(sr)}"
                    + (f" ({sr.detail})" if sr.detail else "")
                )
        else:
            typer.echo(
                "今日无产品候选（空轮保持空）。可用 `uv run finch products daily --refresh`。"
            )
        return
    typer.echo(f"每日产品候选（{len(products)}）：")
    for i, p in enumerate(products, 1):
        _render_product_line(i, p)


@products_app.command("list")
def products_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """全部已保存产品（按分数降序）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    repo = ProductRepository(ws)
    products = sorted(repo.list_all(), key=lambda p: (-p.score_total, p.id))
    if as_json:
        typer.echo(
            json.dumps([p.model_dump(mode="json") for p in products], ensure_ascii=False, indent=2)
        )
        return
    if not products:
        typer.echo("暂无产品。")
        return
    typer.echo(f"全部产品（{len(products)}）：")
    for i, p in enumerate(products, 1):
        _render_product_line(i, p)


@products_app.command("show")
def products_show(
    product_id: str = typer.Argument(..., help="产品 id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看单个产品的完整档案。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    repo = ProductRepository(ws)
    p = repo.get(product_id)
    if p is None:
        typer.echo(f"product not found: {product_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(p.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    _render_product_full(p)


@products_app.command("explore")
def products_explore(
    product_id: str = typer.Argument(..., help="产品 id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    refresh: bool = typer.Option(False, "--refresh", help="强制重新分析（默认幂等复用已存结果）"),
    skip_feedback: bool = typer.Option(
        False, "--skip-feedback", help="不研究用户反馈，仅基于已有资料分析"
    ),
) -> None:
    """选中一个产品 → 8 点理解（方案分析；默认纳入用户反馈研究）。"""
    from finch.sources.connectors.github import GitHubConnector
    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import DiscoveryOrchestrator
    from finch.sources.store import ArtifactRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ProductRepository(ws)
    sol_repo = SolutionRepository(ws)
    p = repo.get(product_id)
    if p is None:
        typer.echo(f"product not found: {product_id}")
        raise typer.Exit(code=1)

    ProductPresentationRepository(ws).record_selected(product_id=product_id, surface="explore")

    runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())

    feedback_report: FeedbackReport | None = None
    if not skip_feedback and settings.products.feedback.enabled:
        orchestrator = DiscoveryOrchestrator(
            ws, gateway=OpenCliGateway(profile=settings.opencli.profile)
        )
        feedback_report = research_feedback(
            p,
            runner=runner,
            repository=ProductFeedbackRepository(ws),
            settings=settings,
            orchestrator=orchestrator,
            github_connector=GitHubConnector(),
            force=False,
        )

    artifacts = ArtifactRepository(ws).list_by_ids(p.source_refs)
    solution = explore_product(
        runner,
        p,
        artifacts,
        sol_repo,
        version=settings.products.solution.version,
        force=refresh,
        feedback_report=feedback_report,
    )

    if solution is None:
        typer.echo("方案分析未生成（证据不足或 LLM 失败）。")
        raise typer.Exit(code=1)

    if as_json:
        payload = solution.model_dump(mode="json")
        if feedback_report is not None:
            payload["feedback"] = feedback_report.model_dump(mode="json")
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if feedback_report is not None:
        _render_feedback_report(feedback_report)
        typer.echo("")
    _render_solution(solution, p)


@products_app.command("feedback")
def products_feedback(
    product_id: str = typer.Argument(..., help="产品 id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    refresh: bool = typer.Option(False, "--refresh", help="强制重新搜索（默认幂等复用缓存）"),
) -> None:
    """研究一个产品的真实用户反馈（定向搜索 → 结构化 → 报告）。"""
    from finch.sources.connectors.github import GitHubConnector
    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import DiscoveryOrchestrator

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    p = ProductRepository(ws).get(product_id)
    if p is None:
        typer.echo(f"product not found: {product_id}")
        raise typer.Exit(code=1)

    runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
    orchestrator = DiscoveryOrchestrator(
        ws, gateway=OpenCliGateway(profile=settings.opencli.profile)
    )
    report = research_feedback(
        p,
        runner=runner,
        repository=ProductFeedbackRepository(ws),
        settings=settings,
        orchestrator=orchestrator,
        github_connector=GitHubConnector(),
        force=refresh,
    )

    if report is None:
        typer.echo("反馈研究未启用。")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    _render_feedback_report(report)


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


# 兼容再导出：现有测试通过 `finch.cli.<name>` 导入/打补丁，保持零改动。
# isort: off
from finch.cli.frozen import (  # noqa: E402,F401
    _lookback_hours,
    _persist_discovery,
    _render_opportunity_assessment_coverage,
    _render_preferred_opportunity,
    _render_preferred_skips,
    _require_notion,
    _run_daily_full,
    _run_discovery,
    conversations_get,
    peers_get,
)
from finch.angle_discovery.mindmap_service import MindMapService  # noqa: E402,F401
from finch.angle_discovery.service import AngleDiscoveryService  # noqa: E402,F401
from finch.article.service import ArticleAnalysisService  # noqa: E402,F401
from finch.content.voice import load_voice_profile  # noqa: E402,F401
from finch.content.writer import rewrite_with_instruction  # noqa: E402,F401
from finch.content_summary.service import ContentSummaryService  # noqa: E402,F401
from finch.drafts.service import DraftService  # noqa: E402,F401
from finch.evidence.extractor import Extractor  # noqa: E402,F401
from finch.github.commit_reader import CommitReader  # noqa: E402,F401
from finch.github.commit_reader import load_commit_details  # noqa: E402,F401
from finch.ideas.commit_service import CommitService  # noqa: E402,F401
from finch.ideas.divergence import IdeaDiverger  # noqa: E402,F401
from finch.ideas.fragment_service import FragmentService  # noqa: E402,F401
from finch.learn.reflection import WeeklyReflectionService  # noqa: E402,F401
from finch.opportunities.prepare import prepare_contribution  # noqa: E402,F401
from finch.storage.repositories import ConversationThreadRepository  # noqa: E402,F401
# isort: on


class _CliModule(types.ModuleType):
    """让 `monkeypatch.setattr(finch.cli, name, ...)` 同步到已抽出的子模块。

    命令体原样搬进 `frozen.py` 后，其模块级名字（如 `load_settings`）不再与本包共享；
    测试仍对 `finch.cli.<name>` 打补丁，这里把补丁转发给定义了同名全局的子模块。
    """

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name.startswith("__") or name == "frozen":
            return
        for mod in (frozen,):
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _CliModule


if __name__ == "__main__":
    app()
