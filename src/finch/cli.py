"""Finch CLI（spec 10）。"""

import difflib
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
import yaml
from pydantic import ValidationError

from .article.models import ArticleReport
from .article.service import ArticleAnalysisService
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
from .conversations.models import ConversationThread
from .conversations.service import (
    ConversationService,
    ConversationServiceError,
    active_observation_notes,
    commitment_id_for,
)
from .dialogue.models import DialogueNote
from .dialogue.service import DialogueService, DialogueServiceError
from .discovery.daily import DailyDiscoveryResult
from .drafts.service import DraftCreateResult, DraftService
from .engagement.flow import EngagementRunResult
from .engagement.metrics import (
    compute_relationship_metrics,
    explain_recommendation_adjustments,
    render_relationship_metrics,
)
from .engagement.models import (
    ActionFeedbackValue,
    DiscoverySnapshot,
    InterestFeedbackValue,
    PresentationRecord,
    RecommendationEntry,
    RecommendationFeedback,
)
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.gh_client import GhClient, GhError
from .github.local_repo import resolve_commit_repo
from .ideas.commit_service import CommitService
from .ideas.divergence import IdeaDiverger
from .ideas.fragment_service import FragmentService
from .ideas.models import FactBundle, IdeaExploration, Selection
from .ideas.service import IdeaService
from .inbox.models import DecisionAction, InboxTrack
from .inbox.service import InboxDecisionService, list_items
from .learn.models import Feedback, OutcomeAssessment
from .learn.reflection import (
    WeeklyReflectionService,
    idea_revision_diff_lines,
    render_reflection,
)
from .learn.weekly import weekly_analysis
from .llm.openai_compatible import create_runner
from .opportunities.models import Opportunity as PreferredOpportunity
from .opportunities.models import OpportunityStatus
from .opportunities.prepare import (
    PreparedArtifact,
    PreparedContribution,
    artifact_id_for,
    effective_form,
    prepare_contribution,
)
from .opportunities.repository import (
    ArtifactRepository as PreferredArtifactRepository,
)
from .opportunities.repository import (
    OpportunityRepository as PreferredOpportunityRepository,
)
from .opportunities.service import OpportunityService
from .peers.models import PeerProfile
from .peers.service import PeerService, profile_url_for
from .practice.service import PracticeService
from .profile import bootstrap as profile_bootstrap
from .profile.models import (
    PracticeEvidenceStatus,
    PracticeItem,
    load_practice_profile,
    save_practice_profile,
)
from .profile.render import render_user_practices
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
    DiscoverySnapshotRepository,
    DraftRepository,
    EvidenceRepository,
    FeedbackRepository,
    FeedbackSnapshotRepository,
    IdeaExplorationRepository,
    InteractionRecordRepository,
    PeerRepository,
    PracticeSessionRepository,
    PresentationRecordRepository,
    PublicationIntentRepository,
    RecommendationFeedbackRepository,
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

sources_app = typer.Typer(help="跨平台 OpenCLI 采集源（只读）")
app.add_typer(sources_app, name="sources")

repos_app = typer.Typer(help="X 分享的 GitHub 仓库发现与热度榜（只读）")
app.add_typer(repos_app, name="repos")

voice_app = typer.Typer(help="Manage the author voice profile (local, no auto-publish)")
app.add_typer(voice_app, name="voice")

profile_app = typer.Typer(help="Manage the user's confirmed practice profile (local only)")
app.add_typer(profile_app, name="profile")

ideas_app = typer.Typer(help="Idea 候选流（commit / 用户片段 / 对话提炼 + 状态转换）")
app.add_typer(ideas_app, name="ideas")

drafts_app = typer.Typer(help="Draft 生成（已确认 idea → 草稿，不自动发布）")
app.add_typer(drafts_app, name="drafts")

review_app = typer.Typer(help="Review original drafts (accept/revise/skip, no auto-publish)")
app.add_typer(review_app, name="review")

connect_app = typer.Typer(
    help=(
        "连接主循环：today / daily / person / record-presented / "
        "prepare / feedback / assess / artifact-status"
    )
)
app.add_typer(connect_app, name="connect")

peers_app = typer.Typer(help="同行档案与关系上下文")
app.add_typer(peers_app, name="peers")

people_app = typer.Typer(help="今日承诺面：需回应/兑现的真实对话线索")
app.add_typer(people_app, name="people")

connections_app = typer.Typer(help="连接机会与互动记录（record / follow-up）")
app.add_typer(connections_app, name="connections")

collisions_app = typer.Typer(help="跨领域碰撞")
app.add_typer(collisions_app, name="collisions")

experiments_app = typer.Typer(help="一周内小实验")
app.add_typer(experiments_app, name="experiments")

inspirations_app = typer.Typer(help="轻量灵感笔记")
app.add_typer(inspirations_app, name="inspirations")

conversations_app = typer.Typer(help="对话线索与跟进")
app.add_typer(conversations_app, name="conversations")

practice_app = typer.Typer(help="表达练习")
app.add_typer(practice_app, name="practice")

style_app = typer.Typer(help="分析一段文本/链接的写作特点")
app.add_typer(style_app, name="style")

article_app = typer.Typer(help="分析一篇文章的表达任务、读者变化与方法有效性")
app.add_typer(article_app, name="article")

community_app = typer.Typer(help="社区匹配与进入助手（发现、观察、回访可进入的社区）")
app.add_typer(community_app, name="community")

dialogue_app = typer.Typer(help="讨论摘要记忆（薄持久化，可检索；命令由 Skill 使用）")
app.add_typer(dialogue_app, name="dialogue")


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


def _render_exploration(exploration, job: "ContentJob | None") -> str:
    """发散结果的呈现：推荐角度 + 其余角度 + 淘汰角度 + 改选命令。"""
    if not exploration.angles:
        if exploration.rejected_angles:
            lines = ["（无角度值得写；淘汰: "
                     + "; ".join(f"[{r.index}] {r.core_point}（{r.reason}）"
                                 for r in exploration.rejected_angles) + "）"]
        else:
            lines = ["（无角度值得写）"]
        return "\n".join(lines)
    rec = exploration.recommended_index
    lines = [f"我看出 {len(exploration.angles)} 个可能观点，推荐第 {rec}："]
    for a in exploration.angles:
        marker = "〔推荐〕" if a.index == rec else f"  {a.index}."
        lines.append(f"{marker} {a.core_point}")
        lines.append(f"      情境: {a.reader_situation}")
        lines.append(f"      所得({a.takeaway_kind}): {a.reader_takeaway}")
        lines.append(f"      证据: {a.evidence_support}")
        if a.counterexample_or_limit:
            lines.append(f"      边界: {a.counterexample_or_limit}")
    if exploration.recommendation_reason:
        lines.append(f"推荐理由: {exploration.recommendation_reason}")
    if exploration.rejected_angles:
        lines.append("淘汰: " + "; ".join(
            f"[{r.index}] {r.core_point}（{r.reason}）" for r in exploration.rejected_angles
        ))
    if job is not None:
        lines.append("")
        lines.append(f"已建候选: {job.id} ({job.status.value})")
        lines.append(f"uv run finch ideas confirm {job.id}")
        lines.append(f"改选: uv run finch ideas choose {exploration.id} <index>")
    return "\n".join(lines)


_CONNECT_CARD_LIMIT = 6

def _thread_next_step(thread: ConversationThread) -> str:
    now = datetime.now(UTC)
    # Important review due with no new facts → remind only, never auto-greet.
    if (
        thread.important_review_enabled
        and thread.next_review_at is not None
        and thread.next_review_at <= now
        and not thread.pending_triggers
    ):
        return "审阅近况 / 继续观察（重要关系周期回顾；无新事实不制造问候）"
    open_commitments = [
        c
        for c in thread.commitments
        if c.status.value == "open" and (c.due_at is None or c.due_at <= now)
    ]
    notes = active_observation_notes(thread)
    if open_commitments:
        return "履行或确认自己的开放承诺"
    trigger_vals = {
        t.value if hasattr(t, "value") else str(t) for t in thread.pending_triggers
    }
    if "new_evidence" in trigger_vals:
        return "分享新证据或实验结果并继续对话"
    if thread.open_questions or thread.open_question_notes:
        return "回答未解问题或提出实验"
    if notes:
        kinds = {n.kind.value for n in notes if n.kind is not None}
        if "usage_feedback" in kinds:
            return "基于使用反馈准备一条有上下文的跟进"
        return "澄清 observation 中仍未知的问题"
    if thread.pending_triggers:
        return "处理待跟进触发（新回复/承诺/证据/相关更新）"
    return "已无未解问题；确认是否关闭或延续新主题"


def _peer_home_url(peer: PeerProfile) -> str | None:
    """主页链接：优先身份上的 url，否则按平台 + handle 推导。"""
    for identity in peer.platform_identities:
        if identity.url:
            return identity.url
        derived = profile_url_for(
            identity.platform,
            username=identity.username,
            author_id=identity.author_id,
        )
        if derived:
            return derived
    return None


def _peer_signal_post_url(peer: PeerProfile) -> str | None:
    """代表帖：source_refs 里第一条 http(s) 链接。"""
    for ref in peer.source_refs:
        if ref.startswith(("http://", "https://")):
            return ref
    return None
def _render_peer_card(peer: PeerProfile) -> str:
    """同行决策卡：谁 / 为什么值得连 / 链接 / 下一步；show 命令带真实 id。"""
    lines = [f"谁: {peer.display_name or peer.id}"]
    if (peer.why_relevant or "").strip():
        lines.append(f"为什么值得连: {peer.why_relevant}")
    if (peer.next_context or "").strip():
        lines.append(f"下一步上下文: {peer.next_context}")
    if peer.shared_topics:
        lines.append(f"共同话题: {', '.join(peer.shared_topics)}")
    home = _peer_home_url(peer)
    if home:
        lines.append(f"主页: {home}")
    signal = _peer_signal_post_url(peer)
    if signal:
        lines.append(f"代表帖: {signal}")
    lines.append(f"uv run finch peers show {peer.id}")
    return "\n".join(lines)


def _render_peer_cards(peers: list[PeerProfile], *, limit: int = _CONNECT_CARD_LIMIT) -> str:
    if not peers:
        return "no peers"
    shown = peers[:limit]
    parts = ["\n\n".join(_render_peer_card(p) for p in shown)]
    if len(peers) > limit:
        parts.append(
            f"共 {len(peers)} 个同行，以上 {len(shown)} 个。其余：uv run finch peers list"
        )
    return "\n\n".join(parts)


def _render_peer_detail(peer: PeerProfile) -> str:
    lines = [
        f"谁: {peer.display_name or peer.id}",
        f"阶段: {peer.relationship_stage.value}",
    ]
    if peer.expertise_topics:
        lines.append(f"专长: {', '.join(peer.expertise_topics)}")
    if peer.shared_topics:
        lines.append(f"共同话题: {', '.join(peer.shared_topics)}")
    if (peer.why_relevant or "").strip():
        lines.append(f"为什么值得连: {peer.why_relevant}")
    if (peer.next_context or "").strip():
        lines.append(f"下一步上下文: {peer.next_context}")
    home = _peer_home_url(peer)
    if home:
        lines.append(f"主页: {home}")
    signal = _peer_signal_post_url(peer)
    if signal:
        lines.append(f"代表帖: {signal}")
    lines.append("uv run finch connect prepare")
    return "\n".join(lines)
def _render_thread_card(thread: ConversationThread, *, for_follow_up: bool = False) -> str:
    """对话线索决策卡：话题 / 未解问题 / 观察笔记 / 建议下一步。"""
    lines = [f"话题: {thread.topic}"]
    if thread.open_questions:
        lines.append(f"未解问题: {'; '.join(thread.open_questions)}")
    notes = active_observation_notes(thread)
    if notes:
        summary = "; ".join(
            f"{n.kind.value if n.kind else 'note'}: {n.text[:60]}" for n in notes[:3]
        )
        lines.append(f"观察: {summary}")
    open_c = [c for c in thread.commitments if c.status.value == "open"]
    if open_c:
        lines.append(f"开放承诺: {len(open_c)}")
    if for_follow_up or thread.open_questions or notes or open_c:
        lines.append(f"建议下一步: {_thread_next_step(thread)}")
    if for_follow_up:
        lines.append(f"uv run finch conversations show {thread.id}")
    elif thread.open_questions or notes or open_c:
        lines.append(f"uv run finch conversations follow-up {thread.id}")
    else:
        lines.append(f"uv run finch conversations show {thread.id}")
    return "\n".join(lines)


def _render_thread_cards(
    threads: list[ConversationThread],
    *,
    limit: int = _CONNECT_CARD_LIMIT,
    needs_follow_up: bool = False,
) -> str:
    if not threads:
        return "no conversations"
    shown = threads[:limit]
    parts = [
        "\n\n".join(
            _render_thread_card(t, for_follow_up=False) for t in shown
        )
    ]
    if len(threads) > limit:
        rest = (
            "uv run finch conversations list --needs-follow-up"
            if needs_follow_up
            else "uv run finch conversations list"
        )
        parts.append(f"共 {len(threads)} 条线索，以上 {len(shown)} 条。其余：{rest}")
    return "\n\n".join(parts)


