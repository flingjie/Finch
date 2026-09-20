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
from .drafts.service import DraftCreateResult, DraftService
from .engagement.flow import EngagementRunResult
from .engagement.metrics import (
    compute_relationship_metrics,
    explain_recommendation_adjustments,
    render_relationship_metrics,
)
from .engagement.models import (
    DiscoverySnapshot,
    InteractionAction,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
    Opportunity,
    PresentationRecord,
    RecommendationEntry,
    RecommendationFeedback,
)
from .engagement.named import connect_named, parse_named_target
from .engagement.proposals import generate_proposals
from .engagement.scoring import ScoredPost, rank_candidates, score_posts
from .engagement.search import fetch_post_by_url
from .evidence.extractor import Extractor, build_cards
from .github.commit_reader import CommitReader, load_commit_details
from .github.gh_client import GhClient
from .github.local_repo import resolve_commit_repo
from .ideas.commit_service import CommitService
from .ideas.fragment_service import FragmentService
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
from .peers.models import PeerProfile
from .peers.service import PeerService, profile_url_for
from .practice.service import PracticeService
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
    InteractionRecordRepository,
    InteractionRepository,
    OpportunityRepository,
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

voice_app = typer.Typer(help="Manage the author voice profile (local, no auto-publish)")
app.add_typer(voice_app, name="voice")

ideas_app = typer.Typer(help="Idea 候选流（commit / 用户片段 / 对话提炼 + 状态转换）")
app.add_typer(ideas_app, name="ideas")

drafts_app = typer.Typer(help="Draft 生成（已确认 idea → 草稿，不自动发布）")
app.add_typer(drafts_app, name="drafts")

review_app = typer.Typer(help="Review original drafts (accept/revise/skip, no auto-publish)")
app.add_typer(review_app, name="review")

connect_app = typer.Typer(
    help="连接主循环：daily / prepare / with / approve / reject / edit / record"
)
app.add_typer(connect_app, name="connect")

peers_app = typer.Typer(help="同行档案与关系上下文")
app.add_typer(peers_app, name="peers")

people_app = typer.Typer(help="今日承诺面：需回应/兑现的真实对话线索")
app.add_typer(people_app, name="people")

connections_app = typer.Typer(help="连接机会与互动记录（用户亲自发布）")
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


_IDEA_CARD_LIMIT = 6

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


def _render_idea_cards(jobs: list[ContentJob], *, limit: int = _IDEA_CARD_LIMIT) -> str:
    """本次结果的决策卡列表；超出 limit 时指向 `ideas list`。"""
    if not jobs:
        return "no idea candidates"
    shown = jobs[:limit]
    parts: list[str] = [
        "\n\n".join(_render_idea_card(job) for job in shown)
    ]
    if len(jobs) > limit:
        parts.append(
            f"共 {len(jobs)} 个候选，以上 {len(shown)} 个。其余：uv run finch ideas list"
        )
    first = shown[0]
    if first.status == ContentJobStatus.PROPOSED:
        parts.append(f"uv run finch ideas skip {first.id} --reason ...")
    return "\n\n".join(parts)


_CONNECT_CARD_LIMIT = 6

_ACTION_LABELS = {
    InteractionAction.IGNORE: "忽略",
    InteractionAction.BOOKMARK: "收藏",
    InteractionAction.OBSERVE_AUTHOR: "观察",
    InteractionAction.DRAFT_REPLY: "回复",
    InteractionAction.DRAFT_QUOTE: "引用",
    InteractionAction.DRAFT_DM: "私信",
}


def _action_label(action: InteractionAction) -> str:
    return _ACTION_LABELS.get(action, action.value)


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


def _render_opportunity_card(opp: Opportunity, *, index: int | None = None) -> str:
    """轻量机会卡：正在做什么 / 为何相关 / 可贡献什么 / 下一步 / 时间与不确定性。"""
    prefix = f"{index}. " if index is not None else ""
    who = opp.peer_id
    if opp.post is not None:
        who = opp.post.author_name or opp.post.author_id or opp.peer_id
    link = opp.source_refs[0] if opp.source_refs else ""
    doing = opp.shared_problem.strip() or opp.source_excerpt.strip() or opp.novelty_reason.strip()
    if not doing and opp.post is not None:
        doing = " ".join(opp.post.content.split())[:120]
    lines = [
        f"{prefix}谁: {who}",
        f"模式: {opp.suggested_mode.value}",
    ]
    if link:
        lines.append(f"来源: {link}")
    if doing:
        lines.append(f"正在做什么: {doing}")
    if opp.why_relevant.strip():
        lines.append(f"为何相关: {opp.why_relevant}")
    if opp.contribution_basis_refs:
        lines.append(f"可贡献什么: 关联实践 {', '.join(opp.contribution_basis_refs[:3])}")
        if opp.opening.strip():
            lines.append(f"切入点: {opp.opening}")
    elif opp.opening.strip():
        lines.append(f"可贡献什么: 需先准备 — {opp.opening}")
    elif opp.suggested_mode.value == "learn":
        lines.append("可贡献什么: 需先准备（先了解）")
    else:
        lines.append("可贡献什么: 需先准备")
    action = opp.next_action or "observe"
    minutes = opp.estimated_minutes
    if minutes is not None:
        lines.append(f"建议下一步: {action}（约 {minutes} 分钟）")
    else:
        lines.append(f"建议下一步: {action}")
    if opp.uncertainty.strip():
        lines.append(f"不确定性: {opp.uncertainty}")
    if opp.novelty_reason.strip():
        lines.append(f"增量: {opp.novelty_reason}")
    lines.append(f"uv run finch connect prepare --opportunity {opp.id}")
    return "\n".join(lines)


