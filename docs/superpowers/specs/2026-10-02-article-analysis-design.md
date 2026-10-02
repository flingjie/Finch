# article_analysis：文章表达分析

日期：2026-10-02
状态：已确认（实现计划见 docs/superpowers/plans/2026-10-02-article-analysis.md）
代码基线：`main` `8199350`
对照：`2026-09-08-writing-style-analysis-design.md`（表面风格七维；本 Skill 不替代它）

## 1. 背景与问题

用户需要看懂一篇文章「为什么这样写」：作者要完成什么表达任务、面向谁、用了什么方法、相对该任务是否有效，以及哪些方法可迁移到自己的表达。

现有 `writing-style-analysis`（`finch style analyze`）观察开头、结构、节奏、用词、立场、具体性、读者关系，并建议可借鉴技巧。它**不以**「表达任务 → 读者变化 → 按任务加权的成功标准」为骨架，因此无法直接承担本需求。

本设计新增独立训练工具 Skill **`article_analysis`**，与 `writing-style-analysis` 并列；不进入 connect / drafts / inbox 默认流水线。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 与 style 关系 | **新 Skill**，不演进 / 不合并 `StyleReport` |
| D2 | 命名 | Skill / 包名 **`article_analysis`**（不用 `article-expression-analysis`） |
| D3 | 持久化 | **即算即打印**，不写 Workspace（与 style MVP 一致） |
| D4 | 输入 | **`--text` / `--file` / `--url`**，复用 `style.SourceResolver` |
| D5 | 后置动作 | MVP **只出报告**；不改写、不生成分享稿、不自动进 `expression-practice` |
| D6 | 成功标准呈现 | **纯定性文字**；无 1–5 分、无 strong/weak 枚举、无总分（LLM 不输出 total） |
| D7 | 实现形态 | **并行包** `src/finch/article/` + `finch article analyze`；暂不抽 shared `analysis/` |
| D8 | Voice 对比 | MVP **不做** `--compare-voice` |

## 3. 目标与非目标

**目标**

- 输入一篇文章（文本 / 文件 / URL），产出有原文依据的五段表达拆解。
- 作者未明示意图时标注「根据文章推断」，不替作者断言。
- 可执行性可标「不适用」；不得因无 CTA 判定文章失败。
- 可借鉴方法固定 2–3 项，每项含方法 / 此处为何有效 / 适用条件 / 小练习。

**非目标（YAGNI）**

- 不落库、不搜索历史报告、不与 VoiceProfile 对比。
- 不重写原文、不生成分享稿、不自动建议 `practice` / `voice` / `profile`。
- 不接默认连接/表达流水线；不给 Opportunity / Draft 打标签。
- 不做 AI 检测；不评价观点对错；不推断作者性格。
- 不抽公共 `analysis/` 包；不改 `StyleReport` 形状。

## 4. 五步分析（产品契约）

1. **表达任务**：主题 vs 目的；主任务 + 次任务；常见类型含解释 / 说服 / 发布 / 教授 / 分享经历 / 引发讨论。
2. **读者与预期变化**：写给谁 → 读前状态 → 读后应发生什么；压成一句「面向 ___，从 ___ 转变为 ___」；检查术语/案例是否匹配读者。
3. **成功标准（按任务侧重）**：清晰度、具体性、可信度、可执行性——均为定性判断 + 依据；可执行性可「不适用」。
4. **表达拆解**：`原文片段 → 方法 → 对读者的作用 → 适用条件或代价`；观察开头 / 结构 / 解释 / 论证 / 语言 / 结尾。
5. **可借鉴方法**：2–3 项，含小练习。

默认呈现顺序：表达任务 → 读者与预期变化 → 表达特点 → 目标达成情况 → 可借鉴方法。

## 5. 模块结构

```
src/finch/article/
  models.py                  # ArticleReport + 嵌套模型
  service.py                 # ArticleAnalysisService.analyze(ResolvedSource)
  __init__.py

prompts/
  analyze-article.md         # 五步结构化推理

skills/article_analysis/
  SKILL.md
  references/
    analysis-steps.md        # 五步判据
    output-contract.md       # 报告字段契约
  evals/cases.yaml

# 复用，不复制：
src/finch/style/source_resolver.py
src/finch/webfetch/
```

CLI：`article_app = typer.Typer(...)` → `finch article analyze`。

### 5.1 职责

- `SourceResolver`：与 style 相同的输入规范化（正文、`content_hash`、`source_type`/`source_ref`）。
- `ArticleAnalysisService`：读 prompt → `StructuredInferenceRunner` → Pydantic 校验 → 覆盖确定性字段。
- Skill：意图路由、调 CLI、按五段呈现；不做后置改写。

## 6. 数据模型