def _render_thread_detail(thread: ConversationThread) -> str:
    lines = [
        f"话题: {thread.topic}",
        f"同行: {thread.peer_id}",
        f"状态: {thread.status.value}",
        f"revision: {thread.revision}",
    ]
    if thread.open_questions:
        lines.append(f"未解问题: {'; '.join(thread.open_questions)}")
    if thread.agreements:
        lines.append(f"共识: {', '.join(thread.agreements)}")
    if thread.disagreements:
        lines.append(f"分歧: {', '.join(thread.disagreements)}")
    if thread.possible_experiments:
        lines.append(f"可实验: {', '.join(thread.possible_experiments)}")
    notes = active_observation_notes(thread)
    if notes:
        lines.append("观察笔记:")
        for n in notes:
            kind = n.kind.value if n.kind else "note"
            tool = f" tool={n.tool_ref}" if n.tool_ref else ""
            lines.append(f"  - [{kind}] {n.text} (src={n.source_ref}{tool})")
    open_c = [c for c in thread.commitments if c.status.value == "open"]
    if open_c:
        lines.append("开放承诺:")
        for c in open_c:
            lines.append(f"  - {c.text or c.id} (src={c.source_ref})")
    if thread.open_questions or notes or open_c:
        lines.append(f"uv run finch conversations follow-up {thread.id}")
    return "\n".join(lines)


