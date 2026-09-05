# define_jobs flash + plan/expand split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `define_jobs` 从单次生成 63 张卡片的巨大 JSON（`deepseek-v4-pro`、90s 必超时）改为 `deepseek-v4-flash` 驱动的两阶段拆分（`plan_topics` 一次小请求 + `expand_job` 并行小请求），并把 critique 的检查器切到 `deepseek-v4-pro`。

**Architecture:** 保留图节点 `define_jobs`（写 `content_jobs`、`succeeds_to="JOBS_DEFINED"`），把节点内部实现拆成两阶段：`plan_content_topics`（一次 flash 调用，输出主题分组）→ `expand_content_job`（按主题并行 flash 调用，输出完整 `ContentJob`），最后在 Python 侧确定性去重/排序/校验。模型/超时/输出上限经 `LLMSettings.for_node(name)` 解析，烘进每个 runner 实例。默认模型切到 `deepseek-v4-flash`，critique 检查器单独用 `deepseek-v4-pro`。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、标准库 `urllib`（无 SDK）、`ThreadPoolExecutor`（`pool.map` 保序）。

## Global Constraints

- Python 3.12+；Pydantic 2（`StrEnum`/`Literal`/`Field`）。
- `StructuredInferenceRunner.run(prompt, output_model, *, timeout=...)` 协议保持不变 —— 模型/超时/输出上限烘进 runner 实例，不在调用点硬编码模型。
- 图 runtime 单线程、确定性、幂等、可 replay；节点内并行仅用 `ThreadPoolExecutor` + `pool.map`（保序）。
- 子进程/HTTP 调用 timeout 逐调用设置；JSON 输出经 Pydantic 校验。
- Ruff 选择 `E,F,I,B,UP`；mypy 覆盖 `src`；双语 docstring 匹配周边文件。

---

## Design decisions (locked)

1. **拆分粒度 = 节点内部两阶段，不新增图节点。** 保留节点名 `define_jobs`（`writes="content_jobs"`），避免改状态机（`state.py`）、replay 和 ~15 处测试 `Seed(name="define_jobs")`。`plan_topics`/`expand_job` 是**两个 LLM 调用类型**，对应两个 `llm.nodes.*` 配置项与两个 prompt。并行用 `pool.map`（与 `make_draft_node`/`_run_checks` 同款）。
2. **配置形状：`llm.model` 作默认 + `llm.nodes.<name>` 覆盖。** 不引入 `llm.default` 嵌套块（现有 `create_runner` 已读 `llm.model`）。`for_node(name)` 合并节点覆盖到 `LLMNodeSettings(model=self.model)`。
3. **模型/超时/上限烘进 runner。** `OpenAICompatibleRunner.__init__(..., timeout, max_tokens)`；`create_runner(llm, node_name=None)` 按节点构建。协议 `run` 不新增参数。
4. **`max_output_tokens` 映射到 HTTP `max_tokens`。** 配置字段名 `max_output_tokens`（贴合方案），请求体字段 `max_tokens`（OpenAI/DeepSeek 兼容）。
5. **critique 检查器切 pro，rewrite 留在 codex。** `make_critique_node` 增加 `checker_runner` 参数（默认 `CodexRunner`）；`rewrite` 仍用 `CodexRunner`。检查器 `__init__` 的 runner 类型从 `CodexRunner | None` 放宽为 `StructuredInferenceRunner | None`（8 个文件机械改动）。

## File map

- **Modify** `src/finch/settings.py` — 新增 `LLMNodeSettings`、`LLMSettings.nodes`、`for_node()`。
- **Modify** `src/finch/llm/openai_compatible.py` — `OpenAICompatibleRunner` 增 `timeout`/`max_tokens`；`create_runner(llm, node_name)`。
- **Modify** `src/finch/content/checkers/{evidence,decision,specificity,portability,voice,structure,actionability,safety}.py` — runner 类型 `CodexRunner | None` → `StructuredInferenceRunner | None`。
- **Modify** `src/finch/graph/content_nodes.py` — `default_checker_suite` 类型；`make_critique_node` 增 `checker_runner`；重写 `make_define_jobs_node`。
- **Modify** `src/finch/content/jobs.py` — 移除 `define_content_jobs`，新增 `TopicProposal`/`PlanTopicsOutput`/`plan_content_topics`/`expand_content_job`。
- **Modify** `src/finch/graph/daily.py` — 按节点解析 runner，传入 `make_define_jobs_node` 与 `make_critique_node`。
- **Modify** `src/finch/cli.py` — `run_daily`/`run_resume` 构建 per-node runner 字典。
- **Create** `prompts/plan-content-topics.md`、`prompts/expand-content-job.md`。
- **Modify** `finch.yaml` — `llm.model: deepseek-v4-flash` + `llm.nodes.{plan_topics,expand_job,critique}`。
- **Tests**: `tests/unit/test_settings.py`、`tests/unit/test_openai_compatible.py`、`tests/unit/test_jobs.py`、`tests/graph/test_content_nodes.py`、`tests/graph/test_daily.py`、`tests/unit/test_cli_run.py`。

