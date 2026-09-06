# NEEDS_INPUT 交互步骤（UX 门面）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `finch run daily` / `finch run resolve` 的 `NEEDS_INPUT` 状态从「运行异常」观感改成「Daily 交互步骤」，在 TTY 中内联选择器并自动恢复，非 TTY 输出紧凑结果。

**Architecture:** 在既有 `gate/resolve.py` 之上叠加纯渲染层（`gate/render.py`）与交互层（`gate/interactive.py`），CLI 只负责接线。内部 Graph 状态、`position_gate` 语义、`replay` 全部不变。

**Tech Stack:** Python 3.12+，typer（CLI），Pydantic 2，pytest，ruff（line-length 100, py312），mypy。

## Global Constraints

- Python 3.12+；Pydantic 2（`StrEnum`/`Literal`/`Field`）；ruff 选择 `E,F,I,B,UP`，行长 100。
- 双语（中/英）docstring，匹配所在文件；面向用户的文案用中文。
- 不引入 `asyncio.gather`；子进程 args 用数组；确定性（无 LLM 判定交互/状态文案）。
- 不变量：**Evidence first**、**No auto-publish**、**External ≠ evidence**、**Deterministic totals** 全部不变。
- 不修改 `GraphRuntime` / `replay` / `position_gate` 的 single-primary 选择逻辑。
- 交互路径通过 `typer.prompt` 读取输入（与现有 `run resolve` 一致，可用 `CliRunner(input=...)` 测试）。
- TTY 检测用 `sys.stdout.isatty()`；`--interactive` / `--non-interactive` 显式覆盖，二者同时给出报错。

---

### Task 1: 状态命名 + 渲染纯函数（`gate/render.py`）

**Files:**
- Modify: `src/finch/gate/render.py`
- Test: `tests/unit/test_gate_render.py`

**Interfaces:**
- Consumes: `InputRequest` / `ProposedPosition` / `InputAction`（`gate/models.py`，已存在），`EvidenceCard`（`evidence/models.py`，已存在）。
- Produces（后续 Task 2/3/4 依赖）:
  - `state_label(state: str) -> str`
  - `render_confirm_card(request: InputRequest, cards: list[EvidenceCard]) -> str`
  - `render_daily_summary(*, posts_found: int | None, engagement_drafts: int, pending_original: int) -> str`
  - `render_compact_resolve(request: InputRequest, *, engagement_drafts: int = 0) -> str`
  - `render_outcome(action: InputAction | None) -> str`
  - `render_produced(*, engagement_drafts: int, original_drafts: int) -> str`
  - 保留 `render_evidence` / `render_position_diff` 不变；删除 `render_input_request`。

- [ ] **Step 1: 写失败测试** — 替换 `tests/unit/test_gate_render.py` 的 import 与旧断言，并新增新函数测试。

```python
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.gate.models import InputAction, InputRequest, ProposedPosition
from finch.gate.render import (
    render_compact_resolve,
    render_confirm_card,
    render_daily_summary,
    render_evidence,
    render_outcome,
    render_position_diff,
    render_produced,
    state_label,
)


def _request():
    return InputRequest(
        run_id="r1",
        job_id="j1",
        topic="topic here",
        why_now="why now here",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
        questions=["q1"],
    )


def test_render_confirm_card_contains_sections():
    text = render_confirm_card(_request(), [])
    assert "topic here" in text
    assert "主张：c" in text
    assert "方案：d" in text
    assert "取舍：t" in text
    assert "q1" in text
    assert "依据：0 张证据卡" in text


def test_render_confirm_card_empty_position():
    text = render_confirm_card(
        _request().model_copy(update={"proposed_position": ProposedPosition()}), []
    )
    assert "主张：(未填)" in text


def test_state_label_mapping():
    assert state_label("COMPLETED") == "已完成"
    assert state_label("NEEDS_INPUT") == "等待你的确认"
    assert state_label("SKIPPED") == "已跳过"
    assert state_label("FAILED") == "运行失败"
    assert state_label("BLOCKED") == "运行失败"
    assert state_label("WEIRD_STATE") == "WEIRD_STATE"


def test_render_daily_summary_with_and_without_engagement():
    full = render_daily_summary(posts_found=30, engagement_drafts=3, pending_original=1)
    assert "✓ 今日分析已完成" in full
    assert "扫描 30 条帖子" in full
    assert "生成 3 条互动草稿" in full
    assert "1 个值得展开的主题" in full
    no_eng = render_daily_summary(posts_found=None, engagement_drafts=0, pending_original=1)
    assert "互动内容" not in no_eng
    assert "1 个值得展开的主题" in no_eng


def test_render_compact_resolve_recommends_confirm():
    text = render_compact_resolve(_request(), engagement_drafts=3)
    assert "Daily 分析完成" in text
    assert "uv run finch run resolve --confirm" in text
    assert "另外有 3 条互动草稿" in text


def test_render_outcome_and_produced():
    assert render_outcome(InputAction.CONFIRM) == "✓ 已确认你的立场"
    assert render_outcome(InputAction.EDIT) == "✓ 已更新你的立场"
    assert render_outcome(InputAction.SKIP) == "✓ 已跳过这个主题"
    assert render_outcome(InputAction.STOP) == "✓ 已保存并退出"
    assert render_outcome(None) == "✓ 已保存进度并退出"
    produced = render_produced(engagement_drafts=3, original_drafts=1)
    assert "互动草稿：3 条" in produced
    assert "原创草稿：1 条" in produced


def test_render_evidence():
    cards = [EvidenceCard(
        id="ev1", event_id="e", claim="the claim", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["t"],
    )]
    assert "the claim" in render_evidence(cards)
    assert "无证据" in render_evidence([])


def test_render_position_diff_shows_change():
    before = ProposedPosition(claim="c", decision="d", tradeoff="t")
    after = ProposedPosition(claim="c2", decision="d", tradeoff="t")
    diff = render_position_diff(before, after)
    assert "-claim: c" in diff
    assert "+claim: c2" in diff
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_gate_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_confirm_card' ...`