def _idea_next_steps(job: ContentJob) -> list[str]:
    """按状态给出可复制的下一步 CLI 命令。"""
    if job.status == ContentJobStatus.PROPOSED:
        return [
            f"uv run finch ideas confirm {job.id}",
            f"uv run finch ideas skip {job.id} --reason ...",
        ]
    if job.status == ContentJobStatus.CONFIRMED:
        return [f"uv run finch drafts create {job.id}"]
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
    """单个草稿的决策卡：正文 + 可复制审核命令。"""
    lines = [
        f"> {draft.body}",
        "",
        "状态：未发布",
        "",
        f"uv run finch review approve {draft.id}",
        f'uv run finch drafts revise {draft.id} --instruction "..."',
        f"uv run finch review skip {draft.id} --reason ...",
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


def _lookback_hours(value: str | None) -> int | None:
    """解析 ``--lookback``：`24h` / `30d` / `720`（小时数）→ 小时数。"""
    if value is None:
        return None
    v = value.strip()
    try:
        if v.endswith("h"):
            return int(v[:-1])
        if v.endswith("d"):
            return int(v[:-1]) * 24
        return int(v)
    except ValueError:
        raise typer.BadParameter(
            f"invalid --lookback: {value!r} (use like 24h / 30d / 720)"
        ) from None


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
        None, "--source", help="twitter|reddit|github|v2ex|weixin|xiaohongshu"
    ),
    all_sources: bool = typer.Option(False, "--all", help="同步全部已注册源"),
    query: list[str] = typer.Option([], "--query", "-q", help="查询词（可重复）"),
    url: list[str] = typer.Option([], "--url", help="URL 导入（公众号/笔记等）"),
    limit: int = typer.Option(20, "--limit", help="每查询条数上限"),
) -> None:
    """只读同步：raw 落盘 → RawArtifact → Peer/Person 投影（单源失败不阻塞）。"""
    from finch.sources.models import Source
    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import DiscoveryOrchestrator
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
    else:
        assert src is not None
        results = [orch.sync_source(src, contexts.get(src))]

    for r in results:
        typer.echo(
            f"{r.source.value}: {r.status.value} "
            f"raw={r.raw_count} norm={r.normalized_count} new={r.created_count} "
            f"projected={r.projected_count}"
            + (f" ({r.detail})" if r.detail else "")
        )


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
    bundles = CommitService(reader, extractor).to_facts(details, repo=repo)
    diverge = IdeaDiverger(
        cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    )
    idea_service = IdeaService(ContentJobRepository(ws))
    exploration_repo = IdeaExplorationRepository(ws)
    results: list[tuple[IdeaExploration, ContentJob | None]] = []
    for bundle in bundles:
        exploration = diverge.explore(bundle)
        job: ContentJob | None = None
        if exploration.recommended_index is not None:
            angle = next(
                a for a in exploration.angles if a.index == exploration.recommended_index
            )
            job = idea_service.create_from_angle(
                angle, bundle=bundle, generator=exploration.generator
            )
            exploration.selections.append(
                Selection(index=angle.index, job_id=job.id, at=datetime.now(UTC))
            )
        exploration_repo.upsert(exploration)
        results.append((exploration, job))
    if as_json:
        payload = [
            {
                "exploration_id": e.id,
                "recommended_index": e.recommended_index,
                "job_id": (job.id if job is not None else None),
            }
            for e, job in results
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo("\n\n".join(
            _render_exploration(e, job) for e, job in results
        ))


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


@ideas_app.command("choose")
def ideas_choose(
    exploration_id: str = typer.Argument(..., help="exploration id（finch ideas commit 输出）"),
    angle_index: int = typer.Argument(..., help="要选择的角度序号（1-based）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从一次发散中选择（或改选）一个角度，为其创建 ContentJob。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    exploration_repo = IdeaExplorationRepository(ws)
    exploration = exploration_repo.get(exploration_id)
    if exploration is None:
        typer.echo(f"exploration not found: {exploration_id}")
        raise typer.Exit(code=1)
    angle = next((a for a in exploration.angles if a.index == angle_index), None)
    if angle is None:
        typer.echo(f"angle {angle_index} not in exploration {exploration_id}")
        raise typer.Exit(code=1)
    bundle = FactBundle(
        facts=list(exploration.facts),
        source_refs=list(exploration.source_refs),
        boundaries=exploration.boundaries,
        evidence_status=exploration.evidence_status or "observed",
        origin=exploration.origin,
        source_kind=exploration.source_kind or "commit",
    )
    job = IdeaService(ContentJobRepository(ws)).create_from_angle(
        angle, bundle=bundle, generator=exploration.generator
    )
    exploration.selections.append(
        Selection(index=angle.index, job_id=job.id, at=datetime.now(UTC))
    )
    exploration_repo.upsert(exploration)
    if as_json:
        typer.echo(json.dumps(
            {"exploration_id": exploration.id, "job_id": job.id, "status": job.status.value},
            ensure_ascii=False, indent=2,
        ))
    else:
        typer.echo(f"已选择角度 {angle.index}: {job.id} ({job.status.value})")
        typer.echo(f"uv run finch ideas confirm {job.id}")


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
        f"uv run finch review approve {draft.id}",
        f'uv run finch drafts revise {draft.id} --instruction "..."',
        f"uv run finch review skip {draft.id} --reason ...",
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


@drafts_app.command("write")
def drafts_write(
    text: str = typer.Argument(..., help="要写成草稿的文本"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """把一段文本直接写成草稿（直接写作短路：不强制先确认立场，草稿仍为待审）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        idea = FragmentService(runner).from_text(text)
        job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
    except (RuntimeError, StructuredOutputError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
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
            job.id,
            version="1.0.0",
            format="original",
            voice_version="1.0.0",
            allow_unconfirmed=True,
        )
    except (KeyError, ValueError, RuntimeError, StructuredOutputError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        payload = {
            "draft_id": result.draft.id,
            "idea_id": job.id,
            "status": "drafted",
            "body": result.draft.body,
            "critic_rounds": result.critic_rounds,
            "outcome": result.outcome,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_draft_result(result))


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
    presentations = PresentationRecordRepository(ws).list_all()
    rec_feedback = RecommendationFeedbackRepository(ws).list_all()
    worth = sum(
        1
        for f in rec_feedback
        if f.dimension == "interest" and f.value == "worth_following"
    )
    prepared = sum(
        1
        for o in PreferredOpportunityRepository(ws).list_all()
        if o.status == OpportunityStatus.READY
    )
    rel_metrics = compute_relationship_metrics(
        peers=PeerRepository(ws).list_all(),
        interactions=InteractionRecordRepository(ws).list_all(),
        threads=ConversationThreadRepository(ws).list_all(),
        snapshots=FeedbackSnapshotRepository(ws).list_all(),
        jobs=ContentJobRepository(ws).list_jobs(),
        now=datetime.now(UTC),
        presentation_count=len(presentations),
        worth_following_count=worth,
        prepared_count=prepared,
    )
    from finch.opportunities.funnel import (
        compute_opportunity_funnel,
        render_opportunity_funnel,
    )

    funnel = compute_opportunity_funnel(
        snapshots=DiscoverySnapshotRepository(ws).list_all(),
        opportunities=PreferredOpportunityRepository(ws).list_all(),
        opportunity_repo=PreferredOpportunityRepository(ws),
        interactions=InteractionRecordRepository(ws).list_all(),
        feedbacks=FeedbackSnapshotRepository(ws).list_all(),
        presentations=presentations,
        since=since,
    )
    records = InteractionRecordRepository(ws).list_all()
    message_excerpts = [
        f"[{r.id}] {r.direction}: {(r.body or r.published_body)[:160]}"
        for r in sorted(records, key=lambda x: x.occurred_at, reverse=True)[:6]
        if (r.body or r.published_body)
    ]
    idea_diffs = idea_revision_diff_lines(ContentJobRepository(ws).list_jobs())
    from finch.conversations.service import active_observation_notes

    all_threads = ConversationThreadRepository(ws).list_all()
    observation_lines: list[str] = []
    commitment_lines: list[str] = []
    for t in all_threads:
        for n in active_observation_notes(t):
            kind = n.kind.value if n.kind else "note"
            observation_lines.append(f"[{t.id}] {kind}: {n.text[:120]}")
        for c in t.commitments:
            if c.status.value == "open":
                commitment_lines.append(
                    f"[{t.id}] open ({c.owner}): {c.text or c.id}"
                )
    try:
        reflection = WeeklyReflectionService(runner).reflect(
            report,
            relationship_metrics=rel_metrics,
            feedbacks=window_feedbacks,
            threads=all_threads,
            voice_profile=load_voice_profile(settings.paths.voice_profile_path),
            message_excerpts=message_excerpts,
            idea_diffs=idea_diffs,
            observation_notes=observation_lines,
            open_commitments=commitment_lines,
        )
    except (RuntimeError, StructuredOutputError) as exc:
        typer.echo(f"weekly reflection failed: {exc}")
        raise typer.Exit(code=1) from exc
    if as_json:
        payload = reflection.model_dump(mode="json")
        payload["opportunity_funnel"] = funnel.model_dump(mode="json")
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(render_opportunity_funnel(funnel))
        typer.echo("")
        typer.echo(render_reflection(reflection))


def _voice_profile_path() -> Path:
    settings = load_settings()
    return settings.paths.voice_profile_path


@voice_app.command("show")
def voice_show(
    as_json: bool = typer.Option(False, "--json", help="输出完整画像"),
) -> None:
    """加载并打印声音画像（默认摘要；--json 输出完整结构）。"""
    profile = load_voice_profile(_voice_profile_path())
    if as_json:
        typer.echo(
            yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        )
        return
    prefer = profile.preferred_patterns[:3]
    avoid = profile.avoid_phrases[:3]
    lines = [
        "画像摘要",
        f"- 偏好模式: {len(profile.preferred_patterns)} 条",
        f"- 避免表达: {len(profile.avoid_phrases)} 条",
        f"- 已批准样例: {len(profile.approved_examples)}",
        f"- 已拒绝样例: {len(profile.rejected_examples)}",
    ]
    if prefer:
        lines += ["", "偏好（最多 3）："] + [f"- {p}" for p in prefer]
    if avoid:
        lines += ["", "避免（最多 3）："] + [f"- {p}" for p in avoid]
    lines += [
        "",
        "uv run finch voice propose",
        "uv run finch voice show --json",
    ]
    typer.echo("\n".join(lines))


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
    avoid = proposal.avoid_phrases[:3]
    prefer = proposal.preferred_patterns[:3]
    if not avoid and not prefer:
        typer.echo("暂无更新候选（需要带 diff 的已批准样例）")
        typer.echo("uv run finch voice show --json")
        return
    lines = ["更新候选（最多 3+3，不会自动写入）"]
    for phrase in avoid:
        lines.append(f"- 避免：「{phrase}」")
    for phrase in prefer:
        lines.append(f"- 偏好：「{phrase}」")
    if len(proposal.avoid_phrases) > 3 or len(proposal.preferred_patterns) > 3:
        lines.append("其余候选见：uv run finch voice propose --json")
    lines += [
        "",
        "确认后请人工改 voice-profile，或继续用 approve-example 积累样例。",
        "uv run finch voice show --json",
    ]
    typer.echo("\n".join(lines))

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


def _practice_profile_path() -> Path:
    return load_settings().paths.practice_profile_path


def _set_confirmed(item_id: str, value: bool) -> None:
    path = _practice_profile_path()
    profile = load_practice_profile(path)
    item = profile.get(item_id)
    if item is None:
        available = ", ".join(i.id for i in profile.items) or "(empty)"
        typer.echo(f"practice item not found: {item_id}. available: {available}")
        raise typer.Exit(code=1)
    item.confirmed = value
    save_practice_profile(profile, path)
    typer.echo(f"{'confirmed' if value else 'revoked'}: {item_id}")


@profile_app.command("show")
def profile_show(
    as_json: bool = typer.Option(False, "--json", help="输出完整 YAML"),
) -> None:
    """列出实践条目，标出 confirmed / 未确认。"""
    profile = load_practice_profile(_practice_profile_path())
    if as_json:
        typer.echo(
            yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        )
        return
    if not profile.items:
        typer.echo(
            "实践画像为空。先运行 `uv run finch profile init`，或 `finch profile add` 手工添加。"
        )
        return
    lines = [f"实践画像（{len(profile.confirmed_items())}/{len(profile.items)} 已确认）", ""]
    for item in profile.items:
        flag = "已确认" if item.confirmed else "未确认"
        lines.append(f"- [{item.id}] {flag} ({item.status.value}) {item.domain}: {item.claim}")
        if item.boundaries:
            lines.append(f"    boundaries: {item.boundaries}")
    lines += ["", "uv run finch profile confirm <id>", "uv run finch profile revoke <id>"]
    typer.echo("\n".join(lines))


@profile_app.command("confirm")
def profile_confirm(item_id: str = typer.Argument(..., help="practice item id")) -> None:
    """确认一条实践（之后才会进入 prompt）。"""
    _set_confirmed(item_id, True)


@profile_app.command("revoke")
def profile_revoke(item_id: str = typer.Argument(..., help="practice item id")) -> None:
    """撤销确认（条目保留但不再进入 prompt）。"""
    _set_confirmed(item_id, False)


@profile_app.command("add")
def profile_add(
    item_id: str = typer.Option(..., "--id", help="slug，如 pharmacy-background"),
    domain: str = typer.Option(..., "--domain", help="领域标签"),
    claim: str = typer.Option(..., "--claim", help="一句话：我真的做过什么"),
    refs: list[str] = typer.Option(  # noqa: B008
        [], "--ref", help="公开 URL（可多次）；缺省则 author_stated"
    ),
    offers: list[str] = typer.Option([], "--offer", help="可贡献形式（可多次）"),  # noqa: B008
    boundaries: str = typer.Option("", "--boundaries", help="明确不能替我说的话"),
) -> None:
    """手工添加一条实践（默认未确认，需再 confirm）。"""
    path = _practice_profile_path()
    profile = load_practice_profile(path)
    if profile.get(item_id) is not None:
        typer.echo(f"practice item already exists: {item_id}")
        raise typer.Exit(code=1)
    status = PracticeEvidenceStatus.SOURCED if refs else PracticeEvidenceStatus.AUTHOR_STATED
    profile.items.append(
        PracticeItem(
            id=item_id,
            domain=domain,
            claim=claim,
            evidence_refs=list(refs),
            status=status,
            can_offer=list(offers),
            boundaries=boundaries,
            confirmed=False,
        )
    )
    save_practice_profile(profile, path)
    typer.echo(f"added (unconfirmed): {item_id} — run `uv run finch profile confirm {item_id}`")


@profile_app.command("init")
def profile_init(
    repos: list[str] = typer.Option(  # noqa: B008
        [], "--repo", help="额外仓库 owner/name（可多次）；默认遍历 finch.yaml repositories"
    ),
) -> None:
    """从你自己仓库的 README 起草未确认的实践条目（只读 gh；只追加新 id，不覆盖已有）。"""
    settings = load_settings()
    path = settings.paths.practice_profile_path
    targets = list(dict.fromkeys([*settings.repositories, *repos]))
    if not targets:
        typer.echo("no repositories configured; pass --repo owner/name")
        raise typer.Exit(code=1)
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    gh = GhClient()
    drafts: list[PracticeItem] = []
    skipped: list[tuple[str, str]] = []
    for repo in targets:
        try:
            readme = gh.readme(repo)
        except GhError as exc:
            skipped.append((repo, f"readme unavailable: {exc}"))
            continue
        items = profile_bootstrap.draft_items_from_readme(runner, repo=repo, readme=readme)
        if not items:
            skipped.append((repo, "no first-hand practice drafted"))
            continue
        drafts.extend(items)
    if not drafts:
        typer.echo("no drafts produced; nothing written.")
        for repo, why in skipped:
            typer.echo(f"  skipped {repo}: {why}")
        raise typer.Exit(code=1)
    profile = load_practice_profile(path)
    merged, added = profile_bootstrap.merge_drafts(profile, drafts)
    save_practice_profile(merged, path)
    lines = [f"drafted {len(added)} new unconfirmed item(s) → {path}"]
    lines += [f"  + {item_id}" for item_id in added]
    lines += [f"  skipped {repo}: {why}" for repo, why in skipped]
    lines += [
        "",
        "无公开资产的经历（药学背景 / 健身 / 阅读 / 出版）请用 `uv run finch profile add` 手工补。",
        "逐条审阅后 `uv run finch profile confirm <id>`；只有已确认条目会进入机会评估与贡献制作。",
    ]
    typer.echo("\n".join(lines))


def _decision_service(ws: Workspace) -> InboxDecisionService:
    return InboxDecisionService(
        jobs=ContentJobRepository(ws),
        drafts=DraftRepository(ws),
        decisions=DecisionRecordRepository(ws),
        publication_intents=PublicationIntentRepository(ws),
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
        typer.echo(f"uv run finch review approve {draft.id}")
        typer.echo(f'uv run finch drafts revise {draft.id} --instruction "..."')
        typer.echo(f"uv run finch review skip {draft.id} --reason ...")


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


@review_app.command("weekly")
def review_weekly(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """周/月复盘：关系北极星 + 可解释推荐反馈调整建议。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peers = PeerRepository(ws).list_all()
    interactions = InteractionRecordRepository(ws).list_all()
    rel = compute_relationship_metrics(
        peers=peers,
        interactions=interactions,
        threads=ConversationThreadRepository(ws).list_all(),
        snapshots=FeedbackSnapshotRepository(ws).list_all(),
        jobs=ContentJobRepository(ws).list_jobs(),
        now=datetime.now(UTC),
    )
    adjustments = explain_recommendation_adjustments(
        RecommendationFeedbackRepository(ws).list_all()
    )
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "metrics": rel.model_dump(mode="json"),
                    "recommendation_adjustments": adjustments,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo(render_relationship_metrics(rel))
    typer.echo("\n## 推荐调整建议（需人工确认）")
    for adj in adjustments:
        typer.echo(f"- signal: {adj['signal']}")
        typer.echo(f"  effect: {adj['effect']}")
        typer.echo(f"  why: {adj['rationale']}")


def _run_discovery(
    settings: Settings,
    *,
    lookback_hours: int | None = None,
    question: str | None = None,
) -> EngagementRunResult:
    """执行统一每日发现（sources → people → shortlist → opportunities）。"""
    from finch.discovery.daily import run_daily_discovery

    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    daily = run_daily_discovery(
        settings, runner=runner, lookback_hours=lookback_hours, question=question
    )
    assert daily.engagement is not None
    return daily.engagement


def _run_daily_full(
    settings: Settings,
    *,
    lookback_hours: int | None = None,
    question: str | None = None,
):
    """Full daily result including shortlist + connection opportunities."""
    from finch.discovery.daily import run_daily_discovery

    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    return run_daily_discovery(
        settings, runner=runner, lookback_hours=lookback_hours, question=question
    )


def _recommendations_payload(recs) -> dict | None:
    """Serialize a DailyRecommendationSet into a JSON-ready dict."""
    if recs is None:
        return None

    def _row(r):
        c = r.candidate
        return {
            "person_id": r.person_id,
            "peer_id": c.peer.id,
            "display_name": c.peer.display_name or c.peer.id,
            "tier": r.tier,
            "rank": r.rank,
            "direction": r.direction,
            "platform": c.platform,
            "score": c.score.total,
            "artifact_ids": list(c.artifact_ids),
            "hit_labels": list(c.hit_labels),
        }

    return {
        "priority": [_row(r) for r in recs.priority],
        "summary": [_row(r) for r in recs.summary],
        "browse": [_row(r) for r in recs.browse],
        "shortfall": dict(recs.shortfall),
    }


def _render_recommendations(recs) -> list[str]:
    """文本模式分层渲染：priority 带证据/命中，browse 每人一行。"""
    if recs is None:
        return []

    def _name(c):
        return c.peer.display_name or c.peer.id

    lines: list[str] = []
    if recs.priority:
        lines.append(f"## 今日重点 ({len(recs.priority)})")
        for r in recs.priority:
            c = r.candidate
            lines.append(
                f"{r.rank + 1}. {_name(c)} ({c.platform}) "
                f"score={c.score.total:.2f} [{r.direction}]"
            )
            lines.append(f"   evidence: {', '.join(c.artifact_ids[:3])}")
            if c.hit_labels:
                lines.append(f"   hit: {', '.join(c.hit_labels[:5])}")
        lines.append("")
    if recs.summary:
        lines.append(f"## 值得浏览 ({len(recs.summary)})")
        for r in recs.summary:
            c = r.candidate
            lines.append(
                f"{r.rank + 1}. {_name(c)} ({c.platform}) "
                f"score={c.score.total:.2f} [{r.direction}]"
            )
            lines.append(f"   evidence: {', '.join(c.artifact_ids[:2])}")
        lines.append("")
    if recs.browse:
        lines.append(f"## 扩展发现 ({len(recs.browse)})")
        for r in recs.browse:
            c = r.candidate
            lines.append(
                f"{r.rank + 1}. {_name(c)} ({c.platform}) score={c.score.total:.2f}"
            )
        lines.append("")
    if recs.shortfall:
        lines.append(f"shortfall: {recs.shortfall}")
    return lines


def _entries_payload(entries: list[RecommendationEntry], shortfall: dict[str, int]) -> dict:
    """把持久化推荐条目还原为与 _recommendations_payload 同构的 JSON dict。"""
    by_tier: dict[str, list] = {"priority": [], "summary": [], "browse": []}
    for e in entries:
        by_tier.setdefault(e.tier, []).append(e.model_dump(mode="json"))
    result: dict = dict(by_tier)
    result["shortfall"] = dict(shortfall)
    return result


def _render_recommendation_entries(
    entries: list[RecommendationEntry], shortfall: dict[str, int]
) -> list[str]:
    """文本模式分层渲染持久化推荐条目（与 _render_recommendations 对齐）。"""
    by_tier: dict[str, list] = {}
    for e in entries:
        by_tier.setdefault(e.tier, []).append(e)

    def _name(e):
        return e.display_name or e.peer_id

    lines: list[str] = []
    for tier, title in (
        ("priority", "今日重点"),
        ("summary", "值得浏览"),
        ("browse", "扩展发现"),
    ):
        rows = by_tier.get(tier, [])
        if not rows:
            continue
        lines.append(f"## {title} ({len(rows)})")
        for e in rows:
            if tier == "priority":
                lines.append(
                    f"{e.rank + 1}. {_name(e)} ({e.platform}) "
                    f"score={e.score:.2f} [{e.direction}]"
                )
                lines.append(f"   evidence: {', '.join(e.artifact_ids[:3])}")
                if e.hit_labels:
                    lines.append(f"   hit: {', '.join(e.hit_labels[:5])}")
            elif tier == "summary":
                lines.append(
                    f"{e.rank + 1}. {_name(e)} ({e.platform}) "
                    f"score={e.score:.2f} [{e.direction}]"
                )
                lines.append(f"   evidence: {', '.join(e.artifact_ids[:2])}")
            else:
                lines.append(
                    f"{e.rank + 1}. {_name(e)} ({e.platform}) score={e.score:.2f}"
                )
        lines.append("")
    if shortfall:
        lines.append(f"shortfall: {shortfall}")
    return lines


def _render_home_entries(
    entries: list[RecommendationEntry], home_ids: list[str]
) -> list[str]:
    """首页 3 重点（D7）：从持久化条目按 home_person_ids 顺序渲染。"""
    by_id = {e.person_id: e for e in entries}
    home = [by_id[pid] for pid in home_ids if pid in by_id]
    if not home:
        return []
    lines = [f"## 今日重点 ({len(home)})"]
    for e in home:
        name = e.display_name or e.peer_id
        lines.append(f"{e.rank + 1}. {name} ({e.platform}) [{e.direction}]")
        if e.artifact_ids:
            lines.append(f"   evidence: {', '.join(e.artifact_ids[:3])}")
    lines.append("")
    return lines


def _render_preferred_opportunity(opp: PreferredOpportunity) -> list[str]:
    """渲染首选机会（0-1）为可读文本（规范 §6.1 六问）；按状态区分标题。"""
    headings = {
        OpportunityStatus.PROPOSED: "## 首选机会",
        OpportunityStatus.SELECTED: "## 已选定机会",
        OpportunityStatus.READY: "## 成果待审阅",
        OpportunityStatus.PARKED: "## 已暂存机会",
        OpportunityStatus.CLOSED: "## 已关闭机会",
    }
    lines = [headings.get(opp.status, "## 首选机会")]
    lines.append(f"机会 ID：{opp.id}")
    lines.append(f"状态：{opp.status.value}")
    source = opp.thread_ref or next(
        (ref.source_ref for ref in opp.evidence_refs if ref.source_ref), ""
    )
    if source:
        lines.append(f"来源：{source}")
    lines.append(f"话题：{opp.topic or '（未给出）'}")
    if opp.entry_kind is not None:
        lines.append(f"入口：{opp.entry_kind.value}")
    if opp.why_me:
        lines.append(f"为什么值得参与：{opp.why_me}")
    if opp.why_continue:
        lines.append(f"对方为什么可能接话：{opp.why_continue}")
    if opp.proposal is not None:
        lines.append(f"最小贡献：{opp.proposal.contribution}")
        lines.append(f"形式：{opp.proposal.form.value}")
        if opp.proposal.expected_output:
            lines.append(f"可见结果：{opp.proposal.expected_output}")
        if opp.proposal.scope:
            lines.append(f"范围：{opp.proposal.scope}")
        if opp.proposal.cost_note:
            lines.append(f"成本与未知：{opp.proposal.cost_note}")
    if opp.previous_opportunity_id:
        lines.append(f"接续自：{opp.previous_opportunity_id}")
    if opp.open_questions:
        lines.append(f"待确认：{'；'.join(opp.open_questions)}")
    lines.append("")
    return lines


def _render_opportunity_assessment_coverage(assessments: list) -> list[str]:
    """说明本轮评估覆盖（跳过 / 评估失败）并附上跳过原因（规范 §5.5）。"""
    if not assessments:
        return []
    skipped = [a for a in assessments if a.outcome == "skipped"]
    failed = [a for a in assessments if a.outcome == "eval_failed"]
    parts: list[str] = []
    if skipped:
        parts.append(f"{len(skipped)} 跳过")
    if failed:
        parts.append(f"{len(failed)} 评估失败")
    if not parts:
        return []
    lines = [f"本次评估 {len(assessments)} 位候选人：{'、'.join(parts)}。"]
    reasons = [a.reason for a in assessments if a.reason and a.outcome != "recommended"]
    if reasons:
        lines.append("为何未首选：" + "；".join(reasons[:3]))
    return lines


def _render_preferred_skips(assessments: list) -> list[str]:
    """有首选时附一句其他候选为何未优先（规范 §5.5）。"""
    reasons = [
        a.reason for a in assessments if a.outcome == "skipped" and a.reason
    ]
    if not reasons:
        return []
    return [f"其他候选未优先：{'；'.join(reasons[:3])}", ""]


def _prepare_new_opportunity(
    settings: Settings,
    ws: Workspace,
    opportunity_id: str,
    *,
    reaction: str | None = None,
) -> PreparedContribution | None:
    """对首选机会（新聚合）执行 select → prepare_contribution → ready（幂等）。

    proposed → select → 生成正文 → 登记 Artifact → mark_ready；parked / closed 不可准备，
    返回 None。已 ready：无 ``reaction`` 直接返回现有成果；有 ``reaction`` 则 ready → selected
    （合法的「调整贡献」转换）后重新生成。
    """
    service = OpportunityService(
        PreferredOpportunityRepository(ws), artifacts=PreferredArtifactRepository(ws)
    )
    opp = service.get(opportunity_id)
    if opp is None:
        return None
    if opp.status in (OpportunityStatus.PARKED, OpportunityStatus.CLOSED):
        return None
    if opp.status == OpportunityStatus.READY:
        if reaction is None:
            return _prepared_contribution_for(ws, opp)
        opp = service.select(opportunity_id)
    elif opp.status == OpportunityStatus.PROPOSED:
        opp = service.select(opportunity_id)
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    return prepare_contribution(
        opportunity=opp,
        runner=runner,
        service=service,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        confirmed_jobs=ContentJobRepository(ws).list_jobs(),
        practice_profile=load_practice_profile(settings.paths.practice_profile_path),
        reaction=reaction,
    )


def _prepared_contribution_for(
    ws: Workspace, opp: PreferredOpportunity
) -> PreparedContribution:
    """从已 ready 机会重建可直接审阅的成果（正文 + 来源 + 执行状态）。"""
    art_repo = PreferredArtifactRepository(ws)
    artifacts: list[PreparedArtifact] = []
    # 只呈现当前形式 + 反应对应的成果，避免 regenerate 后旧澄清问题一并出现
    current_id = artifact_id_for(opp, effective_form(opp))
    refs = [current_id] if current_id in opp.artifact_refs else list(opp.artifact_refs)
    for ref in refs:
        art = art_repo.get(opp.id, ref)
        if art is None:
            continue
        artifacts.append(
            PreparedArtifact(
                id=art.id,
                kind=art.kind,
                body=art_repo.read_content(opp.id, ref) or "",
                source_refs=list(art.source_refs),
                execution_status=art.execution_status,
            )
        )
    latest = opp.reactions[-1] if opp.reactions else None
    return PreparedContribution(
        opportunity=opp,
        artifacts=artifacts,
        reaction=latest,
        form_forced=latest is None,
    )


def _render_prepared_contribution(pc: PreparedContribution) -> list[str]:
    """渲染 prepare 结果：机会摘要 + 反应状态 + 可审阅正文。"""
    lines = _render_preferred_opportunity(pc.opportunity)
    if pc.reaction is not None:
        lines.append(f"你的反应（第 {pc.reaction.seq} 条）：{pc.reaction.text}")
    else:
        lines.append("无反应：本次只准备澄清问题")
    lines.append("")
    for a in pc.artifacts:
        lines.append(f"成果（{a.kind.value}）：")
        lines.append(a.body)
        lines.append("")
    return lines


def _prepared_payload(pc: PreparedContribution) -> dict:
    """把 prepare 结果序列化为 JSON（含正文，直接可审阅）。"""
    return {
        "opportunity_id": pc.opportunity.id,
        "status": pc.opportunity.status.value,
        "reaction": (
            {"seq": pc.reaction.seq, "text": pc.reaction.text} if pc.reaction else None
        ),
        "form_forced": pc.form_forced,
        "artifacts": [
            {
                "id": a.id,
                "kind": a.kind.value,
                "body": a.body,
                "source_refs": list(a.source_refs),
                "execution_status": a.execution_status.value,
            }
            for a in pc.artifacts
        ],
    }


def _persist_discovery(
    ws: Workspace,
    result: EngagementRunResult,
    *,
    snapshot_id: str | None = None,
) -> DiscoverySnapshot | None:
    """把发现结果的同行落库；可选写入 DiscoverySnapshot。"""
    peers = PeerRepository(ws)
    peer_svc = PeerService()
    for ranked in result.peers:
        merged = peer_svc.merge_discovered(peers.get(ranked.profile.id), ranked.profile)
        peers.upsert(merged)

    if result.status == "failed":
        return DiscoverySnapshotRepository(ws).latest()

    # F1：保留 run_daily_discovery 已写入的完整 50 人推荐与首页投影，避免覆盖。
    latest = DiscoverySnapshotRepository(ws).latest()
    recommendations = latest.recommendations if latest is not None else []
    recommendation_shortfall = (
        latest.recommendation_shortfall if latest is not None else {}
    )
    home_person_ids = latest.home_person_ids if latest is not None else []
    preferred_opportunity_id = (
        latest.preferred_opportunity_id if latest is not None else ""
    )
    opportunity_assessments = (
        list(latest.opportunity_assessments) if latest is not None else []
    )
    plan_id = latest.plan_id if latest is not None else ""
    plan_summary = latest.plan_summary if latest is not None else {}
    ranking_version = latest.ranking_version if latest is not None else "1"

    snap_id = snapshot_id or result.run_id
    snapshot = DiscoverySnapshot(
        id=snap_id,
        created_at=datetime.now(UTC),
        context_fingerprint=result.context_fingerprint,
        source_coverage={
            "posts_found": result.posts_found,
            "status": result.status,
            **(result.source_coverage or {}),
        },
        failures=(latest.failures if latest is not None else []),
        ranked_opportunity_ids=(
            latest.ranked_opportunity_ids if latest is not None else []
        ),
        ranking_version=ranking_version,
        plan_id=plan_id,
        plan_summary=plan_summary,
        recommendations=recommendations,
        recommendation_shortfall=recommendation_shortfall,
        home_person_ids=home_person_ids,
        preferred_opportunity_id=preferred_opportunity_id,
        opportunity_assessments=opportunity_assessments,
    )
    DiscoverySnapshotRepository(ws).upsert(snapshot)
    return snapshot


def _record_presentations(
    ws: Workspace,
    snapshot_id: str,
    opportunity_ids: list[str],
) -> None:
    repo = PresentationRecordRepository(ws)
    now = datetime.now(UTC)
    for oid in opportunity_ids:
        rec = PresentationRecord(
            id=f"{snapshot_id}:{oid}",
            snapshot_id=snapshot_id,
            opportunity_id=oid,
            presented_at=now,
            presentation_semantics_version="2",
        )
        repo.upsert(rec)


def _record_person_presentations(
    ws: Workspace,
    snapshot_id: str,
    entries: list[RecommendationEntry],
    *,
    surface: str,
) -> None:
    """D10：把实际展示到首页/浏览的人物写入 person 级 presented（驱动冷却）。"""
    from finch.peers.presentation import PersonPresentationRepository

    repo = PersonPresentationRepository(ws)
    for e in entries:
        repo.record_shown(
            person_id=e.person_id,
            peer_id=e.peer_id,
            platform=e.platform,
            surface=surface,
            snapshot_id=snapshot_id,
        )


def _connect_daily_refresh_status(
    *,
    daily: DailyDiscoveryResult | None,
    snapshot: DiscoverySnapshot | None,
    stale: bool,
) -> str:
    """JSON 新鲜度词。无快照是 ``missing``；空刷新是 ``empty``，不叫 ``refreshed``。"""
    if daily is not None:
        status = daily.engagement.status if daily.engagement is not None else "failed"
        if status == "failed":
            return "failed"
        if status == "empty":
            return "empty"
        return "refreshed"
    if snapshot is None:
        return "missing"
    return "stale" if stale else "fresh"


def _snapshot_fresh(snapshot: DiscoverySnapshot | None, ttl_hours: int) -> bool:
    if snapshot is None:
        return False
    age = datetime.now(UTC) - snapshot.created_at
    return age <= timedelta(hours=ttl_hours)


def _load_today_payload(
    ws: Workspace,
    settings: Settings,
    *,
    limit: int,
) -> tuple[TodayFocus, DiscoverySnapshot | None]:
    """纯读取今日投影（无网络/LLM）。"""
    now = datetime.now(UTC)
    snapshot = DiscoverySnapshotRepository(ws).latest()
    threads = ConversationThreadRepository(ws).list_all()
    needs_follow_up = [
        t for t in threads if ConversationService().needs_follow_up(t, now=now)
    ]
    idea_candidates = [
        j for j in ContentJobRepository(ws).list_jobs()
        if j.status == ContentJobStatus.PROPOSED
    ]
    focus = build_today_focus(
        threads=needs_follow_up,
        ideas=idea_candidates,
        now=now,
    )
    return focus, snapshot


def _render_daily(focus: TodayFocus) -> str:
    def _section(title: str, body: str) -> str:
        return f"## {title}\n{body}"

    conv = focus["conversations"]
    ideas = focus["ideas"]

    def _with_more(body: str, shown: int, total: int) -> str:
        if shown and total > shown:
            return f"{body}\n… 还有 {total - shown} 个"
        return body

    conv_body = (
        _render_thread_cards(conv["items"], limit=len(conv["items"]) or 1)
        if conv["items"]
        else "- (none)"
    )
    idea_body = (
        "\n\n".join(_render_idea_card(j) for j in ideas["items"])
        if ideas["items"]
        else "- (none)"
    )
    return "\n\n".join([
        _section(
            "需要继续的对话",
            _with_more(conv_body, len(conv["items"]), conv["total"]),
        ),
        _section(
            "可分享的素材 / 观点候选",
            _with_more(idea_body, len(ideas["items"]), ideas["total"]),
        ),
    ])
@connect_app.command("today")
def connect_today(
    limit: int = typer.Option(10, "--limit", help="展示机会数（目标 8–12）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """已由 connect daily 首页取代：委托同一快照投影（纯读，不调用网络/LLM）。"""
    connect_daily(refresh=False, limit=limit, view="home", as_json=as_json)


@connect_app.command("daily")
def connect_daily(
    refresh: bool = typer.Option(False, "--refresh", help="先刷新再读取"),
    limit: int = typer.Option(50, "--limit", help="展示机会数"),
    view: str = typer.Option("home", "--view", help="home|browse"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    lookback: str | None = typer.Option(None, "--lookback", help="时间窗（24h/30d/720h）"),
    question: str | None = typer.Option(None, "--question", help="本轮问题/意图"),
) -> None:
    """连接主循环入口：首页 3 重点（默认）+ 50 人分层浏览（--view browse）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    snapshot = DiscoverySnapshotRepository(ws).latest()
    # D1：默认只读；仅在显式 --refresh 时刷新，不因过期隐式抓取。
    need_refresh = refresh
    daily = None
    if need_refresh:
        daily = _run_daily_full(
            settings, lookback_hours=_lookback_hours(lookback), question=question
        )
        result = daily.engagement
        assert result is not None
        # 失败或空结果不覆盖上一份可用快照；状态由 refresh_status 说明。
        keep_previous = snapshot is not None and result.status in {"failed", "empty"}
        if not keep_previous:
            snapshot = _persist_discovery(ws, result)

    focus, snapshot = _load_today_payload(ws, settings, limit=limit)
    stale = snapshot is not None and not _snapshot_fresh(
        snapshot, settings.engagement.snapshot_ttl_hours
    )

    # F1：非刷新读取时从快照重放完整 50 人推荐。
    rec_entries = snapshot.recommendations if snapshot is not None else []
    rec_shortfall = snapshot.recommendation_shortfall if snapshot is not None else {}
    home_ids = snapshot.home_person_ids if snapshot is not None else []

    preferred: PreferredOpportunity | None = None
    assessments: list = []
    if daily is not None:
        preferred = daily.preferred_opportunity
        assessments = list(daily.opportunity_assessments)
    elif snapshot is not None:
        assessments = list(snapshot.opportunity_assessments)
        if snapshot.preferred_opportunity_id:
            preferred = PreferredOpportunityRepository(ws).get(
                snapshot.preferred_opportunity_id
            )

    refresh_status = _connect_daily_refresh_status(
        daily=daily, snapshot=snapshot, stale=stale
    )

    if as_json:
        typer.echo(json.dumps({
            "schema_version": 2,
            "snapshot_created_at": (
                snapshot.created_at.isoformat() if snapshot else None
            ),
            "stale": stale,
            "refresh_status": refresh_status,
            "snapshot_id": snapshot.id if snapshot else None,
            "refreshed": need_refresh,
            "home_person_ids": home_ids,
            "preferred_opportunity": (
                preferred.model_dump(mode="json") if preferred else None
            ),
            "opportunity_assessments": [
                {
                    "person_id": a.person_id,
                    "outcome": a.outcome,
                    "reason": a.reason,
                    "opportunity_id": (
                        a.opportunity.id
                        if getattr(a, "opportunity", None) is not None
                        else getattr(a, "opportunity_id", None)
                    ),
                    "fingerprint": getattr(a, "fingerprint", "") or "",
                }
                for a in assessments
            ],
            "recommendations": (
                _recommendations_payload(daily.recommendations)
                if (daily is not None and daily.recommendations is not None)
                else _entries_payload(rec_entries, rec_shortfall)
            ),
            "conversations_needing_follow_up": [
                t.model_dump(mode="json") for t in focus["conversations"]["items"]
            ],
            "idea_candidates": [
                j.model_dump(mode="json") for j in focus["ideas"]["items"]
            ],
        }, ensure_ascii=False, indent=2))
        return
    if snapshot is None:
        typer.echo("no discovery snapshot; run: uv run finch connect daily --refresh")
        return
    if stale:
        typer.echo(
            f"(snapshot from {snapshot.created_at:%Y-%m-%d %H:%M} UTC; "
            f"run with --refresh for fresh results)"
        )
    if view == "browse":
        rec_lines = (
            _render_recommendations(daily.recommendations)
            if (daily is not None and daily.recommendations is not None)
            else _render_recommendation_entries(rec_entries, rec_shortfall)
        )
        shown_entries = rec_entries
    else:
        if preferred is not None:
            rec_lines = _render_preferred_opportunity(preferred)
            rec_lines.extend(_render_preferred_skips(assessments))
        else:
            rec_lines = [
                "今天没有值得优先投入的讨论（--view browse 可浏览 50 人列表）。",
            ]
            rec_lines.extend(_render_opportunity_assessment_coverage(assessments))
            rec_lines.append("")
        shown_entries = []
    # D10：仅文本前台实际输出时记录曝光；首页只记实际展示的人物，浏览记全部展开条目。
    _record_person_presentations(
        ws,
        snapshot.id,
        shown_entries,
        surface="browse" if view == "browse" else "home",
    )
    # 首页实际展示首选机会时写入 PresentationRecord（漏斗「呈现首选」口径）。
    if view != "browse" and preferred is not None:
        _record_presentations(ws, snapshot.id, [preferred.id])
    if rec_lines:
        typer.echo("\n".join(rec_lines))
    else:
        typer.echo("(run with --refresh for daily recommendations)")
        typer.echo("")
    typer.echo(_render_daily(focus))


@connect_app.command("person")
def connect_person(
    person_id: str = typer.Argument(..., help="person_id 或 peer_id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看一个人的完整证据与档案（只读，不生成互动准备）。"""
    from finch.peers.evidence_repo import CreatorEvidenceRepository
    from finch.peers.person_service import PersonRepository
    from finch.storage.repositories import PeerRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peer_repo = PeerRepository(ws)
    person_repo = PersonRepository(ws)
    evidence_repo = CreatorEvidenceRepository(ws)

    peer = peer_repo.get(person_id)
    person = person_repo.get(person_id)
    if peer is None and person is not None and person.identities:
        peer = peer_repo.get(person.identities[0].peer_id)
    if person is None and peer is not None and peer.person_id:
        person = person_repo.get(peer.person_id)
    if peer is None:
        typer.echo(f"person/peer not found: {person_id}")
        raise typer.Exit(code=1)

    resolved = person.person_id if person else (peer.person_id or "")
    evs = evidence_repo.list_for_person(resolved) if resolved else []

    if as_json:
        typer.echo(json.dumps({
            "person_id": resolved,
            "peer": peer.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in evs],
        }, ensure_ascii=False, indent=2))
        return
    # D10：明确查看某人是 selected 事件（显式动作，与被动曝光分开，不影响冷却）。
    if resolved:
        from finch.peers.presentation import PersonPresentationRepository

        platform = (
            peer.platform_identities[0].platform if peer.platform_identities else ""
        )
        PersonPresentationRepository(ws).record_selected(
            person_id=resolved,
            peer_id=peer.id,
            platform=platform,
            surface="person",
        )
    typer.echo(_render_peer_detail(peer))
    if evs:
        typer.echo("")
        typer.echo(f"## 证据 ({len(evs)})")
        for e in evs:
            typer.echo(f"- [{e.kind.value}] {e.claim} (confidence={e.confidence:.2f})")


@connect_app.command("record-presented")
def connect_record_presented(
    snapshot_id: str = typer.Option(..., "--snapshot-id", help="快照 id"),
    surface: str = typer.Option("home", "--surface", help="home|browse"),
    person_ids: list[str] = typer.Option([], "--person-id", help="实际展示的 person_id（可重复）"),
    opportunity_ids: list[str] = typer.Option(
        [], "--opportunity-id", help="实际展示的 opportunity_id（可重复）"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """显式记录「已输出到答复」的 person/opportunity（JSON 驱动流；文本路径已自动记录）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    if surface not in ("home", "browse"):
        if as_json:
            typer.echo(
                json.dumps(
                    {"ok": False, "error": "surface must be home|browse"},
                    ensure_ascii=False,
                )
            )
        else:
            typer.echo("surface must be home|browse")
        raise typer.Exit(code=1)
    snapshot = DiscoverySnapshotRepository(ws).latest()
    if snapshot is None or snapshot.id != snapshot_id:
        if as_json:
            typer.echo(
                json.dumps(
                    {"ok": False, "error": "snapshot not found or not latest"},
                    ensure_ascii=False,
                )
            )
        else:
            typer.echo("snapshot not found or not latest")
        raise typer.Exit(code=1)
    known_persons = {e.person_id for e in snapshot.recommendations}
    known_opportunities = set(snapshot.ranked_opportunity_ids) | set(
        snapshot.selected_opportunity_ids
    )
    unknown_persons = [pid for pid in person_ids if pid not in known_persons]
    unknown_opportunities = [oid for oid in opportunity_ids if oid not in known_opportunities]
    if unknown_persons or unknown_opportunities:
        payload = {
            "ok": False,
            "error": "ids not in snapshot",
            "unknown_persons": unknown_persons,
            "unknown_opportunities": unknown_opportunities,
        }
        if as_json:
            typer.echo(json.dumps(payload, ensure_ascii=False))
        else:
            typer.echo(
                "ids not in snapshot: "
                f"persons={unknown_persons} opportunities={unknown_opportunities}"
            )
        raise typer.Exit(code=1)
    wanted = set(person_ids)
    entries = [e for e in snapshot.recommendations if e.person_id in wanted]
    _record_person_presentations(ws, snapshot_id, entries, surface=surface)
    _record_presentations(ws, snapshot_id, opportunity_ids)
    payload = {"ok": True, "persons": len(entries), "opportunities": len(opportunity_ids)}
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(
            f"recorded {payload['persons']} persons, {payload['opportunities']} opportunities"
        )


@connect_app.command("prepare")
def connect_prepare(
    opportunity_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--opportunity",
            help="选中的首选机会 ID（可重复；必须至少指定一个）",
        ),
    ] = None,
    reaction: str | None = typer.Option(
        None,
        "--reaction",
        help="用户对这条机会的原话（可选；不回答请不传。无反应时只准备澄清问题）",
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """为选中的首选机会制作贡献（select → 生成正文 → 登记 Artifact → ready）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ids = list(opportunity_ids or [])
    if not ids:
        typer.echo(
            "selection required: pass one or more --opportunity <id> "
            "(browse with connect daily; do not prepare the whole list)"
        )
        raise typer.Exit(code=1)
    if reaction is not None and not reaction.strip():
        typer.echo("reaction must not be blank; omit --reaction if the user did not answer")
        raise typer.Exit(code=1)
    if reaction is not None and len(ids) > 1:
        typer.echo("--reaction applies to exactly one --opportunity")
        raise typer.Exit(code=1)
    cap = settings.discovery.daily_people.deep_prepare_limit
    over_cap = len(ids) > cap
    selected = ids[:cap]
    prepared: list[PreparedContribution] = []
    misses: list[str] = []
    for oid in selected:
        pc = _prepare_new_opportunity(settings, ws, oid, reaction=reaction)
        if pc is None:
            misses.append(oid)
        else:
            prepared.append(pc)
    if as_json:
        payload = {
            "opportunities": [_prepared_payload(pc) for pc in prepared],
            "misses": misses,
            "over_cap": over_cap,
            "cap": cap,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        if over_cap or misses or not prepared:
            raise typer.Exit(code=1)
        return
    for pc in prepared:
        typer.echo("\n".join(_render_prepared_contribution(pc)))
    if misses:
        typer.echo("could not prepare: " + ", ".join(misses))
    if over_cap:
        typer.echo(
            f"batch limit is {cap}; prepared first {cap} of {len(ids)} selected"
        )
    if over_cap or misses or not prepared:
        if not prepared:
            typer.echo("no opportunities prepared")
        raise typer.Exit(code=1)


@connect_app.command("feedback")
def connect_feedback(
    path: str | None = typer.Option(None, "--file", help="feedback.json（批量）"),
    snapshot: str = typer.Option("", "--snapshot", help="快照 ID（轻量内联）"),
    opportunity: str = typer.Option("", "--opportunity", help="机会 ID（轻量内联）"),
    dimension: str = typer.Option("", "--dimension", help="interest|action"),
    value: str = typer.Option("", "--value", help="兴趣/行动值"),
    reason: str = typer.Option("", "--reason", help="可选原因"),
) -> None:
    """校验并记录推荐反馈（兴趣 / 行动维度）。

    轻量内联：--opportunity + --dimension + --value（如 --value no_time_today 表示今天没时间，
    属瞬态跳过，不映射为长期排斥）；或用 --file 批量。
    """
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    inline_given = bool(opportunity or dimension or value)
    if path and inline_given:
        typer.echo("pass either --file OR --opportunity/--dimension/--value, not both")
        raise typer.Exit(code=1)
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
    elif opportunity and dimension and value:
        if not snapshot:
            typer.echo("inline feedback requires --snapshot <id>")
            raise typer.Exit(code=1)
        items = [
            {
                "snapshot_id": snapshot,
                "opportunity_id": opportunity,
                "dimension": dimension,
                "value": value,
                "reason": reason,
            }
        ]
    else:
        typer.echo("pass --file feedback.json OR --opportunity + --dimension + --value")
        raise typer.Exit(code=1)
    repo = RecommendationFeedbackRepository(ws)
    interest_values = {v.value for v in InterestFeedbackValue}
    action_values = {v.value for v in ActionFeedbackValue}
    saved = 0
    for item in items:
        if "created_at" not in item:
            item = {**item, "created_at": datetime.now(UTC).isoformat()}
        if "id" not in item:
            # 稳定幂等键：同一 (opportunity, dimension, value) 重复记录覆盖而非追加。
            digest = hashlib.sha256(
                f"{item.get('opportunity_id')}:{item.get('dimension')}:{item.get('value')}".encode()
            ).hexdigest()[:12]
            item = {**item, "id": f"rfb_{digest}"}
        try:
            feedback = RecommendationFeedback.model_validate(item)
        except ValidationError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        if feedback.dimension == "interest" and feedback.value not in interest_values:
            typer.echo(f"invalid interest value: {feedback.value}")
            raise typer.Exit(code=1)
        if feedback.dimension == "action" and feedback.value not in action_values:
            typer.echo(f"invalid action value: {feedback.value}")
            raise typer.Exit(code=1)
        repo.upsert(feedback)
        saved += 1
    typer.echo(f"saved {saved} recommendation feedback record(s)")


@connect_app.command("assess")
def connect_assess(
    url: str = typer.Option(..., "--url", help="指定讨论 URL"),
    question: str = typer.Option("", "--question", help="用户当前问题/意图"),
    person: str = typer.Option("", "--person", help="已知 person_id（可选）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """入口 2：看看这个帖子，我能补充什么（webfetch → 同一机会判断）。"""
    from finch.opportunities.from_url import assess_from_url

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    user_practices = render_user_practices(
        load_practice_profile(settings.paths.practice_profile_path)
    )
    result = assess_from_url(
        url=url,
        runner=runner,
        service=OpportunityService(PreferredOpportunityRepository(ws)),
        user_context=question,
        user_practices=user_practices,
        person_ref=person or None,
    )
    if as_json:
        typer.echo(json.dumps({
            "outcome": result.outcome,
            "reason": result.reason,
            "url": result.url,
            "opportunity": (
                result.opportunity.model_dump(mode="json")
                if result.opportunity is not None
                else None
            ),
        }, ensure_ascii=False, indent=2))
        if result.opportunity is None:
            raise typer.Exit(code=1)
        return
    if result.opportunity is None:
        typer.echo(f"未形成机会（{result.outcome}）：{result.reason or '无最小贡献'}")
        raise typer.Exit(code=1)
    typer.echo("\n".join(_render_preferred_opportunity(result.opportunity)))


@connect_app.command("artifact-status")
def connect_artifact_status(
    opportunity: str = typer.Option(..., "--opportunity", help="机会 ID"),
    artifact: str = typer.Option(..., "--artifact", help="成果 ID"),
    execution: str = typer.Option(
        ...,
        "--execution",
        help="ran_ok|ran_failed|unclear|not_run|n/a",
    ),
    note: str = typer.Option("", "--note", help="观察/限制说明"),
    real_material: bool = typer.Option(
        False, "--real-material", help="声明材料来自真实经历（USER_ATTESTED）"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """回填演示/成果执行事实（唯一可将 not_run 改为 ran_* 的路径）。"""
    from finch.opportunities.models import ExecutionStatus, MaterialOrigin

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        status = ExecutionStatus(execution)
    except ValueError as exc:
        typer.echo(f"invalid --execution: {execution}")
        raise typer.Exit(code=1) from exc
    service = OpportunityService(
        PreferredOpportunityRepository(ws),
        artifacts=PreferredArtifactRepository(ws),
    )
    try:
        art = service.update_artifact(
            opportunity,
            artifact,
            execution_status=status,
            material_origin=MaterialOrigin.REAL if real_material else None,
            author_note=note or None,
        )
    except KeyError as exc:
        typer.echo(f"not found: {exc}")
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps(art.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"updated {art.id}: execution={art.execution_status.value} "
        f"origin={art.material_origin.value}"
    )


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
    typer.echo(_render_peer_cards(peers))


@people_app.command("shortlist")
def people_shortlist(
    today: bool = typer.Option(
        True, "--today/--all", help="--today 只列需回应的承诺；--all 列全部线索"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """今日承诺面：需回应/兑现的真实对话线索（关系域投影，非发现推荐、不受冷却限制）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    threads = ConversationThreadRepository(ws).list_all()
    if today:
        now = datetime.now(UTC)
        threads = [
            t for t in threads if ConversationService().needs_follow_up(t, now=now)
        ]
    if as_json:
        typer.echo(json.dumps(
            [t.model_dump(mode="json") for t in threads], ensure_ascii=False, indent=2
        ))
        return
    if not threads:
        typer.echo("no commitments needing follow-up" if today else "no conversations")
        return
    typer.echo(_render_thread_cards(threads, needs_follow_up=today))


@connections_app.command("today")
def connections_today(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """今日承诺面：需回应/兑现的真实对话线索（委托 people shortlist --today）。"""
    people_shortlist(today=True, as_json=as_json)


@connections_app.command("record")
def connections_record(
    person: str = typer.Option(..., "--person", help="person_id 或 peer_id"),
    url: str = typer.Option("", "--url", help="互动链接（可选）"),
    body: str = typer.Option("", "--body", help="已发送正文摘要"),
    platform: str = typer.Option("x", "--platform", help="平台"),
    direction: str = typer.Option("outbound", "--direction", help="outbound|inbound"),
    opportunity: str = typer.Option("", "--opportunity", help="关联的首选机会 ID（可选）"),
) -> None:
    """用户亲自发布后登记互动（MVP 不验证远端是否真正发布）。"""
    from finch.connections.service import apply_stage_upgrade, review_relationship
    from finch.engagement.models import InteractionRecord, VerificationStatus
    from finch.peers.person_service import PersonRepository, PersonService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peer_repo = PeerRepository(ws)
    peer = peer_repo.get(person)
    person_id = person
    if peer is None:
        # try as person_id → first linked peer
        prow = PersonRepository(ws).get(person)
        if prow and prow.identities:
            peer = peer_repo.get(prow.identities[0].peer_id)
            person_id = prow.person_id
    if peer is None:
        typer.echo(f"person/peer not found: {person}")
        raise typer.Exit(code=1)

    person_svc = PersonService(PersonRepository(ws))
    ensured = person_svc.ensure_from_peer(peer)
    person_id = ensured.person_id
    if peer.person_id != person_id:
        peer = peer.model_copy(update={"person_id": person_id})

    record_id = f"rec_{hashlib.sha256(f'{peer.id}:{url}:{body}'.encode()).hexdigest()[:12]}"
    now = datetime.now(UTC)
    record = InteractionRecord(
        id=record_id,
        peer_id=peer.id,
        platform=platform,
        source_url=url or f"manual:{peer.id}",
        published_body=body,
        body=body,
        occurred_at=now,
        observed_at=now,
        direction=cast(Literal["outbound", "inbound", "unknown"], direction),
        verification_status=VerificationStatus.USER_ATTESTED,
        provenance="connections.record",
        opportunity_id=opportunity or None,
    )
    InteractionRecordRepository(ws).upsert(record)

    prior = InteractionRecordRepository(ws).list_by_peer(peer.id)
    review = review_relationship(
        peer,
        person_id=person_id,
        bidirectional_exchanges=len(prior),
        natural_next_reason="user recorded a public interaction",
    )
    updated = apply_stage_upgrade(peer, review)
    peer_repo.upsert(updated)
    typer.echo(f"recorded {record.id}; stage → {updated.relationship_stage.value}")


@connections_app.command("follow-up")
def connections_follow_up(
    opportunity: str = typer.Option(..., "--opportunity", help="前次机会 ID"),
    reply_body: str = typer.Option("", "--reply-body", help="对方回应正文"),
    reply_url: str = typer.Option("", "--reply-url", help="对方回应链接"),
    person: str = typer.Option(
        "", "--person", help="person_id 或 peer_id（默认真机会 person_ref）"
    ),
    platform: str = typer.Option("x", "--platform", help="平台"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """获知真实回应后提出接续建议（登记 inbound + 可选新 proposed 机会）。"""
    from finch.opportunities.follow_up import follow_up_opportunity
    from finch.peers.person_service import PersonRepository
    from finch.storage.repositories import FeedbackSnapshotRepository

    if not reply_body.strip() and not reply_url.strip():
        typer.echo("pass --reply-body and/or --reply-url")
        raise typer.Exit(code=1)
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    opp_repo = PreferredOpportunityRepository(ws)
    previous = opp_repo.get(opportunity)
    if previous is None:
        typer.echo(f"opportunity not found: {opportunity}")
        raise typer.Exit(code=1)

    peer_id = ""
    if person:
        peer = PeerRepository(ws).get(person)
        if peer is None:
            prow = PersonRepository(ws).get(person)
            if prow and prow.identities:
                peer = PeerRepository(ws).get(prow.identities[0].peer_id)
        if peer is None:
            typer.echo(f"person/peer not found: {person}")
            raise typer.Exit(code=1)
        peer_id = peer.id
    elif previous.person_ref:
        # person_ref 可能是 person_id；尝试解析到 peer。
        prow = PersonRepository(ws).get(previous.person_ref)
        if prow and prow.identities:
            peer_id = prow.identities[0].peer_id
        else:
            peer_id = previous.person_ref
    else:
        typer.echo("pass --person (opportunity has no person_ref)")
        raise typer.Exit(code=1)

    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    result = follow_up_opportunity(
        previous=previous,
        reply_body=reply_body,
        reply_url=reply_url,
        peer_id=peer_id,
        platform=platform,
        runner=runner,
        service=OpportunityService(opp_repo),
        interactions=InteractionRecordRepository(ws),
        feedbacks=FeedbackSnapshotRepository(ws),
    )
    if as_json:
        typer.echo(json.dumps({
            "interaction_id": result.interaction.id,
            "meaningful": result.meaningful,
            "skip_reason": result.skip_reason,
            "reused_interaction": result.reused_interaction,
            "opportunity": (
                result.opportunity.model_dump(mode="json")
                if result.opportunity is not None
                else None
            ),
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"recorded {result.interaction.id}"
        f"{' (reused)' if result.reused_interaction else ''}; "
        f"meaningful={result.meaningful}"
    )
    if result.opportunity is not None:
        typer.echo("\n".join(_render_preferred_opportunity(result.opportunity)))
    else:
        typer.echo(result.skip_reason or "暂无接续建议")


@collisions_app.command("generate")
def collisions_generate(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从当前 shortlist / 证据生成一张 CollisionCard（每周深度一张）。"""
    from finch.collisions.service import CollisionService
    from finch.discovery.daily import build_shortlist_candidates
    from finch.peers.shortlist import select_daily_shortlist

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    candidates, recent = build_shortlist_candidates(ws)
    items = select_daily_shortlist(candidates, recent_platforms=recent)
    result = CollisionService(ws, runner=runner).generate_from_shortlist(
        items,
        your_domains=list(settings.interests.long_term_interests),
    )
    if result.saved is None:
        typer.echo(result.skipped_reason or "; ".join(result.failures) or "no collision")
        raise typer.Exit(code=1)
    card = result.saved
    if as_json:
        typer.echo(json.dumps(card.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(f"saved {card.collision_id}")
    typer.echo(f"  {card.their_domain} × {card.your_domain}")
    typer.echo(f"  question: {card.shared_question}")
    typer.echo(f"  hypothesis: {card.falsifiable_hypothesis}")


@collisions_app.command("weekly")
def collisions_weekly(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """本周只深度处理一个最值得验证的碰撞。"""
    from finch.collisions.models import pick_weekly_collision
    from finch.collisions.repository import CollisionRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    cards = CollisionRepository(ws).list_all()
    picked = pick_weekly_collision(cards)
    if picked is None:
        typer.echo("no collisions")
        return
    if as_json:
        typer.echo(json.dumps(picked.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(f"collision: {picked.collision_id}")
    typer.echo(f"  {picked.their_domain} × {picked.your_domain}")
    typer.echo(f"  question: {picked.shared_question}")
    typer.echo(f"  hypothesis: {picked.falsifiable_hypothesis}")


@experiments_app.command("start")
def experiments_start(
    collision_id: str = typer.Argument(..., help="collision id"),
    action: str = typer.Option(..., "--action", help="最小行动"),
    observe: str = typer.Option(..., "--observe", help="观察计划"),
    stop: str = typer.Option(..., "--stop", help="停止条件"),
) -> None:
    """从 CollisionCard 启动一周内小实验。"""
    from finch.collisions.models import start_experiment
    from finch.collisions.repository import CollisionRepository, ExperimentRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    card = CollisionRepository(ws).get(collision_id)
    if card is None:
        typer.echo(f"collision not found: {collision_id}")
        raise typer.Exit(code=1)
    exp = start_experiment(
        card,
        minimal_action=action,
        observation_plan=observe,
        stop_condition=stop,
    )
    ExperimentRepository(ws).save(exp)
    typer.echo(f"started {exp.experiment_id} due {exp.due_at}")


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
    typer.echo(_render_peer_detail(peer))


@peers_app.command("get")
def peers_get(peer_id: str = typer.Argument(..., help="peer id")) -> None:
    """Agent 用确定性读取：等同 peers show --json。"""
    peers_show(peer_id, as_json=True)


@inspirations_app.command("save")
def inspirations_save(
    text: str = typer.Option(..., "--text", help="想保留的启发（用户原话）"),
    source: Annotated[
        list[str] | None, typer.Option("--source", help="来源 URL/ID（可重复）")
    ] = None,
    origin: str = typer.Option(
        "user_input",
        "--origin",
        help="observation|conversation|practice|simulation|user_input",
    ),
    person: Annotated[
        list[str] | None, typer.Option("--person", help="关联 person_id（可重复）")
    ] = None,
    conversation: str | None = typer.Option(
        None, "--conversation", help="关联 conversation_id"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """保存一条启发（用户明确「记下来」；相同 text 幂等）。"""
    from finch.inspirations.models import InspirationOrigin
    from finch.inspirations.service import InspirationService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        origin_value = InspirationOrigin(origin)
    except ValueError:
        valid = ", ".join(o.value for o in InspirationOrigin)
        typer.echo(f"invalid --origin: {origin} (use one of {valid})")
        raise typer.Exit(code=1) from None
    insp = InspirationService(ws).save(
        text=text,
        origin=origin_value,
        source_refs=source or [],
        person_ids=person or [],
        conversation_id=conversation,
    )
    if as_json:
        typer.echo(json.dumps(insp.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"saved {insp.id}")


@inspirations_app.command("list")
def inspirations_list(
    all_rows: bool = typer.Option(False, "--all", help="含已归档"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """列出灵感笔记（默认不含已归档）。"""
    from finch.inspirations.service import InspirationService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    rows = InspirationService(ws).list(include_archived=all_rows)
    if as_json:
        typer.echo(
            json.dumps([r.model_dump(mode="json") for r in rows], ensure_ascii=False, indent=2)
        )
        return
    if not rows:
        typer.echo("(no inspirations)")
        return
    for r in rows:
        typer.echo(f"{r.id}\t{r.origin.value}\t{r.text}")


@inspirations_app.command("show")
def inspirations_show(
    inspiration_id: str = typer.Argument(..., help="inspiration id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看一条灵感笔记。"""
    from finch.inspirations.repository import InspirationRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    insp = InspirationRepository(ws).get(inspiration_id)
    if insp is None:
        typer.echo(f"not found: {inspiration_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(insp.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    typer.echo(f"id: {insp.id}")
    typer.echo(f"origin: {insp.origin.value}")
    typer.echo(f"text: {insp.text}")
    if insp.source_refs:
        typer.echo("source_refs: " + ", ".join(insp.source_refs))
    if insp.person_ids:
        typer.echo("person_ids: " + ", ".join(insp.person_ids))
    if insp.notes:
        typer.echo("notes:")
        for n in insp.notes:
            typer.echo(f"  - {n.text}")
    if insp.archived_at:
        typer.echo(f"archived_at: {insp.archived_at:%Y-%m-%d}")


def _echo_community_card(profile) -> None:
    typer.echo(f"id: {profile.id}")
    typer.echo(f"community: {profile.name}")
    if profile.platforms:
        typer.echo(f"platforms: {', '.join(profile.platforms)}")
    typer.echo(f"fit_score: {profile.fit_score}")
    if profile.canonical_url:
        typer.echo(f"canonical_url: {profile.canonical_url}")
    if profile.recommendation_state:
        typer.echo(f"recommendation_state: {profile.recommendation_state.value}")
    if profile.why_fit:
        typer.echo("why_fit:")
        for line in profile.why_fit:
            typer.echo(f"  - {line}")
    if profile.recent_evidence:
        typer.echo("recent_evidence:")
        for e in profile.recent_evidence:
            suffix = f" {e.url}" if e.url else ""
            typer.echo(f"  - {e.topic} ({e.relevance}){suffix}")
    if profile.people:
        typer.echo("people:")
        for p in profile.people:
            typer.echo(f"  - {p.name}: {p.reason}")
    if profile.entry_point:
        typer.echo(f"entry_point: {profile.entry_point.discussion}")
        typer.echo(f"  suggested_angle: {profile.entry_point.suggested_angle}")
    if profile.first_contribution:
        typer.echo(
            f"first_contribution: {profile.first_contribution.type} — "
            f"{profile.first_contribution.proposal}"
        )
    if profile.risks:
        typer.echo("risks:")
        for r in profile.risks:
            typer.echo(f"  - {r}")


@community_app.command("context")
def community_context(
    week: str | None = typer.Option(None, "--week", help="ISO 周（默认本周，如 2026-W39）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """快照当前实践上下文到 profile.yaml（不进行公开搜索；搜索由 community-scout Skill 执行）。"""
    from finch.communities.service import CommunityService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = CommunityService(ws)
    context = svc.snapshot_context(settings, ws)
    svc.repo.write_context(context)
    target_week = week or context.week
    report = svc.repo.read_report(target_week)  # 仅当已有报告时附带展示，不声称本次产生
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "week": target_week,
                    "context": context.model_dump(mode="json"),
                    "report": report,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo(f"week: {target_week}")
    typer.echo(f"interests: {', '.join(context.interests) or '(none)'}")
    typer.echo(f"current_questions: {', '.join(context.current_questions) or '(none)'}")
    if report:
        typer.echo(f"\n{report}")
    else:
        typer.echo(f"\n(no report for {target_week} yet — 按 community-scout Skill 执行发现)")


@community_app.command("save")
def community_save(
    card_file: str = typer.Option(..., "--file", help="社区行动卡 YAML 路径"),
    week: str | None = typer.Option(None, "--week", help="ISO 周（默认本周）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """保存一张社区行动卡（顶层 community 对应模型 name；id 由 name 内容寻址）。"""
    from finch.communities.models import CommunityProfile
    from finch.communities.service import CommunityService, week_label

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    raw = yaml.safe_load(Path(card_file).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "community" not in raw:
        typer.echo("card 缺少顶层 `community` 字段（见 references/community-card-schema.md）")
        raise typer.Exit(code=1)
    data = dict(raw)
    data["name"] = data.pop("community")
    try:
        profile = CommunityProfile(**data)
    except ValidationError as exc:
        typer.echo(f"card 校验失败: {exc}")
        raise typer.Exit(code=1) from None
    if not profile.week:
        profile = profile.model_copy(update={"week": week or week_label()})
    saved = CommunityService(ws).save(profile)
    if as_json:
        typer.echo(json.dumps(saved.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"saved {saved.id} ({saved.name})")


@community_app.command("inspect")
def community_inspect(
    community_id: str = typer.Argument(..., help="community id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看一张社区行动卡（`--json` 附带最新反馈与完整反馈历史）。"""
    from finch.communities.service import CommunityService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = CommunityService(ws)
    profile = svc.inspect(community_id)
    if profile is None:
        typer.echo(f"not found: {community_id}")
        raise typer.Exit(code=1)
    if as_json:
        payload = {
            "profile": profile.model_dump(mode="json"),
            "feedback": [f.model_dump(mode="json") for f in svc.feedback_for(community_id)],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _echo_community_card(profile)


@community_app.command("feedback")
def community_feedback(
    community_id: str = typer.Argument(..., help="community id"),
    result: str = typer.Option(
        ..., "--result", help="ignored|saved|joined|interacted|repeated|contributed"
    ),
    note: str = typer.Option("", "--note", help="可选备注"),
    reason_kind: str = typer.Option(
        "", "--reason-kind", help="原因短标签（no_time/too_general/…）"
    ),
    interaction_ref: str = typer.Option("", "--ref", help="用户报告的真实互动链接"),
    ref_kind: str = typer.Option(
        "", "--ref-kind", help="public_url|user_stated（默认 public_url）"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """记录一次跟进状态（append-only；校验社区存在，不自动改变下次评分）。"""
    from finch.communities.models import CommunityResult
    from finch.communities.service import CommunityNotFoundError, CommunityService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        result_value = CommunityResult(result)
    except ValueError:
        valid = ", ".join(r.value for r in CommunityResult)
        typer.echo(f"invalid --result: {result} (use one of {valid})")
        raise typer.Exit(code=1) from None
    if interaction_ref and not ref_kind:
        ref_kind = "public_url"
    try:
        feedback = CommunityService(ws).record_feedback(
            community_id,
            result_value,
            note=note,
            reason_kind=reason_kind,
            interaction_ref=interaction_ref,
            ref_kind=ref_kind,
        )
    except CommunityNotFoundError:
        typer.echo(f"not found: {community_id}")
        raise typer.Exit(code=1) from None
    if as_json:
        typer.echo(json.dumps(feedback.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"recorded {feedback.result.value} for {community_id}")


@community_app.command("list")
def community_list(
    week: str | None = typer.Option(None, "--week", help="按 ISO 周过滤"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    show_all: bool = typer.Option(False, "--all", help="输出原始历史（默认每个社区一条最新投影）"),
) -> None:
    """列出候选社区与最近反馈状态（默认去重，每个稳定社区一条）。"""
    from finch.communities.models import identity_key
    from finch.communities.service import CommunityService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = CommunityService(ws)
    if show_all:
        candidates = svc.repo.list_candidates()
        if week:
            candidates = [c for c in candidates if c.week == week]
    else:
        candidates = svc.repo.list_latest_profiles(week)
    latest = svc.repo.latest_feedback_by_identity()
    if as_json:
        payload = []
        for c in candidates:
            fb = latest.get(identity_key(c))
            payload.append(
                {
                    "profile": c.model_dump(mode="json"),
                    "feedback": fb.model_dump(mode="json") if fb else None,
                }
            )
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not candidates:
        typer.echo("(no communities)")
        return
    for c in candidates:
        fb = latest.get(identity_key(c))
        state = fb.result.value if fb else "-"
        typer.echo(f"{c.id}\t{c.week}\t{c.fit_score}\t{state}\t{c.name}")


@community_app.command("run")
def community_run(
    intent: str = typer.Option(
        "weekly",
        "--intent",
        help="weekly|question|revisit（当前三者行为一致，见 community-scout SKILL）",
    ),
    goal: str = typer.Option("", "--goal", help="本次探索目标（问题模式/回访时必填）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """跑一趟社区发现（有界 loop：search→inspect→propose→finish），写决策记录。"""
    from finch.communities.models import RunIntent
    from finch.communities.repository import CommunityRepository
    from finch.communities.scout import CommunityLoop, WebFetcherSearchSource

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        run_intent = RunIntent(intent)
    except ValueError:
        typer.echo(f"invalid --intent: {intent} (weekly|question|revisit)")
        raise typer.Exit(code=1) from None
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    repo = CommunityRepository(ws)
    loop = CommunityLoop(
        runner,
        WebFetcherSearchSource(settings.community_scout.search_urls),
        repo,
        budget=settings.community_scout,
    )
    run = loop.run(run_intent, goal)
    if as_json:
        typer.echo(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{run.run_id}\t{run.status}\tcards={run.cards_proposed}\tcandidates={run.candidates_found}")


@community_app.command("runs")
def community_runs(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出历史 run（倒序）。"""
    from finch.communities.repository import CommunityRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runs = list(reversed(CommunityRepository(ws).list_runs()))
    if as_json:
        typer.echo(
            json.dumps(
                [r.model_dump(mode="json") for r in runs], ensure_ascii=False, indent=2
            )
        )
        return
    for r in runs:
        typer.echo(f"{r.run_id}\t{r.intent.value}\t{r.status}\t{r.cards_proposed}")


@community_app.command("run-trace")
def community_run_trace(
    run_id: str = typer.Argument(..., help="run id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """复盘一次 run 的 per-step 决策记录。"""
    from finch.communities.repository import CommunityRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = CommunityRepository(ws)
    steps = repo.list_steps(run_id)
    if as_json:
        typer.echo(
            json.dumps(
                [s.model_dump(mode="json") for s in steps], ensure_ascii=False, indent=2
            )
        )
        return
    for s in steps:
        typer.echo(f"{s.action.value}\t{s.decision}\t{s.outcome}")


@inspirations_app.command("note")
def inspirations_note(
    inspiration_id: str = typer.Argument(..., help="inspiration id"),
    text: str = typer.Option(..., "--text", help="追加的笔记"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """追加一条笔记（append-only，不覆盖原话）。"""
    from finch.inspirations.service import InspirationService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    insp = InspirationService(ws).note(inspiration_id, text=text)
    if insp is None:
        typer.echo(f"not found: {inspiration_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(insp.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"noted {inspiration_id} ({len(insp.notes)} notes)")


@inspirations_app.command("archive")
def inspirations_archive(
    inspiration_id: str = typer.Argument(..., help="inspiration id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """归档一条灵感笔记。"""
    from finch.inspirations.service import InspirationService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    insp = InspirationService(ws).archive(inspiration_id)
    if insp is None:
        typer.echo(f"not found: {inspiration_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(insp.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"archived {inspiration_id}")


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
    typer.echo(
        _render_thread_cards(threads, needs_follow_up=needs_follow_up)
    )


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
    typer.echo(_render_thread_detail(thread))


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
    notes = [
        {
            "id": n.id,
            "kind": n.kind.value if n.kind else None,
            "text": n.text,
            "source_ref": n.source_ref,
            "tool_ref": n.tool_ref,
        }
        for n in active_observation_notes(thread)
    ]
    next_step = _thread_next_step(thread)
    if as_json:
        typer.echo(json.dumps({
            "conversation_id": thread.id,
            "topic": thread.topic,
            "open_questions": open_questions,
            "observation_notes": notes,
            "commitments": [
                c.model_dump(mode="json")
                for c in thread.commitments
                if c.status.value == "open"
            ],
            "pending_triggers": [t.value for t in thread.pending_triggers],
            "next_step": next_step,
            "revision": thread.revision,
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(_render_thread_card(thread, for_follow_up=True))


@conversations_app.command("ingest")
def conversations_ingest(
    peer_id: str = typer.Option(..., "--peer", help="peer id"),
    platform: str = typer.Option("x", "--platform"),
    url: str = typer.Option("", "--url", help="消息 URL"),
    body: str = typer.Option("", "--body", help="正文；缺省则标记未知"),
    message_id: str | None = typer.Option(None, "--message-id", help="平台消息 ID"),
    direction: str = typer.Option("unknown", "--direction", help="outbound|inbound|unknown"),
    topic: str = typer.Option("general", "--topic"),
    attested: bool = typer.Option(False, "--attested", help="用户声明属实"),
    trigger: str | None = typer.Option(
        None,
        "--trigger",
        help="可选显式触发：related_update|new_evidence（inbound 仍自动 new_reply）",
    ),
    note_kind: str | None = typer.Option(
        None, "--note-kind", help="problem|workaround|usage_feedback"
    ),
    note: str | None = typer.Option(None, "--note", help="观察笔记正文"),
    tool_ref: str | None = typer.Option(None, "--tool-ref", help="可选工具名/链接"),
    notes_file: str | None = typer.Option(None, "--notes-file", help="多条笔记 JSON 列表"),
    expected_revision: int | None = typer.Option(
        None, "--expected-revision", help="写入笔记时的期望 revision"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """导入一条已发生的互动事实（不要求发布批准；幂等保留原发生时间）。"""
    from finch.conversations.models import FollowUpTrigger, ObservationKind
    from finch.engagement.models import VerificationStatus

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = ConversationService()
    records = InteractionRecordRepository(ws)
    threads = ConversationThreadRepository(ws)
    existing = None
    if message_id:
        existing = records.find_by_platform_message_id(message_id)
    elif url:
        existing = records.find_by_source_url(url)
    now = datetime.now(UTC)
    verification = (
        VerificationStatus.USER_ATTESTED if attested else VerificationStatus.UNVERIFIED
    )
    body_text = body if body else ""
    if not body_text and not url:
        typer.echo("provide --body or --url")
        raise typer.Exit(code=1)
    rec = svc.ingest_record(
        existing,
        peer_id=peer_id,
        platform=platform,
        source_url=url or f"local:{message_id or 'unknown'}",
        body=body_text or "[unknown body]",
        occurred_at=existing.occurred_at if existing else now,
        platform_message_id=message_id,
        direction=direction,
        verification_status=verification,
        observed_at=now,
    )
    records.upsert(rec)
    thread = threads.get(ConversationService().open_thread(peer_id=peer_id, topic=topic).id)
    if thread is None:
        thread = svc.open_thread(peer_id=peer_id, topic=topic, root_message_id=message_id)
    thread = svc.append_interaction(thread, rec.id, occurred_at=rec.occurred_at)
    if direction == "inbound":
        thread = svc.add_trigger(thread, FollowUpTrigger.NEW_REPLY)
    if trigger:
        try:
            thread = svc.add_trigger(thread, FollowUpTrigger(trigger))
        except ValueError:
            typer.echo(f"invalid --trigger: {trigger}")
            raise typer.Exit(code=1) from None

    note_payloads: list[dict] = []
    if notes_file:
        try:
            loaded = json.loads(Path(notes_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            typer.echo(f"invalid --notes-file: {exc}")
            raise typer.Exit(code=1) from exc
        if not isinstance(loaded, list):
            typer.echo("--notes-file must be a JSON list")
            raise typer.Exit(code=1)
        note_payloads.extend(loaded)
    if note_kind or note:
        if not note_kind or not note:
            typer.echo("--note-kind and --note must be provided together")
            raise typer.Exit(code=1)
        note_payloads.append(
            {"kind": note_kind, "text": note, "tool_ref": tool_ref}
        )

    if note_payloads:
        rev = expected_revision if expected_revision is not None else thread.revision
        known = set(thread.interaction_ids) | {rec.id}
        for payload in note_payloads:
            if not isinstance(payload, dict):
                typer.echo("each note must be a JSON object")
                raise typer.Exit(code=1)
            try:
                kind_raw = payload.get("kind")
                if not isinstance(kind_raw, str) or not kind_raw.strip():
                    raise ValueError("note kind is required")
                tool_raw = payload.get("tool_ref")
                supersedes_raw = payload.get("supersedes_id")
                obs = svc.build_observation_note(
                    kind=ObservationKind(kind_raw),
                    text=str(payload.get("text") or ""),
                    source_ref=str(payload.get("source_ref") or rec.id),
                    tool_ref=None if tool_raw is None else str(tool_raw),
                    supersedes_id=None if supersedes_raw is None else str(supersedes_raw),
                )
                thread = svc.upsert_observation_note(
                    thread,
                    obs,
                    expected_revision=rev,
                    known_interaction_ids=known,
                )
                rev = thread.revision
            except (ConversationServiceError, ValueError) as exc:
                typer.echo(f"note rejected: {exc}")
                raise typer.Exit(code=1) from exc

    threads.upsert(thread)
    if as_json:
        typer.echo(json.dumps({
            "record": rec.model_dump(mode="json"),
            "thread_id": thread.id,
            "revision": thread.revision,
            "observation_notes": [
                n.model_dump(mode="json") for n in active_observation_notes(thread)
            ],
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(f"ingested {rec.id} into {thread.id} (rev {thread.revision})")


@conversations_app.command("note")
def conversations_note(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    path: str = typer.Option(..., "--file", help="note.json"),
    expected_revision: int = typer.Option(..., "--expected-revision"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """向线索追加一条 observation 笔记（带 revision 校验）。"""
    from finch.conversations.models import ObservationKind

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ConversationThreadRepository(ws)
    thread = repo.get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"invalid note file: {exc}")
        raise typer.Exit(code=1) from exc
    if not isinstance(raw, dict):
        typer.echo("note file must be a JSON object")
        raise typer.Exit(code=1)
    svc = ConversationService()
    try:
        kind_raw = raw.get("kind")
        if not isinstance(kind_raw, str) or not kind_raw.strip():
            raise ValueError("note kind is required")
        tool_raw = raw.get("tool_ref")
        supersedes_raw = raw.get("supersedes_id")
        obs = svc.build_observation_note(
            kind=ObservationKind(kind_raw),
            text=str(raw.get("text") or ""),
            source_ref=str(raw.get("source_ref") or ""),
            tool_ref=None if tool_raw is None else str(tool_raw),
            supersedes_id=None if supersedes_raw is None else str(supersedes_raw),
        )
        updated = svc.upsert_observation_note(
            thread,
            obs,
            expected_revision=expected_revision,
            known_interaction_ids=set(thread.interaction_ids),
        )
    except (ConversationServiceError, ValueError) as exc:
        typer.echo(f"note rejected: {exc}")
        raise typer.Exit(code=1) from exc
    repo.upsert(updated)
    if as_json:
        typer.echo(updated.model_dump_json(indent=2))
    else:
        typer.echo(f"noted {obs.id} on {updated.id} revision={updated.revision}")


@conversations_app.command("commit")
def conversations_commit(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    text: str = typer.Option(..., "--text", help="承诺正文（须用户明确）"),
    source_ref: str = typer.Option(..., "--source-ref", help="InteractionRecord id"),
    due: str | None = typer.Option(None, "--due", help="到期时间 ISO8601"),
    owner: str = typer.Option("self", "--owner", help="self|peer"),
    expected_revision: int | None = typer.Option(None, "--expected-revision"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """记录一条明确承诺（不能从礼貌回复推断）。"""
    from finch.conversations.models import Commitment

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ConversationThreadRepository(ws)
    thread = repo.get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    if source_ref not in thread.interaction_ids:
        # Also allow any known record id in workspace
        if InteractionRecordRepository(ws).get(source_ref) is None:
            typer.echo(f"source_ref not found: {source_ref}")
            raise typer.Exit(code=1)
    due_at = datetime.fromisoformat(due) if due else None
    commitment = Commitment(
        id=commitment_id_for(source_ref, text),
        owner=owner,  # type: ignore[arg-type]
        source_ref=source_ref,
        text=text,
        due_at=due_at,
    )
    svc = ConversationService()
    try:
        updated = svc.add_commitment(
            thread, commitment, expected_revision=expected_revision
        )
    except ConversationServiceError as exc:
        typer.echo(f"commit rejected: {exc}")
        raise typer.Exit(code=1) from exc
    repo.upsert(updated)
    if as_json:
        typer.echo(commitment.model_dump_json(indent=2))
    else:
        typer.echo(f"commitment {commitment.id} on {updated.id}")


@conversations_app.command("experiment")
def conversations_experiment(
    action: str = typer.Argument(..., help="add | record"),
    conversation_id: str = typer.Argument(..., help="conversation id"),
    hypothesis: str = typer.Option(..., "--hypothesis", help="假设"),
    method: str = typer.Option("", "--method", help="最小方法"),
    expected: str = typer.Option("", "--expected", help="期望观察"),
    result: str = typer.Option("", "--result", help="实际结果（record 必填）"),
    source_ref: str = typer.Option("", "--source-ref", help="来源引用"),
    artifact_ref: str | None = typer.Option(None, "--artifact-ref", help="结果产物引用"),
    expected_revision: int | None = typer.Option(None, "--expected-revision"),
) -> None:
    """采纳小实验（add）或记录结果（record）。建议动作不会自动变成实验。"""
    from finch.conversations.models import MiniExperiment

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ConversationThreadRepository(ws)
    thread = repo.get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    svc = ConversationService()
    try:
        if action == "add":
            if not source_ref.strip():
                typer.echo("--source-ref is required when adding an experiment")
                raise typer.Exit(code=1)
            exp = MiniExperiment(
                hypothesis=hypothesis,
                method=method,
                expected_observation=expected,
                artifact_ref=source_ref.strip(),
            )
            updated = svc.add_experiment(
                thread,
                exp,
                expected_revision=expected_revision,
                source_ref=source_ref.strip(),
            )
        elif action == "record":
            if not result.strip():
                typer.echo("--result is required for record")
                raise typer.Exit(code=1)
            updated = svc.record_experiment_result(
                thread,
                hypothesis=hypothesis,
                actual_result=result,
                artifact_ref=artifact_ref or (source_ref.strip() or None),
                expected_revision=expected_revision,
            )
        else:
            typer.echo("action must be add or record")
            raise typer.Exit(code=1)
    except ConversationServiceError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    repo.upsert(updated)
    typer.echo(f"experiment {action} on {updated.id} revision={updated.revision}")


@conversations_app.command("mark-important")
def conversations_mark_important(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    cadence_days: int = typer.Option(30, "--cadence-days", help="回顾周期（天）"),
    clear: bool = typer.Option(False, "--clear", help="关闭周期回顾"),
    expected_revision: int | None = typer.Option(None, "--expected-revision"),
) -> None:
    """将线索标为重要同行并设定周期回顾（默认关闭；到期只提醒审阅，不自动问候）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ConversationThreadRepository(ws)
    thread = repo.get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    svc = ConversationService()
    try:
        if clear:
            updated = svc.clear_important_review(
                thread, expected_revision=expected_revision
            )
        else:
            updated = svc.mark_important(
                thread,
                cadence_days=cadence_days,
                now=datetime.now(UTC),
                expected_revision=expected_revision,
            )
    except ConversationServiceError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    repo.upsert(updated)
    if clear:
        typer.echo(f"cleared important review on {updated.id}")
    else:
        typer.echo(
            f"marked important {updated.id} cadence={cadence_days}d "
            f"next={updated.next_review_at}"
        )


@conversations_app.command("defer")
def conversations_defer(
    conversation_id: str = typer.Argument(..., help="conversation id"),
    days: int = typer.Option(7, "--days", help="推迟天数"),
) -> None:
    """推迟跟进（到期前不出现在 needs-follow-up）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ConversationThreadRepository(ws)
    thread = repo.get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    until = datetime.now(UTC) + timedelta(days=days)
    updated = ConversationService().defer(thread, until=until)
    repo.upsert(updated)
    typer.echo(f"deferred {conversation_id} until {until.isoformat()}")


@conversations_app.command("close")
def conversations_close(
    conversation_id: str = typer.Argument(..., help="conversation id"),
) -> None:
    """关闭对话线索。"""
    from finch.conversations.models import ThreadStatus

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ConversationThreadRepository(ws)
    thread = repo.get(conversation_id)
    if thread is None:
        typer.echo(f"conversation not found: {conversation_id}")
        raise typer.Exit(code=1)
    updated = ConversationService().set_status(thread, ThreadStatus.CLOSED)
    repo.upsert(updated)
    typer.echo(f"closed {conversation_id}")


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


@article_app.command("analyze")
def article_analyze(
    text: str = typer.Option(None, "--text", help="要分析的文本"),
    file: str = typer.Option(None, "--file", help="文本文件"),
    url: str = typer.Option(None, "--url", help="要分析的链接（X/Reddit/普通网页）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """分析文章表达：任务、读者变化、方法拆解与可借鉴技巧（不落库）。"""
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
        if not source.body.strip():
            typer.echo("empty body after resolve")
            raise typer.Exit(code=1)
        runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
        report = ArticleAnalysisService(runner).analyze(source)
    except (RuntimeError, StructuredOutputError, OSError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        typer.echo(_render_article_report(report))


def _render_article_report(report: ArticleReport) -> str:
    task = report.expression_task
    inferred = "（根据文章推断）" if task.inferred else ""
    aud = report.audience_change
    lines = [
        "# 文章表达分析",
        "",
        f"## 表达任务{inferred}",
        f"- 主题：{task.topic}",
        f"- 主任务：{task.primary_task}",
    ]
    if task.secondary_tasks:
        lines.append(f"- 次任务：{'；'.join(task.secondary_tasks)}")
    lines += [
        "",
        "## 读者与预期变化",
        f"面向 **{aud.who}**，试图让他们从 **{aud.before}**，转变为 **{aud.after}**。",
        f"- 读者匹配：{aud.fit_check}",
        "",
        "## 表达特点",
    ]
    for t in report.techniques:
        caveat = f" 代价：{t.caveat}" if t.caveat else ""
        lines.append(
            f"- 「{t.excerpt}」→ {t.method} → {t.reader_effect}.{caveat}"
        )
    if not report.techniques:
        lines.append("- （无拆解条目）")
    eff = report.effectiveness
    lines += [
        "",
        "## 目标达成情况",
        f"- 清晰度：{eff.clarity}",
        f"- 具体性：{eff.concreteness}",
        f"- 可信度：{eff.credibility}",
        f"- 可执行性：{eff.actionability}",
        "",
        "## 可借鉴方法",
    ]
    for m in report.transferable_methods:
        lines += [
            f"- **{m.method}**",
            f"  为何有效：{m.why_effective_here}",
            f"  适用：{m.when_to_use}",
            f"  练习：{m.mini_exercise}",
        ]
    if report.limitations:
        lines += ["", "## 局限"]
        lines += [f"- {x}" for x in report.limitations]
    return "\n".join(lines)


@dialogue_app.command("save")
def dialogue_save(
    file: Path = typer.Option(..., "--file", help="note.json（完整 DialogueNote）"),
    expected_revision: int = typer.Option(
        0, "--expected-revision", help="0=创建，>0=要求当前 revision 匹配"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """创建或追加一条讨论摘要（薄持久化；幂等 + revision 冲突）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        incoming = DialogueNote.model_validate_json(file.read_text(encoding="utf-8"))
        note = DialogueService(ws).save(incoming, expected_revision=expected_revision)
    except DialogueServiceError as exc:
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            typer.echo(f"error: {exc}")
        raise typer.Exit(code=1) from None
    except ValidationError:
        message = "invalid DialogueNote JSON"
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        else:
            typer.echo(f"error: {message}")
        raise typer.Exit(code=1) from None
    except OSError as exc:
        message = exc.strerror or "unable to read note file"
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        else:
            typer.echo(f"error: {message}")
        raise typer.Exit(code=1) from None
    if as_json:
        typer.echo(json.dumps(note.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"saved {note.id} revision={note.revision}")


@dialogue_app.command("search")
def dialogue_search(
    query: str = typer.Argument(..., help="检索关键词（topic_key / 主题 / 摘要文本）"),
    limit: int = typer.Option(3, "--limit", help="最多返回条数"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """关键词召回讨论摘要（最多 limit 条，读取不调用网络）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    notes = DialogueService(ws).search(query, limit=limit)
    if as_json:
        typer.echo(
            json.dumps(
                [n.model_dump(mode="json") for n in notes],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for n in notes:
            latest = n.checkpoints[-1] if n.checkpoints else None
            status = latest.position_status.value if latest else "unresolved"
            typer.echo(f"{n.id}\t{n.topic_key}\t{status}")


@dialogue_app.command("show")
def dialogue_show(
    note_id: str = typer.Argument(..., help="note id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """读取单条讨论摘要。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    note = DialogueService(ws).show(note_id)
    if note is None:
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
        else:
            typer.echo("not found")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(note.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{note.topic}\n  topic_key={note.topic_key} revision={note.revision}")


@dialogue_app.command("forget")
def dialogue_forget(
    note_id: str = typer.Argument(..., help="note id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """删除单条讨论摘要。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ok = DialogueService(ws).forget(note_id)
    if as_json:
        typer.echo(json.dumps({"ok": ok}, ensure_ascii=False))
    else:
        typer.echo("deleted" if ok else "not found")
    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
