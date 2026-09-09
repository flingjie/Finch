# Connection-First Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining connection-first gaps — today-focus projection, `signals-to-idea` source, field alignment, and cleanup — per the spec at `docs/superpowers/specs/2026-09-09-connection-first-gap-closure-design.md`.

**Architecture:** Deterministic Python domain logic does all state, sorting, truncation, and idempotency; LLM (via `CodexRunner` subprocess) only produces semantic candidates. No Graph Runtime, no storage-layer change.

**Tech Stack:** Python 3.12, Pydantic 2 (`StrEnum`/`Literal`/`Field`/`field_validator`), Typer CLI, pytest, ruff (`E,F,I,B,UP`, line-length 100), mypy.

## Global Constraints

- Run tests with `uv run pytest`, lint with `uv run ruff check .`, type-check with `uv run mypy src`.
- Domain services stay deterministic and single-threaded; no `asyncio.gather`; subprocess args as arrays.
- LLM output never carries a `total`; weighted totals are computed in Python only.
- Public replies/quotes remain human-approved; nothing in this plan auto-publishes.
- `origin` is a traceability label only — it drives no gating logic, only CLI display.
- Conventional commit messages matching the repo (`feat(...)`, `refactor(...)`, `test(...)`, `docs(...)`, `chore(...)`).

---

### Task 1: Rename `Idea.origin` enum to three values with legacy read-compat

**Files:**
- Modify: `src/finch/content/jobs.py:8-61` (define `IdeaOrigin`, update `ContentJob.origin`, add validator)
- Modify: `src/finch/ideas/models.py:20-49` (import `IdeaOrigin`, update `IdeaCandidate.origin`)
- Modify: `src/finch/ideas/fragment_service.py:75` (`_to_candidate` origin param type)
- Modify: `src/finch/ideas/fragment_service.py:130` (`from_text` → `origin="practice"`)
- Modify: `src/finch/ideas/commit_service.py:112` (`origin="commit"` → `origin="practice"`)
- Test: `tests/unit/test_jobs.py`, `tests/unit/test_fragment_service.py`, `tests/unit/test_commit_service.py`, `tests/unit/test_ideas_models.py`, `tests/unit/test_idea_contract.py`, `tests/unit/test_ideas_service.py`, `tests/unit/test_cli_ideas.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `IdeaOrigin = Literal["practice", "conversation", "synthesis"]` (exported from `finch.content.jobs`); `ContentJob.origin: IdeaOrigin | None` with a legacy before-validator; `IdeaCandidate.origin: IdeaOrigin`.

- [ ] **Step 1: Write the failing test — legacy values normalize on read**

Append to `tests/unit/test_jobs.py`:

```python
def test_origin_legacy_values_normalize():
    from finch.content.jobs import ContentJob

    job = ContentJob(
        id="idea_x", source_card_ids=[], reader_problem="r",
        recommended_format="short_post", status="proposed", origin="commit",
    )
    assert job.origin == "practice"
    assert ContentJob(
        id="idea_y", source_card_ids=[], reader_problem="r",
        recommended_format="short_post", status="proposed", origin="user",
    ).origin == "practice"
    assert ContentJob(
        id="idea_z", source_card_ids=[], reader_problem="r",
        recommended_format="short_post", status="proposed", origin="search",
    ).origin == "synthesis"
    assert ContentJob(
        id="idea_w", source_card_ids=[], reader_problem="r",
        recommended_format="short_post", status="proposed", origin="conversation",
    ).origin == "conversation"