- [ ] **Step 3: 实现渲染函数** — 重写 `src/finch/gate/render.py`。

```python
"""Gate 交互层渲染：确认卡、摘要、紧凑结果与状态文案（纯函数，无 IO）。"""

import difflib

from finch.evidence.models import EvidenceCard

from .models import InputAction, InputRequest, ProposedPosition


_STATE_LABELS = {
    "COMPLETED": "已完成",
    "NEEDS_INPUT": "等待你的确认",
    "SKIPPED": "已跳过",
    "FAILED": "运行失败",
    "BLOCKED": "运行失败",
}


def state_label(state: str) -> str:
    """把内部 GraphState 映射为面向用户的文案；未知名回退原值。"""
    return _STATE_LABELS.get(state, state)


def _position_lines(position: ProposedPosition) -> list[str]:
    return [
        f"claim: {position.claim}",
        f"decision: {position.decision}",
        f"tradeoff: {position.tradeoff}",
        f"change_mind_if: {position.change_mind_if or ''}",
    ]


def render_position_diff(before: ProposedPosition, after: ProposedPosition) -> str:
    """返回 before → after 的 unified diff 文本。"""
    return "\n".join(
        difflib.unified_diff(_position_lines(before), _position_lines(after), lineterm="")
    )


def render_evidence(cards: list[EvidenceCard]) -> str:
    """渲染证据卡清单（查看完整证据动作）。"""
    if not cards:
        return "（无证据）"
    return "\n".join(f"- {card.claim} [{card.confidence.value}]" for card in cards)


def render_confirm_card(request: InputRequest, cards: list[EvidenceCard]) -> str:
    """渲染作者立场确认卡（面向用户，隐藏内部概念）。"""
    pos = request.proposed_position
    lines = [
        "主题",
        request.topic or "(无主题)",
        "",
        "建议立场",
        f"主张：{pos.claim or '(未填)'}",
        f"方案：{pos.decision or '(未填)'}",
        f"取舍：{pos.tradeoff or '(未填)'}",
    ]
    if pos.change_mind_if:
        lines += ["", "什么情况会改变这个决定？", pos.change_mind_if]
    if request.questions:
        lines += ["", "待回答问题"]
        lines += [f"- {q}" for q in request.questions]
    lines += ["", f"依据：{len(cards)} 张证据卡"]
    return "\n".join(lines)


def render_daily_summary(
    *, posts_found: int | None, engagement_drafts: int, pending_original: int
) -> str:
    """Daily 决策前摘要：互动内容（可选）+ 原创内容。``posts_found`` 为 None 时跳过互动区块。"""
    lines = ["✓ 今日分析已完成", ""]
    if posts_found is not None:
        lines += [
            "互动内容",
            f"  扫描 {posts_found} 条帖子",
            f"  生成 {engagement_drafts} 条互动草稿，等待审核",
            "",
        ]
    lines += [
        "原创内容",
        f"  找到 {pending_original} 个值得展开的主题，需要确认你的立场",
    ]
    return "\n".join(lines)


def render_compact_resolve(request: InputRequest, *, engagement_drafts: int = 0) -> str:
    """非 TTY 紧凑结果：说明分析已完成 + 推荐动作 + 其他命令。"""
    lines = [
        "Daily 分析完成，现有 1 项需要确认。",
        "",
        f"主题：{request.topic or '(无主题)'}",
        "推荐：确认当前立场并继续生成草稿",
        "",
        "  uv run finch run resolve --confirm",
        "",
        "其他操作：",
        "  uv run finch run resolve --edit",
        '  uv run finch run resolve --skip --reason "..."',
        "  uv run finch run resolve --stop",
        "",
        "查看完整信息：",
        "  uv run finch run resolve --json",
    ]
    if engagement_drafts:
        lines += ["", f"另外有 {engagement_drafts} 条互动草稿等待人工审核。"]
    return "\n".join(lines)


def render_outcome(action: InputAction | None) -> str:
    """一次决策后的人性化结果行。"""
    if action is InputAction.CONFIRM:
        return "✓ 已确认你的立场"
    if action is InputAction.EDIT:
        return "✓ 已更新你的立场"
    if action is InputAction.SKIP:
        return "✓ 已跳过这个主题"
    if action is InputAction.STOP:
        return "✓ 已保存并退出"
    return "✓ 已保存进度并退出"


def render_produced(*, engagement_drafts: int, original_drafts: int) -> str:
    """决策 + 恢复完成后的今日产出块。"""
    return "\n".join([
        "✓ 原创草稿已生成",
        "",
        "今日产出",
        f"  互动草稿：{engagement_drafts} 条，等待审核",
        f"  原创草稿：{original_drafts} 条，等待审核",
    ])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_gate_render.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/gate/render.py tests/unit/test_gate_render.py
git commit -m "feat(gate): add state_label + user-facing render functions"
```