---

## Task 1: LLM 节点配置（`LLMNodeSettings` + `for_node`）

**Files:**
- Modify: `src/finch/settings.py:28-33`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces: `LLMNodeSettings(model, timeout_seconds, max_output_tokens, max_concurrency)`；`LLMSettings.nodes: dict[str, LLMNodeSettings]`；`LLMSettings.for_node(name) -> LLMNodeSettings`。

- [ ] **Step 1: 写失败测试** — 在 `tests/unit/test_settings.py` 追加：

```python
from finch.settings import LLMNodeSettings, LLMSettings


def test_for_node_returns_default_model_when_no_node():
    llm = LLMSettings(model="deepseek-v4-flash")
    cfg = llm.for_node("define_jobs")
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.timeout_seconds == 90.0
    assert cfg.max_output_tokens is None
    assert cfg.max_concurrency == 1


def test_for_node_merges_node_over_default():
    llm = LLMSettings(
        model="deepseek-v4-flash",
        nodes={"critique": LLMNodeSettings(
            model="deepseek-v4-pro", timeout_seconds=300.0, max_output_tokens=4096,
        )},
    )
    cfg = llm.for_node("critique")
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.timeout_seconds == 300.0
    assert cfg.max_output_tokens == 4096


def test_for_node_inherits_default_model_when_node_model_blank():
    llm = LLMSettings(
        model="deepseek-v4-flash",
        nodes={"plan_topics": LLMNodeSettings(timeout_seconds=120.0, max_output_tokens=2000)},
    )
    cfg = llm.for_node("plan_topics")
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.timeout_seconds == 120.0
    assert cfg.max_output_tokens == 2000
```

- [ ] **Step 2: 运行确认失败** — `uv run pytest tests/unit/test_settings.py -k for_node -v` → `LLMNodeSettings`/`for_node` 未定义。

- [ ] **Step 3: 实现** — 在 `settings.py` 加：

```python
class LLMNodeSettings(BaseModel):
    """单个 LLM 节点的模型/超时/输出上限/并发覆盖。"""

    model: str = ""
    timeout_seconds: float = 90.0
    max_output_tokens: int | None = None
    max_concurrency: int = 1
```

并把 `LLMSettings` 改为：

```python
class LLMSettings(BaseModel):
    """OpenAI 兼容 LLM 配置（base_url + model；api_key 优先读环境变量 LLM_API_KEY）。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    nodes: dict[str, LLMNodeSettings] = Field(default_factory=dict)

    def for_node(self, name: str) -> LLMNodeSettings:
        """按节点名解析合并后的节点配置；未配置时回退到默认模型。"""
        node = self.nodes.get(name)
        if node is None:
            return LLMNodeSettings(model=self.model)
        return LLMNodeSettings(
            model=node.model or self.model,
            timeout_seconds=node.timeout_seconds,
            max_output_tokens=node.max_output_tokens,
            max_concurrency=node.max_concurrency,
        )
```

- [ ] **Step 4: 运行确认通过** — `uv run pytest tests/unit/test_settings.py -v`。

- [ ] **Step 5: 提交** — `git add src/finch/settings.py tests/unit/test_settings.py && git commit -m "feat(llm): add per-node LLMNodeSettings and for_node resolution"`

---

## Task 2: runner 增 `max_tokens`/`timeout`，`create_runner` 按节点构建

**Files:**
- Modify: `src/finch/llm/openai_compatible.py`
- Test: `tests/unit/test_openai_compatible.py`

**Interfaces:**
- Produces: `OpenAICompatibleRunner(base_url, api_key, model, *, timeout=90.0, max_tokens=None)`；`run(prompt, output_model, *, timeout=None, max_attempts=2)`；`create_runner(llm, node_name=None) -> OpenAICompatibleRunner | None`。

