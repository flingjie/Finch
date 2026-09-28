# community-scout 智能交互改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `community-scout` from a fixed "3 cards / 7 fields / 4-dim score" weekly report into three entry modes (weekly / question / revisit) with observe-vs-actionable layering, deep-read-on-selection, and a feedback loop that changes the next recommendation — all backed by backward-compatible model fields and deduped CLI reads.

**Architecture:** Skill + deterministic Python domain services (unchanged pattern). Search, judgment, layering, and presentation stay in the Skill (LLM); deterministic persistence, identity, dedup, and validation stay in Python (`communities/`). `finch community discover` is renamed `context` (snapshot only); search + card-saving becomes explicitly the Skill's job. New model fields are all optional/defaulted so old JSONL/YAML keep loading.

**Tech Stack:** Python 3.12+, Pydantic 2 (`StrEnum`/`Field`/`BaseModel`), Typer CLI, file Workspace (YAML/JSONL atomic write). No new dependencies.

## Global Constraints

- Python 3.12+; Pydantic 2 models; `ruff` selects `E,F,I,B,UP`; line length 100.
- All new fields optional/defaulted; old cards/feedback must load via `model_validate` with defaults.
- `CommunityResult` six values (`ignored/saved/joined/interacted/repeated/contributed`) unchanged; `observe`/`actionable` live in a **new** `RecommendationState` enum, never in `result`.
- `id` stays name-hash (`community_id_for`); `canonical_url` is only a cross-week dedup key, never rewrites history.
- Single-user serial write; no new database, no `asyncio.gather`.
- Verify with `uv run pytest`, `uv run ruff check .`, `uv run mypy src`.
- Domain services deterministic and single-threaded; subprocess args as arrays (no shell concat).

---

## File Map

- `src/finch/communities/models.py` — add `RecommendationState`, `identity_key`, new fields on `CommunityProfile`/`EntryPoint`/`CommunityFeedback`.
- `src/finch/communities/repository.py` — add `list_latest_profiles()` and `feedback_for()`.
- `src/finch/communities/service.py` — add `CommunityNotFoundError`; validate existence in `record_feedback` + new params; add `feedback_for()` delegator.
- `src/finch/cli.py` — rename `discover`→`context`; `list` dedup + `--all`; `inspect --json` shape; `feedback` validation + `--ref`/`--ref-kind`/`--reason-kind`.
- `tests/unit/test_communities.py` — model/repo/service tests.
- `tests/unit/test_cli_community.py` — CLI tests.
- `skills/community-scout/SKILL.md`, `references/{scoring-rubric,presentation,community-card-schema}.md` — Skill rewrite.

---

### Task 1: Model fields + `RecommendationState` + `identity_key`

**Files:**
- Modify: `src/finch/communities/models.py`
- Test: `tests/unit/test_communities.py`

**Interfaces:**
- Produces: `RecommendationState` (`OBSERVE="observe"`, `ACTIONABLE="actionable"`); `identity_key(profile: CommunityProfile) -> str`; `CommunityProfile.canonical_url/recommendation_state/intent/question/practice_refs/source_checked_at`; `EntryPoint.url/status`; `CommunityFeedback.reason_kind/interaction_ref/ref_kind`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_communities.py` and update its imports:

```python
from finch.communities.models import (
    CommunityProfile,
    CommunityResult,
    EntryPoint,
    RecommendationState,
    community_id_for,
    identity_key,
)
```

Add these tests (also add `import pytest` at the top — Task 3 will use it, but harmless to add now):

```python
def test_old_profile_loads_with_defaults():
    data = {"id": "comm_x", "name": "Temporal Community", "fit_score": 80}
    p = CommunityProfile.model_validate(data)
    assert p.canonical_url == ""
    assert p.recommendation_state is None
    assert p.intent == "" and p.question == "" and p.practice_refs == []
    assert p.source_checked_at is None


def test_new_profile_fields_roundtrip():
    p = CommunityProfile(
        name="Temporal",
        canonical_url="https://temporal.io/community",
        recommendation_state=RecommendationState.ACTIONABLE,
        intent="question",
        question="failure replay",
        practice_refs=["FDE-Gym"],
        source_checked_at=datetime(2026, 9, 28, tzinfo=UTC),
        entry_point=EntryPoint(
            discussion="d", suggested_angle="a", url="https://x/1", status="open"
        ),
    )
    back = CommunityProfile.model_validate(p.model_dump(mode="json"))
    assert back.recommendation_state == RecommendationState.ACTIONABLE
    assert back.entry_point.url == "https://x/1"
    assert back.entry_point.status == "open"


def test_feedback_new_fields_default():
    fb = CommunityFeedback(community_id="comm_x", result=CommunityResult.INTERACTED)
    assert fb.reason_kind == "" and fb.interaction_ref == "" and fb.ref_kind == ""


