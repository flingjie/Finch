# 单一决策点 — Plan 2（CLI 面：revise + next + daily --json）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐单一决策点的 CLI 面：`decide --action revise --instruction "<NL>"`（NL 重写 + Critic + 返回 diff）、`finch next --json`（决策卡）、`finch daily --json`（结构化 run 结果）。Codex 编排（`$finch` skill）留待 Plan 3。

**Architecture:** 复用 Plan 1 的 `DecisionService`/`DecisionRecord`/`Draft.run_id`；新增 `rewrite_with_instruction`（NL 重写，顺手修复 `rewrite` 丢 `run_id` 的 Plan 1 缺口）；`next --json` 在 CLI 层组装决策卡；`daily --json` 在 `run_daily` 尾部分支输出结构化结果。

**Tech Stack:** Python 3.12+，Pydantic 2，SQLModel/SQLite，typer，pytest，ruff（E,F,I,B,UP 行长 100），mypy。

## Global Constraints

- Python 3.12+；Pydantic 2；ruff `E,F,I,B,UP` 行长 100；mypy 通过。
- 不变量不变：Evidence first、No auto-publish、External ≠ evidence、Deterministic totals。
- `decide revise` 用 Codex 子进程（rewrite + critique）做 NL 重写，确定性编排：Finch 负责顺序/校验/Pydantic，子进程 args 用数组、per-call 超时。
- `next --json` / `daily --json` 是只读查询，不改变任何状态。
- `must_ask`/`risks` 字段先返回空默认（`position_conflict`/`safety_risk` 信号组装留待 Plan 3），但 JSON 形状必须包含它们（供 Plan 3 的编排层消费）。
- 加性：不 drop 旧命令/旧记录。

---

### Task 1: `rewrite_with_instruction`（并修复 `rewrite` 丢 `run_id`）

**Files:**
- Modify: `src/finch/content/writer.py`
- Test: `tests/unit/test_writer.py`

**Interfaces:**
- Produces:
  - `rewrite_with_instruction(runner, draft, instruction, cards_by_id, job=None) -> Draft`
  - `rewrite(runner, draft, failed_checks, cards_by_id, job=None) -> Draft`（重构为委托内部 `_rewrite`，并保留 `run_id`）

- [ ] **Step 1: 写失败测试**

`tests/unit/test_writer.py` 追加：

```python
from finch.content.models import Draft, DraftKind


def test_rewrite_preserves_run_id(monkeypatch):
    from finch.content import writer
    from finch.content.checkers.base import CheckResult
    from finch.evidence.models import ClaimConfidence, EvidenceCard

    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, body="before", claims=[], run_id="r1")

    def fake_run(prompt, model):
        return Draft(id="d1", kind=DraftKind.ORIGINAL, body="after", claims=[])

    monkeypatch.setattr(writer, "_sanitize_draft_claims", lambda d: d)
    # 用 monkeypatch 替换 runner.run：见下方 note
    class _Runner:
        def run(self, prompt, model):
            return fake_run(prompt, model)

    out = writer.rewrite(_Runner(), draft, [], {})
    assert out.run_id == "r1"
    assert out.body == "after"


def test_rewrite_with_instruction_uses_nl_instruction(monkeypatch):
    from finch.content import writer

    captured = {}

    class _Runner:
        def run(self, prompt, model):
            captured["prompt"] = prompt
            return Draft(id="d1", kind=DraftKind.ORIGINAL, body="revised", claims=[])

    monkeypatch.setattr(writer, "_sanitize_draft_claims", lambda d: d)
    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, body="before", claims=[], run_id="r1")
    out = writer.rewrite_with_instruction(_Runner(), draft, "语气弱一点", {})
    assert out.body == "revised"
    assert "语气弱一点" in captured["prompt"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_writer.py -q`
Expected: FAIL — `AttributeError: module 'finch.content.writer' has no attribute 'rewrite_with_instruction'`，且 `test_rewrite_preserves_run_id` 因 `run_id` 丢失失败。

