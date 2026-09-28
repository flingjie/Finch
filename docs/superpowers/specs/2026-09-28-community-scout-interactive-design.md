# Finch community-scout 智能交互改造 — 设计规格

日期：2026-09-28
状态：已确认，待进入 implementation plan。
代码基线：`main` `3c2dedee4591a99ddb08d9645bc47b9e9f89d9c4`（实施时须比较最新 HEAD）。
来源：对 `docs/Finch-Community-Scout-Interactive-Implementation-Plan.md`（2026-09-27）做决策收敛后形成。

## 1. 目标与完成定义

让 Finch 帮用户**围绕具体问题进入并持续参与合适的社区**：发现公开社区，解释其中实际讨论与可参与空间，用户选择后深入阅读，参与后根据真实结果决定继续、观察或离开。

本次范围 = 原 plan 的 **P1 + P2**（P0 的固定样例与基线作为实施前置，不单独成阶段；P3 四周试运行本次不进入 spec）：

1. **P1 交互与 Skill**：三种入口（本周探索 / 围绕问题 / 指定社区回访）、候选分层（观察 vs 可参与）、选中后深读、诚实的"当前不宜发言"表达，复用现有卡字段先行跑通，新增状态用 `note` 表达。
2. **P2 最小状态与反馈闭环**：兼容新增字段、稳定社区标识、最新投影去重、反馈引用与存在性校验，回访优先读已发生互动与未完成承诺。

边界不变：公开可访问资料；Finch 只读发现、分析、建议和记录，用户亲自加入、发言、贡献。不借用私有 Discord/Slack 消息，不把他人经验当用户实践。不引入通用 Graph Runtime、向量库、自动调权或另一套总控 Skill。

## 2. 已确认的交互决策

| 维度 | 实施要求 |
|---|---|
| 三种入口 | 本周探索最多 3 个、点名首选；围绕问题优先 1 个 + 备选 1 个；指定社区回访给继续/观察/暂缓 |
| 候选分层 | 观察 `observe` / 可参与 `actionable` 两态持久化；四层（公开发现/可观察/可参与/持续参与）是 Skill 推理阶梯 |
| 排序 | 先看问题匹配、可带入材料、具体入口、互动开放度、交流成本；`fit_score` 只作旧卡读取与诊断 |
| 连续交互 | 首轮完成任务，最多一个改变下一步的追问；未选择不批量生成回复/贡献方案/人物档案 |
| 建议动作 | 读讨论 / 有依据提问 / 准备回复 / 贡献真实案例 / 观察 / 暂缓；草稿仅用户选中后准备 |
| 真实互动 | 用户亲自执行；已发生状态须用户明确报告；可公开核验尽量核验，私下真实事件标来源类型 |
| 时间预算 | 用户未给时间预算不假定其有时间写代码；默认建议轻量、真实的一步 |
| 临时意图 | 只影响本次搜索，不静默写入长期兴趣 |

## 3. 当前代码实际具备什么（已对代码核验）

| 实际位置 | 当前行为 | 本次调整 |
|---|---|---|
| `skills/community-scout/SKILL.md` | 每周 3 张固定七字段行动卡；采集、判断、去重由 Skill 执行 | 三种入口、候选分层、按需深读、连续对话；周报保留 3 个默认深推 |
| `references/scoring-rubric.md` | 四维权重 35/25/20/20；近 30 天活动/具体入口/公开来源为硬门槛 | 改为"证据检查 + 情境判断 + 取舍解释"；可核验为证据条件，近期入口为立即参与条件 |
| `references/presentation.md` | 每张卡固定七问题 | 周报/按问题/深读/回访四种呈现；默认一句话建议 + 来源 + 一个下一步 |
| `references/community-card-schema.md` | 顶层 `community` 对应 `name`，id/week 自动补全 | 增加观察态、可参与态与旧卡示例；明确字段可空语义 |
| `communities/models.py` | `CommunityProfile` 保存名称/分数/证据/入口/贡献/周次；`CommunityFeedback` 状态 + 备注 | 加 `recommendation_state`、`canonical_url`、意图/证据时间、`interaction_ref`/`reason_kind` |
| `communities/repository.py` | 候选与反馈追加 JSONL；同 ID `inspect` 取最后一条；`list` 返回重复候选 | 稳定 ID 最新投影与完整历史；同次重复保存幂等展示；旧数据可读 |
| `communities/service.py` | 保存卡、记录反馈；反馈不影响后续判断 | 汇总实际反馈供 Skill 读取，谨慎调整本轮理由，不自动改权重 |
| `cli.py` | `feedback` 不校验社区存在；`discover` 写上下文但不会创建周报 | `discover` → `context`；校验状态动作；更新帮助与 JSON 契约 |