- [ ] **Step 1: 写失败测试** — `tests/unit/test_openai_compatible.py` 追加：

```python
def _runner_with_cap():
    return OpenAICompatibleRunner(
        "https://gateway.example/v1", "secret", "deepseek-v4-flash",
        timeout=180.0, max_tokens=6000,
    )


def test_runner_sends_max_tokens_and_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(_response('{"value": 42}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _runner_with_cap().run("score this", _Out)

    data = json.loads(captured["request"].data)
    assert data["model"] == "deepseek-v4-flash"
    assert data["max_tokens"] == 6000
    assert captured["timeout"] == 180.0


def test_runner_no_max_tokens_when_unset(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return _FakeResponse(_response('{"value": 1}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _runner().run("p", _Out)

    assert "max_tokens" not in json.loads(captured["request"].data)
```

- [ ] **Step 2: 运行确认失败** — `uv run pytest tests/unit/test_openai_compatible.py -k max_tokens -v`。

- [ ] **Step 3: 实现** — `OpenAICompatibleRunner` 改为：

```python
def __init__(self, base_url: str, api_key: str, model: str, *,
             timeout: float = 90.0, max_tokens: int | None = None) -> None:
    self.base_url = base_url.rstrip("/")
    self.api_key = api_key
    self.model = model
    self.timeout = timeout
    self.max_tokens = max_tokens

def run(self, prompt, output_model, *, timeout=None, max_attempts=2):
    effective_timeout = self.timeout if timeout is None else timeout
    last_error = None
    for _ in range(max_attempts):
        try:
            return self._run_once(prompt, output_model, timeout=effective_timeout)
        except (json.JSONDecodeError, StructuredOutputError) as exc:
            last_error = exc
    raise StructuredOutputError(...)
```

`_run_once` 签名加 `max_tokens` 并在 payload 里条件加入：

```python
def _run_once(self, prompt, output_model, *, timeout):
    schema = json.dumps(output_model.model_json_schema())
    payload: dict = {
        "model": self.model,
        "messages": [ ... 同现有 ... ],
        "temperature": 0,
    }
    if self.max_tokens is not None:
        payload["max_tokens"] = self.max_tokens
    payload_bytes = json.dumps(payload).encode("utf-8")
    ...
```

`create_runner` 改为：

```python
def create_runner(llm: LLMSettings, node_name: str | None = None) -> OpenAICompatibleRunner | None:
    if not llm.base_url:
        return None
    api_key = os.environ.get("LLM_API_KEY") or llm.api_key
    if not api_key:
        return None
    node = llm.for_node(node_name) if node_name else LLMNodeSettings(model=llm.model)
    if not node.model:
        return None
    return OpenAICompatibleRunner(
        base_url=llm.base_url, api_key=api_key, model=node.model,
        timeout=node.timeout_seconds, max_tokens=node.max_output_tokens,
    )
```

（注意：`create_runner` 现需 `from ..settings import LLMNodeSettings, LLMSettings`。）

- [ ] **Step 4: 运行确认通过** — `uv run pytest tests/unit/test_openai_compatible.py -v`。

- [ ] **Step 5: 提交** — `git add src/finch/llm/openai_compatible.py tests/unit/test_openai_compatible.py && git commit -m "feat(llm): support max_tokens, per-instance timeout, per-node create_runner"`

---

## Task 3: 检查器 runner 类型放宽为 `StructuredInferenceRunner`

**Files:**
- Modify: `src/finch/content/checkers/evidence.py`、`decision.py`、`specificity.py`、`portability.py`、`voice.py`、`structure.py`、`actionability.py`、`safety.py`
- Modify: `src/finch/graph/content_nodes.py`（`default_checker_suite` 签名）

**Interfaces:**
- Consumes: `StructuredInferenceRunner`（`llm/base.py`）。
- Produces: 各 checker `__init__(runner: StructuredInferenceRunner | None = None)`；`default_checker_suite(runner: StructuredInferenceRunner | None, voice_profile=None)`。