---

### Task 2: 交互选择器（`gate/interactive.py`）

**Files:**
- Create: `src/finch/gate/interactive.py`
- Test: `tests/unit/test_gate_interactive.py`

**Interfaces:**
- Consumes: `render_confirm_card` / `render_evidence`（Task 1），`InputAction` / `InputRequest` / `ProposedPosition`（`gate/models.py`），`EvidenceCard`。
- Produces（Task 3/4 依赖）:
  - `select_action(request: InputRequest, cards: list[EvidenceCard]) -> InputAction | None`（`None` = 用户按 `q` 保存进度退出）
  - `edit_position_inline(proposed: ProposedPosition) -> ProposedPosition`（空 Enter 保留原值）

- [ ] **Step 1: 写失败测试** — 新建 `tests/unit/test_gate_interactive.py`。

```python
import pytest

from finch.gate import interactive
from finch.gate.models import InputAction, InputRequest, ProposedPosition


class _FakeTyper:
    def __init__(self, prompts):
        self._prompts = list(prompts)
        self.echoed = []

    def prompt(self, text, default=""):
        if not self._prompts:
            raise AssertionError("no more prompts")
        return self._prompts.pop(0)

    def echo(self, text=""):
        self.echoed.append(text)


def _request():
    return InputRequest(
        run_id="r1", job_id="j1", topic="t",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
    )


def test_select_action_enter_confirms(monkeypatch):
    fake = _FakeTyper([""])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is InputAction.CONFIRM


def test_select_action_keys(monkeypatch):
    for key, expected in [("e", InputAction.EDIT), ("s", InputAction.SKIP)]:
        fake = _FakeTyper([key])
        monkeypatch.setattr(interactive, "typer", fake)
        assert interactive.select_action(_request(), []) is expected


def test_select_action_q_returns_none(monkeypatch):
    fake = _FakeTyper(["q"])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is None


def test_select_action_d_shows_evidence_then_confirms(monkeypatch):
    fake = _FakeTyper(["d", ""])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is InputAction.CONFIRM
    assert any("（无证据）" in e for e in fake.echoed)


def test_select_action_invalid_retries(monkeypatch):
    fake = _FakeTyper(["x", ""])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is InputAction.CONFIRM
    assert any("无效选择" in e for e in fake.echoed)


def test_edit_position_inline_keeps_fields_on_empty(monkeypatch):
    fake = _FakeTyper(["", "方案2", "", ""])
    monkeypatch.setattr(interactive, "typer", fake)
    out = interactive.edit_position_inline(
        ProposedPosition(claim="c", decision="d", tradeoff="t", change_mind_if="cm")
    )
    assert out.claim == "c"
    assert out.decision == "方案2"
    assert out.tradeoff == "t"
    assert out.change_mind_if == "cm"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_gate_interactive.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.gate.interactive'`

- [ ] **Step 3: 实现交互选择器** — 新建 `src/finch/gate/interactive.py`。

```python
"""Gate 交互层：确认选择器与逐字段立场编辑（有 TTY 时调用）。"""

import typer

from finch.evidence.models import EvidenceCard

from .models import InputAction, InputRequest, ProposedPosition
from .render import render_confirm_card, render_evidence


def edit_position_inline(proposed: ProposedPosition) -> ProposedPosition:
    """逐字段编辑立场；直接回车（空输入）保留原值。"""
    typer.echo("编辑你的立场（直接回车表示保留原内容）")
    claim = typer.prompt("主张", default=proposed.claim or "")
    decision = typer.prompt("方案", default=proposed.decision or "")
    tradeoff = typer.prompt("取舍", default=proposed.tradeoff or "")
    change_mind_if = typer.prompt("改变决定的条件", default=proposed.change_mind_if or "")
    return ProposedPosition(
        claim=claim,
        decision=decision,
        tradeoff=tradeoff,
        change_mind_if=change_mind_if or None,
    )


def select_action(request: InputRequest, cards: list[EvidenceCard]) -> InputAction | None:
    """渲染确认卡 + 菜单并读取选择；``None`` 表示保存进度并退出（q）。"""
    typer.echo(render_confirm_card(request, cards))
    typer.echo("")
    typer.echo("如何处理？")
    typer.echo("")
    typer.echo("❯ [Enter] 确认立场并继续生成草稿")
    typer.echo("  [e]    编辑立场")
    typer.echo("  [s]    跳过这个主题")
    typer.echo("  [d]    查看完整依据")
    typer.echo("  [q]    保存进度并退出")
    while True:
        choice = typer.prompt("> ", default="").strip().lower()
        if choice == "":
            return InputAction.CONFIRM
        if choice == "e":
            return InputAction.EDIT
        if choice == "s":
            return InputAction.SKIP
        if choice == "d":
            typer.echo(render_evidence(cards))
            continue
        if choice == "q":
            return None
        typer.echo(f"无效选择：{choice}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_gate_interactive.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/gate/interactive.py tests/unit/test_gate_interactive.py
git commit -m "feat(gate): add interactive selector + inline position editor"
```