def test_origin_rejects_unknown_value():
    import pytest

    from finch.content.jobs import ContentJob

    with pytest.raises(Exception):
        ContentJob(
            id="idea_x", source_card_ids=[], reader_problem="r",
            recommended_format="short_post", status="proposed", origin="llm",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_jobs.py -v`
Expected: FAIL — `origin="commit"` is not in `Literal["practice", "conversation", "synthesis"]`, and there is no validator yet.

- [ ] **Step 3: Implement the enum rename**

In `src/finch/content/jobs.py`, replace the import line `from pydantic import BaseModel` with `from pydantic import BaseModel, field_validator`, add the type alias and legacy map just above `class ContentJob`, and change the `origin` field + add the validator.

```python
# 在 ContentJob 上方新增
IdeaOrigin = Literal["practice", "conversation", "synthesis"]

# 旧工作区中已存的 legacy origin 值 → 新枚举（读取时归一化，避免校验崩溃）。
_LEGACY_ORIGIN = {"commit": "practice", "user": "practice", "search": "synthesis"}
```

Inside `class ContentJob`, change:

```python
    origin: Literal["commit", "search", "user", "conversation"] | None = None
```

to:

```python
    origin: IdeaOrigin | None = None
```

and add the validator as the last method of the class:

```python
    @field_validator("origin", mode="before")
    @classmethod
    def _normalize_legacy_origin(cls, v):
        if isinstance(v, str) and v in _LEGACY_ORIGIN:
            return _LEGACY_ORIGIN[v]
        return v
```

In `src/finch/ideas/models.py`, change the import:

```python
from finch.content.jobs import AuthorPosition, CommunicationGoal
```

to:

```python
from finch.content.jobs import AuthorPosition, CommunicationGoal, IdeaOrigin
```

and change the field:

```python
    origin: Literal["commit", "search", "user", "conversation"]
```

to:

```python
    origin: IdeaOrigin
```

In `src/finch/ideas/fragment_service.py`, change the `_to_candidate` signature's `origin` type from `Literal["commit", "search", "user", "conversation"]` to `IdeaOrigin` (add `IdeaOrigin` to the `from finch.content.jobs import ...` line), change `from_text`'s `_to_candidate(..., origin="user", ...)` to `origin="practice"`.

In `src/finch/ideas/commit_service.py`, change `origin="commit"` to `origin="practice"`.

- [ ] **Step 4: Run tests to verify the new enum test passes**

Run: `uv run pytest tests/unit/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Update the remaining tests that assert old origin values**

In `tests/unit/test_fragment_service.py:40`, change `assert idea.origin == "user"` to `assert idea.origin == "practice"`.

In `tests/unit/test_commit_service.py:92`, change `assert idea.origin == "commit"` to `assert idea.origin == "practice"`.

In `tests/unit/test_ideas_models.py:19`, change `origin="commit"` to `origin="practice"`; in the `test_idea_candidate_origin_rejects_invalid` helper at line 64-66, the invalid value `"llm"` stays (it is still invalid); at line 85 change `origin="user"` to `origin="practice"`.

In `tests/unit/test_idea_contract.py`, change `origin="user"` (line 17) → `origin="practice"`, `origin="commit"` (line 48) → `origin="practice"`, and `assert job.origin == "user"` (line 71) → `assert job.origin == "practice"`.

In `tests/unit/test_ideas_service.py`, change `origin="commit"` (line 38) → `origin="practice"` and `assert job.origin == "commit"` (line 97) → `assert job.origin == "practice"`.

In `tests/unit/test_cli_ideas.py`, change `origin="commit"` (in the `_candidate()` helper) → `origin="practice"`.

- [ ] **Step 6: Run full test + lint + type-check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(ideas): collapse origin enum to practice/conversation/synthesis"
```

---

### Task 2: Add `PeerProfile.possible_next_actions` and `InteractionRecord.follow_up_at`

**Files:**
- Modify: `src/finch/peers/models.py:32-45` (`PeerProfile`)
- Modify: `src/finch/engagement/models.py:106-124` (`InteractionRecord`)
- Test: `tests/unit/test_peer_models.py`, `tests/unit/test_engagement_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PeerProfile.possible_next_actions: list[str]` (default `[]`); `InteractionRecord.follow_up_at: datetime | None` (default `None`).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_peer_models.py`:

```python
def test_peer_profile_possible_next_actions_defaults_empty():
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
    )
    assert profile.possible_next_actions == []
```

Append to `tests/unit/test_engagement_models.py`:

```python
from datetime import datetime


def test_interaction_record_follow_up_at_defaults_none():
    rec = InteractionRecord(
        id="rec_1", proposal_id="p1", peer_id="peer_abc", platform="x",
        source_url="https://x.com/a/1", occurred_at=datetime(2026, 9, 1),
    )
    assert rec.follow_up_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_peer_models.py tests/unit/test_engagement_models.py -v`
Expected: FAIL — `possible_next_actions` / `follow_up_at` are unexpected keyword arguments.

- [ ] **Step 3: Implement the fields**

In `src/finch/peers/models.py`, inside `PeerProfile`, add after `next_context`:

```python
    possible_next_actions: list[str] = Field(default_factory=list)
```

In `src/finch/engagement/models.py`, inside `InteractionRecord`, add after `follow_up_status`:

```python
    follow_up_at: datetime | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_peer_models.py tests/unit/test_engagement_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(models): add possible_next_actions and follow_up_at"
```

---

### Task 3: Add deterministic today-focus projection and wire into `finch connect daily`

**Files:**
- Modify: `src/finch/projections.py` (add `build_today_focus`)
- Modify: `src/finch/cli.py:1135-1203` (`_render_daily` + `connect_daily`)
- Test: `tests/unit/test_projections.py`

**Interfaces:**
- Consumes: `RankedPeer` (from `finch.engagement.flow`), `InteractionProposal`/`InteractionStatus` (from `finch.engagement.models`), `ConversationThread` (from `finch.conversations.models`), `ContentJob` (from `finch.content.jobs`).
- Produces: `build_today_focus(*, peers, contributions, threads, ideas, now=None) -> dict[str, dict]`, where each section is `{"items": list, "total": int}`. Keys: `conversations`, `peers`, `contributions`, `ideas`. Truncation limits: 2 / 3 / 3 / 1.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_projections.py`:

```python
from datetime import UTC, datetime, timedelta

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.conversations.models import ConversationThread
from finch.engagement.flow import RankedPeer
from finch.engagement.models import InteractionAction, InteractionProposal
from finch.engagement.relationship import PeerValue
from finch.projections import build_today_focus


def _peer_value(total):
    return PeerValue(
        topic_overlap=0.5, practical_depth=0.5, contribution_space=0.5,
        continuity_potential=0.5, repetition_penalty=0.0, promotion_risk=0.0,
        total=total, reasons=[],
    )


def test_build_today_focus_ranks_and_truncates():
    now = datetime(2026, 9, 9, tzinfo=UTC)
    old = ConversationThread(
        id="thread_old", peer_id="p", topic="old",
        open_questions=["q"], last_activity_at=now - timedelta(days=30),
    )
    recent = ConversationThread(
        id="thread_recent", peer_id="p", topic="recent", last_activity_at=now,
    )
    never = ConversationThread(id="thread_never", peer_id="p", topic="never")

    p1 = RankedPeer(profile=PeerProfile(id="p1", platform_identities=[]), value=_peer_value(0.9))
    p2 = RankedPeer(profile=PeerProfile(id="p2", platform_identities=[]), value=_peer_value(0.5))
    p3 = RankedPeer(profile=PeerProfile(id="p3", platform_identities=[]), value=_peer_value(0.7))
    p4 = RankedPeer(profile=PeerProfile(id="p4", platform_identities=[]), value=_peer_value(0.1))

    focus = build_today_focus(
        peers=[p1, p2, p3, p4],
        contributions=[],
        threads=[recent, old, never],
        ideas=[],
        now=now,
    )
    # 对话：超期最久优先（never → old → recent），截断到 2
    assert [t.id for t in focus["conversations"]["items"]] == ["thread_never", "thread_old"]
    assert focus["conversations"]["total"] == 3
    # 同行：peer_value 降序，截断到 3
    assert [rp.profile.id for rp in focus["peers"]["items"]] == ["p1", "p3", "p2"]
    assert focus["peers"]["total"] == 4
```

Note: the test imports `PeerProfile`, `PlatformIdentity`, `PeerValue`, `RankedPeer` — add these to the existing imports at the top of `tests/unit/test_projections.py` (which already imports `PeerProfile`, `PlatformIdentity`, `ConversationScore`, `ExternalPost`, `InteractionAction`, `InteractionProposal`, `InteractionStatus`, `ContentJob`, `ContentJobStatus`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_projections.py::test_build_today_focus_ranks_and_truncates -v`
Expected: FAIL — `build_today_focus` not defined.

- [ ] **Step 3: Implement `build_today_focus`**

In `src/finch/projections.py`, add the type imports at the top (extend the existing imports):

```python
from finch.conversations.models import ConversationThread
from finch.engagement.flow import RankedPeer
from finch.engagement.models import InteractionProposal
```

then add the function (after `build_pending_actions`):

```python
def _thread_overdue_key(thread: ConversationThread, now: datetime) -> tuple:
    if thread.last_activity_at is None:
        age = 10**9  # 从未互动 → 最需跟进
    else:
        age = (now - thread.last_activity_at).days
    return (-age, -len(thread.open_questions), thread.id)


def _position_complete(job: ContentJob) -> bool:
    position = job.author_position
    return position is not None and bool(position.decision) and bool(position.tradeoff)


def build_today_focus(
    *,
    peers: list[RankedPeer],
    contributions: list[InteractionProposal],
    threads: list[ConversationThread],
    ideas: list[ContentJob],
    now: datetime | None = None,
) -> dict[str, dict]:
    """确定性「今日聚焦」投影：四段排序 + top-N 截断（纯 Python，无 LLM）。"""
    now = now or datetime.now(UTC)
    conv_sorted = sorted(threads, key=lambda t: _thread_overdue_key(t, now))
    peer_sorted = sorted(peers, key=lambda rp: (-rp.value.total, rp.profile.id))
    contrib_sorted = sorted(contributions, key=lambda c: (-c.score.total, c.id))
    idea_sorted = sorted(ideas, key=lambda j: (not _position_complete(j), j.id))
    return {
        "conversations": {"items": conv_sorted[:2], "total": len(conv_sorted)},
        "peers": {"items": peer_sorted[:3], "total": len(peer_sorted)},
        "contributions": {"items": contrib_sorted[:3], "total": len(contrib_sorted)},
        "ideas": {"items": idea_sorted[:1], "total": len(idea_sorted)},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_projections.py::test_build_today_focus_ranks_and_truncates -v`
Expected: PASS.

- [ ] **Step 5: Wire into `finch connect daily`**

In `src/finch/cli.py`, change the projections import at line 47:

```python
from .projections import build_daily_context, build_pending_actions
```

to:

```python
from .projections import build_daily_context, build_pending_actions, build_today_focus
```

Replace `_render_daily` (lines 1135-1165) with:

```python
def _render_daily(focus: dict) -> str:
    def _section(title, section):
        items = section["items"]
        lines = [f"## {title}"]
        if not items:
            lines.append("- (none)")
        for i in items:
            if hasattr(i, "topic"):
                lines.append(f"- {i.id}\t{i.topic}\t{i.status.value}")
            elif hasattr(i, "value"):
                lines.append(f"- {i.profile.display_name or i.profile.id}\tpeer_value={i.value.total:.2f}")
            elif hasattr(i, "post"):
                snippet = " ".join(i.post.content.split())[:60]
                lines.append(f"- {i.id}\t[{i.action.value}]\t{snippet}")
            else:
                lines.append(f"- {i.id}\t{i.core_message}")
        if items and section["total"] > len(items):
            lines.append(f"… 还有 {section['total'] - len(items)} 个")
        return "\n".join(lines)

    return "\n\n".join([
        _section("需要继续的对话", focus["conversations"]),
        _section("今天最值得连接的同行", focus["peers"]),
        _section("可贡献的具体内容", focus["contributions"]),
        _section("从近期交流产生的观点候选", focus["ideas"]),
    ])
```

In `connect_daily` (lines 1168-1203), replace the final `typer.echo(_render_daily(needs_follow_up, result.peers, result.candidates, idea_candidates))` with:

```python
    focus = build_today_focus(
        peers=result.peers,
        contributions=result.candidates,
        threads=needs_follow_up,
        ideas=idea_candidates,
        now=now,
    )
    typer.echo(_render_daily(focus))
```

(The `--json` branch stays as-is — it already emits full lists.)

- [ ] **Step 6: Run full test + lint + type-check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green. (If `_render_daily`'s `hasattr` dispatch trips mypy, narrow with explicit `isinstance` against the four types — `ConversationThread`, `RankedPeer`, `InteractionProposal`, `ContentJob` — instead of `hasattr`.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(context): deterministic today-focus projection for connect daily"
```

---

### Task 4: Add `signals-to-idea` (community signal aggregation source)

**Files:**
- Modify: `src/finch/ideas/fragment_service.py` (add `_SIGNALS_PROMPT` + `from_signals`)
- Modify: `src/finch/cli.py` (add `ideas_signals` command on `ideas_app`)
- Modify: `skills/idea-discovery/SKILL.md` (4th source)
- Create: `skills/idea-discovery/references/signals-signals.md`
- Test: `tests/unit/test_fragment_service.py`, `tests/unit/test_cli_ideas.py`

**Interfaces:**
- Consumes: `PeerProfile` (from `finch.peers.models`), `ConversationThread` (from `finch.conversations.models`), `SourceRef` (from `finch.ideas.models`), `IdeaDraftOutput`, `_to_candidate` (same module).
- Produces: `FragmentService.from_signals(*, peers=None, threads=None) -> IdeaCandidate | None` with `origin="synthesis"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_fragment_service.py` (which already imports `ConversationThread`, `FragmentService`, `_out`):

```python
from finch.peers.models import PeerProfile, PlatformIdentity


def test_from_signals_returns_none_without_tension():
    svc = FragmentService(FakeRunner(_out()))
    # 只有共同主题、没有未解问题/分歧 → 无值得综合的张力
    peers = [PeerProfile(
        id="p1", platform_identities=[PlatformIdentity(platform="x", author_id="a")],
        shared_topics=["agent evals"],
    )]
    assert svc.from_signals(peers=peers, threads=[]) is None


def test_from_signals_synthesizes_with_origin_synthesis():
    thread = ConversationThread(
        id="thread_1", peer_id="p", topic="agent evals",
        open_questions=["how to reproduce flaky evals?"],
        disagreements=["replays are enough"],
    )
    peer = PeerProfile(
        id="p1", platform_identities=[PlatformIdentity(platform="x", author_id="a", url="https://x.com/a/1")],
        shared_topics=["agent evals"],
    )
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_signals(peers=[peer], threads=[thread])
    assert idea is not None
    assert idea.origin == "synthesis"
    assert ("conversation", "thread_1") in {(r.type, r.ref) for r in idea.source_refs}
    assert ("post", "https://x.com/a/1") in {(r.type, r.ref) for r in idea.source_refs}


def test_from_signals_returns_none_when_llm_says_no_idea():
    empty = _out().model_copy(update={"core_point": ""})
    thread = ConversationThread(
        id="thread_1", peer_id="p", topic="t", open_questions=["q"],
    )
    svc = FragmentService(FakeRunner(empty))
    assert svc.from_signals(peers=[], threads=[thread]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fragment_service.py::test_from_signals_synthesizes_with_origin_synthesis -v`
Expected: FAIL — `FragmentService` has no attribute `from_signals`.

- [ ] **Step 3: Implement `from_signals`**

In `src/finch/ideas/fragment_service.py`, add `PeerProfile` to imports (`from finch.peers.models import PeerProfile`), and add the prompt + method. Insert the prompt string next to `_THREAD_PROMPT`:

```python
_SIGNALS_PROMPT = """\
You aggregate recurring community signals (peers' shared topics + open conversation
questions/disagreements) into a single publishable engineering Idea candidate, or decline.

Rules:
- Synthesize only a real engineering decision, recurring problem, or unresolved tension
  worth developing. News, hype, mechanical changes, or pure sentiment → decline by returning
  an empty core_point ("").
- Never invent personal experience. The user has NOT personally lived every signal; the
  candidate must remain a third-person, attributed synthesis (boundaries keep it hedged).
- author_position is always proposed, never confirmed.
- recommended_format: reply | quote | short_post | thread | dm | do_not_publish.

## Signals

{signal_text}

Return JSON matching the schema (core_point / observation / reader_problem / why_worth_saying /
intent / open_question / author_position / boundaries / recommended_format / communication_goal).
"""
```

Add the method to `FragmentService` (after `from_thread`):

```python
    def from_signals(
        self,
        *,
        peers: list[PeerProfile] | None = None,
        threads: list[ConversationThread] | None = None,
    ) -> IdeaCandidate | None:
        """聚合社区信号 → 一个 IdeaCandidate（origin=synthesis）；无张力或无价值 → None。

        社区信号 = 同行反复讨论的主题 + 对话线索里的未解问题/分歧。未解问题/分歧是
        必要的「张力」信号：只有共同主题、没有张力时直接返回 None，不调用 LLM。
        """
        peers = peers or []
        threads = threads or []

        signal_lines: list[str] = []
        source_refs: list[SourceRef] = []
        for p in peers:
            topics = [t for t in (p.shared_topics or p.current_interests) if t]
            if not topics:
                continue
            signal_lines.append(f"peer {p.display_name or p.id}: {'; '.join(topics)}")
            for ident in p.platform_identities:
                if ident.url:
                    source_refs.append(SourceRef(type="post", ref=ident.url, summary=p.display_name))

        has_tension = False
        for t in threads:
            if t.open_questions:
                has_tension = True
                signal_lines.append(f"open question [{t.topic}]: {t.open_questions[0]}")
            if t.disagreements:
                has_tension = True
                signal_lines.append(f"disagreement [{t.topic}]: {t.disagreements[0]}")
            if t.open_questions or t.disagreements:
                source_refs.append(SourceRef(type="conversation", ref=t.id, summary=t.topic))

        if not has_tension:
            return None

        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _SIGNALS_PROMPT.format(signal_text="\n".join(signal_lines)),
                IdeaDraftOutput,
            ),
        )
        if not (out.core_point or "").strip():
            return None
        return _to_candidate(out, origin="synthesis", source_refs=source_refs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fragment_service.py -v`
Expected: PASS.

- [ ] **Step 5: Add the `finch ideas signals` command**

In `src/finch/cli.py`, add the command right after `ideas_create` (after line 380):

```python
@ideas_app.command("signals")
def ideas_signals(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """聚合社区信号（同行共同主题 + 未解问题/分歧）为一个 idea 候选并落库。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    idea = service.from_signals(
        peers=PeerRepository(ws).list_all(),
        threads=ConversationThreadRepository(ws).list_all(),
    )
    if idea is None:
        typer.echo("no community signal worth an idea")
        raise typer.Exit(code=0)
    try:
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
```

- [ ] **Step 6: Write the skill reference + update SKILL.md**

Create `skills/idea-discovery/references/signals-signals.md`:

```markdown
# 社区信号 → Idea 判据

`signals` 来源聚合多源社区信号，判断是否值得综合成一个观点。与 commit / fragment /
conversation 共用同一份「读者值得知道」的判断。

## 输入信号

- `PeerProfile.shared_topics` / `current_interests`：同行反复讨论的主题。
- `ConversationThread.open_questions` / `disagreements`：未解问题与分歧（必要张力）。

## 判据

**值得综合**（产出 `origin=synthesis`）：

- 多位同行反复讨论同一问题，但存在分歧或未解机制。
- 一个具体的未解问题/分歧，用户有实践视角可补充。

**跳过**（返回空）：

- 只有共同主题、没有未解问题/分歧（无张力，不调用 LLM）。
- 新闻、融资、纯情绪、机械变化、无明确结论的噪音。
- 无法归属到具体同行或对话的泛泛「行业趋势」。

## 边界

- 外部信号 ≠ 个人证据：`author_position.status` 一律 `proposed`，不得写成亲历。
- 不生成草稿（→ `idea-to-draft` / `expression-practice`）。
- 只读，不自动发布。
```

Update `skills/idea-discovery/SKILL.md`: in the frontmatter `description`, add signals to the source list; in the "## 三种来源" section (rename to "## 四种来源") add:

```markdown
- **signals 来源**：`finch ideas signals`，见 `references/signals-signals.md`。
```

and add `references/signals-signals.md` to the "## 参考" list.

- [ ] **Step 7: Run full test + lint + type-check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(ideas): add signals source (community signal aggregation)"
```

---

### Task 5: Clean up stale content-first docstrings and dead references

**Files:**
- Modify: `src/finch/__init__.py:1`
- Modify: `src/finch/content/critic.py:1-4`
- Modify: `skills/expression-practice/SKILL.md:16`
- Modify: `prompts/critique-draft.md:15`

**Interfaces:** none — doc-only changes.

- [ ] **Step 1: Apply the docstring/reference edits**

`src/finch/__init__.py`, change:

```python
"""Finch: evidence-driven builder companion."""
```

to:

```python
"""Finch: peer-connection and personal-expression system."""
```

`src/finch/content/critic.py`, change the module docstring:

```python
"""Draft 语义审查（contract C5）：六维打分 + 三个语义 flag + 蕴含判定。

同时承载 Critic Suite 默认检查器套件（``default_checker_suite``）与并行执行器
（``_run_checks``），供 graph 的 write 节点与 idea 服务复用。
"""
```

to:

```python
"""Draft 语义审查（contract C5）：六维打分 + 三个语义 flag + 蕴含判定。

同时承载 Critic Suite 默认检查器套件（``default_checker_suite``）与并行执行器
（``_run_checks``），供 idea-to-draft 与 idea 服务复用。
"""
```

`skills/expression-practice/SKILL.md:16`, change:

```text
1. 选 Idea（`finch ideas list` 挑选，或关联一个 Opportunity）。
```

to:

```text
1. 选 Idea（`finch ideas list` 挑选）。
```

`prompts/critique-draft.md:15`, change:

```text
  - voice: consistency with the builder's voice (measured, evidence-first, no hype).
```

to:

```text
  - voice: consistency with the user's voice (measured, evidence-first, no hype).
```

- [ ] **Step 2: Verify nothing broke**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all green (these are doc-only edits; pytest guards against accidental breakage).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs: drop stale builder/graph framing from docstrings and prompts"
```

---

## Self-Review Notes

- **Spec coverage:** Design 1 → Task 3; Design 2 → Task 4; Design 3.1 (origin) → Task 1; Design 3.2/3.3 (fields) → Task 2; Design 4 (cleanup) → Task 5. All sections covered.
- **Type consistency:** `IdeaOrigin` defined once in `content/jobs.py` (Task 1), imported by `ideas/models.py` and `fragment_service.py`; `build_today_focus` keys (`conversations`/`peers`/`contributions`/`ideas`) are consumed verbatim by `_render_daily` (Task 3); `from_signals` signature `(peers=None, threads=None)` matches the CLI call (Task 4).
- **Deferred scope (documented):** commit evidence aggregation is already served by `finch ideas commit` (Task 4 note), so `from_signals` focuses on community signals (peers + threads) and does not re-aggregate commits. Twitter search aggregation is deferred as a follow-up; the `signals` source is valuable without it.