- [ ] **Step 3: 实现**

将 `src/finch/content/writer.py` 的 `rewrite`（约 216-241 行）重构为：

```python
def rewrite(
    runner: CodexRunner,
    draft: Draft,
    failed_checks: list[CheckResult],
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    """按 Critic 失败检查器指令重写（保留 run_id 等来源字段）。"""
    return _rewrite(runner, draft, _render_failed_checks(failed_checks), cards_by_id, job)


def rewrite_with_instruction(
    runner: CodexRunner,
    draft: Draft,
    instruction: str,
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    """按自然语言指令重写（单一决策点 `decide revise` 路径）。"""
    return _rewrite(runner, draft, instruction, cards_by_id, job)


def _rewrite(
    runner: CodexRunner,
    draft: Draft,
    instructions: str,
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    card_ids = {ref.evidence_card_id for ref in draft.claims}
    cards = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]
    prompt = _REWRITE_PROMPT.format(
        body=draft.body,
        job_context=_render_job_context(job),
        rewrite_instructions=instructions,
        cards=_render_cards(cards),
    )
    out = _sanitize_draft_claims(cast(Draft, runner.run(prompt, Draft)))
    return out.model_copy(
        update={
            "id": draft.id,
            "kind": draft.kind,
            "candidate_id": draft.candidate_id,
            "language": draft.language,
            "content_job_id": draft.content_job_id,
            "position_statement": draft.position_statement,
            "run_id": draft.run_id,
        }
    )
```

（`_render_failed_checks` / `_render_job_context` / `_render_cards` / `_sanitize_draft_claims` 已在 writer.py 中。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_writer.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/content/writer.py tests/unit/test_writer.py
git commit -m "feat(writer): rewrite_with_instruction + preserve run_id in rewrite"
```

---

### Task 2: `DecisionService.revise`（NL 重写 + Critic + 持久化）

**Files:**
- Modify: `src/finch/review/decision.py`
- Test: `tests/unit/test_decision.py`

**Interfaces:**
- Consumes: `rewrite_with_instruction`（Task 1）、`critique`/`evaluate_passed`（`content/critic.py`）、`compute_diff`（`review/service.py`）、`content_hash`（已有）。
- Produces: `DecisionService.revise(job_id, instruction, *, runner, cards_by_id, gates) -> dict`，返回 `{"new_body", "diff", "critic"}`（`critic` 为 `CritiqueResult.model_dump(mode="json")`）。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_decision.py` 追加：

```python
def test_revise_rewrites_and_persists(monkeypatch, tmp_path):
    from finch.content.critic import CritiqueResult
    from finch.review.decision import DecisionService

    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="before", content_job_id="j1", run_id="r1")
    )
    svc = _svc(store)

    class _Runner:
        def run(self, prompt, model):
            return Draft(id="d1", kind=DraftKind.ORIGINAL, body="after", claims=[])

    monkeypatch.setattr(
        "finch.review.decision.rewrite_with_instruction",
        lambda runner, draft, instruction, cards_by_id, job=None: Draft(
            id=draft.id, kind=draft.kind, body="after", claims=[], content_job_id="j1", run_id="r1"
        ),
    )
    monkeypatch.setattr(
        "finch.review.decision.critique",
        lambda runner, draft, cards_by_id: CritiqueResult(passed=True, checks=[]),
    )

    result = svc.revise("j1", "语气弱一点", runner=_Runner(), cards_by_id={}, gates=None)
    assert result["new_body"] == "after"
    assert "before" in result["diff"] and "after" in result["diff"]
    # 修订正文已落库，后续 accept 会绑定到新正文
    assert DraftRepository(store).get_draft("d1").body == "after"
    rec = DecisionRecordRepository(store).get("j1")
    assert rec is not None and rec.action == DecisionAction.REVISE
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_decision.py -q`
Expected: FAIL — `AttributeError: 'DecisionService' object has no attribute 'revise'`。

- [ ] **Step 3: 实现 `revise`**

