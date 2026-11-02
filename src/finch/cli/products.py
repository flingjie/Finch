"""Finch CLI：products 子命令（产品发现 / 反馈研究 / 方案分析）。"""

import json
from typing import cast

import typer

from finch.codex.runner import CodexRunner
from finch.llm.openai_compatible import create_runner
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
from finch.settings import load_settings
from finch.storage.workspace import Workspace

products_app = typer.Typer(help="AI 产品发现 + 创业者连接（产品候选 / 方案分析）")


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