def _render_opportunity_cards(opps: list[Opportunity], *, limit: int = 12) -> str:
    if not opps:
        return "- (none)"
    shown = opps[:limit]
    return "\n\n".join(
        _render_opportunity_card(o, index=i) for i, o in enumerate(shown, start=1)
    )


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


def _render_proposal_card(proposal: InteractionProposal) -> str:
    """互动提案决策卡：对方问题 / 贡献点 / 证据 / 提纲 / 下一步；完整草稿仅在有时展示。"""
    outline = (proposal.outline or "").strip()
    draft = (proposal.revised_draft or proposal.draft or "").strip()
    lines = [f"动作: {_action_label(proposal.action)}"]
    if (proposal.source_summary or "").strip():
        lines.append(f"对方问题: {proposal.source_summary.strip()}")
    if (proposal.value_added or "").strip():
        lines.append(f"我能补充: {proposal.value_added.strip()}")
    elif (proposal.why_this_person or "").strip():
        lines.append(f"为什么是这个人: {proposal.why_this_person}")
    if proposal.contribution_basis_refs:
        lines.append(f"证据: {', '.join(proposal.contribution_basis_refs[:5])}")
    if outline:
        lines.append("提纲:")
        for raw in outline.splitlines():
            bullet = raw.strip()
            if bullet:
                lines.append(f"  - {bullet.lstrip('- ').strip()}")
    elif draft:
        preview = " ".join(draft.split())[:120]
        lines.append(f"草稿预览: {preview}")
    if (proposal.why_now or "").strip():
        lines.append(f"为什么现在: {proposal.why_now}")
    if (proposal.expected_conversation_opening or "").strip():
        lines.append(f"预期开口: {proposal.expected_conversation_opening}")
    if proposal.factual_risks:
        lines.append(f"事实风险: {'; '.join(proposal.factual_risks[:3])}")
    if proposal.status == InteractionStatus.PROPOSED:
        lines.append(f"下一步: uv run finch connect approve {proposal.id}")
        lines.append(f"登记发布: uv run finch connect record {proposal.id} --url <url>")
    return "\n".join(lines)


def _render_proposal_cards(
    proposals: list[InteractionProposal],
    *,
    limit: int = _CONNECT_CARD_LIMIT,
    include_reject: bool = True,
) -> str:
    if not proposals:
        return "no interaction proposals"
    shown = proposals[:limit]
    parts = ["\n\n".join(_render_proposal_card(p) for p in shown)]
    if len(proposals) > limit:
        parts.append(
            f"共 {len(proposals)} 个候选，以上 {len(shown)} 个。"
            "其余：uv run finch connect prepare --json"
        )
    first = shown[0]
    if include_reject and first.status == InteractionStatus.PROPOSED:
        parts.append(f"uv run finch connect reject {first.id} --reason ...")
    return "\n\n".join(parts)


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
    if job.status == ContentJobStatus.DRAFTED:
        return ["uv run finch review list"]
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
    ideas = CommitService(reader, extractor).to_ideas(details, repo=repo)
    idea_service = IdeaService(ContentJobRepository(ws))
    jobs = [idea_service.create_candidate(idea) for idea in ideas]
    if as_json:
        payload = [
            {
                "id": job.id,
                "origin": job.origin,
                "core_point": job.core_message,
                "reader_problem": job.reader_problem,
                "why_now": job.why_now,
                "recommended_format": job.recommended_format.value,
                "status": job.status.value,
                "generation_key": job.generation_key,
            }
            for job in jobs
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_idea_cards(jobs))


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