---

### Task 3: `finch run resolve` 接线（内联编辑 / 无参交互 / 新 flag）

**Files:**
- Modify: `src/finch/cli.py`（imports、`run_resolve`、`_resume_and_echo` 返回 run + `state_label`）
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `select_action` / `edit_position_inline`（Task 2），`render_compact_resolve` / `render_outcome` / `state_label`（Task 1），既有 `resolve_input` / `_edit_position` / `_resume_nodes` / `_resume_and_echo`。
- Produces: 更新后的 `run_resolve` 命令面（`--edit` 内联、`--edit-editor`、`--interactive`/`--non-interactive`/`--verbose`）；`_resume_and_echo` 现在返回 `RunRecord`。

- [ ] **Step 1: 更新 import** — 在 `src/finch/cli.py` 顶部加入：

```python
import sys
```

并把第 35–37 行附近的 gate import 改为：

```python
from .gate.interactive import edit_position_inline, select_action
from .gate.models import InputAction, InputRequest, ProposedPosition
from .gate.render import (
    render_compact_resolve,
    render_outcome,
    render_position_diff,
    state_label,
)
from .gate.resolve import parse_position_yaml, position_yaml, resolve_input
```

（删除对 `render_input_request` 的 import；`render_evidence` 不再被 cli 直接使用，一并移除。）

- [ ] **Step 2: 改 `_resume_and_echo` 返回 run 并用 `state_label`** — 替换 `src/finch/cli.py:488-493`：

```python
def _resume_and_echo(
    store: Store, nodes: list[Node], run_id: str, *, verbose: bool = False
) -> RunRecord:
    """replay + 打印 state（人性化文案）+ 持久化 run 输出 + 打印 brief，返回 run。"""
    run = replay(store, nodes, run_id)
    typer.echo(state_label(run.state))
    if verbose:
        typer.echo(f"[internal] state={run.state} run_id={run.id}")
    _persist_run_outputs(store, run_id)
    _echo_daily_brief(store, run_id)
    return run
```

（需要 import `RunRecord`：在 storage 的 import 块加入 `from .storage.database import RunRecord, Store`。当前 `Store` 已 import，仅补 `RunRecord`。）

- [ ] **Step 3: 重写 `run_resolve`** — 替换 `src/finch/cli.py:506-605`：

