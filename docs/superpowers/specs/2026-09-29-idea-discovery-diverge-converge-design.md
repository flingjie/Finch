# Finch idea-discovery 发散→核验→收敛改造 — 设计规格

日期：2026-09-29
状态：已确认，待进入 implementation plan。
代码基线：`main` `62adc4b162587e2b9b31db8b406a49a0fd93e064`（实施时须比较最新 HEAD）。
来源：对 idea-discovery 现有实现（`skills/idea-discovery/` 与 `src/finch/ideas/`）做决策收敛后形成。

## 1. 目标与完成定义

把 `idea-discovery` 从「提取一个明确决策」调整为「**从具体经历发散出几个可讨论的观点，再收敛到有证据、能供别人使用的一个**」。关键变化发生在创建 `ContentJob` 之前；现有 `PROPOSED → CONFIRMED → DRAFTED` 状态机不变。

- 发散时不要求模型只找 `decision.statement`，而是分别寻找：反常现象、失败与修正、设计取舍、可复用方法、仍未解决的问题。没有足够材料时允许输出零个候选，不强凑"洞察"。
- 收敛时判断**可迁移性**（而非抽象程度），每个候选回答四问：读者情境 / 读者所得 / 证据支持到哪一步 / 何时不成立。不给单一"通用价值分"。
- 收敛结果附简短推荐理由，并保留被淘汰候选的原因，用户可挑战 Finch 的判断。

**本次范围 = commit 路径先行**；fragment / conversation / signals 三种来源仍走单候选旧路径，待共享接口在 commit 上验证后再扩展（第二阶段，不在本 spec 内）。

## 2. 已确认的交互决策

| 维度 | 实施要求 |
|---|---|
| 选择模型 | 发散产出整个 `IdeaExploration`（所有角度 + 淘汰理由）持久化；只为选中角度建一个 `ContentJob` |
| commit 流程 | `finch ideas commit` 发散 → 持久化 exploration → 用推荐角度自动建一个 `PROPOSED` job |
| 改选 | 新命令 `finch ideas choose <exploration_id> <angle_index>` 改用另一角度 → 建第二个 job；exploration 的 `selections` 追加可追溯 |
| 推荐 ≠ 确认 | 自动创建的是 `PROPOSED`（建议立场），不违反「confirmed 只来自用户明确确认」不变量 |
| 流水线结构 | 发散（LLM 1 次）→ 核验（确定性 Python）→ 收敛（LLM 1 次小型）；核验放确定性代码，LLM 永不产出数值 `total` |
| 空角度 | 材料存在但核验后全淘汰 → 仍持久化 `angles=[]` 的 exploration（保留"看过但全淘汰"记录），不建 job |
| 归属 vs 强度 | `evidence_status`（归属：谁的经验）与 `evidence_support`（主张强度：支持到哪一步）**分开两个字段** |

## 3. 当前代码实际具备什么（已对代码核验）

| 实际位置 | 当前行为 | 本次调整 |
|---|---|---|
| `ideas/commit_service.py` | `to_ideas`：噪声过滤 + `EngineeringEvent` 提取 + 安全扫描；`_has_clear_decision`（decision 非空且非 UNKNOWN）门禁；1 事件 → 1 `IdeaCandidate` | 门禁放宽为 `_has_traceable_material`（problem/decision/result 任一非空且非 UNKNOWN）；拆出 `to_facts`，不再直接产 candidate |
| `ideas/fragment_service.py` | `from_text`/`from_thread`/`from_signals` 各一次 LLM 出单候选；代码守门 `peer report:` / `observed` 降级 / 信号张力 | 本阶段不动 |
| `ideas/service.py` | `IdeaService.create_candidate` 幂等落库；状态机 confirm/revise/drafted/skip | 新增 `create_from_angle`；`create_candidate` 原样保留 |
| `ideas/models.py` | `IdeaCandidate` 完整契约；`SourceRef` / `IdeaBoundaries` / `IdeaGenerator` | `IdeaCandidate` 加可选字段；新增 `IdeaAngle` / `RejectedAngle` / `Selection` / `IdeaExploration` |
| `storage/repositories.py` | `ContentJobRepository` 用 `_write(ws, "ideas", id, job)` 写 YAML | 新增 `IdeaExplorationRepository` 写 `<var>/explorations/<id>.yaml` |
| `cli.py` | `ideas_commit` 直接 `to_ideas` → `create_candidate`；`ideas_create`/`ideas_signals` 走 FragmentService | `ideas_commit` 改走 `to_facts` + `diverge`；新增 `ideas_choose` |
| `skills/idea-discovery/SKILL.md` | 「提取一个决策」措辞 + 三问（problem/decision/result） | 补发散描述；commit 路径说明先发散 |
| `skills/idea-discovery/references/presentation.md` | 「结论 → 3 条卡 → 写/展开/换一批」 | 新形状「我看出 N 个角度，推荐第 X…」+ 改选映射 |