核验结论（撰写本 spec 时逐项确认）：

- `community discover` 只调 `snapshot_context` + `read_report`，**不进行公开搜索**；`read_report` 读不到时打印 `(no report ... 按 community-scout Skill 执行发现)`。
- `CommunityRepository.get_candidate` 用 `reversed(list_candidates())` 取同 id 最后一条；`list_candidates` 返回全部历史（`list` 命令会重复显示同一社区多次保存）。
- `CommunityService.record_feedback` 不做社区存在性校验，`feedback <id>` 对不存在 id 静默写入。
- `id` 由 `community_id_for(name)` 对 `name` 做 SHA256 内容寻址（`comm_<12 hex>`），相似但不同 name 不合并。
- `CommunityResult` 六值 `ignored/saved/joined/interacted/repeated/contributed`；`CommunityFeedback` 仅 `community_id/result/note/at`。
- 现有 `_shared/dialogue-policy.md`、`agent-presentation.md`、`topic-dialogue` 与 `finch dialogue` 已提供任务后延伸与讨论摘要，community-scout 复用、不复制对话状态机。

## 4. 数据模型改动（`communities/models.py`）

全部新字段可空/有默认值，旧 JSONL 与旧卡 YAML 继续可读。

**新增 `RecommendationState` 枚举**：`observe | actionable`。这是"Finch 建议什么"，与用户实际动作正交：

- 四层推理阶梯中「可观察候选」→ `observe`，「当前可参与」→ `actionable`；「公开发现」是发现溯源（见 `canonical_url`/`source_checked_at`）而非保存状态；「持续参与」由 `CommunityFeedback` 历史承载，不是推荐状态。

**`CommunityProfile` 新增字段：**

| 字段 | 类型 | 用途 |
|---|---|---|
| `canonical_url` | `str = ""` | 跨周稳定标识（优先主页/项目链接） |
| `recommendation_state` | `RecommendationState \| None = None` | 观察 vs 可参与 |
| `intent` | `str = ""` | `weekly` / `question` / `revisit` |
| `question` | `str = ""` | 问题模式的具体问题 |
| `practice_refs` | `list[str] = []` | 用户带入本次匹配的实践材料 |
| `source_checked_at` | `datetime \| None = None` | 资料最近核验时间 |

**`EntryPoint` 新增字段**：`url: str = ""`（确切讨论链接）、`status: str = ""`（`open`/`closed`/`unknown`/空）。保留 `discussion`/`suggested_angle` 兼容旧卡；未知时不声称可发言。

**`CommunityFeedback` 新增字段**：`reason_kind: str = ""`（`no_time`/`too_general`/`language_barrier`/`deep_but_later` 等短标签）、`interaction_ref: str = ""`（真实互动链接）、`ref_kind: str = ""`（`public_url`/`user_stated`）。六值 `CommunityResult` 不变，三者与结果正交。

**编码的不变量：** 已发生状态（`interacted`/`repeated`/`contributed`）无 `interaction_ref` 时为 `user_stated`（自述、未公开核验），带 `interaction_ref` + `ref_kind=public_url` 时可核验。"准备/打算"不得记成 `interacted`；机器 JSON 读取不产生"已展示/已参与"事实。

## 5. 身份与去重（`communities/repository.py`）

- `identity_key(profile) = canonical_url if canonical_url else id`。有规范 URL 时跨周稳定去重，无则回退 name-hash id；**不**凭名称合并不同社区（旧 name-hash id 继续解析）。
- 新增 `list_latest_profiles()`：每个 `identity_key` 一条，按追加顺序取最后一条；`list_candidates()` 保留原始历史（审计）。
- `save` 保持 append-only；幂等去重发生在投影层（`list` 命令），不改变写入语义。
- `get_candidate(community_id)` 语义不变（同 id 取最新），但新增可携带反馈的读取路径供回访使用。

## 6. Service 与 CLI 改动