- [ ] **Step 1: 机械替换** — 每个 checker 文件：`from finch.codex.runner import CodexRunner` → `from finch.llm.base import StructuredInferenceRunner`；`def __init__(self, runner: CodexRunner | None = None)` → `def __init__(self, runner: StructuredInferenceRunner | None = None)`。其余（`self._runner.run(...)`、raise 文案）不动。
- [ ] **Step 2: `default_checker_suite`** — `content_nodes.py` 里签名 `runner: CodexRunner` → `runner: StructuredInferenceRunner | None`，并把 `StructuredInferenceRunner` 加入该文件已有 import（当前第 30 行已 import）。
- [ ] **Step 3: 运行确认** — `uv run pytest tests/unit/test_checkers.py tests/graph/test_content_nodes.py -v`（类型注解不影响运行，但确认无 import 断链）。
- [ ] **Step 4: mypy** — `uv run mypy src/finch/content/checkers`。
- [ ] **Step 5: 提交** — `git add src/finch/content/checkers src/finch/graph/content_nodes.py && git commit -m "refactor(checkers): accept StructuredInferenceRunner instead of CodexRunner"`

---

## Task 4: `jobs.py` 拆分 `plan_content_topics` + `expand_content_job`

**Files:**
- Modify: `src/finch/content/jobs.py`
- Create: `prompts/plan-content-topics.md`、`prompts/expand-content-job.md`
- Test: `tests/unit/test_jobs.py`

**Interfaces:**
- Consumes: `StructuredInferenceRunner`、`EvidenceCard`、`MatchResult`（`evidence/models.py`）、`DiscussionCandidate`（`twitter/models.py`）。
- Produces:
  - `TopicProposal(id, title, card_ids: list[str], candidate_id: str | None = None)`
  - `PlanTopicsOutput(items: list[TopicProposal])`
  - `plan_content_topics(runner, cards, match_results, candidates) -> PlanTopicsOutput`
  - `expand_content_job(runner, topic, cards_by_id, candidate) -> ContentJob`

- [ ] **Step 1: 写失败测试** — `tests/unit/test_jobs.py` 把 `define_content_jobs` import 替换为 `plan_content_topics, expand_content_job, TopicProposal, PlanTopicsOutput`，并新增：

```python
class _TopicsRunner(CodexRunner):
    def __init__(self, topics):
        self.topics = topics
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        if output_model is PlanTopicsOutput:
            return PlanTopicsOutput(items=self.topics)
        raise AssertionError(f"unexpected output_model {output_model}")


def test_plan_content_topics_skips_runner_when_no_cards():
    runner = _ExplodingRunner()
    out = plan_content_topics(runner, [], [], [])
    assert out.items == []
    assert runner.calls == 0


def test_expand_content_job_forces_confirmed_false():
    class _JobRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            return ContentJob(
                id="job1", source_card_ids=["ev1"], candidate_id="t1",
                reader_problem="r", audience="a",
                intended_effect=IntendedEffect(understand="u"),
                author_position=AuthorPosition(
                    claim="c", decision="d", tradeoff="t", confirmed=True,
                ),
                success_criteria=[], recommended_format=DraftKind.REPLY,
                status=ContentJobStatus.READY,
            )

    topic = TopicProposal(id="tp1", title="t", card_ids=["ev1"], candidate_id="t1")
    card = EvidenceCard(
        id="ev1", event_id="e", claim="token bucket", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["rate"],
    )
    job = expand_content_job(_JobRunner(), topic, {"ev1": card}, None)
    assert job.author_position is not None
    assert job.author_position.confirmed is False
```

（保留原有 `test_define_content_jobs_skips_runner_when_no_cards` 的意图，重命名覆盖到 `plan_content_topics`。）

- [ ] **Step 2: 运行确认失败**。

- [ ] **Step 3: 实现** — `jobs.py` 删除 `define_content_jobs`（保留 `ContentJob`/`ContentJobsOutput` 相关模型不动，`ContentJobsOutput` 仍被部分测试引用），新增：