核验结论（撰写本 spec 时逐项确认）：

- `_has_clear_decision` 位于 `commit_service.py`，仅查 `decision.statement` 与 `decision.confidence`；`result`/`problem` 有内容但 decision 空洞时会整体跳过——这正是要放宽的点。
- 安全扫描 `scan_cards` 在 `to_ideas` 内、`extractor.extract` 之后执行；新流程必须保持「安全扫描先于一切发散 LLM 调用」。
- `IdeaService.create_candidate` 的幂等键 = `sha256(skill, version, canonical_source_refs, sha256(core_point))`；改选不同角度 → 不同 `core_point` → 不同 `generation_key` 与 `id`，天然产生第二个 job，无需改幂等逻辑。
- `FragmentService` 复用 `StructuredInferenceRunner.run(prompt, OutputModel)`；新发散/收敛 prompt 沿用同一 runner 接口与 inline prompt 风格。
- `ContentJob` 无 `reader_situation` / `reader_takeaway` / `takeaway_kind` / `evidence_support` / `counterexample_or_limit` 字段；这些放 `IdeaCandidate`（落库前）与 `IdeaExploration`（可追溯），不扩 `ContentJob` 持久化 schema——`IdeaCandidate` 的映射决定 `ContentJob` 现有字段（`reader_problem` / `why_now` / `author_position` / `boundaries`）如何填。

## 4. 数据模型改动（`ideas/models.py`）

全部新字段可空/有默认值，旧候选与旧 YAML 继续可读。

**新增枚举：**

- `TakeawayKind = Literal["diagnosis", "decision_criteria", "method", "pitfall"]`——读者所得类型（Q2）。
- `EvidenceSupport = Literal["observed_this_run", "inferred_cause", "unverified_general"]`——主张强度（Q3），映射到 `boundaries` 桶：`observed_this_run→known`、`inferred_cause→inferred`、`unverified_general→unknown`。

**`IdeaAngle`**（通过核验的候选角度）：

| 字段 | 类型 | 用途 |
|---|---|---|
| `index` | `int` | 1-based，稳定，用于呈现与改选 |
| `core_point` | `str` | 单一中心主张（选中后即 candidate.core_point） |
| `reader_situation` | `str` | Q1 别人在什么情境遇到它 |
| `reader_takeaway` | `str` | Q2 给它什么帮助 |
| `takeaway_kind` | `TakeawayKind` | 诊断步骤 / 决策标准 / 可尝试方法 / 值得避免的错误 |
| `evidence_support` | `EvidenceSupport` | Q3 证据支持到哪一步 |
| `counterexample_or_limit` | `str` | Q4 何时不成立 |
| `source_refs` | `list[SourceRef]` | 该角度引用的真实事实/来源 |

**`RejectedAngle`**：`index` + `core_point` + `reason`（淘汰理由，核验或收敛阶段）。

**`Selection`**：`index` + `job_id` + `at`（每次选择，自动推荐 + 改选，可追溯）。

**`IdeaExploration`**：