**`context` 取代 `discover`**（`finch community context [--week] [--json]`）：

- 快照当前实践上下文到 `profile.yaml` 并输出；不搜索、不声称生成周报。
- 若该周已存在报告则附带展示（不变的额外读取），但不声称本次产生。
- 周报生成责任明确在 Skill：读上下文 → 有界公开搜索 → `save` 有证据的卡 → 呈现。持久化产物是**保存的卡**；`list --week` 渲染周视图。本次不实现 `reports/<week>.md` 持久化（见 §8 决策 2）。

**`list` 默认去重**（修复重复展示）：

- 默认输出最新投影（每个 `identity_key` 一条，各带最新反馈）；`--all` 输出原始历史；`--week` 过滤；`--json` 结构不变（`{profile, feedback}`）。

**`inspect <id> --json`** 附带最新反馈 + 完整反馈历史（回访流程一次读齐）。

**`feedback` 校验 + 扩展**：

- 校验 id 存在，不存在则 exit 1，不静默写入。
- 新增 `--ref`（`interaction_ref`）、`--ref-kind`（默认 `public_url`，仅在给 `--ref` 时有效）、`--reason-kind`。
- `CommunityResult` 六值不变；`observe/actionable` 不是 feedback 结果。

## 7. Skill 改动（`SKILL.md` + 三个 references）

- **三种入口**（§2 表格）落地为首轮交付差异；周报最多 3 个深推，按问题不强求 3 个，无入口可建议观察。
- `scoring-rubric.md` → 证据检查 + 情境判断 + 取舍解释；四维 `fit_score` 保留供旧卡读取/诊断，不是用户面前结论；"近 30 天活动"降级为周报及时性默认窗口与诊断信号，不无条件否决更新慢但高价值的开源社区。
- `presentation.md` → 周报 / 按问题探索 / 选中深读 / 回访四种呈现；默认一句明确建议 + 必要来源 + 一个下一步，详情按需展开；不固定七字段。
- `community-card-schema.md` → 观察态、可参与态、旧卡示例；字段可空语义明确。
- 候选分层取代一次打分淘汰；建议动作限定为 plan 所列集合；准备草稿仅用户选中后。
- 连续交互：首轮完成任务，最多一个可能改变下一步的追问；复用 `_shared/dialogue-policy.md` 与 `topic-dialogue`，不规定 4–6 轮、不每轮强行追问；用户"只看结果"直接交付。
- 搜索预算（试运行参数）：周探索最多 20 个跨源候选 → 筛 6 个读近期证据 → 深入最多 3 个、展示最多 3 个；问题模式优先 1 + 备选 1；选中深读最多 5 条相关公开讨论。分源失败返回部分结果与缺口，不因一处超时伪造全网结论。

## 8. 本设计明确的三项决策（原 plan 未定）

1. **`recommendation_state` 两态持久化，四层推理阶梯不持久化**：plan §3.2 的四层是 Skill 判断过程，§5 的 `observe/actionable` 是持久化结果。落成两态：`observe`（有可核验证据但当前无切入线程，可推荐阅读）、`actionable`（有仍相关话题且能说明可提问/分享/贡献）。「公开发现」记为 `canonical_url`/`source_checked_at` 溯源；「持续参与」由反馈历史承载。
2. **周报文件持久化本次不做**：持久化产物是保存的卡，`list --week` 渲染周视图。plan 中"若不自动写 `reports/<week>.md`，则 `discover` 不得把候选列表称为已生成周报"——改为 `context` 后无此歧义。若后续要持久化 markdown 报告，是独立小改动，不阻塞本次。
3. **`list` 默认去重而非新增 `--latest` 标志**：plan 建议 `list --latest`。默认去重更贴近常见用途（"我有哪些社区"），`--all` 保留原始历史；`--latest` 不单独设标志。

（若以上任一与你预期不符，实现前可改；其余微决策均从 plan 自身指引收敛。）

## 9. 分阶段任务与文件清单

### P1：交互与 Skill（最小可用）