```python
@run_app.command("resolve")
def run_resolve(
    run_id: str | None = typer.Argument(None, help="run id（缺省取最近 NEEDS_INPUT 的 run）"),
    confirm: bool = typer.Option(False, "--confirm", help="确认并继续"),  # noqa: B008
    edit: bool = typer.Option(False, "--edit", help="逐字段编辑立场并继续"),  # noqa: B008
    edit_editor: bool = typer.Option(False, "--edit-editor", help="用 $EDITOR 编辑立场并继续"),  # noqa: B008
    file: Path | None = typer.Option(None, "--file", help="从 YAML 文件读取立场"),  # noqa: B008
    skip: bool = typer.Option(False, "--skip", help="跳过当前主题，尝试下一个"),  # noqa: B008
    reason: str | None = typer.Option(None, "--reason", help="--skip 的拒绝理由"),  # noqa: B008
    stop: bool = typer.Option(False, "--stop", help="结束今天的原创轨道"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="输出 JSON（供 Skill/Agent）"),  # noqa: B008
    interactive: bool = typer.Option(False, "--interactive", help="强制交互选择器"),  # noqa: B008
    non_interactive: bool = typer.Option(False, "--non-interactive", help="强制紧凑输出"),  # noqa: B008
    verbose: bool = typer.Option(False, "--verbose", help="显示内部状态与 run_id"),  # noqa: B008
) -> None:
    """处理当前阻塞点（author position confirmation），完成后自动 resume。"""
    if interactive and non_interactive:
        typer.echo("--interactive 与 --non-interactive 互斥")
        raise typer.Exit(code=1)

    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()

    resolved_run_id = run_id or _latest_needs_input_run_id(store)
    if resolved_run_id is None:
        typer.echo("no run awaiting input")
        raise typer.Exit(code=1)
    try:
        request = _read_input_request(store, resolved_run_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    flags = [confirm, edit, edit_editor, skip, stop, file is not None]
    if sum(1 for f in flags if f) > 1:
        typer.echo("choose exactly one of --confirm/--edit/--edit-editor/--file/--skip/--stop")
        raise typer.Exit(code=1)

    if reason is not None and not skip:
        typer.echo("--reason requires --skip")
        raise typer.Exit(code=1)

    if as_json:
        if any(flags):
            typer.echo("--json 不能与动作 flag 同时使用")
            raise typer.Exit(code=1)
        typer.echo(request.model_dump_json(indent=2))
        return

    jobs_repo = ContentJobRepository(store)
    approvals_repo = PositionApprovalRepository(store)
    cards = _cards_for(request, store)

    action: InputAction | None
    edited: ProposedPosition | None = None
    skip_reason: str | None = reason

    use_interactive = interactive or (not non_interactive and sys.stdout.isatty())

    try:
        if confirm:
            action = InputAction.CONFIRM
        elif edit:
            action = InputAction.EDIT
            edited = edit_position_inline(request.proposed_position)
        elif edit_editor:
            action = InputAction.EDIT
            edited = _edit_position(request.proposed_position)
            typer.echo(render_position_diff(request.proposed_position, edited))
        elif file is not None:
            action = InputAction.EDIT
            edited = parse_position_yaml(file.read_text())
            typer.echo(render_position_diff(request.proposed_position, edited))
        elif skip:
            action = InputAction.SKIP
        elif stop:
            action = InputAction.STOP
        elif use_interactive:
            action = select_action(request, cards)
            if action is None:
                typer.echo(render_outcome(None))
                return
            if action is InputAction.EDIT:
                edited = edit_position_inline(request.proposed_position)
            elif action is InputAction.SKIP:
                skip_reason = typer.prompt("跳过理由", default="not_now")
        else:
            typer.echo(render_compact_resolve(request))
            raise typer.Exit(code=0)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    try:
        summary = resolve_input(
            request,
            action,
            jobs_repo=jobs_repo,
            approvals_repo=approvals_repo,
            edited_position=edited,
            skip_reason=skip_reason,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(render_outcome(action))
    if verbose:
        typer.echo(f"[internal] resolve={summary}")

    nodes = _resume_nodes(settings, store)
    _resume_and_echo(store, nodes, resolved_run_id, verbose=verbose)
```

- [ ] **Step 4: 更新既有 resolve 测试** — 在 `tests/unit/test_cli_run.py` 中：

`test_run_resolve_confirm_resumes`（约 795 行）：把 `assert "confirmed" in r.output` 改为：

```python
    assert "已确认你的立场" in r.output
```

`test_run_resolve_edit_applies`（约 884 行）：monkeypatch 目标改为 `cli.edit_position_inline`：

```python
    monkeypatch.setattr(
        cli, "edit_position_inline",
        lambda proposed: ProposedPosition(claim="edited", decision="d", tradeoff="t"),
    )
```

`test_run_resolve_interactive_confirm`（约 904 行）：invoke 加 `--interactive`，输入改 Enter：

```python
    r = CliRunner().invoke(app, ["run", "resolve", "--interactive"], input="\n")
    assert r.exit_code == 0, r.output
    assert "已确认你的立场" in r.output
    assert ContentJobRepository(store).get_job(request.job_id).author_position.confirmed is True
    assert resumed["run_id"] == "r1"
```

`test_run_resolve_interactive_show_evidence_returns_without_resume`（约 923 行）：invoke 加 `--interactive`，输入 `"d\nq\n"`：

```python
    r = CliRunner().invoke(app, ["run", "resolve", "--interactive"], input="d\nq\n")
    assert r.exit_code == 0, r.output
    assert "无证据" in r.output  # render_evidence 空卡片
    assert resumed == []  # q 直接 return，不 resume
```

`test_run_resolve_interactive_invalid_choice`（约 940 行）：改为无效后重试、q 退出：

```python
    r = CliRunner().invoke(app, ["run", "resolve", "--interactive"], input="x\nq\n")
    assert r.exit_code == 0, r.output
    assert "无效选择" in r.output
```

（保留 `test_run_resolve_json_fetches_input_request`、`--skip`、`--file`、`--reason`、`file_missing/malformed` 不变。）

- [ ] **Step 5: 新增 `--edit-editor` 与紧凑路径测试** — 追加到 `tests/unit/test_cli_run.py`：