在 `DecisionService` 末尾新增：

```python
    def revise(
        self,
        job_id: str,
        instruction: str,
        *,
        runner: CodexRunner,
        cards_by_id: dict,
        gates: QualityGates | None,
    ) -> dict:
        """按 NL 指令重写 + 重跑 Critic + 持久化修订正文，返回 {new_body, diff, critic}。

        ``revise`` 不写 ACCEPT 记录；它把修订正文落库（``upsert_draft``），
        使后续 ``accept`` 的 ``approved_content_hash`` 绑定到最新正文。
        """
        job, draft = self._require_job_and_draft(job_id)
        new_draft = rewrite_with_instruction(runner, draft, instruction, cards_by_id, job)
        critic = critique(runner, new_draft, cards_by_id)
        diff = compute_diff(draft.body, new_draft.body)

        self.drafts.upsert_draft(new_draft)
        self.reviews.save_review(
            ReviewDecision(
                id=f"rev_{draft.id}", draft_id=draft.id,
                action=ReviewAction.REVISE, revised_body=new_draft.body, diff=diff,
                decided_at=datetime.now(UTC),
            )
        )
        fingerprint = (
            position_fingerprint(job.author_position) if job.author_position else ""
        )
        self.decisions.save(
            DecisionRecord(
                id=f"dec_{job_id}", job_id=job_id, draft_id=draft.id,
                action=DecisionAction.REVISE,
                position_source=(
                    job.author_position.position_source if job.author_position else PositionSource.INFERRED
                ),
                position_fingerprint=fingerprint,
                approved_content_hash=content_hash(new_draft.body),
                revised_body=new_draft.body, diff=diff,
                decided_at=datetime.now(UTC),
            )
        )
        return {
            "new_body": new_draft.body,
            "diff": diff,
            "critic": critic.model_dump(mode="json"),
        }
```

（顶部 import：`CodexRunner`、`critique`、`compute_diff`、`rewrite_with_instruction`、`QualityGates`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_decision.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/review/decision.py tests/unit/test_decision.py
git commit -m "feat(review): DecisionService.revise (NL rewrite + critic)"
```

---

### Task 3: `finch decide --action revise --instruction`

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `DecisionService.revise`（Task 2）、`EvidenceRepository`、`create_runner`/`CodexRunner`、`settings.quality_gates`。
- Produces: `finch decide <job-id> --action revise --instruction "<NL>" [--json]`，`--json` 输出 `{"new_body","diff","critic"}`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_cli_run.py` 追加（monkeypatch `DecisionService.revise` 避免真 Codex 调用）：

```python
def test_decide_revise(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    monkeypatch.setattr(
        cli, "DecisionService",
        lambda **kw: _FakeDecisionService(revise={"new_body": "v2", "diff": "", "critic": {}}),
    )
    r = CliRunner().invoke(
        app, ["decide", "j1", "--action", "revise", "--instruction", "语气弱一点", "--json"]
    )
    assert r.exit_code == 0, r.output
    assert "v2" in r.output
```

（`_FakeDecisionService` 是测试内的小类，`revise(...)` 返回给定 dict。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k revise`
Expected: FAIL — 当前 `--action revise` 返回 "not yet supported"。

- [ ] **Step 3: 实现**

在 `src/finch/cli.py` 的 `decide` 命令中，把 `else:`（revise not supported）分支替换为：

```python
        elif action_enum is DecisionAction.REVISE:
            if not instruction:
                typer.echo("--action revise requires --instruction")
                raise typer.Exit(code=1)
            cards_by_id = {
                c.id: c for c in EvidenceRepository(store).list_cards()
            }
            runner = create_runner(settings.llm) or CodexRunner()
            result = svc.revise(
                item_id, instruction,
                runner=runner, cards_by_id=cards_by_id, gates=settings.quality_gates,
            )
            record = None