| 字段 | 类型 | 用途 |
|---|---|---|
| `id` | `str` | `expl_<sha256[:8]>`，由规范化来源 + 事实指纹决定，幂等 |
| `origin` | `IdeaOrigin` | practice（commit 路径） |
| `source_kind` | `SourceKind \| None` | commit |
| `facts` | `list[str]` | 可追溯事实卡（来自 EngineeringEvent） |
| `source_refs` | `list[SourceRef]` | 全部来源 |
| `angles` | `list[IdeaAngle]` | 通过核验的角度（0–4） |
| `rejected_angles` | `list[RejectedAngle]` | 淘汰角度及理由 |
| `recommended_index` | `int \| None` | 收敛推荐；无角度时为 None |
| `recommendation_reason` | `str` | 推荐理由（可迁移性判断，无数值分） |
| `selections` | `list[Selection]` | 选择历史 |
| `generator` | `IdeaGenerator` | 产出 Skill 与版本 |

**`IdeaCandidate` 新增可选字段**（默认空/None，向后兼容）：`reader_situation`、`reader_takeaway`、`takeaway_kind`、`evidence_support`、`counterexample_or_limit`。commit 路径由 `IdeaAngle` 填充；fragment/conversation/signals 旧路径不填。

## 5. 发散服务（新 `src/finch/ideas/divergence.py`）

`IdeaDiverger(runner: StructuredInferenceRunner)`，方法 `explore(*, facts, source_refs, origin, source_kind, evidence_status) -> IdeaExploration`。调用方（`to_facts` 的门禁）保证 `facts` 非空，故 `explore` 恒返回一个 `IdeaExploration`；材料存在但核验后全淘汰时 `angles=[]`（仍持久化，见 §2）。

**流程：**

1. **发散（LLM 1 次）**：prompt 要求从事实卡分别寻找「反常现象 / 失败与修正 / 设计取舍 / 可复用方法 / 仍未解决的问题」，输出 2–4 个角度，每个带四问 + 指向事实的 `source_refs`。无材料可发散 → 返回空角度列表。
2. **核验（确定性 Python）**，逐条：
   - `source_refs` 无法解析到真实事实/来源 → 淘汰，记「无来源」；
   - `evidence_support` 与来源置信度冲突（如角度称 `observed_this_run` 但来源是 `externally_reported`）→ 降级为 `inferred_cause`/`unverified_general`；
   - `core_point` 与另一角度近似重复 → 保留更具体者，记「重复」。
3. **收敛（LLM 1 次小型）**：给定存活角度，返回 `recommended_index` + `recommendation_reason`（判断可迁移性，**不给数值分**）；无可迁移者返回空（`recommended_index=None`，角度清空并全部记入 `rejected_angles`）。
4. 组装 `IdeaExploration`，计算幂等 `id`（规范化来源 + 事实指纹）。

`evidence_status`（归属）由调用方传入，与 `evidence_support`（主张强度）分开：commit 来源 `evidence_status` 恒 `observed`（作者亲历），`evidence_support` 独立映射到 `boundaries`。

## 6. `CommitService` 改动

- `to_ideas` 改为 `to_facts(...) -> list[FactBundle]`：保留噪声过滤、`EngineeringEvent` 提取、安全扫描（**仍先于一切 LLM 调用**）。
- 门禁从 `_has_clear_decision` 放宽为 `_has_traceable_material`：`problem`/`decision`/`result` **任一**语句非空且置信度非 `UNKNOWN` 即可进入发散。
- `FactBundle = {facts, source_refs, boundaries, evidence_status="observed", origin="practice", source_kind="commit"}`。`CommitService` 保持纯领域逻辑，不调 LLM。
- `_event_to_idea`（事件 → 单 candidate 的直接映射）移除或改由 `IdeaDiverger` 消费 `FactBundle`。

## 7. `IdeaService` + 选择溯源

- 新增 `create_from_angle(angle: IdeaAngle, *, origin, source_refs, source_kind, evidence_status, facts, boundaries) -> ContentJob`：把角度映射为 `IdeaCandidate` 后复用现有 `create_candidate`（幂等、状态机不变）。