```python
class ExpressionTask(BaseModel):
    topic: str
    primary_task: str
    secondary_tasks: list[str] = Field(default_factory=list)
    inferred: bool = False  # True → 呈现「根据文章推断」

class AudienceChange(BaseModel):
    who: str
    before: str
    after: str
    fit_check: str  # 术语/案例/背景是否适合这些读者

class TechniqueBreakdown(BaseModel):
    excerpt: str
    method: str
    reader_effect: str
    caveat: str = ""

class Effectiveness(BaseModel):
    clarity: str
    concreteness: str
    credibility: str
    actionability: str  # 可写「不适用：…」

class TransferableMethod(BaseModel):
    method: str
    why_effective_here: str
    when_to_use: str
    mini_exercise: str

class ArticleReport(BaseModel):
    # 确定性（service 覆盖）
    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""

    # 判断（LLM）
    expression_task: ExpressionTask
    audience_change: AudienceChange
    techniques: list[TechniqueBreakdown] = Field(default_factory=list)
    effectiveness: Effectiveness
    transferable_methods: list[TransferableMethod] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
```

- `id = article_{sha256(f"{content_hash}:{ANALYZER_VERSION}")[:16]}`
- `transferable_methods`：prompt 要求 2–3 条；Pydantic `Field(min_length=2, max_length=3)` 校验，不合规则 schema 失败 → exit 1（不静默截断）。
- **禁止**任何总分 / 加权分数字段。

## 7. 服务与数据流

```text
exactly one of --text | --file | --url
        ↓
SourceResolver.resolve_*
        ↓
ArticleAnalysisService.analyze(source)
  prompt = analyze-article.md.format(body=source.body)
  raw = runner.run(prompt, ArticleReport)
  return raw.model_copy(update={id, source_type, source_ref, content_hash})
        ↓
CLI: _render_article_report(report)  |  --json → model_dump_json
```

- Runner 选取与 `style analyze` 一致（`create_runner(settings.llm, "critique")` 或 `CodexRunner`）。
- 空正文、URL 不可用、schema 失败：fail-closed，exit 1，不打印半份报告。

## 8. CLI

```bash
finch article analyze --text "..."
finch article analyze --file article.md
finch article analyze --url "https://..."
finch article analyze <任一输入> --json
```

- 恰好一个输入源；否则 exit 1（文案对齐 style）。
- 无人机对比、无保存开关。
- 文本渲染固定五段标题；`inferred=true` 时在表达任务段标注「根据文章推断」。

## 9. Skill

### 9.1 定位

训练工具（与 `writing-style-analysis` / `sticky-message` / `feynman-practice` 同类）。核心产物是**有原文依据的表达拆解**。

### 9.2 路由

| 用户意图 | Skill |
|---|---|
| 风格特点、节奏、用词、可借鉴句式 | `writing-style-analysis` |
| 为什么这样写、面向谁、是否达成表达目的 | `article_analysis` |

意图不清时问一句澄清，默认不同时跑两个分析。

### 9.3 边界

- 不重写、不分享稿、不自动练习闭环。
- 分析完成后的延伸点检查按 `_shared/dialogue-policy.md`；若无可延伸则自然结束（不硬推 practice）。

### 9.4 文档同步

实现时更新 `CLAUDE.md` / `AGENTS.md` 的 Skill 列表与 CLI 面：`finch article analyze`。

## 10. Prompt 要点（`prompts/analyze-article.md`）

- 输入：`{body}`（及必要的 sample 说明）。
- 强制：每个 technique 必须有 `excerpt`；意图不确定时 `inferred=true`。
- 强制：`actionability` 在无行动目标时写「不适用」+ 一句理由。
- 强制：`transferable_methods` 2–3 条，含 `mini_exercise`。
- 禁止：输出总分、复制大段原文当「方法」、断言作者未写出的意图（除非 `inferred`）。

## 11. 错误处理

| 场景 | 行为 |
|---|---|
| 0 个或多个输入旗标 | exit 1 |
| resolve 后正文为空 | exit 1 |
| URL / webfetch 失败 | exit 1 + 原因 |
| LLM / Pydantic 失败 | exit 1，无假报告 |

## 12. 测试

- `tests/unit/test_article_models.py`：嵌套模型校验；无 total 字段。
- `tests/unit/test_article_service.py`：mock runner；确定性字段覆盖；`inferred` / `actionability` 透传。
- `tests/unit/test_cli_article.py`：输入旗标互斥；`--json` 形状；失败 exit 1。
- Prompt contract：`analyze-article.md` 占位符与 `.format()` 参数一致。
- Skill evals：路由样例（风格 vs 表达任务）；呈现含五段标题；推断意图时有标注。

## 13. 验收

1. `finch article analyze --text "..."` 打印五段报告，无 Workspace 新文件。
2. `--url` 失败时 exit 1，无半份 JSON。
3. 报告含至少一条带 `excerpt` 的 technique，以及 2–3 条 transferable_methods。
4. `writing-style-analysis` 行为与契约不变。
5. `uv run pytest` / `ruff check .` / `mypy src` 全绿。

## 14. 后续（不在本 spec）

- Workspace 持久化与按 hash 去重。
- `--compare-voice`。
- 分析完成后可选挂到 `expression-practice` / 缩写练习。
- 抽取 `src/finch/analysis/` 共享 SourceResolver 入口。