```python
def test_run_resolve_edit_editor_applies(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    request = _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_resume_nodes", lambda s, st: [])
    monkeypatch.setattr(cli, "_resume_and_echo", lambda st, nodes, rid, **kw: None)
    monkeypatch.setattr(
        cli, "_edit_position",
        lambda proposed: ProposedPosition(claim="edited", decision="d", tradeoff="t"),
    )

    r = CliRunner().invoke(app, ["run", "resolve", "--edit-editor"])
    assert r.exit_code == 0, r.output
    assert ContentJobRepository(store).get_job(request.job_id).author_position.claim == "edited"


def test_run_resolve_non_interactive_compact(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["run", "resolve", "--non-interactive"])
    assert r.exit_code == 0, r.output
    assert "Daily 分析完成" in r.output
    assert "uv run finch run resolve --confirm" in r.output


def test_run_resolve_interactive_non_interactive_conflict(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["run", "resolve", "--interactive", "--non-interactive"])
    assert r.exit_code == 1
    assert "互斥" in r.output
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k resolve`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): resolve inline edit, no-arg interactive, --edit-editor/--interactive/--verbose"
```

---

### Task 4: `finch run daily` 摘要 + 自动恢复循环 + 紧凑输出

**Files:**
- Modify: `src/finch/cli.py`（`run_daily`、`_echo_dual_track_result`、新增 `_finish_daily` / `_original_draft_count`）
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `render_daily_summary` / `render_compact_resolve` / `render_outcome` / `render_produced` / `state_label`（Task 1），`select_action` / `edit_position_inline`（Task 2），`_read_input_request` / `_cards_for` / `_persist_run_outputs` / `_echo_daily_brief` / `resolve_input`（既有），`replay`（既有）。
- Produces: 更新后的 `run_daily`（TTY 摘要+选择器循环 / 非 TTY 紧凑）；`_echo_dual_track_result` 用 `state_label`；新增 `_finish_daily` / `_original_draft_count` 助手。

- [ ] **Step 1: 更新 `_echo_dual_track_result` 用 `state_label`** — 替换 `src/finch/cli.py:169-170`：

```python
    if result.original is not None:
        typer.echo(state_label(result.original.state))
        _persist_run_outputs(store, result.original.id)
        _echo_daily_brief(store, result.original.id)
```

- [ ] **Step 2: 新增助手** — 在 `_echo_dual_track_result` 之后加入：

```python
def _original_draft_count(store: Store, run_id: str) -> int:
    """读 draft 节点输出，统计本次 run 生成的原创草稿数。"""
    record = store.find_node(run_id, "draft", "default")
    if record is None or not record.output_json:
        return 0
    return len(parse_items(json.loads(record.output_json), Draft))


def _finish_daily(
    store: Store,
    nodes: list[Node],
    run_id: str,
    *,
    engagement_drafts: int,
    posts_found: int | None,
    use_interactive: bool,
    verbose: bool,
) -> None:
    """run 停在 NEEDS_INPUT 时的收尾：TTY 进交互循环自动恢复，非 TTY 输出紧凑结果。"""
    request = _read_input_request(store, run_id)
    if not use_interactive:
        typer.echo(
            render_daily_summary(
                posts_found=posts_found,
                engagement_drafts=engagement_drafts,
                pending_original=1,
            )
        )
        typer.echo("")
        typer.echo(render_compact_resolve(request, engagement_drafts=engagement_drafts))
        return

    jobs_repo = ContentJobRepository(store)
    approvals_repo = PositionApprovalRepository(store)
    while True:
        typer.echo(
            render_daily_summary(
                posts_found=posts_found,
                engagement_drafts=engagement_drafts,
                pending_original=1,
            )
        )
        typer.echo("")
        cards = _cards_for(request, store)
        action = select_action(request, cards)
        if action is None:
            typer.echo(render_outcome(None))
            return
        edited: ProposedPosition | None = None
        skip_reason: str | None = None
        if action is InputAction.EDIT:
            edited = edit_position_inline(request.proposed_position)
        elif action is InputAction.SKIP:
            skip_reason = typer.prompt("跳过理由", default="not_now")
        try:
            resolve_input(
                request,
                action,
                jobs_repo=jobs_repo,
                approvals_repo=approvals_repo,
                edited_position=edited,
                skip_reason=skip_reason,
            )
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        typer.echo(render_outcome(action))
        run = _resume_and_echo(store, nodes, run_id, verbose=verbose)
        if run.state != GraphState.NEEDS_INPUT.value:
            typer.echo(
                render_produced(
                    engagement_drafts=engagement_drafts,
                    original_drafts=_original_draft_count(store, run_id),
                )
            )
            return
        request = _read_input_request(store, run_id)