```

并在 `decide` 函数签名加 `instruction: str | None = typer.Option(None, "--instruction", help="--action revise 的自然语言指令")`，尾部输出改为：

```python
    if as_json:
        if record is not None:
            typer.echo(record.model_dump_json(indent=2))
        else:
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo(result["new_body"] if result else record.action.value)
```

（注：`accept`/`skip` 分支设 `record=...`、`result=None`；`revise` 分支设 `result=...`、`record=None`。在命令开头初始化 `record = None; result = None`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k decide`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): finch decide revise --instruction"
```

---

### Task 4: `finch next --json`（决策卡）

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `DraftRepository.list_drafts`、`ContentJobRepository.get_job`、`EvidenceRepository.list_cards`、`DecisionRecordRepository.list`。
- Produces: `finch next --json` → 待决策卡 `{job_id, topic, why_now, position{claim,decision,tradeoff,source}, evidence[], draft, draft_id, must_ask, ask_reasons[], risks[]}`，或 `{"status": "none"}`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_cli_run.py` 追加：

```python
def test_next_json_returns_card(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(
            job_id="j1",
            author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
            core_message="topic here", why_now="why now",
        )
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="body", content_job_id="j1")
    )
    r = CliRunner().invoke(app, ["next", "--json"])
    assert r.exit_code == 0, r.output
    assert '"job_id": "j1"' in r.output
    assert "topic here" in r.output
    assert '"must_ask": []' in r.output


def test_next_json_none(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["next", "--json"])
    assert r.exit_code == 0, r.output
    assert '"status": "none"' in r.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k next`
Expected: FAIL — `No such command 'next'`。

- [ ] **Step 3: 实现 `next`**

在 `src/finch/cli.py` 新增：

```python
@app.command("next")
def next_item(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """返回下一个待决策卡（primary ContentJob + 其草稿），无则 status=none。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()

    decided_job_ids = {
        rec.job_id for rec in DecisionRecordRepository(store).list()
        if rec.action in {DecisionAction.ACCEPT, DecisionAction.SKIP}
    }
    drafts = [d for d in DraftRepository(store).list_drafts() if d.content_job_id]
    pending = [d for d in drafts if d.content_job_id not in decided_job_ids]
    if not pending:
        payload = {"status": "none"}
    else:
        draft = pending[0]
        job = ContentJobRepository(store).get_job(draft.content_job_id)
        cards_by_id = {c.id: c for c in EvidenceRepository(store).list_cards()}
        evidence = [
            {"id": cid, "claim": cards_by_id[cid].claim}
            for cid in (job.source_card_ids if job else []) if cid in cards_by_id
        ]
        pos = job.author_position if job else None
        payload = {
            "status": "review_required",
            "job_id": job.id if job else draft.content_job_id,
            "topic": (job.core_message or job.reader_problem) if job else "",
            "why_now": job.why_now if job else "",
            "position": {
                "claim": pos.claim if pos else "",
                "decision": pos.decision if pos else "",
                "tradeoff": pos.tradeoff if pos else "",
                "source": (pos.position_source.value if pos and pos.position_source else "inferred"),
            },
            "evidence": evidence,
            "draft": draft.body,
            "draft_id": draft.id,
            "must_ask": [],
            "ask_reasons": [],
            "risks": [],
        }
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(payload.get("topic") or payload.get("status", "none"))
```

（`next` 命令需 import `DecisionRecordRepository` / `DecisionAction` / `EvidenceRepository` / `ContentJobRepository` / `DraftRepository`——均已在本文件其他命令用过。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k next`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): finch next --json (decision card)"
```

---