```python
class TopicProposal(BaseModel):
    """plan-topics 输出的一个内容主题：主题 id、一句话标题、所属卡片与候选归属。"""

    id: str
    title: str
    card_ids: list[str]
    candidate_id: str | None = None


class PlanTopicsOutput(BaseModel):
    items: list[TopicProposal]


def plan_content_topics(
    runner: StructuredInferenceRunner,
    cards: list[EvidenceCard],
    match_results: list[MatchResult],
    candidates: list[DiscussionCandidate],
) -> PlanTopicsOutput:
    """一次 flash 调用：把证据卡聚类成少量主题并决定 reply/original 归属。"""
    if not cards:
        return PlanTopicsOutput(items=[])
    slim_cards = [
        {"id": c.id, "claim": c.claim, "topics": c.topics} for c in cards
    ]
    slim_matches = [
        {"candidate_id": m.candidate_id, "card_ids": m.card_ids} for m in match_results
    ]
    slim_candidates = [{"id": c.id, "text": c.text} for c in candidates]
    template = Path("prompts/plan-content-topics.md").read_text()
    prompt = template.replace("{cards}", dumps(slim_cards)).replace(
        "{matches}", dumps(slim_matches)
    ).replace("{candidates}", dumps(slim_candidates))
    return cast(PlanTopicsOutput, runner.run(prompt, PlanTopicsOutput))


def expand_content_job(
    runner: StructuredInferenceRunner,
    topic: TopicProposal,
    cards_by_id: dict[str, EvidenceCard],
    candidate: DiscussionCandidate | None,
) -> ContentJob:
    """一次 flash 调用：把单个主题展开成完整 ContentJob，并强制 confirmed=False。"""
    cards = [cards_by_id[cid] for cid in topic.card_ids if cid in cards_by_id]
    template = Path("prompts/expand-content-job.md").read_text()
    prompt = template.replace("{topic}", dumps(topic.model_dump(mode="json"))).replace(
        "{cards}", dumps([c.model_dump(mode="json") for c in cards])
    ).replace("{candidate}", dumps(candidate.model_dump(mode="json")) if candidate else "null")
    job = cast(ContentJob, runner.run(prompt, ContentJob))
    if job.author_position is not None:
        job.author_position = job.author_position.model_copy(update={"confirmed": False})
    return job
```

- [ ] **Step 4: 写两个 prompt**：

`prompts/plan-content-topics.md`：系统要求“聚类证据卡、最多 N 个主题、只回 `{"items":[...]}`”，字段 `id/title/card_ids/candidate_id`；给出示例。`prompts/expand-content-job.md`：沿用现有 `define-content-jobs.md` 的单 job 字段说明，但输入是单个主题 + 该主题卡片 + 候选。

- [ ] **Step 5: 运行确认通过** — `uv run pytest tests/unit/test_jobs.py -v`。

- [ ] **Step 6: 提交** — `git add src/finch/content/jobs.py prompts/plan-content-topics.md prompts/expand-content-job.md tests/unit/test_jobs.py && git commit -m "feat(content): split define_content_jobs into plan_topics + expand_job"`

---

## Task 5: 重写 `make_define_jobs_node`（两阶段 + 并行 + 校验）

**Files:**
- Modify: `src/finch/graph/content_nodes.py:453-506`
- Test: `tests/graph/test_content_nodes.py`

**Interfaces:**
- Consumes: `plan_content_topics`、`expand_content_job`、`TopicProposal`（Task 4）。
- Produces: `make_define_jobs_node(plan_runner, expand_runner, expand_concurrency=4, jobs_repo=None) -> Node`，节点名仍为 `"define_jobs"`、`writes="content_jobs"`、`succeeds_to="JOBS_DEFINED"`、`reads=["match_results", "evidence_cards", "candidates"]`。

- [ ] **Step 1: 更新测试** — `tests/graph/test_content_nodes.py` 里 `make_define_jobs_node(runner)` → `make_define_jobs_node(runner, runner)`；`FakeJobsRunner` 改为返回两阶段输出（`PlanTopicsOutput` for plan，`ContentJob` for expand）。新增“无卡短路”“并行保序”用例。
- [ ] **Step 2: 实现** — `make_define_jobs_node` 的 `run` 改为：解析 match/cards/candidates → 无卡短路 → `plan_content_topics` → 校验 topic（`card_ids` ⊆ 可用、`candidate_id` 存在于 match）→ 按 `expand_concurrency` 用 `ThreadPoolExecutor` + `pool.map` 并行 `expand_content_job` → 复用原 `validate_source_cards` 过滤 → upsert/返回。`candidate_id` 解析用 `candidates_by_id`。
- [ ] **Step 3: 运行确认** — `uv run pytest tests/graph/test_content_nodes.py -v`。
- [ ] **Step 4: 提交** — `git add src/finch/graph/content_nodes.py tests/graph/test_content_nodes.py && git commit -m "feat(graph): two-phase define_jobs node with parallel expand"`

---

## Task 6: `daily_nodes` + `cli.py` 按节点解析 runner