```

- [ ] **Step 3: 重写 `run_daily`** — 替换 `src/finch/cli.py:328-399`：

```python
@run_app.command("daily")
def run_daily(
    interactive: bool = typer.Option(False, "--interactive", help="强制交互选择器"),  # noqa: B008
    non_interactive: bool = typer.Option(False, "--non-interactive", help="强制紧凑输出"),  # noqa: B008
    verbose: bool = typer.Option(False, "--verbose", help="显示内部状态与 run_id"),  # noqa: B008
) -> None:
    """运行每日 Graph：同步 commit → 提取证据卡 → 收集推文 → 匹配证据 → 撰写与审查草稿。"""
    if interactive and non_interactive:
        typer.echo("--interactive 与 --non-interactive 互斥")
        raise typer.Exit(code=1)

    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    gh = GhClient()
    opencli = OpenCliClient()
    reddit_opencli = RedditOpenCliClient()

    repos = resolve_repositories(settings, gh)

    ingestion_repo = CommitIngestionRepository(store)
    cursor_repo = RepoCursorRepository(store)
    existing_topics = {
        topic for card in EvidenceRepository(store).list_cards() for topic in card.topics
    }
    groups_by_repo = Ingestor(gh, settings, ingestion_repo, cursor_repo).ingest(
        repos, existing_topics=existing_topics
    )

    repo_is_private: dict[str, bool] = {}
    known_commit_urls: set[str] = set()
    for repo in repos:
        repo_is_private[repo] = gh.repo_view(repo).is_private
    for repo, groups in groups_by_repo.items():
        for group in groups:
            for commit in group:
                known_commit_urls.add(f"https://github.com/{repo}/commit/{commit.sha}")

    nodes = daily_nodes(
        settings=settings,
        store=store,
        gh=gh,
        opencli=opencli,
        extractor=Extractor(
            create_runner(settings.llm) or CodexRunner(),
            settings=settings.extraction,
            cache_path=settings.paths.cache_dir / "extraction_cache.json",
        ),
        runner=CodexRunner(),
        groups_by_repo=groups_by_repo,
        known_commit_urls=known_commit_urls,
        repo_is_private=repo_is_private,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        inference_runners={
            "match_evidence": create_runner(settings.llm, "match_evidence"),
            "plan_topics": create_runner(settings.llm, "plan_topics"),
            "expand_job": create_runner(settings.llm, "expand_job"),
            "critique": create_runner(settings.llm, "critique"),
        },
    )

    use_interactive = interactive or (not non_interactive and sys.stdout.isatty())

    if settings.engagement.enabled:
        start = time.monotonic()
        result = run_dual_track(
            original_track=lambda rid: GraphRuntime(store, nodes).run(run_id=rid),
            engagement_track=lambda rid: run_discovery_engagement_flow(
                settings, opencli, CodexRunner(),
                run_id=rid, reddit_opencli=reddit_opencli,
            ),
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        _persist_engagement_candidates(result, store)
        _persist_engagement_run_stats(result, store, latency_ms=latency_ms)

        engagement = result.engagement
        engagement_drafts = sum(
            1 for c in (engagement.candidates if engagement else []) if c.draft is not None
        )
        posts_found = engagement.posts_found if engagement else 0

        original = result.original
        if (
            original is not None
            and original.state == GraphState.NEEDS_INPUT.value
            and not result.original_failed
        ):
            _finish_daily(
                store,
                nodes,
                original.id,
                engagement_drafts=engagement_drafts,
                posts_found=posts_found,
                use_interactive=use_interactive,
                verbose=verbose,
            )
            return

        _echo_dual_track_result(result, store)
        return

    run = GraphRuntime(store, nodes).run()
    if run.state == GraphState.NEEDS_INPUT.value:
        _finish_daily(
            store,
            nodes,
            run.id,
            engagement_drafts=0,
            posts_found=None,
            use_interactive=use_interactive,
            verbose=verbose,
        )
        return
    typer.echo(state_label(run.state))
    if verbose:
        typer.echo(f"[internal] state={run.state} run_id={run.id}")
    _persist_run_outputs(store, run.id)
    _echo_daily_brief(store, run.id)
```

- [ ] **Step 4: 更新既有 daily 测试**

`tests/unit/test_cli_run.py::test_run_daily_enabled_echoes_engagement_summary`：断言新增 `"已完成"`（state_label 输出）：

```python
    assert "engagement: no posts found" in r.output
    assert "已完成" in r.output
```

（`test_run_daily_uses_ingestor` 不变——空 nodes 落在 COMPLETED，非 TTY 不触发交互，exit 0。）

- [ ] **Step 5: 新增 daily 交互/紧凑测试** — 追加到 `tests/unit/test_cli_run.py`：

```python
def test_run_daily_non_interactive_compact_on_needs_input(monkeypatch, tmp_path):
    """原创轨道停在 NEEDS_INPUT 时，非 TTY 输出紧凑结果而非阻塞。"""
    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        paths=Paths(db_path=tmp_path / "finch.db"),
        engagement=EngagementSettings(enabled=False),
    )
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    class FakeIngestor:
        def __init__(self, gh, settings, ingestion, cursor):
            pass

        def ingest(self, repos, existing_topics=None):
            return {}

    class FakeGh:
        def repo_view(self, repo):
            from finch.github.models import RepoInfo

            return RepoInfo(name_with_owner=repo, default_branch="main",
                            url="https://github.com/" + repo, is_private=False)

    from finch.graph.events import NodeResult
    from finch.graph.nodes import Node

    class BlockingGate(Node):
        def run(self, ctx):
            from finch.gate.models import InputRequest, ProposedPosition

            request = InputRequest(
                run_id=ctx.get("run_id", ""), job_id="j1", topic="t",
                proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
            )
            return NodeResult(
                status="needs_input", output={"input_request": request.model_dump(mode="json")},
            )

    def build_nodes(**kw):
        return [BlockingGate(name="position_gate", reads=[], writes="ready_jobs")]

    monkeypatch.setattr(cli, "GhClient", lambda: FakeGh())
    monkeypatch.setattr(cli, "Ingestor", FakeIngestor)
    monkeypatch.setattr(cli, "daily_nodes", build_nodes)
    monkeypatch.setattr(cli, "load_voice_profile", lambda path: None)

    r = CliRunner().invoke(app, ["run", "daily", "--non-interactive"])
    assert r.exit_code == 0, r.output
    assert "Daily 分析完成" in r.output
    assert "uv run finch run resolve --confirm" in r.output


