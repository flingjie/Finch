# 证据含义与可信作者绑定（阶段 A）设计

日期：2026-11-02
状态：设计已评审（brainstorming）
基线：`main@0c99fbb`（Phase 1 admission 已落地）
来源计划：《Finch 下一步实现计划：从问题线索到投入判断》（2026-11-01）阶段 A；
《Finch 优化计划：长期研究中的权限、状态与人的注意力》（2026-11-02）作为后续轮次路线图，本轮不实现。

## 1. 目标与范围

把三件事在数据和呈现上分开，使系统不再把「摘录能回溯」说成「问题已核实」：

- **引用已核对**：由确定性代码得出（来源可回溯、摘录匹配）。
- **主张支持情况**：支持 / 冲突 / 未知；只能由宿主显式标注，附理由；代码不做语义推断。
- **问题复现情况**：本轮不加字段，只在文档写明「复现只能由实验结果更新，属下一轮（阶段 C）」。

同时：作者身份改由采集端绑定，LLM 草稿不再有能力发明身份；文档定位统一为
「找到值得自己解决的问题」。

### 1.1 明确不做

- 不改存储结构、不做磁盘迁移（留给阶段 B「观察日志 + 投影」重构，一次完成）。
- 不加实验计划 / 实验结果 / 决策记录（阶段 C）。
- 不做 GitHub 评论拆分为独立可引用身份（属后续采集调整；本轮一条来源最多算一个人）。
- 不做问题 ID 稳定化与安全合并（阶段 B）。
- 不做授权记录、决定卡片、持续跟踪（长期研究计划 P1–P3，后续轮次）。
- 不从正文解析 `@login` 当作者身份——与「身份由采集端绑定」原则冲突。

### 1.2 已锁定决策

| 决策 | 选择 |
|------|------|
| 范围 | 只做阶段 A；B、C 各写自己的规格 |
| 主张支持 | 按每条证据记（`claim_support`），宿主标注；复现按整张问题，留下一轮 |
| 作者绑定层级 | 只用工件上已有的 `author_identity`；草稿 ID 必须对上来源外部 ID |
| 旧数据兼容 | 只做兼容读取，不改写磁盘；迁移留给 B |
| 架构方向 | B 立项为「观察日志 + 投影」（见 §9） |

## 2. 数据模型（`src/finch/problem_discovery/models.py`）

### 2.1 `Admission.VERIFIED` → `SOURCE_CHECKED`

- 枚举值改为 `source_checked`，含义收窄为「引用已核对」：至少一条证据通过
  `build_problem` 的来源回溯与摘录匹配检查。它不表示主张被支持，更不表示问题已复现。
- 兼容读取：反序列化遇到旧值 `verified` 时映射为 `SOURCE_CHECKED`，不改写磁盘；
  下次正常 upsert 时自然写成新值。
- `admission_reason` 规范值相应改为 `source_checked`（原 `has_valid_evidence`）；
  旧 reason 字符串原样保留可读。
- `DRAFT` / `QUARANTINED` / `ACCEPTED` 含义不变；`ACCEPTED` 保护逻辑不变。

### 2.2 `ProblemEvidence` 新增字段

```python
claim_support: Literal["supported", "conflicting", "unknown"] = "unknown"
claim_support_reason: str = ""
```

- 默认 `unknown`。只有宿主经写入路径显式标注才能是 `supported` 或 `conflicting`，
  且 `claim_support_reason` 非空；理由为空时门禁强制回落 `unknown`。
- `discover` 自动抽取路径一律写 `unknown`（LLM 草稿即使携带该字段也被忽略）。
- 人工接受（`accepted`）不改变 `claim_support`，不提升证据等级。

### 2.3 `author_identity_confidence` 退役（兼容保留）

- 字段保留以兼容读取旧数据，但**不再参与任何门禁**。
- 新的身份可信来自一个可核验的事实：草稿作者 ID 与采集端 `author_identity` 绑定成功。
  不存在「LLM 自报 verified」这条路径。

## 3. 证据门禁（`src/finch/problem_discovery/discover.py`）

### 3.1 来源作者映射

`_collect_sources` 返回值增加每条来源的作者身份：

- `RawArtifact` → `(author_identity.external_id, author_identity.handle)`。
- `ImportedDiscussion` 只有自由文本 `author`，无结构化身份 → 视为无绑定身份。

### 3.2 身份核对（替代草稿自报）

`build_problem` 对每条通过摘录核对的证据：

- 草稿 `author_id` 与该 `source_ref` 来源上的外部 ID 一致（大小写折叠后相等）→
  绑定成功，写入来源上的 `external_id` / `handle`。
- 不一致或来源无结构化身份 → `author_id` / `author` 清空，按无身份处理。
  **证据本身保留**（摘录核对不受影响），只是不参与独立作者计数。
- 一条来源最多算一个人：GitHub 评论当前拼接在正文里、工件只保留发帖人，
  评论者不另算；同一外部 ID 出现在多条来源仍算一个人。

### 3.3 E2 升级条件（收紧）

全部满足才可达 E2：

1. 至少两条有效证据，来自**不同来源**（不同 `source_ref`）；
2. 绑定**两个不同**的外部 ID（§3.2 的绑定成功者）；
3. 两条证据的 `experience` 均为 `first_hand`，且来自**宿主标注**：
   `build_problem` 增加 `host_authored` 标志，仅 validate-upsert（宿主）路径为真；
   discover 路径 LLM 草稿自报的 `first_hand` 保留为元数据但不参与 E2 计数——
   即纯 discover 路径实际封顶 E1，E2 只能经宿主复核产生。
   转述 / 认领 / 机器人分析 / 营销不计入。