**Files:**
- Modify: `src/finch/graph/daily.py`
- Modify: `src/finch/cli.py`
- Test: `tests/graph/test_daily.py`、`tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `create_runner(llm, node_name)`（Task 2）。
- Produces: `daily_nodes(..., inference_runners: dict[str, StructuredInferenceRunner] | None = None, ...)`。

- [ ] **Step 1: `daily.py`** — `daily_nodes` 增参 `inference_runners: dict[str, StructuredInferenceRunner | None] | None = None`。内部：`def _resolve(node_name): 返回 inference_runners[node_name] or runner`。`make_match_node(_resolve("match_evidence"), ...)`；`make_define_jobs_node(_resolve("plan_topics"), _resolve("expand_job"), expand_concurrency=settings.llm.for_node("expand_job").max_concurrency, jobs_repo=...)`；`make_critique_node(runner, rewrite, gates, checkers=default_checker_suite(_resolve("critique"), voice_profile), voice_profile=...)`。
- [ ] **Step 2: `cli.py`** — `run_daily`/`run_resume` 里把 `inference_runner=create_runner(settings.llm)` 换成：

```python
inference_runners={
    "match_evidence": create_runner(settings.llm, "match_evidence"),
    "plan_topics": create_runner(settings.llm, "plan_topics"),
    "expand_job": create_runner(settings.llm, "expand_job"),
    "critique": create_runner(settings.llm, "critique"),
},
```

（`extractor=Extractor(create_runner(settings.llm) or CodexRunner(), ...)` 保持默认 flash 不变。）

- [ ] **Step 3: 更新 `test_daily.py`** — `test_daily_runtime_full_pipeline_and_hydration` 的 `FakeRunner` 增 `PlanTopicsOutput` 分支（返回把单卡分到 `TopicProposal(id="tp1", card_ids=["ev_evt_problem"], candidate_id="t1")`）；`expand` 仍返回原 `ContentJob`。结构测试（11 节点名/reads/writes）不变。
- [ ] **Step 4: 运行确认** — `uv run pytest tests/graph/test_daily.py tests/unit/test_cli_run.py -v`。
- [ ] **Step 5: 提交** — `git add src/finch/graph/daily.py src/finch/cli.py tests/graph/test_daily.py tests/unit/test_cli_run.py && git commit -m "feat(graph): resolve per-node inference runners in daily_nodes/cli"`

---

## Task 7: `finch.yaml` 默认 flash + 节点覆盖

**Files:**
- Modify: `finch.yaml:70-73`

- [ ] **Step 1: 改配置**：

```yaml
llm:
  base_url: https://model-gateway.shuwenda.icu/v1
  model: deepseek-v4-flash
  nodes:
    plan_topics:
      model: deepseek-v4-flash
      timeout_seconds: 120
      max_output_tokens: 2000
    expand_job:
      model: deepseek-v4-flash
      timeout_seconds: 90
      max_output_tokens: 1200
      max_concurrency: 4
    critique:
      model: deepseek-v4-pro
      timeout_seconds: 300
      max_output_tokens: 4096
```

- [ ] **Step 2: 验证加载** — `uv run pytest tests/unit/test_settings.py -v`（`load_settings(Path("finch.yaml"))` 不破坏现有断言；`for_node` 行为已在 Task 1 覆盖）。
- [ ] **Step 3: 提交** — `git add finch.yaml && git commit -m "config(finch): default deepseek-v4-flash with per-node overrides"`

---

## Task 8: 全量校验 + 收尾

- [ ] **Step 1: 全量测试** — `uv run pytest`。
- [ ] **Step 2: lint/type** — `uv run ruff check . && uv run mypy src`。
- [ ] **Step 3: 修复** 所有失败，直到全绿。
- [ ] **Step 4: 提交** 任何遗漏文件。

---

## Acceptance criteria（对照原方案）

用同一份 63-card 输入跑 `pro` 基线 vs `flash` 两阶段：`Completion tokens ≤ 6000`（expand 单次 ≤1200）、单请求 < 90s（expand）且整体墙钟显著下降、连续 10 次超时率 0、Schema 校验 ≥ 99%、Job 数 ≤ 8、`source_card_ids` 引用完整率 100%。注：这些是运行期指标，需真实网关 + 63-card 输入在提交后手动验证（CI 无真实 LLM）。

## Out of scope（本轮不做）

- `extract_events`/`match_evidence` 的显式超时/模型改动（`llm.model` 切 flash 已使其默认走 flash；`match_evidence` 的 90s 硬编码 `_MATCH_TIMEOUT_SECONDS` 保留）。
- 引入 `llm.default` 嵌套块。
- `evidence 聚类/引用化/缓存`（P1 后续）。