- [ ] 更新 `skills/community-scout/SKILL.md`：三种入口、分层判断、选中后深读、自然语言取舍。
- [ ] 更新 `references/scoring-rubric.md`：证据检查 + 情境判断 + 取舍解释；保留旧分数说明兼容。
- [ ] 更新 `references/presentation.md`：四种呈现；默认一句话建议 + 来源 + 一个下一步。
- [ ] 更新 `references/community-card-schema.md`：观察态/可参与态/旧卡示例。
- [ ] 对齐 `_shared/dialogue-policy.md`；不规定轮次，用户"只看结果"直接交付。
- [ ] 以现有卡字段跑 5 次真实任务，记录用户为何选/不选；新状态先用 `note` 表达。

验收：用户说"先了解"/"不要起草"/"不想参与这个"时后续步骤正确改变；无材料不编亲历；首轮无需填满七项才有用。

### P2：最小状态与反馈闭环

- [ ] `communities/models.py`：§4 全部新增字段与枚举。
- [ ] `communities/repository.py`：`identity_key`、`list_latest_profiles`、反馈历史读取路径。
- [ ] `communities/service.py`：反馈存在性校验、汇总实际反馈供 Skill 读取（不自动改权重）。
- [ ] `cli.py`：`discover` → `context`；`list` 默认去重 + `--all`；`inspect --json` 附带反馈；`feedback` 校验 + `--ref`/`--ref-kind`/`--reason-kind`。
- [ ] `tests/unit/test_cli_community.py` + `tests/unit/test_communities.py` 增补（见 §10）。
- [ ] 回访优先读已发生互动与未完成承诺；报告参与建议与真实事件分开。
- [ ] 可选：在 weekly reflection 增加一段社区参与回看，避免把社区互动重复计为新真人关系。

验收：旧卡可读；重复保存不重复展示；不存在的社区不能默默写反馈；已互动不再收到首次加入建议；"没时间"不导致永久过滤。

### 文件范围

主要修改：`skills/community-scout/SKILL.md`、三个 references、`src/finch/communities/models.py`、`repository.py`、`service.py`、`src/finch/cli.py`、`tests/unit/test_cli_community.py`、`tests/unit/test_communities.py`。必要时 `README.md` 与 `docs/product-contract.md`。只有真实回访需要时才接入 `learn/reflection.py` 及关联测试。

## 10. 必须通过的验收用例

| 场景 | 必须观察到的结果 |
|---|---|
| 旧卡缺少新字段 | `inspect`/`list` 仍可读；历史 id 与反馈可查 |
| 同社区本周重复发现 | 最新投影一条；来源历史保留；不重复计展示 |
| 有实践证据但暂无线程 | 推荐 `observe`，不声称能立即参与 |
| 讨论已关闭或无法读取 | 不建议直接发言，标明核验局限 |
| 用户没个人实践材料 | 可基于观察提问，不生成虚构案例 |
| 用户只说"考虑看看" | 最多保存意向，不记录已加入/互动 |
| 用户说"我回复了" | 按其报告记录真实事件及来源；未核验不声称已核验 |
| 候选搜索部分来源失败 | 披露覆盖范围，保留可用结果 |
| 机器 JSON 读取 | 不产生"已展示/已参与"事实 |
| `feedback` 不存在的 id | exit 1，不静默写反馈 |
| `list` 默认 vs `--all` | 默认一条/社区，`--all` 原始历史 |
| `context` 命令 | 快照上下文；不声称搜索/生成周报 |

确定性测试重点覆盖存储、去重、校验与反馈写入。修改 Python 后执行相关单测，再按仓库约定 `uv run pytest`、`uv run ruff check .`、`uv run mypy src`，区分基线已有失败与本次新增失败。语义质量以固定来源样例人工对比，不写只镜像文案的测试。

## 11. 兼容、上线与回退

- 全部新字段可空/有默认值；旧卡、旧反馈、旧周报样例迁移后继续可读。
- `CommunityResult` 六值不变；`observe/actionable` 只作推荐状态，不进 feedback 结果。
- `id` 仍由 name 内容寻址，`canonical_url` 仅作跨周去重键，不重写历史 id。
- JSONL 维持单用户串行写假设，不引入新数据库；重复 `save` 不令本周展示重复，原始历史保留。
- `discover` 更名 `context` 是唯一破坏性 CLI 改动；该命令近期新增、迁移成本近零。
- 回退 P2 时停止新字段写入与 `feedback` 校验即可，旧文件保留供恢复，不删除用户记录。
- 未发生的用户反馈与未来互动不得在开发验收中模拟成产品效果。