@ideas_app.command("signals")
def ideas_signals(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """聚合社区信号（同行共同主题 + 未解问题/分歧）为一个 idea 候选并落库。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    try:
        idea = service.from_signals(
            peers=PeerRepository(ws).list_all(),
            threads=ConversationThreadRepository(ws).list_all(),
        )
        if idea is not None:
            job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
    except (RuntimeError, StructuredOutputError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    if idea is None:
        typer.echo("no community signal worth an idea")
        raise typer.Exit(code=0)
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
        for p in InteractionRepository(ws).list_all()
        if p.status.value in {"proposed", "approved", "executed", "rejected"}
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
        typer.echo(reflection.model_dump_json(indent=2))
    else:
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


def _decision_service(ws: Workspace) -> InboxDecisionService:
    return InboxDecisionService(
        jobs=ContentJobRepository(ws),
        drafts=DraftRepository(ws),
        decisions=DecisionRecordRepository(ws),
        publication_intents=PublicationIntentRepository(ws),
        interactions=InteractionRepository(ws),
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
        interactions=InteractionRepository(ws),
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


def _run_discovery(settings: Settings) -> EngagementRunResult:
    """执行统一每日发现（sources → people → shortlist → opportunities）。"""
    from finch.discovery.daily import run_daily_discovery

    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    daily = run_daily_discovery(settings, runner=runner)
    assert daily.engagement is not None
    return daily.engagement


def _run_daily_full(settings: Settings):
    """Full daily result including shortlist + connection opportunities."""
    from finch.discovery.daily import run_daily_discovery

    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    return run_daily_discovery(settings, runner=runner)


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


def _persist_discovery(
    ws: Workspace,
    result: EngagementRunResult,
    *,
    snapshot_id: str | None = None,
) -> DiscoverySnapshot | None:
    """把发现结果的同行与机会落库；可选写入 DiscoverySnapshot。"""
    peers = PeerRepository(ws)
    opportunities = OpportunityRepository(ws)
    peer_svc = PeerService()
    for ranked in result.peers:
        merged = peer_svc.merge_discovered(peers.get(ranked.profile.id), ranked.profile)
        peers.upsert(merged)
    for opp in result.opportunities:
        opportunities.upsert(opp)
    for candidate in result.candidates:
        InteractionRepository(ws).upsert(candidate, run_id=result.run_id)

    if not result.opportunities and result.status == "failed":
        return DiscoverySnapshotRepository(ws).latest()

    # F1：保留 run_daily_discovery 已写入的完整 50 人推荐与首页投影，避免覆盖。
    latest = DiscoverySnapshotRepository(ws).latest()
    recommendations = latest.recommendations if latest is not None else []
    recommendation_shortfall = (
        latest.recommendation_shortfall if latest is not None else {}
    )
    home_person_ids = latest.home_person_ids if latest is not None else []

    snap_id = snapshot_id or result.run_id
    snapshot = DiscoverySnapshot(
        id=snap_id,
        created_at=datetime.now(UTC),
        context_fingerprint=result.context_fingerprint,
        source_coverage={
            "posts_found": result.posts_found,
            "opportunity_count": len(result.opportunities),
            "status": result.status,
            **(result.source_coverage or {}),
        },
        failures=[
            {"platform": f.platform, "query": f.query or "", "reason": f.reason}
            for f in result.failures
        ],
        ranked_opportunity_ids=[o.id for o in result.opportunities],
        ranking_version="1",
        recommendations=recommendations,
        recommendation_shortfall=recommendation_shortfall,
        home_person_ids=home_person_ids,
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
    opp_repo = OpportunityRepository(ws)
    opps: list[Opportunity] = []
    if snapshot is not None:
        opps = opp_repo.list_by_ids(snapshot.ranked_opportunity_ids[:limit])
    threads = ConversationThreadRepository(ws).list_all()
    needs_follow_up = [
        t for t in threads if ConversationService().needs_follow_up(t, now=now)
    ]
    idea_candidates = [
        j for j in ContentJobRepository(ws).list_jobs()
        if j.status == ContentJobStatus.PROPOSED
    ]
    # Peers from ranked opportunities' peer_ids if present; else empty ranked list.
    peer_repo = PeerRepository(ws)
    ranked_peers = []
    from finch.engagement.flow import RankedPeer
    from finch.engagement.relationship import PeerValue

    for opp in opps:
        profile = peer_repo.get(opp.peer_id)
        if profile is None:
            continue
        ranked_peers.append(
            RankedPeer(
                profile=profile,
                value=PeerValue(
                    topic_overlap=0.0,
                    practical_depth=0.0,
                    contribution_space=0.0,
                    continuity_potential=0.0,
                    repetition_penalty=0.0,
                    promotion_risk=0.0,
                    total=opp.score_total,
                    reasons=[],
                ),
            )
        )
    focus = build_today_focus(
        peers=ranked_peers,
        contributions=InteractionRepository(ws).list_pending()[:3],
        threads=needs_follow_up,
        ideas=idea_candidates,
        opportunities=opps,
        now=now,
        opportunity_limit=limit,
    )
    return focus, snapshot


def _render_daily(focus: TodayFocus) -> str:
    def _section(title: str, body: str) -> str:
        return f"## {title}\n{body}"

    conv = focus["conversations"]
    opps = focus["opportunities"]
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
    opp_body = _render_opportunity_cards(opps["items"], limit=len(opps["items"]) or 1)
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
            "新发现的交流机会",
            _with_more(opp_body, len(opps["items"]), opps["total"]),
        ),
        _section(
            "可分享的素材 / 观点候选",
            _with_more(idea_body, len(ideas["items"]), ideas["total"]),
        ),
    ])


def _prepare_opportunity(
    settings: Settings,
    ws: Workspace,
    opportunity_id: str,
) -> InteractionProposal | None:
    """对指定机会深读并生成提案（≤ prepare 路径，不刷全量发现）。

    try/repro/observe → 无公开回复草稿的最小贡献清单（observe_author）。
    ask/reply/case → 草稿路径；无实践依据时拦截虚构亲历。
    """
    from finch.engagement.opportunity import assign_next_action
    from finch.engagement.proposals import (
        context_version_for,
        generation_key_for,
    )
    from finch.peers.service import peer_id_for

    opp = OpportunityRepository(ws).get(opportunity_id)
    if opp is None:
        return None
    url = opp.source_refs[0] if opp.source_refs else ""
    if not url:
        return None
    post = fetch_post_by_url(
        url, opencli=OpenCliClient(), reddit_opencli=RedditOpenCliClient()
    )
    if post is None and opp.post is not None:
        post = opp.post
    if post is None:
        return None

    practice_refs = list(settings.interests.practice_refs)
    # Prefer opportunity-linked basis; fall back to interests.
    basis = list(opp.contribution_basis_refs) or list(practice_refs)
    time_budget = settings.interests.time_budget_minutes
    next_action = opp.next_action
    minutes = opp.estimated_minutes
    if next_action is None:
        next_action, minutes = assign_next_action(
            opp.suggested_mode,
            has_practice=bool(basis),
            time_budget=time_budget,
        )
    elif minutes is None:
        _, minutes = assign_next_action(
            opp.suggested_mode,
            has_practice=bool(basis),
            time_budget=time_budget,
        )

    ctx_ver = context_version_for(
        practice_refs=practice_refs,
        current_questions=settings.interests.current_questions,
    )
    peer_id = opp.peer_id or peer_id_for(post.platform, post.author_id)

    # Min-contribution path: no public reply draft.
    if next_action in {"try", "repro", "observe"}:
        from finch.engagement.models import ConversationScore

        checklist = _min_contribution_checklist(opp, next_action, minutes or 10, basis)
        action = InteractionAction.OBSERVE_AUTHOR
        score = ConversationScore(
            relevance=0.8,
            novelty=0.7,
            discussability=0.5,
            practical_evidence=0.7,
            relationship_value=0.5,
            total=0.75,
            reasons=[opp.why_relevant or "selected opportunity"],
        )
        proposal = InteractionProposal(
            id=f"{post.platform}:{post.id}:{action.value}",
            post=post,
            score=score,
            action=action,
            draft="",  # no public reply
            intent=checklist,
            source_summary=opp.source_excerpt or (post.content[:200] if post else ""),
            factual_risks=["min contribution — not yet done; do not mark complete"],
            approval_required=False,
            peer_id=peer_id,
            why_this_person=opp.why_relevant,
            expected_conversation_opening=opp.opening,
            why_now=opp.novelty_reason or opp.shared_problem or opp.why_relevant,
            generation_key=generation_key_for(
                peer_id=peer_id,
                post_id=post.id,
                action=action,
                context_version=ctx_ver,
            ),
        )
        InteractionRepository(ws).upsert(proposal, run_id=f"prepare_{opportunity_id}")
        return proposal

    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    scored = score_posts(runner, [post], settings.engagement.weights)
    ranked = rank_candidates(scored, min_candidate_score=settings.engagement.min_candidate_score)
    if not ranked:
        from finch.engagement.models import ConversationScore

        synthetic = ScoredPost(
            post=post,
            score=ConversationScore(
                relevance=0.8,
                novelty=0.8,
                discussability=0.8,
                practical_evidence=0.7,
                relationship_value=0.5,
                total=0.78,
                reasons=[opp.why_relevant or "selected opportunity"],
            ),
        )
        ranked = [synthetic]
    proposals = generate_proposals(
        runner,
        ranked,
        settings.engagement,
        context_version=ctx_ver,
        contribution_basis_refs=basis,
        full_draft=False,
    )
    if not proposals:
        return None
    proposal = proposals[0]
    if opp.peer_id:
        proposal = proposal.model_copy(
            update={
                "peer_id": opp.peer_id,
                "why_this_person": opp.why_relevant,
                "expected_conversation_opening": opp.opening,
                "why_now": opp.novelty_reason or opp.why_relevant,
            }
        )
    InteractionRepository(ws).upsert(proposal, run_id=f"prepare_{opportunity_id}")
    return proposal


def _min_contribution_checklist(
    opp: Opportunity,
    next_action: str,
    minutes: int,
    basis: list[str],
) -> str:
    """Internal checklist for try/repro/observe — not a public reply draft."""
    lines = [
        f"最小贡献（{next_action}，约 {minutes} 分钟）",
        f"对象: {opp.shared_problem or opp.source_excerpt[:120] or opp.id}",
    ]
    if next_action == "observe":
        lines.append("动作: 阅读来源与上下文，记下一个具体问题；暂不回复。")
    elif next_action == "try":
        lines.append("动作: 按对方分享试跑一个具体任务，记录卡点（未完成不算已试用）。")
    else:
        lines.append("动作: 复现对方描述的边界/失败，记录结果引用（artifact）。")
    if basis:
        lines.append("可用个人实践: " + ", ".join(basis[:5]))
    else:
        lines.append("个人实践: 无 — 只提问或观察，禁止声称已测试。")
    if opp.uncertainty:
        lines.append(f"不确定性: {opp.uncertainty}")
    return "\n".join(lines)


@connect_app.command("refresh")
def connect_refresh(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """有界刷新发现池并持久化快照，返回覆盖情况。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    previous = DiscoverySnapshotRepository(ws).latest()
    result = _run_discovery(settings)
    snapshot: DiscoverySnapshot | None
    if result.status == "failed" and previous is not None:
        snapshot = previous
        coverage = {
            "kept_previous": True,
            "previous_id": previous.id,
            "previous_created_at": previous.created_at.isoformat(),
            "failure_summary": result.summary,
        }
    else:
        snapshot = _persist_discovery(ws, result)
        coverage = {
            "kept_previous": False,
            "posts_found": result.posts_found,
            "opportunity_count": len(result.opportunities),
            "failures": len(result.failures),
            "status": result.status,
        }
    if as_json:
        typer.echo(json.dumps({
            "snapshot_id": snapshot.id if snapshot else None,
            "coverage": coverage,
            "partial": bool(result.failures) or result.status != "succeeded",
            "failures": [
                {"platform": f.platform, "query": f.query, "reason": f.reason}
                for f in result.failures
            ],
            "opportunity_ids": [o.id for o in result.opportunities],
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(f"snapshot: {snapshot.id if snapshot else 'none'}")
    typer.echo(json.dumps(coverage, ensure_ascii=False))
    if result.failures or result.status != "succeeded":
        typer.echo(
            f"部分结果：status={result.status}, "
            f"failures={len(result.failures)}（已返回已有机会，未静默清空）"
        )


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
) -> None:
    """连接主循环入口：首页 3 重点（默认）+ 50 人分层浏览（--view browse）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    snapshot = DiscoverySnapshotRepository(ws).latest()
    # D1：默认只读；仅在显式 --refresh 时刷新，不因过期隐式抓取。
    need_refresh = refresh
    stale = snapshot is not None and not _snapshot_fresh(
        snapshot, settings.engagement.snapshot_ttl_hours
    )
    daily = None
    if need_refresh:
        daily = _run_daily_full(settings)
        result = daily.engagement
        assert result is not None
        if result.status == "failed" and snapshot is not None:
            pass  # keep previous
        else:
            snapshot = _persist_discovery(ws, result)

    focus, snapshot = _load_today_payload(ws, settings, limit=limit)

    # Connection opportunities for the priority tier (backward-compat JSON field).
    connections_payload: list[dict] = []
    if daily is not None:
        for conn in daily.connections:
            connections_payload.append(conn.model_dump(mode="json"))

    # F1：非刷新读取时从快照重放完整 50 人推荐。
    rec_entries = snapshot.recommendations if snapshot is not None else []
    rec_shortfall = snapshot.recommendation_shortfall if snapshot is not None else {}
    home_ids = snapshot.home_person_ids if snapshot is not None else []

    if as_json:
        typer.echo(json.dumps({
            "snapshot_id": snapshot.id if snapshot else None,
            "refreshed": need_refresh,
            "home_person_ids": home_ids,
            "recommendations": (
                _recommendations_payload(daily.recommendations)
                if (daily is not None and daily.recommendations is not None)
                else _entries_payload(rec_entries, rec_shortfall)
            ),
            "shortlist": [
                {
                    "slot": i.slot.value,
                    "peer_id": i.candidate.peer.id,
                    "person_id": i.candidate.person_id,
                    "score": i.candidate.score.total,
                    "platform": i.candidate.platform,
                    "artifact_ids": i.candidate.artifact_ids,
                }
                for i in (daily.shortlist if daily else [])
            ],
            "connections": connections_payload,
            "conversations_needing_follow_up": [
                t.model_dump(mode="json") for t in focus["conversations"]["items"]
            ],
            "peers": [rp.profile.model_dump(mode="json") for rp in focus["peers"]["items"]],
            "opportunities": [
                o.model_dump(mode="json") for o in focus["opportunities"]["items"]
            ],
            "contributions": [
                c.model_dump(mode="json") for c in focus["contributions"]["items"]
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
        rec_lines = _render_home_entries(rec_entries, home_ids)
        by_id = {e.person_id: e for e in rec_entries}
        shown_entries = [by_id[pid] for pid in home_ids if pid in by_id]
    # D10：仅文本前台实际输出时记录曝光；首页只记实际展示的人物，浏览记全部展开条目。
    _record_presentations(
        ws, snapshot.id, [o.id for o in focus["opportunities"]["items"]]
    )
    _record_person_presentations(
        ws,
        snapshot.id,
        shown_entries,
        surface="browse" if view == "browse" else "home",
    )
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


@connect_app.command("more")
def connect_more(
    snapshot_id: str = typer.Option(..., "--snapshot", help="快照 ID"),
    limit: int = typer.Option(5, "--limit", help="追加展示数"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从快照剩余未呈现合格机会中取下一批（无网络/LLM）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    snapshot = DiscoverySnapshotRepository(ws).get(snapshot_id)
    if snapshot is None:
        typer.echo(f"snapshot not found: {snapshot_id}")
        raise typer.Exit(code=1)
    presented = PresentationRecordRepository(ws).presented_ids(snapshot_id)
    remaining_ids = [
        oid for oid in snapshot.ranked_opportunity_ids if oid not in presented
    ]
    if not remaining_ids:
        msg = "no more qualified opportunities in snapshot; run connect refresh to search again"
        if as_json:
            typer.echo(json.dumps({"opportunities": [], "message": msg}, ensure_ascii=False))
            return
        typer.echo(msg)
        return
    batch_ids = remaining_ids[:limit]
    opps = OpportunityRepository(ws).list_by_ids(batch_ids)
    if as_json:
        typer.echo(json.dumps({
            "snapshot_id": snapshot_id,
            "opportunities": [o.model_dump(mode="json") for o in opps],
        }, ensure_ascii=False, indent=2))
        return
    # D10：仅文本前台实际输出时记录曝光（--json 机器读取无副作用）。
    _record_presentations(ws, snapshot_id, [o.id for o in opps])
    typer.echo(_render_opportunity_cards(opps, limit=limit))


@connect_app.command("expand")
def connect_expand(
    from_id: str | None = typer.Option(None, "--from", help="机会 ID"),
    scope: str | None = typer.Option(None, "--scope", help="范围文本"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """有界扩展：一跳讨论参与者（--from）或按范围从缓存/有界搜索重选（--scope）。"""
    if not from_id and not scope:
        msg = "pass --from <opportunity_id> or --scope TEXT"
        if as_json:
            typer.echo(json.dumps({"ok": False, "message": msg}, ensure_ascii=False))
        else:
            typer.echo(msg)
        raise typer.Exit(code=1)

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())

    from finch.engagement.expand import expand_by_scope, expand_from_opportunity

    if from_id:
        opps, snap, message = expand_from_opportunity(
            settings, ws, from_id, runner=runner
        )
        payload = {
            "ok": True,
            "message": message,
            "snapshot_id": snap.id if snap else None,
            "opportunities": [o.model_dump(mode="json") for o in opps],
        }
        if as_json:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        typer.echo(message)
        if opps:
            typer.echo(_render_opportunity_cards(opps, limit=len(opps)))
        return

    assert scope is not None
    opps, message = expand_by_scope(settings, ws, scope, runner=runner)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "message": message,
                    "opportunities": [o.model_dump(mode="json") for o in opps],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo(message)
    if opps:
        typer.echo(_render_opportunity_cards(opps, limit=len(opps)))


@connect_app.command("prepare")
def connect_prepare(
    opportunity_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--opportunity",
            help="选中的机会 ID（可重复；本批最多 10；必须至少指定一个）",
        ),
    ] = None,
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """为选中机会准备互动提案（必须 --opportunity；本批最多 10；不把浏览列表写成完整回复）。"""
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
    # F2：选中后的深度准备取 deep_prepare_limit 与 max_reply_drafts 的较小值。
    cap = min(
        settings.discovery.daily_people.deep_prepare_limit,
        settings.engagement.max_reply_drafts,
    )
    over_cap = len(ids) > cap
    selected = ids[:cap]
    proposals: list[InteractionProposal] = []
    misses: list[str] = []
    for oid in selected:
        proposal = _prepare_opportunity(settings, ws, oid)
        if proposal is None:
            misses.append(oid)
        else:
            proposals.append(proposal)
    # Record selection on latest snapshot when present.
    snap_repo = DiscoverySnapshotRepository(ws)
    snapshot = snap_repo.latest()
    if snapshot is not None:
        merged = list(dict.fromkeys([*snapshot.selected_opportunity_ids, *selected]))
        snap_repo.upsert(
            snapshot.model_copy(update={"selected_opportunity_ids": merged})
        )
    if as_json:
        payload = {
            "proposals": [p.model_dump(mode="json") for p in proposals],
            "misses": misses,
            "over_cap": over_cap,
            "cap": cap,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        if over_cap or misses or not proposals:
            raise typer.Exit(code=1)
        return
    if proposals:
        typer.echo(_render_proposal_cards(proposals))
    if misses:
        typer.echo(
            "could not prepare: " + ", ".join(misses)
        )
    if over_cap:
        typer.echo(
            f"batch limit is {cap}; prepared first {cap} of {len(ids)} selected"
        )
    if over_cap or misses or not proposals:
        if not proposals:
            typer.echo("no interaction proposals")
        raise typer.Exit(code=1)


@connect_app.command("feedback")
def connect_feedback(
    path: str = typer.Option(..., "--file", help="feedback.json"),
) -> None:
    """校验并记录推荐反馈（兴趣 / 行动维度）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else [raw]
    repo = RecommendationFeedbackRepository(ws)
    saved = 0
    for item in items:
        if "created_at" not in item:
            item = {**item, "created_at": datetime.now(UTC).isoformat()}
        if "id" not in item:
            digest = hashlib.sha256(
                f"{item.get('opportunity_id')}:{item.get('dimension')}:{item.get('value')}:{item['created_at']}".encode()
            ).hexdigest()[:12]
            item = {**item, "id": f"rfb_{digest}"}
        try:
            feedback = RecommendationFeedback.model_validate(item)
        except ValidationError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        if feedback.dimension == "interest" and feedback.value not in {
            "worth_following", "neutral", "unsuitable"
        }:
            typer.echo(f"invalid interest value: {feedback.value}")
            raise typer.Exit(code=1)
        if feedback.dimension == "action" and feedback.value not in {
            "prepare", "save_for_later", "no_opening"
        }:
            typer.echo(f"invalid action value: {feedback.value}")
            raise typer.Exit(code=1)
        repo.upsert(feedback)
        saved += 1
    typer.echo(f"saved {saved} recommendation feedback record(s)")


@connect_app.command("create")
def connect_create(
    input_url: str = typer.Option(..., "--input", help="帖子 URL"),
    topic: str = typer.Option("", "--topic", help="帖子主题（可选）"),
    from_idea: str | None = typer.Option(
        None, "--from-idea", help="已有 idea / ContentJob id（个人素材）"
    ),
    note: str | None = typer.Option(
        None, "--note", help="个人笔记原文（无 commit 也可；会先落为 idea）"
    ),
    full_draft: bool = typer.Option(
        False, "--draft", help="生成完整回复草稿（默认只给提纲）"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """帖子 URL + 可选个人素材 → 提纲或暂不回复（默认不写完整草稿）。

    流程：抓取帖子 → 贡献点检查 → 评分 → 提纲/草稿 → 存为 PROPOSED。
    无贡献时打印「暂不回复」并退出 0，不落库回复提案。
    """
    from finch.engagement.contribution import assess_job_contribution
    from finch.engagement.proposals import ready_gate_blocks
    from finch.ideas.fragment_service import FragmentService
    from finch.ideas.service import IdeaService

    if from_idea and note:
        typer.echo("pass only one of --from-idea / --note")
        raise typer.Exit(code=1)

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())

    job: ContentJob | None = None
    if from_idea:
        job = ContentJobRepository(ws).get_job(from_idea)
        if job is None:
            typer.echo(f"idea not found: {from_idea}")
            raise typer.Exit(code=1)
    elif note:
        try:
            idea = FragmentService(runner).from_text(note)
            job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
            job = job.model_copy(update={"raw_user_text": note})
            ContentJobRepository(ws).upsert_job(job)
        except (RuntimeError, StructuredOutputError, ValueError) as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc

    post = fetch_post_by_url(
        input_url, opencli=OpenCliClient(), reddit_opencli=RedditOpenCliClient(), topic=topic
    )
    if post is None:
        typer.echo(f"could not fetch post: {input_url}")
        raise typer.Exit(code=1)

    if job is not None:
        ok, reason = assess_job_contribution(post, job)
        if not ok:
            payload = {"status": "no_reply", "reason": reason, "url": input_url}
            if as_json:
                typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                typer.echo(f"暂不回复：{reason}")
            return
    else:
        reason = "无个人素材：仅允许提问式提纲，不得声称亲历"

    engagement = settings.engagement
    scored = score_posts(runner, [post], engagement.weights, relationship_by_peer={})
    ranked = rank_candidates(scored, min_candidate_score=engagement.min_candidate_score)
    if not ranked:
        # Manual URL path: still allow outline when contribution check passed.
        from finch.engagement.models import ConversationScore

        ranked = [
            ScoredPost(
                post=post,
                score=ConversationScore(
                    relevance=0.8,
                    novelty=0.8,
                    discussability=0.8,
                    practical_evidence=0.7,
                    relationship_value=0.5,
                    total=0.78,
                    reasons=[reason],
                ),
            )
        ]

    basis = [job.id] if job is not None else list(settings.interests.practice_refs)
    repo = InteractionRepository(ws)
    candidates = generate_proposals(
        runner,
        ranked,
        engagement,
        contribution_basis_refs=basis,
        full_draft=full_draft,
        facts=job.facts if job else None,
        core_message=job.core_message if job else "",
        interpretation=job.interpretation if job else "",
        job=job,
    )
    if not candidates:
        msg = "暂不回复：模型未产出可用提纲，且缺少可贡献增量"
        if as_json:
            typer.echo(json.dumps(
                {"status": "no_reply", "reason": msg, "url": input_url},
                ensure_ascii=False,
                indent=2,
            ))
        else:
            typer.echo(msg)
        return

    candidate = candidates[0]
    body = (candidate.draft or candidate.outline or "")
    blocks = ready_gate_blocks(body=body, job=job)
    if blocks and "secret_detected" in blocks:
        if as_json:
            typer.echo(json.dumps(
                {"status": "blocked", "reasons": blocks, "url": input_url},
                ensure_ascii=False,
                indent=2,
            ))
        else:
            typer.echo(f"暂不回复：敏感内容不得进入可发布状态（{', '.join(blocks)}）")
        return

    # Idempotent: same generation_key returns existing proposal.
    if candidate.generation_key:
        existing = repo.find_by_generation_key(candidate.generation_key)
        if existing is not None:
            candidate = existing
        else:
            repo.upsert(candidate, run_id="create")
    else:
        repo.upsert(candidate, run_id="create")

    if as_json:
        typer.echo(candidate.model_dump_json(indent=2))
    else:
        typer.echo(_render_proposal_card(candidate))


@connect_app.command("with")
def connect_with(
    x: str | None = typer.Option(None, "--x", help="X handle 或 URL"),
    github: str | None = typer.Option(None, "--github", help="GitHub handle 或 URL"),
    from_idea: str | None = typer.Option(None, "--from-idea", help="已有 idea / ContentJob id"),
    note: str | None = typer.Option(None, "--note", help="个人笔记原文"),
    full_draft: bool = typer.Option(False, "--draft", help="生成完整回复草稿"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """点名一个人：落 PeerProfile 并准备一条提纲（跳过今日浏览）。"""
    from finch.ideas.fragment_service import FragmentService
    from finch.ideas.service import IdeaService

    if from_idea and note:
        typer.echo("pass only one of --from-idea / --note")
        raise typer.Exit(code=1)
    if bool(x) == bool(github):
        typer.echo("pass exactly one of --x / --github")
        raise typer.Exit(code=1)

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())

    job: ContentJob | None = None
    if from_idea:
        job = ContentJobRepository(ws).get_job(from_idea)
        if job is None:
            typer.echo(f"idea not found: {from_idea}")
            raise typer.Exit(code=1)
    elif note:
        try:
            idea = FragmentService(runner).from_text(note)
            job = IdeaService(ContentJobRepository(ws)).create_candidate(idea)
            job = job.model_copy(update={"raw_user_text": note})
            ContentJobRepository(ws).upsert_job(job)
        except (RuntimeError, StructuredOutputError, ValueError) as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc

    try:
        target = parse_named_target("x" if x else "github", x or github or "")
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=runner,
        job=job,
        full_draft=full_draft,
    )

    if result.status == "failed":
        typer.echo(result.message)
        raise typer.Exit(code=1)

    if result.status in {"no_reply", "blocked"}:
        if as_json:
            typer.echo(
                json.dumps(
                    {"status": result.status, "message": result.message},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(result.message)
        return

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "status": result.status,
                    "message": result.message,
                    "peer_id": result.peer.id if result.peer else None,
                    "opportunity_id": (result.opportunity.id if result.opportunity else None),
                    "proposal": (
                        result.proposal.model_dump(mode="json") if result.proposal else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    source = (
        f"来源：X @{target.handle}" if target.platform == "x" else f"来源：GitHub {target.handle}"
    )
    typer.echo(source)
    if result.proposal is not None:
        typer.echo(_render_proposal_card(result.proposal))


@connect_app.command("approve")
def connect_approve(proposal_id: str = typer.Argument(..., help="proposal id")) -> None:
    """批准提案（PROPOSED→APPROVED，幂等；批准只创建发布意图，不等于已发布）。"""
    from finch.engagement.proposals import ready_gate_blocks

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = InteractionRepository(ws)
    proposal = repo.get(proposal_id)
    if proposal is None:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1)
    body = proposal.revised_draft or proposal.draft or proposal.outline or ""
    job = None
    for ref in proposal.contribution_basis_refs:
        job = ContentJobRepository(ws).get_job(ref)
        if job is not None:
            break
    blocks = ready_gate_blocks(body=body, job=job)
    if blocks:
        typer.echo(f"cannot approve: {', '.join(blocks)}")
        raise typer.Exit(code=1)
    try:
        repo.approve(proposal_id)
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
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        InteractionRepository(ws).reject(proposal_id, reason)
    except KeyError:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1) from None
    typer.echo(f"rejected {proposal_id}")


@connect_app.command("edit")
def connect_edit(
    proposal_id: str = typer.Argument(..., help="proposal id"),
    path: str = typer.Option(..., "--file", help="人工修订后的草稿文件"),
) -> None:
    """保存人工修订草稿；修改正文使旧批准失效（回到 PROPOSED，revision+1）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = InteractionRepository(ws)
    if repo.get(proposal_id) is None:
        typer.echo(f"proposal not found: {proposal_id}")
        raise typer.Exit(code=1)
    try:
        revised = Path(path).read_text()
    except OSError as exc:
        typer.echo(f"cannot read file: {exc}")
        raise typer.Exit(code=1) from exc
    repo.edit(proposal_id, revised)
    typer.echo(f"edited {proposal_id} (approval invalidated if previously approved)")

@connect_app.command("record")
def connect_record(
    proposal_id: str = typer.Argument(..., help="proposal id"),
    url: str = typer.Option(..., "--url", help="实际发布/互动的 URL"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """记录一次真实互动为 InteractionRecord（需先批准；同一 proposal 幂等，不重复计两次）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    proposal = InteractionRepository(ws).get(proposal_id)
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
    InteractionRecordRepository(ws).upsert(record)
    if proposal.peer_id:
        topic = (
            proposal.post.matched_topics[0]
            if proposal.post.matched_topics
            else (proposal.contribution_type.value if proposal.contribution_type else "general")
        )
        svc = ConversationService()
        opened = svc.open_thread(peer_id=proposal.peer_id, topic=topic)
        thread = ConversationThreadRepository(ws).get(opened.id) or opened
        thread = svc.append_interaction(thread, record.id, occurred_at=record.occurred_at)
        ConversationThreadRepository(ws).upsert(thread)
    if as_json:
        typer.echo(record.model_dump_json(indent=2))
    else:
        typer.echo(f"recorded {record.id}")


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


if __name__ == "__main__":
    app()