def test_run_daily_interactive_auto_resumes(monkeypatch, tmp_path):
    """TTY 交互：Enter 确认后自动恢复并输出今日产出。"""
    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        paths=Paths(db_path=tmp_path / "finch.db"),
        engagement=EngagementSettings(enabled=False),
    )
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    class FakeIngestor:
        def __init__(self, gh, settings, ingestion, cursor):
            pass

        def ingest(self, repos, existing_topics=None):
            return {}

    class FakeGh:
        def repo_view(self, repo):
            from finch.github.models import RepoInfo

            return RepoInfo(name_with_owner=repo, default_branch="main",
                            url="https://github.com/" + repo, is_private=False)

    from finch.gate.models import InputRequest, ProposedPosition
    from finch.graph.events import NodeResult
    from finch.graph.nodes import Node

    class BlockingGate(Node):
        def run(self, ctx):
            request = InputRequest(
                run_id=ctx.get("run_id", ""), job_id="j1", topic="t",
                proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
            )
            return NodeResult(
                status="needs_input", output={"input_request": request.model_dump(mode="json")},
            )

    class DoneGate(Node):
        def run(self, ctx):
            return NodeResult(status="succeeded", output={}, succeeds_to="COMPLETED")

    def build_nodes(**kw):
        return [BlockingGate(name="position_gate", reads=[], writes="ready_jobs")]

    monkeypatch.setattr(cli, "GhClient", lambda: FakeGh())
    monkeypatch.setattr(cli, "Ingestor", FakeIngestor)
    monkeypatch.setattr(cli, "daily_nodes", build_nodes)
    monkeypatch.setattr(cli, "load_voice_profile", lambda path: None)

    # 第一次 run 停在 NEEDS_INPUT；确认后 replay 应继续到 COMPLETED。
    # 为让 replay 复用节点，position_gate 需读 repo 的最新 job；这里用空 job 列表模拟
    # 一个已确认立场的 world：直接 monkeypatch resolve_input 无副作用、replay 走 DoneGate。
    monkeypatch.setattr(
        cli, "resolve_input",
        lambda request, action, **kw: "confirmed",
    )
    def fake_resume(store, nodes, run_id, *, verbose=False):
        from finch.storage.database import RunRecord

        store.upsert_run(RunRecord(id=run_id, state="COMPLETED"))
        return store.get_run(run_id)

    monkeypatch.setattr(cli, "_resume_and_echo", fake_resume)

    r = CliRunner().invoke(app, ["run", "daily", "--interactive"], input="\n")
    assert r.exit_code == 0, r.output
    assert "已确认你的立场" in r.output
    assert "今日产出" in r.output
```

（注：`_finish_daily` 内 `_resume_and_echo` 被 monkeypatch 为返回 COMPLETED 的 run，从而退出循环并打印 `render_produced`。`_original_draft_count` 读不到 draft 节点输出时返回 0，不报错。）

- [ ] **Step 6: 运行全部 CLI 测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): daily summary + auto-resume loop + non-tty compact output"
```

---

### Task 5: 全量回归 + lint/type

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: PASS（全绿）

- [ ] **Step 2: lint + 类型**

Run: `uv run ruff check . && uv run mypy src`
Expected: 无错误（若 `interactive.py` 的 `select_action` 返回类型推断有歧义，加显式 `-> InputAction | None`）。

- [ ] **Step 3: 提交（如有格式/类型修正）**

```bash
git add -A && git commit -m "chore: lint + type fixes for interactive daily step"
```

---

## Self-Review 结果

- **Spec 覆盖**：§1.1 状态分层 → Task 1（`state_label`）+ Task 3/4（`--verbose`）；§3.2 渲染 → Task 1；§3.3 选择器 → Task 2；§3.4 CLI 接线 → Task 3/4；§3.5 自动恢复循环 → Task 4（`_finish_daily` 循环）；§5 错误处理 → 各 Task 的 `typer.Exit` 干净路径 + 非 TTY 不调用 `typer.prompt`；§6 测试 → 各 Task 测试步骤。无遗漏。
- **Placeholder 扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`select_action` / `edit_position_inline` / `render_*` / `state_label` 签名在 Task 1/2 定义、Task 3/4 引用处一致；`_resume_and_echo(..., verbose=False) -> RunRecord` 在 Task 3 定义、Task 4 调用处一致。