| candidate 字段 | 来源 |
|---|---|
| `core_point` / `interpretation` | `angle.core_point` |
| `reader_problem` | `angle.reader_situation` |
| `why_worth_saying` | `angle.reader_takeaway` |
| `author_position.claim` | `angle.core_point` |
| `author_position.decision` | `angle.reader_takeaway` |
| `author_position.tradeoff` | `angle.counterexample_or_limit` |
| `observation` / `facts` | 事实卡（逐字） |
| `boundaries` / `intent` | 由 `evidence_support` 派生（`intent` 默认 `stance`） |
| `reader_situation` / `reader_takeaway` / `takeaway_kind` / `evidence_support` / `counterexample_or_limit` | 角度对应字段直填 |

- 选择溯源：`finch ideas commit` 用 `recommended_index` 调 `create_from_angle` 落一个 job，并把 `Selection(index, job_id)` 追加进 exploration 再 `upsert`；改选 `choose` 用不同 index 再走一次（不同 `core_point` → 不同 `generation_key`/id → 第二个 job），exploration 的 `selections` 追加第二条。

## 8. 持久化 + CLI

- 新 `IdeaExplorationRepository`（`repositories.py`）：`<var>/explorations/<id>.yaml`，沿用 `_write`/`_read`，方法 `upsert` / `get` / `list_all`。
- `finch ideas commit`：`to_facts` → `diverger.explore`（每个 FactBundle）→ persist exploration → 每个有 `recommended_index` 的 exploration 用推荐角度 `create_from_angle` → 渲染「我看出 N 个角度，推荐第 X…」。
- 新 `finch ideas choose <exploration_id> <angle_index>`：校验 exploration 存在且 index 合法 → `create_from_angle` → 报新 job id。角度为空 → 报「无角度值得写」。
- fragment/conversation/signals 路径**本阶段不变**（仍单候选），`IdeaService.create_candidate` 原样保留。

## 9. Skill 层（SKILL.md / presentation.md / prompts）

- `SKILL.md`「四种来源」补发散描述；「产出契约」说明 commit 路径先发散、fragment/conversation/signals 仍单候选。
- `presentation.md` 换新形状：每 source「我看出 N 个可能观点；我推荐第 X，因为〔可迁移理由〕；不过〔证据边界〕」，其余角度列出供改选；回复映射「写 / 改选 2 / 展开 2」→ `confirm` / `choose` / `show`。
- 新增发散 prompt 与收敛 prompt（`divergence.py` 模块常量，沿用 `fragment_service.py` 的 inline prompt 风格，输出模型 `IdeaAnglesOutput` / `ConvergeOutput`）。

## 10. 测试与回放

- **单测**：门禁放宽、核验（来源解析 / 证据降级 / 去重 / 淘汰理由）、角度→候选映射、`create_from_angle` 幂等 + 改选产生第二 job、`choose` 边界（不存在 exploration / 非法 index / 空角度）。
- **更新 `evals/cases.yaml`**：commit 有决策 → 1 角度 → 1 job；机械变化 / 私有 → 空。
- **回放对比**（验收活动，非自动评分公式）：取近期 commit / 对话 / 社区信号各几条做回放，人工比较新旧结果——是否发现原流程漏掉的好观点、推荐理由是否具体、有没有把推测写成事实、用户是否愿意拿它与同行讨论。

## 11. 不变量（不得违反）

- **证据优先**：commit 路径仍 `Commit → EngineeringEvent → EvidenceCard →（发散）→ IdeaCandidate`；发散角度必须可追溯到真实事实/来源，无来源即淘汰。
- **No auto-publish**：本改动不触碰 `gh`/`opencli` 写路径；自动创建的是 `PROPOSED` job，发布/确认仍需人工。
- **外部 ≠ 证据**：核验阶段把「角度宣称 observed 但来源 externally_reported」降级，不提升归属。
- **Deterministic totals**：收敛 LLM 只回 `recommended_index` + 理由，永不产出数值 `total`；核验/淘汰理由在 Python 里决定。
- **Subprocess discipline**：发散/收敛沿用 `StructuredInferenceRunner`，数组参数、超时、Pydantic 校验不变。