def test_identity_key_uses_canonical_url_or_id():
    by_name = CommunityProfile(name="Temporal Community")
    assert identity_key(by_name) == by_name.id
    with_url = CommunityProfile(
        name="Temporal", canonical_url="https://temporal.io/community"
    )
    assert identity_key(with_url) == "https://temporal.io/community"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_communities.py -k "new_profile or old_profile or feedback_new or identity_key" -v`
Expected: FAIL — `ImportError` / `NameError` for `RecommendationState`, `EntryPoint`, `identity_key`.

- [ ] **Step 3: Add the enum and `identity_key`**

In `src/finch/communities/models.py`, after `class CommunityResult` (line 25) add:

```python
class RecommendationState(StrEnum):
    """Finch 建议的社区状态（与用户实际动作 CommunityResult 正交）。"""

    OBSERVE = "observe"
    ACTIONABLE = "actionable"
```

At the bottom, after `community_id_for` (line 102), add:

```python
def identity_key(profile: CommunityProfile) -> str:
    """跨周去重键：有规范 URL 用 URL，否则回退 name-hash id（不凭名称合并不同社区）。"""
    return profile.canonical_url or profile.id
```

- [ ] **Step 4: Add the new fields**

`EntryPoint` (line 43) becomes:

```python
class EntryPoint(BaseModel):
    """一个可切入的具体讨论 + 建议角度。"""

    discussion: str
    suggested_angle: str
    url: str = ""
    status: str = ""  # open / closed / unknown / 空
```

In `CommunityProfile`, insert after `first_contribution: FirstContribution | None = None` (line 71):

```python
    canonical_url: str = ""
    recommendation_state: RecommendationState | None = None
    intent: str = ""
    question: str = ""
    practice_refs: list[str] = Field(default_factory=list)
    source_checked_at: datetime | None = None