其余封顶不变：营销 → E0；无绑定身份的来源 → E1。E3 / E4 保留枚举兼容，
但各自保留来源类型，不当作普遍需求或商业价值的证明。
`claim_support="conflicting"` 不影响等级，只影响呈现（§5）。

### 3.4 跨平台身份

不同平台无法排除同一人时，不宣称已完成跨平台身份核验；计数仍按外部 ID 保守执行
（不同平台的两个 ID 视为两人，是已知限制，写入文档说明而非代码假装解决）。

## 4. 两条写入路径一致

- `discover_problems` 与 `validate_upsert`（shadow 路径）复用同一套
  build（含身份核对）+ admit 门禁和兼容逻辑。
- 宿主标注（`claim_support` / `claim_support_reason` / `experience=first_hand`）
  经 validate-upsert 的草稿字段进入，同样通过身份核对与理由非空检查；
  discover 路径默认 `unknown`。
- `ACCEPTED` 不被重抽静默降级的现有保护保持不变。

## 5. 查询与呈现

- 默认重点候选 = `admission ∈ {source_checked, accepted}` **且**
  没有任何一条证据 `claim_support == "conflicting"`。
- 带冲突标注的卡片进入「待复查」集合；`list` / `summary` / Skill present 口径一致。
- 发现输出如包含隔离（quarantined）材料，必须单独说明，不混入重点问题。
- 呈现措辞：`source_checked` 显示为「引用已核对」；不得出现「已核实 / 已验证」
  一类会被理解为主张成立或问题复现的词。

## 6. 文档定位同步

`README.md`、`docs/product-contract.md`、`AGENTS.md`、`CLAUDE.md` 统一为：

- 问题是默认入口与核心研究对象；产品是已有方案和替代方案；
  人物是问题当事人、验证伙伴和潜在合作者。
- 产品发现、反馈研究、关系能力按问题研究需要调用，不要求先经过产品发现。
- 旧命令可用性不变（冻结面不动）。
- 运行目标：每周至少推进一个有证据支持的投入或放弃决定；分别记录实际验证、
  判断变化和真实反馈，不汇总为总分。此为运行目标，不承诺每周必有产出。

## 7. 验收案例（回归测试按此写）

| # | 场景 | 预期 |
|---|------|------|
| 1 | 真实摘录配相反主张 | 可入库 `source_checked`；呈现「引用已核对，主张未知」；宿主标 `conflicting` 后退出默认重点 |
| 2 | 草稿伪造 `author_id`（与来源外部 ID 不符） | 身份清空，不计入独立作者，E2 不成立 |
| 3 | 同一作者多条评论 / 多条来源 | 一个外部 ID 只算一人，封顶 E1 |
| 4 | 两名真实第一手报告者（两件来源、两个外部 ID、经 validate-upsert 标 first_hand） | E2 成立 |
| 4b | 同样两条证据走纯 discover 路径（LLM 自报 first_hand） | 封顶 E1，E2 不成立 |
| 5 | 人工接受无证据卡片 | `accepted` 但呈现仍明示缺证据 |
| 6 | 旧数据 `admission: verified` | 读取映射 `source_checked`，不触碰磁盘 |
| 7 | `claim_support=supported` 但理由为空 | 门禁回落 `unknown` |
| 8 | control / shadow 隔离 | 不受影响，照旧 |

修改现有依赖旧 `verified` / E2 语义的断言；新增测试针对以上真实失败模式，
避免只测字段存在。受影响面跑 `problem_discovery` 与 CLI 相关测试；
整合后跑全量 pytest、Ruff、mypy。环境受阻时记录具体未运行项，不写「检查通过」。

## 8. 字段写入者原则（本轮起生效的约定）

每个字段声明唯一合法写入者，写入口强制：

| 写入者 | 可写内容 |
|--------|----------|
| 采集边界（connector / import） | 作者身份、URL、时间、原文——下游只读 |
| 确定性代码 | 引用核对结果、等级封顶、独立作者计数、admission |
| 宿主判断 | `claim_support`、`experience=first_hand`——必须附理由 |
| 用户 | `accepted`、放弃及其理由 |

本轮在 A 的字段上执行此原则；后续轮次推广到所有新增字段。

## 9. 后续轮次（路线图，本轮不实现）

- **B：观察日志 + 投影。** 问题存储重构为每问题一个追加式事件流（JSONL，
  沿用文件工作区与原子写），卡片成为确定性重建的视图；刷新 = 追加观察 + 重建，
  不再整卡覆盖。人工进展保护、冲突双方保留、幂等去重、崩溃恢复由结构自然给出。
  磁盘迁移一次性在 B 完成（A 只做兼容读取即为此留路）。
- **C：判断 / 实验 / 结果 / 决策**收敛为新增事件类型 + 投影规则；
  问题级复现状态在 C 落地，只能由实验结果事件推动。
- **长期研究计划 P1–P3**（检查点、授权、决定卡片、有界跟踪）同样建立在
  事件流上；授权与待办绑定稳定问题 ID，前置依赖 B。
- 状态维度一问一枚举、一枚举一个更新者；采集调整（评论拆分、覆盖上限、
  全文补读、URL 抓取）在闭环可用后实施。