### Task 5: `finch daily --json`（结构化结果）

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: 现有 `run_daily` 尾部逻辑、`DraftRepository.list_drafts`、`DecisionRecordRepository.list`。
- Produces: `finch run daily --json` → `{"run_id", "status": "review_required"|"completed", "n_review": int, "n_engagement_drafts": int}`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_cli_run.py` 追加：

```python
def test_run_daily_json(monkeypatch, tmp_path):
    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        paths=Paths(db_path=tmp_path / "finch.db"),
        engagement=EngagementSettings(enabled=False),
    )
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    class FakeIngestor:
        def __init__(self, gh, settings, ingestion, cursor): pass
        def ingest(self, repos, existing_topics=None): return {}

    class FakeGh:
        def repo_view(self, repo):
            from finch.github.models import RepoInfo
            return RepoInfo(name_with_owner=repo, default_branch="main",
                            url="https://github.com/" + repo, is_private=False)

    monkeypatch.setattr(cli, "GhClient", lambda: FakeGh())
    monkeypatch.setattr(cli, "Ingestor", FakeIngestor)
    monkeypatch.setattr(cli, "daily_nodes", lambda **kw: [])
    monkeypatch.setattr(cli, "load_voice_profile", lambda path: None)

    r = CliRunner().invoke(app, ["run", "daily", "--json"])
    assert r.exit_code == 0, r.output
    assert '"status"' in r.output and '"run_id"' in r.output
    assert '"n_review"' in r.output and '"n_engagement_drafts"' in r.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k daily_json`
Expected: FAIL — `run daily` 无 `--json` flag。

- [ ] **Step 3: 实现**

给 `run_daily` 加 `as_json: bool = typer.Option(False, "--json", ...)`，并在两个尾部（engagement 与非 engagement）加结构化输出。先抽一个 helper：

```python
def _daily_json_summary(store: Store, run_id: str, *, engagement_drafts: int) -> str:
    decided = {
        rec.job_id for rec in DecisionRecordRepository(store).list()
        if rec.action in {DecisionAction.ACCEPT, DecisionAction.SKIP}
    }
    drafts = [d for d in DraftRepository(store).list_drafts() if d.content_job_id]
    n_review = sum(1 for d in drafts if d.content_job_id not in decided)
    return json.dumps(
        {
            "run_id": run_id,
            "status": "review_required" if n_review else "completed",
            "n_review": n_review,
            "n_engagement_drafts": engagement_drafts,
        },
        ensure_ascii=False,
        indent=2,
    )
```

- engagement 分支：在 `_persist_engagement_run_stats(...)` 之后、`_finish_daily`/`_echo_dual_track_result` 之前，若 `as_json`，计算 `engagement_drafts` 并 `typer.echo(_daily_json_summary(store, result.original.id if result.original else result.run_id, engagement_drafts=engagement_drafts))`，然后 `return`。
- 非 engagement 分支：`run = GraphRuntime(store, nodes).run()` 之后，若 `as_json`，`typer.echo(_daily_json_summary(store, run.id, engagement_drafts=0))`，然后 `return`（放在 `NEEDS_INPUT`/`_finish_daily` 分支判断之前或之后皆可，但需保证 `--json` 不进入交互/紧凑输出）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k daily`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): finch run daily --json (structured summary)"
```

---

### Task 6: 全量回归

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: PASS（旧测试回归绿）。

- [ ] **Step 2: lint + 类型**

Run: `uv run ruff check . && uv run mypy src`
Expected: 无错误。

- [ ] **Step 3: 提交（如有修正）**

```bash
git add -A && git commit -m "chore: regression fixes for single decision point Plan 2"
```

---

## Self-Review 结果

- **Spec 覆盖（Plan 2 范围）**：§5 `修改` 动作 → Task 1/2/3；§6 `next --json` → Task 4；§6 `daily --json` → Task 5；§6 `decide revise --instruction` → Task 3。`must_ask`/`risks` 空默认按约束返回（`position_conflict`/`safety_risk` 信号组装留 Plan 3）；Codex 编排（`$finch` skill）留 Plan 3。
- **Placeholder 扫描**：无 TBD/TODO。
- **类型一致性**：`rewrite_with_instruction` 签名在 Task 1 定义、Task 2 引用一致；`DecisionService.revise(job_id, instruction, *, runner, cards_by_id, gates) -> dict` 在 Task 2 定义、Task 3 引用一致；`_daily_json_summary` 在 Task 5 定义、Task 5 两处调用一致。
