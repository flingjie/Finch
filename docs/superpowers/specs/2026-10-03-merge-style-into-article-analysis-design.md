# 合并 writing-style-analysis 到 article_analysis

日期：2026-10-03  
状态：待用户审阅  
对照：`docs/superpowers/specs/2026-09-08-writing-style-analysis-design.md`（本设计取代其产品入口）、`docs/superpowers/specs/2026-10-02-article-analysis-design.md`

## 1. 背景与问题

Finch 现有两个并列的只读训练工具：

| | writing-style-analysis | article_analysis |
|---|---|---|
| CLI | `finch style analyze` | `finch article analyze` |
| 报告 | `StyleReport`（七维 + 可借鉴技巧） | `ArticleReport`（任务 / 读者 / 方法 / 有效性 / 清晰透镜） |
| 可选 | `--compare-voice` → `StyleComparison` | 无 |
| 共享 | article 已依赖 `style.SourceResolver` | |

Skill 要求意图不清时澄清，且默认不同时跑两个分析。用户需记两个入口，风格与「为什么这样写」被拆开。目标：合并为单一入口与统一报告。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 合并形态 | **单一入口、统一报告**；删除独立 style Skill / CLI |
| D2 | `--compare-voice` | **本次不做**；需要时再挂回 article |
| D3 | 生成方式 | **一次 LLM 调用**；扩展 `ArticleReport` 纳入风格七维 |
| D4 | 旧入口退场 | **立即删除**（无别名、无静默转发） |
| D5 | 报告结构 | **嵌套 `style: StyleBlock`**，不把七维摊平到顶层 |
| D6 | SourceResolver | **迁入 `src/finch/article/`**，删除 `src/finch/style/` 整包 |
| D7 | CLI 名 | 仍用 `finch article analyze`（不改名） |
| D8 | `rhetorical_patterns` | **不迁入**（YAGNI；公开七维不含此项） |

## 3. 目标与非目标

**目标**

- 一次 `finch article analyze` 产出任务分析 + 写作风格七维。
- 删除 `writing-style-analysis` Skill、`finch style`、`WritingStyleService`、`StyleReport`/`StyleComparison` 独立路径。
- `SourceResolver` / `ResolvedSource` 由 article 包拥有。
- 更新 README、CLAUDE/AGENTS、article Skill；旧 style 设计文档标注被取代。

**非目标**

- `--compare-voice` / `StyleComparison`。
- 落库、自动进 expression-practice、自动改 VoiceProfile。
- 兼容别名或弃用期 shim。
- 新建共享 `analysis/` 包（YAGNI；resolver 直接放 article）。
- 评价观点对错、AI 检测、重写原文。

## 4. 数据模型

放在 `src/finch/article/models.py`（或同包小文件再导出）：

```text
StyleEvidence
  dimension: str
  observation: str
  excerpts: list[str]
  confidence: low | medium | high

StyleBlock
  scope: single_text | multi_sample_author
  overall_confidence: low | medium | high
  opening / structure / rhythm / word_choice /
  stance / concreteness / reader_relationship: list[StyleEvidence]
  signature_patterns: list[str]
  transferable_techniques: list[str]
  potential_weaknesses: list[str]
  experiments_for_me: list[str]

ArticleReport  # 现有字段保留
  ...
  style: StyleBlock          # 默认空 StyleBlock（各列表空），非 Optional
  limitations: list[str]     # 合并任务+风格局限，单一列表
```

确定性字段仍由 service 覆盖：`id`、`source_type`、`source_ref`、`content_hash`。  
`sample_size`：若现 style 服务在 Python 中校正 `scope`/`overall_confidence`，把该逻辑迁入 `ArticleAnalysisService`；否则仅靠提示词规则（与现 article 一致地不信任模型对 id 等字段）。

`clarity_cost_reductions` 与任务五段不变。

## 5. 数据流

```
finch article analyze --text|--file|--url [--json]
  → SourceResolver (article 包)
  → ArticleAnalysisService.analyze   # 一次 LLM → ArticleReport
  → 覆盖确定性字段
  → CLI 渲染 / --json
```

提示词：扩展 `prompts/analyze-article.md`，嵌入风格七维判据（来自现 `analyze-writing-style.md`，缩短）。删除 `prompts/analyze-writing-style.md`。

## 6. CLI 呈现顺序

1. 表达任务  
2. 读者与预期变化  
3. 表达特点（`techniques`）  
4. **写作风格**（七维：observation + 短摘录）  
5. 目标达成情况  
6. 可借鉴方法（`transferable_methods`）  
7. 降低理解成本（若有 `clarity_cost_reductions`）  
8. 局限  

风格块内可附 `signature_patterns` / `transferable_techniques` / `potential_weaknesses` / `experiments_for_me`（简短列表）。  
误用已删除的 `finch style`：typer 自然报未知命令，不写兼容层。

## 7. 删除与迁移清单

| 项 | 动作 |
|---|---|
| `src/finch/style/source_resolver.py` | 迁到 `src/finch/article/source_resolver.py`；更新所有 import |
| `src/finch/style/` 其余 | 删除 |
| `src/finch/cli.py` style_app | 删除 |
| `skills/writing-style-analysis/` | 删除 |
| `prompts/analyze-writing-style.md` | 删除 |
| `tests/unit/test_style_*.py`、`test_cli_style.py` | 删除；风格覆盖迁到 article 测试 |
| README / CLAUDE.md / AGENTS.md | 去掉 `style analyze`；只保留 `article analyze` |
| `skills/article_analysis/SKILL.md` | 去掉「改用 style」路由；说明风格已含本报告 |
| `2026-09-08-writing-style-analysis-design.md` | 文首标注：产品入口已被本设计取代 |

## 8. 测试与验收

**自动化**

- `StyleEvidence` / `StyleBlock` / `ArticleReport.style` 模型测试  
- Service：确定性字段覆盖；prompt 占位符  
- CLI：文本含「写作风格」；`--json` 含 `style`；无 `style` 子命令  
- 全量相关回归 + ruff + mypy  

**验收**

- `rg`：现行 Skill/README/CLAUDE 入口无 `finch style` / `writing-style-analysis`（历史 design 标注除外）  
- 一次 `article analyze` 可见任务段 + 风格段  
- 无 `--compare-voice`  
- 不落库、不改 VoiceProfile  

## 9. MVP 完成定义

用户运行 `finch article analyze`，得到含表达任务分析与七维写作风格的统一 `ArticleReport`；独立 style Skill、CLI 与 `src/finch/style` 包已移除；`SourceResolver` 由 article 拥有；文档只宣传单一入口。
