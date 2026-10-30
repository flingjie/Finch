# Skill-first + Thin Runtime 设计（Phase 0–1）

日期：2026-10-30  
状态：实施中  
决策：宿主 Agent（Cursor/Codex）驱动 Skill；**不**引入进程内 agent loop。  
范围：Phase 0 职责审计 + Phase 1 `problem-discovery` 影子实验。Product↔Problem 功能扩张延后。

## 1. 目标架构

| 单元 | 职责 |
|---|---|
| Skill | 研究能力：选路径、理解、起草问题、筛选呈现 |
| Thin Tools（CLI） | 执行能力：candidates / sync / validate-upsert / journal / compare |
| File Workspace | 持久化：Problems · Discussions · Runs · Shadow · Journal |
| Python Runtime | 完整性、幂等、预算、证据门禁、只读边界 |

Product 与 Problem 保持并列一等对象；本次是架构调整，不是产品方向重构。

## 2. problem-discovery 代码分类（Phase 0）

| 符号 / 文件 | 桶 | 处置 |
|---|---|---|
| `prompts/problem-discovery.md` | research_reasoning | 影子 Skill 承担等价推理；暂保留供 control |
| `assess_problems` | research_reasoning | control 保留；影子路径不调用 |
| `skills/problem-discovery/*` | research_reasoning | 生产呈现；另建 shadow Skill |
| `discover_problems` 批循环 | fixed_orchestration | 实验后可收缩；本期不删 |
| `cli discover --refresh` | fixed_orchestration | control；影子用 `sync` + `validate-upsert` |
| `merge_problem_sources` / `sync_problem_sources` | fixed_orchestration + reliability | 保留；暴露 PD-scoped `sync` |
| `_collect_sources` / fingerprint / budgets | reliability + orchestration | 保留；暴露 `candidates` |
| `build_problem` + level caps | reliability | **必须留 Python** |
| `ids` / repositories / `Workspace.atomic_write` | data_ops | 保留 |
| `summary` / presentation repos | data_ops | 保留；计数真源仍为 Python |
| Source connectors / orchestrator | reliability | 保留；不迁 Skill |

### 不可迁入 Skill

- 证据门禁（source_ref ∈ pool、quote 子串、URL 只来源映射、等级封顶）
- 确定性 ID、原子写、指纹缓存（含 eval_failed 不写 run）
- 预算硬裁剪（lookback / max_sources / max_chars）
- 只读 `gh` / `opencli`；外发 HITL

## 3. 宿主 Agent 契约（Phase 1）

```text
CONTROL:  sync? → discover_problems(assess_problems) → control inbox + DiscoveryRun
SHADOW:   candidates → [optional sync/add] → host drafts JSON
       → validate-upsert --shadow → shadow inbox + journal → compare
```

### 执行预算（Skill 必须遵守）

- 最多 3 次 PD `sync` / 研究回合
- candidates 池不超过配置 `max_sources`（Python 已裁剪）
- 墙钟建议 ≤ 15 分钟；证据不足则写 unknowns 并停止，不编造
- 禁止调用 `discover`（影子路径）
- 禁止发明不在 candidates 中的 `source_ref` / URL

### Shadow 命名空间

- Control：`var/problem_discovery/{problems,runs,discussions}/`
- Shadow：`var/problem_discovery/shadow/{problems,runs,journal}/`
- 共享：artifacts / discussions（导入材料同一来源池）

## 4. Thin tools

| CLI | 作用 |
|---|---|
| `problem-discovery candidates [--json]` | 导出与 control 相同的来源池 + fingerprint + budget_stats |
| `problem-discovery sync [--source] [-q]` | 合并 `problem_discovery.sources` + research_topics 后同步 |
| `problem-discovery validate-upsert --draft … [--shadow]` | `build_problem` 门禁后写入 |
| `problem-discovery journal append\|show [--shadow]` | 运行事件 |
| `problem-discovery compare [--json]` | control vs shadow 差异 |

## 5. 成功标准（见 shadow-results）

证据引用错误率、问题质量、发明引用率（影子必须为 0）、完整交付率、人工救火次数。  
**不以删 LOC 为成功。** 未达标不得删除 `assess_problems` / `discover_problems`。

## 6. 非目标

- 通用 Graph Runtime / 进程内 agent loop
- 重写 Source Adapter / Workspace
- 本期落地 Product↔Problem 关联模型
- 自动外发或 CRM

## 7. Phase 2–3（路线图）

Phase 2：Product research / feedback / solution explorer 同法拆分。  
Phase 3：收敛 CLI 启动/恢复/查看；保留 HITL。