```

`CommunityFeedback` (line 78) becomes:

```python
class CommunityFeedback(BaseModel):
    """一次跟进反馈（append-only，永不覆盖）。"""

    community_id: str
    result: CommunityResult
    note: str = ""
    reason_kind: str = ""  # no_time / too_general / language_barrier / deep_but_later …
    interaction_ref: str = ""  # 真实互动链接；空=未提供
    ref_kind: str = ""  # public_url / user_stated
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_communities.py -v`
Expected: PASS (new + existing tests).

- [ ] **Step 6: Commit**

```bash
git add src/finch/communities/models.py tests/unit/test_communities.py
git commit -m "feat(communities): add recommendation state + optional identity/intent fields"
```

---

### Task 2: Repository — latest-projection dedup + feedback history

**Files:**
- Modify: `src/finch/communities/repository.py`
- Test: `tests/unit/test_communities.py`

**Interfaces:**
- Consumes: `identity_key` (Task 1).
- Produces: `CommunityRepository.list_latest_profiles() -> list[CommunityProfile]`; `CommunityRepository.feedback_for(community_id: str) -> list[CommunityFeedback]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_communities.py`:

```python
def test_list_latest_profiles_dedups_by_id(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    svc.save(_profile())
    svc.save(_profile())  # 同 name → 同 id，追加第二条
    assert len(svc.repo.list_latest_profiles()) == 1


def test_list_latest_profiles_dedups_by_canonical_url_across_names(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    a = svc.save(
        CommunityProfile(
            name="Temporal Community", canonical_url="https://temporal.io/community"
        )
    )
    b = svc.save(
        CommunityProfile(name="Temporal", canonical_url="https://temporal.io/community")
    )
    assert a.id != b.id  # 不同 name → 不同 id
    latest = svc.repo.list_latest_profiles()
    assert len(latest) == 1  # 但同 URL 去重
    assert latest[0].name == "Temporal"  # 取最后追加的一条


def test_feedback_for_returns_history(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    svc.record_feedback(saved.id, CommunityResult.JOINED)
    svc.record_feedback(saved.id, CommunityResult.INTERACTED)
    assert len(svc.repo.feedback_for(saved.id)) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_communities.py -k "list_latest_profiles or feedback_for" -v`
Expected: FAIL — `AttributeError: 'CommunityRepository' object has no attribute 'list_latest_profiles'`.

- [ ] **Step 3: Implement**

In `src/finch/communities/repository.py`, update the import (line 9) to include `identity_key`:

```python
from finch.communities.models import (
    CommunityContext,
    CommunityFeedback,
    CommunityProfile,
    identity_key,
)
```

After `get_candidate` (line 50) add:

```python
    def list_latest_profiles(self) -> list[CommunityProfile]:
        """每个 identity_key 一条最新投影（保持首次出现顺序，取最后追加的值）。"""
        out: dict[str, CommunityProfile] = {}
        for c in self.list_candidates():
            out[identity_key(c)] = c
        return list(out.values())
```

After `latest_feedback_by_id` (line 79) add:

```python
    def feedback_for(self, community_id: str) -> list[CommunityFeedback]:
        """某个社区的全部反馈（append 顺序），供回访读取。"""
        return [f for f in self.list_feedback() if f.community_id == community_id]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_communities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/repository.py tests/unit/test_communities.py
git commit -m "feat(communities): latest-projection dedup + feedback history"
```

---

### Task 3: Service — feedback existence validation + new params

**Files:**
- Modify: `src/finch/communities/service.py`
- Test: `tests/unit/test_communities.py`

**Interfaces:**
- Consumes: `CommunityProfile`/`CommunityFeedback` fields (Task 1).
- Produces: `CommunityNotFoundError`; `CommunityService.record_feedback(community_id, result, *, note="", reason_kind="", interaction_ref="", ref_kind="") -> CommunityFeedback` (raises `CommunityNotFoundError` on unknown id); `CommunityService.feedback_for(community_id) -> list[CommunityFeedback]`.

- [ ] **Step 1: Write the failing tests**

Add `import pytest` to the top of `tests/unit/test_communities.py`, then append:

```python
from finch.communities.service import CommunityNotFoundError  # add to existing import block


def test_record_feedback_unknown_community_raises(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    with pytest.raises(CommunityNotFoundError):
        svc.record_feedback("comm_missing", CommunityResult.JOINED)


def test_record_feedback_stores_new_fields(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    fb = svc.record_feedback(
        saved.id,
        CommunityResult.INTERACTED,
        interaction_ref="https://x/1",
        ref_kind="public_url",
        reason_kind="deep_but_later",
    )
    assert fb.interaction_ref == "https://x/1"
    assert fb.ref_kind == "public_url"
    assert fb.reason_kind == "deep_but_later"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_communities.py -k "unknown_community or stores_new_fields" -v`
Expected: FAIL — `ImportError` for `CommunityNotFoundError` (and `record_feedback` ignores new kwargs → `TypeError`).

- [ ] **Step 3: Implement**

In `src/finch/communities/service.py`, add after the imports:

```python
class CommunityNotFoundError(Exception):
    """对不存在的社区 id 记录反馈时的校验失败。"""
```

Replace `record_feedback` (lines 69-78) with:

```python
    def record_feedback(
        self,
        community_id: str,
        result: CommunityResult,
        *,
        note: str = "",
        reason_kind: str = "",
        interaction_ref: str = "",
        ref_kind: str = "",
    ) -> CommunityFeedback:
        if self.inspect(community_id) is None:
            raise CommunityNotFoundError(community_id)
        feedback = CommunityFeedback(
            community_id=community_id,
            result=result,
            note=note,
            reason_kind=reason_kind,
            interaction_ref=interaction_ref,
            ref_kind=ref_kind,
        )
        self.repo.append_feedback(feedback)
        return feedback
```

After `list_feedback` (line 83) add:

```python
    def feedback_for(self, community_id: str) -> list[CommunityFeedback]:
        """某个社区的全部反馈（供 Skill 回访读取）。"""
        return self.repo.feedback_for(community_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_communities.py tests/unit/test_cli_community.py -v`
Expected: PASS. (`test_cli_feedback_rejects_invalid_result` still passes because the CLI parses `--result` before calling `record_feedback`.)

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/service.py tests/unit/test_communities.py
git commit -m "feat(communities): validate feedback existence + interaction ref"
```

---

### Task 4: CLI — rename `discover` to `context`

**Files:**
- Modify: `src/finch/cli.py:171` (help text), `src/finch/cli.py:3645-3680` (command)
- Test: `tests/unit/test_cli_community.py`

**Interfaces:**
- Produces: `finch community context [--week] [--json]` (snapshot + optional bonus report read; no search).

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_cli_community.py`, rename `test_cli_discover_snapshots_context` → `test_cli_context_snapshots_context` and update the command; add a removal test:

```python
def test_cli_context_snapshots_context(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["community", "context", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert "-W" in payload["week"]
    assert "interests" in payload["context"]
    assert payload["report"] is None


def test_cli_discover_command_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["community", "discover"])
    assert r.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_community.py -k "context or discover_command" -v`
Expected: FAIL — `No such command 'context'` / `No such command 'discover'`.

- [ ] **Step 3: Rename the command**

In `src/finch/cli.py`, replace the `discover` command (lines 3645-3680) with:

```python
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
```

Update the typer help at line 171:

```python
community_app = typer.Typer(help="社区匹配与进入助手（发现、观察、回访可进入的社区）")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_community.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_community.py
git commit -m "feat(community): rename discover to context (snapshot only)"
```

---

### Task 5: CLI — `list` dedup by default + `--all`

**Files:**
- Modify: `src/finch/cli.py:3766-3800`
- Test: `tests/unit/test_cli_community.py`

**Interfaces:**
- Consumes: `CommunityRepository.list_latest_profiles` (Task 2).
- Produces: `finch community list [--week] [--all] [--json]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_community.py`:

```python
def test_cli_list_dedups_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    card = _card(tmp_path)
    assert CliRunner().invoke(app, ["community", "save", "--file", card]).exit_code == 0
    assert CliRunner().invoke(app, ["community", "save", "--file", card]).exit_code == 0
    r = CliRunner().invoke(app, ["community", "list", "--json"])
    assert r.exit_code == 0, r.output
    assert len(json.loads(r.output)) == 1
    r = CliRunner().invoke(app, ["community", "list", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert len(json.loads(r.output)) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_community.py -k "list_dedups" -v`
Expected: FAIL — default `list` returns 2 rows (no dedup), and `--all` is an unknown option.

- [ ] **Step 3: Implement**

Replace `community_list` (lines 3766-3800) with:

```python
@community_app.command("list")
def community_list(
    week: str | None = typer.Option(None, "--week", help="按 ISO 周过滤"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    show_all: bool = typer.Option(False, "--all", help="输出原始历史（默认每个社区一条最新投影）"),
) -> None:
    """列出候选社区与最近反馈状态（默认去重，每个稳定社区一条）。"""
    from finch.communities.service import CommunityService

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = CommunityService(ws)
    candidates = svc.repo.list_candidates() if show_all else svc.repo.list_latest_profiles()
    if week:
        candidates = [c for c in candidates if c.week == week]
    latest = svc.repo.latest_feedback_by_id()
    if as_json:
        payload = []
        for c in candidates:
            fb = latest.get(c.id)
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
        fb = latest.get(c.id)
        state = fb.result.value if fb else "-"
        typer.echo(f"{c.id}\t{c.week}\t{c.fit_score}\t{state}\t{c.name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_community.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_community.py
git commit -m "feat(community): dedup list by default, --all for history"
```

---

### Task 6: CLI — `inspect --json` includes feedback history

**Files:**
- Modify: `src/finch/cli.py:3612-3634` (`_echo_community_card`), `src/finch/cli.py:3716-3734` (`community_inspect`)
- Test: `tests/unit/test_cli_community.py`

**Interfaces:**
- Consumes: `CommunityService.feedback_for` (Task 3).
- Produces: `inspect <id> --json` → `{"profile": {...}, "feedback": [...]}` (**breaking shape change** from bare profile; intentional — young feature).

- [ ] **Step 1: Write the failing tests**

Update the existing `test_cli_save_inspect_feedback_roundtrip` assertion, and add a feedback-history test. In `tests/unit/test_cli_community.py`:

```python
# inside test_cli_save_inspect_feedback_roundtrip, replace the inspect assertion:
    r = CliRunner().invoke(app, ["community", "inspect", saved["id"], "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["profile"]["entry_point"]["discussion"] == "一个活跃的具体讨论"
    assert payload["feedback"] == []


def test_cli_inspect_includes_feedback_history(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    card = _card(tmp_path)
    saved = json.loads(
        CliRunner().invoke(app, ["community", "save", "--file", card, "--json"]).output
    )
    CliRunner().invoke(app, ["community", "feedback", saved["id"], "--result", "joined"])
    r = CliRunner().invoke(app, ["community", "inspect", saved["id"], "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [f["result"] for f in payload["feedback"]] == ["joined"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_community.py -k "inspect" -v`
Expected: FAIL — inspect JSON is still a bare profile (no `feedback` key).

- [ ] **Step 3: Implement**

In `_echo_community_card` (after the `fit_score` line, line 3617), add:

```python
    if profile.canonical_url:
        typer.echo(f"canonical_url: {profile.canonical_url}")
    if profile.recommendation_state:
        typer.echo(f"recommendation_state: {profile.recommendation_state.value}")
```

Replace `community_inspect` (lines 3716-3734) with:

```python
@community_app.command("inspect")
def community_inspect(
    community_id: str = typer.Argument(..., help="community id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看一张社区行动卡（含最新反馈与完整反馈历史）。"""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_community.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_community.py
git commit -m "feat(community): include feedback history in inspect --json"
```

---

### Task 7: CLI — `feedback` existence check + `--ref`/`--ref-kind`/`--reason-kind`

**Files:**
- Modify: `src/finch/cli.py:3737-3763`
- Test: `tests/unit/test_cli_community.py`

**Interfaces:**
- Consumes: `CommunityNotFoundError`, `record_feedback(..., reason_kind=, interaction_ref=, ref_kind=)` (Task 3).
- Produces: `finch community feedback <id> --result <...> [--ref <url>] [--ref-kind public_url|user_stated] [--reason-kind <tag>] [--note <...>] [--json]`; unknown id → exit 1 "not found".

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_community.py`:

```python
def test_cli_feedback_unknown_community(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["community", "feedback", "comm_missing", "--result", "joined"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_cli_feedback_ref_and_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    saved = json.loads(
        CliRunner().invoke(app, ["community", "save", "--file", _card(tmp_path), "--json"]).output
    )
    r = CliRunner().invoke(
        app,
        [
            "community", "feedback", saved["id"], "--result", "interacted",
            "--ref", "https://x/1", "--reason-kind", "deep_but_later", "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["interaction_ref"] == "https://x/1"
    assert out["ref_kind"] == "public_url"  # --ref 无 --ref-kind 时默认 public_url
    assert out["reason_kind"] == "deep_but_later"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_community.py -k "unknown_community or ref_and_reason" -v`
Expected: FAIL — `comm_missing` is silently written (no validation), and `--ref`/`--reason-kind` are unknown options.

- [ ] **Step 3: Implement**

Replace `community_feedback` (lines 3737-3763) with:

```python
@community_app.command("feedback")
def community_feedback(
    community_id: str = typer.Argument(..., help="community id"),
    result: str = typer.Option(
        ..., "--result", help="ignored|saved|joined|interacted|repeated|contributed"
    ),
    note: str = typer.Option("", "--note", help="可选备注"),
    reason_kind: str = typer.Option("", "--reason-kind", help="选择/拒绝原因短标签（no_time/too_general/…）"),
    interaction_ref: str = typer.Option("", "--ref", help="用户报告的真实互动链接"),
    ref_kind: str = typer.Option("", "--ref-kind", help="public_url|user_stated（默认 public_url，仅给 --ref 时生效）"),
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
```

- [ ] **Step 4: Run full verification**

Run: `uv run pytest tests/unit/test_communities.py tests/unit/test_cli_community.py -v`
Then: `uv run ruff check .` and `uv run mypy src`
Expected: all PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_community.py
git commit -m "feat(community): feedback --ref/--reason-kind + existence check"
```

---

### Task 8: Skill — three entry modes + layered recommendation

**Files:**
- Modify: `skills/community-scout/SKILL.md`
- Modify: `skills/community-scout/references/scoring-rubric.md`
- Modify: `skills/community-scout/references/presentation.md`
- Modify: `skills/community-scout/references/community-card-schema.md`

**Interfaces:** none (prose; the Skill now references `finch community context` and the new `feedback --ref/--reason-kind` flags from Tasks 4–7).

- [ ] **Step 1: Rewrite `SKILL.md`** — replace the whole file with:

````markdown
---
name: community-scout
description: >
  帮用户围绕具体问题发现并持续参与合适的公开社区（GitHub Discussions、Reddit、V2EX、X、公开论坛），
  解释其中实际讨论与可参与空间，用户选择后深入阅读，参与后按真实结果决定继续、观察或离开。
  支持本周探索、围绕问题、指定社区回访三种入口。社区是"关系发生的场"：核心 Builder 进
  peer-discovery/People 流程，社区问题进 idea-discovery。用于「这周有哪些值得参与的社区」
  「哪里适合讨论 X」「Temporal Community 最近值得回去吗」类请求。
---

# community-scout

从公开信息中发现值得进入并持续参与的社区。职责：读取 Finch 已掌握的"当前实践上下文"，从公开来源发现候选社区，
按证据与情境分层（观察 `observe` / 可参与 `actionable`），产出可执行的下一步。社区是场域，不是人：
社区里的核心 Builder 交给 `peer-discovery` 继续深挖，社区里的具体问题交给 `idea-discovery`。

本 Skill 只调用 Finch CLI 与只读数据源，不复制业务逻辑。判断是 LLM 按 `references/scoring-rubric.md`
进行；Python 只负责持久化与反馈记录。不因为成员多或消息多就推荐。

## 输入

先用 `finch community context` 快照当前实践上下文（写入 `var/communities/profile.yaml`）：

- 最近 GitHub commits 和正在解决的问题（`finch context` / `finch github reflect`）
- 已确认的关注主题（`profile.yaml` 的 interests / current_questions）
- Finch 发现的高价值内容与同行（`profile.yaml` 的 active_peers / recent_ideas）
- 用户明确输入的探索方向

## 三种入口

| 用户意图 | 示例 | 首轮交付 |
|---|---|---|
| 本周探索 | "这周有哪些值得参与的社区？" | 最多 3 个不同参与价值的社区，点名首选及原因；合格不足如实给实际数量 |
| 围绕问题 | "哪里适合讨论 FDE-Gym 的失败回放？" | 优先 1 个能回应此问题的当前讨论 + 1 个不同路径的备选；没有现成讨论就提出观察/贡献路径 |
| 指定社区/回访 | "Temporal Community 最近值得回去吗？" | 读历史选择、实际反馈和新公开证据，给继续/观察/暂缓建议 |

当前问题、实践引用、历史互动和可投入时间只在有据可查时使用。用户一句话给出的临时意图只影响本次搜索，
不静默写入长期兴趣。用户没给时间预算时不假定其有时间写代码；默认建议轻量、真实的一步。

## 候选分层（而非一次打分淘汰）

1. 公开发现：来源、规范 URL、最近观察时间、可访问范围可记录；来源失败披露覆盖缺口。
2. 可观察候选（`observe`）：有具体可核验证据，但未必有此刻适合参与的帖子；可推荐阅读，不杜撰切入点。
3. 当前可参与（`actionable`）：有仍相关话题，且能说明用户可提问、分享经证实经验或贡献什么。
4. 持续参与：用户确实加入/发言/收到回应/再次互动/贡献；回访优先读真实未完成讨论与承诺。

排序先看问题匹配、可带入材料、具体入口、互动开放度、交流成本；再考虑实践差异与意外发现。
`fit_score` 只作旧卡读取/诊断，不是用户面前的结论。

## CLI

- 快照上下文：`finch community context [--json]`
- 保存一张社区卡：`finch community save --file <card.yaml> [--week 2026-W39]`
- 查看单张卡（含反馈历史）：`finch community inspect <community_id> [--json]`
- 记录跟进：`finch community feedback <community_id> --result <...> [--ref <链接>] [--reason-kind <...>] [--note <...>]`
- 列出候选与状态（默认去重）：`finch community list [--week ...] [--all] [--json]`

数据采集只读入口（发现阶段用，不是新命令）：

- `gh api graphql`（GitHub Discussions / `search repositories`，只读 GraphQL/REST）
- `finch twitter search`（X）
- `finch sources sync --source reddit --query …` / `--source v2ex`（Reddit / V2EX）
- `WebFetcher`（公开论坛、Discord/Slack 公开主页或公开归档；无 JS 渲染，登录墙 fail-closed）

## 搜索预算（试运行参数）

周探索最多 20 个跨源候选 → 筛 6 个读近期证据 → 深入最多 3 个、展示最多 3 个；问题模式优先 1 + 备选 1；
选中深读最多 5 条相关公开讨论。分源失败返回部分结果与缺口，不因一处超时伪造全网结论。
对规范 URL 与作品指纹去重；重试计入预算。

## 向用户呈现

见 `references/presentation.md` 与 `_shared/agent-presentation.md`。
完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束），延伸不得抢占交付物。

- 默认先给一句明确建议 + 必要公开来源 + 一个下一步，详情按需展开；不固定七字段。
- 推荐必须引用公开证据（`evidence_urls`）；成员数/消息量不是核心排序依据。

## 边界

- 不接入私有 Discord/Slack 消息；Discord/Slack 只处理公开主页、公开归档、公开邀请信息。
- 不自动加入社区、不自动发言；`first_contribution` 只是建议，由用户自己执行。草稿仅用户选中后准备。
- 外部帖/公开讨论 ≠ 个人证据（见 `_shared/evidence-policy.md`）。
- 无可核验公开证据不能作肯定推荐；不把"社区主页可访问"当成"近期讨论可访问"。
- 社区里的"人"不是本 Skill 的产出 → 交给 `peer-discovery`；社区里的"问题" → 交给 `idea-discovery`。
- 跟进结果只记录，不自动改变下次评分（反馈→评分闭环留待验证后）。

## 参考

- `references/scoring-rubric.md`
- `references/community-card-schema.md`
- `references/presentation.md`
- `_shared/agent-presentation.md`
- `_shared/evidence-policy.md`
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束
````

- [ ] **Step 2: Rewrite `references/scoring-rubric.md`** — replace the whole file with:

````markdown
# 社区适配判断（证据检查 + 情境判断 + 取舍解释）

由 LLM 判断，不写进 Python。目标是说明"为什么现在适合进入/观察"，不是打一个总分排序。

## 证据检查（可核验是前提）

- 每条推荐结论都能指向公开来源（`recent_evidence[].url` / `evidence_urls` 非空）。
- 无可核验公开证据 → 不能作肯定推荐，只能说明"未找到可核验来源"。
- 来源失败（某平台不可用）→ 披露覆盖缺口，保留可用结果，不伪造全网结论。

## 情境判断

| 维度 | 判断依据（不合成 total） |
|---|---|
| 问题匹配 | 近期讨论是否对应 `profile.yaml` 的 interests / current_questions 或本次问题 |
| 可带入材料 | 用户是否有可分享的实践/案例/代码（`practice_refs`） |
| 具体入口 | 是否有一条仍开放、可回应的讨论（`entry_point.url` + `status`） |
| 互动开放度 | 新成员提问/贡献能否得到有效回应（有具体回复，而非零回复冷场） |
| 交流成本 | 语言、概念门槛、时间成本 |
| Builder 质量 | 是否有持续构建、公开复盘的核心成员（真实作品/复盘，而非 bio/转发） |

旧四维 `fit_score`（实践相关度 35 / Builder 质量 25 / 互动开放度 20 / 信噪比 20）保留供旧卡读取与诊断，
不再作为用户面前的结论或排序依据。

## 分层结论（落 `recommendation_state`）

- **observe（可观察）**：有具体可核验的实践或讨论证据，但此刻没有适合切入的帖子；可推荐阅读，不杜撰切入点。
- **actionable（可参与）**：有仍相关的话题，且能说明用户可提问、分享经证实经验或贡献什么；检查发布时间与讨论是否仍开放，信息不足标未知。

"近 30 天活动"是周报及时性的默认搜索窗口与诊断信号，不能无条件否决更新慢但高价值的开源社区。
成员规模、热度、单个 LLM 总分不决定优先级。

## 取舍解释

排序先看：问题匹配、可带入材料、具体入口、互动开放度、交流成本；再考虑实践差异与意外发现。
前台解释关键取舍（如"更适合提问" vs "更适合贡献复现案例"），并诚实指出"目前不清楚是否已有同类工具，建议先核对"这类缺口。

## 反信号

- 大量消息 ≠ 活跃：看是否同一批成员反复出现（关系密度），不是总消息量。
- 大量成员 ≠ 质量：看是否有持续构建/公开复盘的核心成员。
- 中文/英文都是参与成本，不是硬过滤；质量优先。
````

- [ ] **Step 3: Rewrite `references/presentation.md`** — replace the whole file with:

````markdown
# 社区行动卡呈现配方

形状真源与命令映射。共享呈现原则见 `_shared/agent-presentation.md`（结论 → 决策卡 → 操作）。

默认先给：一句明确建议 + 必要公开来源 + 一个下一步；详情按需展开，不固定七字段。

## 四种呈现

### 1. 周报（本周探索）
- 最多 3 个不同参与价值的社区，点名首选及原因；合格不足就如实给实际数量，不凑数。
- 每个给：这是什么社区、为什么现在适合、近期公开证据（带 url）、一个下一步。
- 有 `actionable` 社区给确切入口；只有 `observe` 就说"可观察，暂无切入点"。

### 2. 按问题探索
- 优先 1 个能回应此问题的当前讨论，再给 1 个不同路径的备选。
- 没有现成讨论 → 提出观察/贡献路径，不编造切入点。

### 3. 选中深读
- 读若干相关公开讨论与关键 Builder，说明已有方案、用户可能补的缺口、无法确认之处。
- 没有实质缺口 → 建议观察或转向备选。

### 4. 回访
- 读历史选择、实际反馈与新的公开证据，给继续/观察/暂缓建议。
- 不把旧加入状态等同于活跃交流；已互动优先读未完成讨论与承诺，不重推"首次加入"。

## 用户回复 → CLI 映射

| 用户说 | 记录命令 |
|---|---|
| 不感兴趣 | `finch community feedback <id> --result ignored` |
| 以后可能参与 | `finch community feedback <id> --result saved` |
| 先观察，暂时不发言 | `finch community feedback <id> --result saved --reason-kind deep_but_later --note "先观察"` |
| 已加入 / 开始关注 | `finch community feedback <id> --result joined` |
| 完成第一次公开互动（有链接） | `finch community feedback <id> --result interacted --ref <公开链接>` |
| 和成员第二次交流 | `finch community feedback <id> --result repeated --ref <链接>` |
| 提交了代码/案例/工具 | `finch community feedback <id> --result contributed --ref <链接>` |
| 没时间 | `finch community feedback <id> --result saved --reason-kind no_time` |
````

- [ ] **Step 4: Rewrite `references/community-card-schema.md`** — replace the whole file with:

````markdown
# 社区行动卡 schema

`finch community save --file <card.yaml>` 吃这个 YAML。顶层 `community` 对应模型字段 `name`；
`id` / `week` / `created_at` 由 CLI 自动补全。新字段均可空，旧卡继续可读。

## 可参与态示例（actionable）

```yaml
community: Temporal Community
canonical_url: https://temporal.io/community
recommendation_state: actionable
intent: question
question: Agent workflow failure replay 的社区
practice_refs:
  - FDE-Gym failure replay 案例
platforms:
  - GitHub Discussions
fit_score: 86
why_fit:
  - 与 durable execution 和 Agent reliability 高度相关
recent_evidence:
  - topic: workflow failure recovery
    relevance: 与 FDE-Gym 的 failure replay 方向相关
    url: https://example.com/discussion/1
entry_point:
  discussion: 一个仍在活跃的具体讨论
  suggested_angle: 分享 FDE-Gym 中的失败回放设计
  url: https://example.com/discussion/1
  status: open
first_contribution:
  type: example
  proposal: 提供一个 Agent workflow failure replay 示例
risks:
  - 英文交流成本中等
evidence_urls:
  - https://example.com/discussion/1
```

## 观察态示例（observe）

```yaml
community: Some Slow-moving Open Source Project
canonical_url: https://github.com/example/project
recommendation_state: observe
recent_evidence:
  - topic: 上月的 release notes
    relevance: 方向相关但当前无线程可切入
    url: https://example.com/release
why_fit:
  - 方向高度相关，但近期无开放讨论
risks:
  - 暂无具体切入点，仅建议阅读
```

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `community` | str | 社区名（对应模型 `name`；`id` 由它内容寻址） |
| `canonical_url` | str = "" | 跨周稳定标识（主页/项目链接）；空则回退 name-hash id 去重 |
| `recommendation_state` | `observe`/`actionable`/空 | Finch 建议的状态；空=旧卡 |
| `intent` | str = "" | `weekly`/`question`/`revisit` |
| `question` | str = "" | 问题模式的具体问题 |
| `practice_refs` | list[str] = [] | 用户带入本次匹配的实践材料 |
| `source_checked_at` | datetime/空 | 资料最近核验时间 |
| `platforms` | list[str] | 社区所在平台 |
| `fit_score` | int 0–100 | 旧四维加权分（诊断用，不是结论） |
| `why_fit` | list[str] | 为什么现在适合你 |
| `recent_evidence` | list[{topic, relevance, url}] | 近期公开证据 |
| `people` | list[{name, reason}] | 值得关注的核心 Builder |
| `entry_point` | {discussion, suggested_angle, url, status} | 可切入讨论 + 链接 + 开放性（`open`/`closed`/`unknown`/空） |
| `first_contribution` | {type, proposal} | 你能贡献的代码/案例/工具 |
| `risks` | list[str] | 参与成本与风险 |
| `evidence_urls` | list[str] | 公开证据链接（去重后） |

## feedback 字段

| 字段 | 说明 |
|---|---|
| `result` | 六值不变：`ignored/saved/joined/interacted/repeated/contributed` |
| `reason_kind` | 选择/拒绝原因短标签（`no_time`/`too_general`/`language_barrier`/`deep_but_later` 等），可为空 |
| `interaction_ref` | 真实互动链接；空=未提供 |
| `ref_kind` | `public_url`/`user_stated`；无 `interaction_ref` 时为空 |

已发生状态（`interacted/repeated/contributed`）无 `interaction_ref` 时为自述（`user_stated`），未公开核验。
"准备/打算"不得记成 `interacted`。
````

- [ ] **Step 5: Verify + commit**

Run: `uv run ruff check .` (SKILL markdown is not linted; this is a sanity no-op) — then:

```bash
git add skills/community-scout/SKILL.md skills/community-scout/references/
git commit -m "docs(community-scout): three entry modes + layered recommendation"
```

- [ ] **Step 6: Manual P1 acceptance (not code)**

Run the Skill end-to-end against at least 5 real requests and confirm: "先了解" / "不要起草" / "不想参与这个" correctly change the next step; no fabricated first-person experience without material; a useful first-round answer does not require all seven fields. This is human-reviewed, not a string test.

---

## Self-Review

- **Spec coverage:** §4 (model fields) → Tasks 1; §5 (identity/dedup) → Tasks 1–2; §6 (service/CLI) → Tasks 3–7; §7 (Skill) → Task 8; §8 decision 1 (two-state) → Task 1; §8 decision 2 (no report file) → Task 4 keeps the bonus read only; §8 decision 3 (`list` dedup default) → Task 5. §9 acceptance table → covered by the tests in Tasks 1–7 (unknown-id feedback, dedup, old-card defaults, `--ref`, JSON shape).
- **Type consistency:** `identity_key`, `list_latest_profiles`, `feedback_for`, `record_feedback(..., reason_kind=, interaction_ref=, ref_kind=)` and `CommunityNotFoundError` are named identically where produced (Tasks 1–3) and consumed (Tasks 2–7).
- **Placeholders:** none — every code step shows complete code; markdown steps show complete replacement content.

**Out of scope / optional follow-ups (not in this plan):** weekly-reflection 社区参与回看段 (§9 optional); `reports/<week>.md` persistence; P3 四周试运行校准. The `docs/Finch-Community-Scout-Interactive-Implementation-Plan.md` source doc remains untracked (user's call to commit).
