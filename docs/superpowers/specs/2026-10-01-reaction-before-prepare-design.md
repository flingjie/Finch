# 先反应、再准备：让用户的一句话进入贡献

日期：2026-10-01
状态：已确认（待写实现计划）
代码基线：`main` `0470171`
前置：`2026-10-01-practice-profile-design.md` §10 提到的「最小表达路径」即本 spec。

## 1. 背景与问题

斋藤孝《输出力》对 Finch 最有价值的提醒是：**把输出设计成用户参与思考的过程**——带着表达任务阅读、
边读边形成反应、先说再写、通过选择呈现个人视角。对照当前代码，Finch 在「发现」和「完整贡献」之间
没有给用户的亲身经验、疑问和情绪留出入口：

| 环节 | 现状 |
|---|---|
| `finch connect daily` 首选机会 | 六问之后以「先做这个切口吗？」收尾——这是方向决策，不是邀请用户说出自己的反应 |
| `finch connect prepare --opportunity` | 只接 `--opportunity`，立刻生成正文；`prompts/prepare-contribution.md` 有 `user_positions`（已确认 ContentJob）和 `user_practices`（已确认实践）两个槽，没有「用户刚才对这条帖子说了什么」 |
| `skills/_shared/dialogue-policy.md` | 任务完成后挑一个延伸点追问，用于挑战判断；不在准备贡献之前收集反应 |
| `finch inspirations` / `finch dialogue` | 能存用户文本，但都不回流到 prepare |

结果是 Finch 太快从外部内容跳到完整结论；用户拿到更多草稿，却没有更清楚地知道自己相信什么，
也缺少开始表达的动力。

## 2. 目标与非目标

**目标**

- 首选机会以**一个用户 10 秒内能回答的具体问题**收尾，指向用户自己的经历或分歧。
- 用户的回答（原话）进入 `connect prepare`，并在成稿中可见地保留。
- 没有用户反应时，Finch **不得**生成完整结论性贡献（方法卡 / 案例 / 演示），只能准备一个澄清问题。
- 改动面限定在「首选机会 → 反应 → prepare」这一条路径，两周内可用真实使用验证。

**非目标（YAGNI）**

- 不给 `connect assess --url`、`connections follow-up` 加反应步骤。
- 不新增 Skill；不新增独立 CLI 命令。
- 不对反应做类型分类（经历 / 疑问 / 假设），不按类型路由贡献形式。
- 反应不写入 Inspiration、DialogueNote、PracticeProfile；不自动建议 `profile add`。
- 不为「假设」类反应自动创建 CollisionCard / MicroExperiment。
- 不把反应次数、练习次数换算成任何指标；不改北极星指标。
- 不改 assess prompt / `OpportunityDraft`；收尾问题由 Codex 在呈现时组织。

## 3. 数据模型（`src/finch/opportunities/models.py`）

```python
class Reaction(BaseModel):
    """用户对这条机会亲口说的话（原话，append-only）。"""
    seq: int                                  # 从 1 起
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class Opportunity(BaseModel):
    ...
    reactions: list[Reaction] = Field(default_factory=list)   # 新增，兼容扩展
```

定位：反应是用户对**这条机会**的话。它不是 `InteractionRecord`（没有发生互动）、不是 `ConversationThread`
笔记（没有对方）、不是 `PracticeItem`（未经 confirm）、不是 `DialogueNote`（不是练习讨论）。它只在这个
聚合内有效，随 Opportunity 快照与事件日志持久化。

旧 YAML 读取时 `reactions` 默认空，行为与今天一致。`Artifact` 不加字段。

## 4. 服务（`src/finch/opportunities/service.py` / `prepare.py`）

### 4.1 `OpportunityService.record_reaction`

```python
def record_reaction(
    self, opportunity_id: str, *, text: str,
    expected_revision: int | None = None, request_id: str | None = None,
) -> Opportunity
```

- 加锁 → 读快照；`closed` 状态拒绝（`ValueError`）；`text.strip()` 为空拒绝（`ValueError`）。
- 内容幂等：`text.strip()` 与最新一条 `reactions[-1].text` 相同 → 不追加、不写事件，返回当前快照。
  这使「LLM 失败后重跑同一条命令」不会重复记录。
- 否则追加 `Reaction(seq=len(reactions) + 1, text=text)`；`revision + 1`；事件
  `event_type="reaction_recorded"`，沿用现有 `request_id` 幂等与「事件已落、快照未推进」的崩溃重放逻辑
  （与 `_transition` 相同）。
- **不改 `status`**：反应不是方向决策，`select` 仍由 prepare 路径执行。

### 4.2 `prepare_contribution(..., reaction: str | None = None)`

在现有锁内顺序执行：

1. 状态校验不变（必须 `selected`）。
2. `reaction` 非空 → 先 `record_reaction`，再读取最新快照。
3. 形式决定（确定性）：

   ```python
   form = p.form if opp.reactions else ContributionForm.CLARIFYING_QUESTION
   ```

   这是本设计唯一的 Python 门禁：**没有任何反应 → 只能准备澄清问题**。有反应 → 沿用 assess 给出的
   `proposal.form`，如何把原话用进去由写作 prompt 决定。
4. `write_contribution` 多传 `user_reaction`（最新一条原话，或 `"(none)"`）与实际 `form`。
5. Artifact id：无反应沿用 `art_{opp.id}_{form}`（不动现有数据）；有反应用 `art_{opp.id}_{form}_r{seq}`。
   同一反应重复 prepare 不产生新文件。

### 4.3 `cli.py::_prepare_new_opportunity(settings, ws, opportunity_id, *, reaction=None)`

| 当前状态 | 无 `--reaction` | 有 `--reaction` |
|---|---|---|
| proposed | select → prepare（无反应 → 澄清问题） | select → prepare（记录反应 → 按 proposal.form） |
| selected | prepare | prepare |
| ready | 返回现有成果（幂等，与今天一致） | select（ready → selected 已是合法「调整贡献」转换）→ prepare 重生成 |
| parked / closed | None | None |

## 5. CLI

`finch connect prepare --opportunity <id> [--reaction "<用户原话>"] [--json]`

- `--reaction` 可选；与多个 `--opportunity` 同时出现时退出码 1（一条反应只属于一条机会）。
- 文本输出在成果前多一行：`你的反应（第 n 条）：<原话>` 或 `无反应：本次只准备澄清问题`。
- JSON `artifacts[]` 不变；`opportunities[]` 条目新增 `"reaction": {"seq": n, "text": "…"} | null`
  与 `"form_forced": true|false`。
- `connect daily --json` 的首选机会条目透出 `reactions`（兼容新增字段）。

## 6. Prompt（`prompts/prepare-contribution.md`）

在 `## User real practices` 之后新增：

```
## User reaction to THIS opportunity (verbatim; the user's own words in this session)

{user_reaction}
```

规则补三条（放在 First-person experience rules 内）：

- 反应中用户说出的经历可以用第一人称写，但**只能复述，不能外推**（不补数字、不补结论、不补用户没说的
  场景），每句标 `[reaction]`。反应与某条 confirmed practice 同时相关时，两者都可引用（`[reaction]` 与
  `[practice-id]` 并列）。
- 反应是疑问 → 把它写成贡献里的那个「可继续的问题」，不替用户回答；反应是猜测 → 写成「我猜测 / 一个假设是」
  并给出怎样验证，不写成结论。
- `(none)` 时 form 已被代码置为 `clarifying_question`：只写一个具体观察 + 一个诚实的问题，忽略
  `expected_output` / `scope` 中方法卡形状的要求；第一人称仍只允许来自 confirmed practices。

其余不变：`{form}` 占位符传入的是 §4.2 决定后的实际形式。

## 7. Skill 呈现

### 7.1 `skills/peer-discovery/SKILL.md` + `references/presentation.md`

六问不变；收尾句从「先做这个切口吗？」改为**一个指向用户自己经历或分歧的具体问题**，由 Codex 从
`why_me` / `open_questions` / 对方帖子的具体细节组织。形状：

```text
今天我会优先跟进 @alice 关于 Agent 重试失败的讨论：她保存了 trace，但同一任务重跑结果仍不同，
现有回复主要建议多跑几次。

这和你处理过的调用超时问题有关，但她讨论的是重复执行带来的后果。

你当时最难处理的是等待太久，还是不确定任务到底执行了没有？
```

问题要求：用户 10 秒内能答；「A 还是 B」或「你那次是怎么处理的」式；禁止「你怎么看」「要不要做」。

用户下一轮 → CLI：

| 用户说 | 动作 |
|---|---|
| 讲经历 / 提问题 / 说猜测（任何实质回答） | `uv run finch connect prepare --opportunity <id> --reaction "<原话>"` |
| 「先准备吧」「直接给我正文」（不回答） | `uv run finch connect prepare --opportunity <id>`；呈现时说明只准备了澄清问题及原因 |
| 「今天不弄」/ 换话题 | 不 prepare，自然结束 |

`--reaction` 传用户原话；Skill 不润色、不补全、不拆分。一段话里既有经历又有疑问就整段传入。

### 7.2 `skills/interaction-preparation/SKILL.md` + `references/presentation.md`

- 呈现正文前先用一句话点出**来自用户的那句**（`[reaction]` 对应内容），再给来源与正文；无反应时写
  「没有你的反应，这次只准备了一个澄清问题」。
- 「改：…」若改的是用户自己的经历或判断 → 再次 `prepare --reaction "<新原话>"`（新 seq，重生成）；
  只是改措辞 → 现有路径。
- 边界补一条：反应可以第一人称写，但不是 confirmed practice，不进 `practice-profile.yaml`，Skill 不自动
  建议 `profile add`。

### 7.3 不改的部分

`_shared/dialogue-policy.md` 不改：收尾问题是任务交付的一部分（替代原收尾句），不是任务后延伸点；
延伸点检查仍在 prepare 交付之后做一次。

## 8. 错误处理

| 场景 | 行为 |
|---|---|
| `--reaction` 为空白 | 退出码 1，提示「反应不能为空；不回答请不传 --reaction」 |
| 机会 closed | 退出码 1（与今天 parked/closed 不可准备一致） |
| 多个 `--opportunity` + `--reaction` | 退出码 1 |
| LLM 写作失败 | 反应已记录（事件已落），成果未生成，机会停在 `selected`；输出说明「反应已记下，正文生成失败，可重试」；重跑同一命令因内容幂等不重复追加反应 |
| 旧 Opportunity 无 `reactions` 字段 | 默认空；prepare 行为同「无反应」 |

## 9. 测试

- `tests/unit/test_opportunity_reaction.py`（新）：`record_reaction` seq 递增、revision+1、事件写入、closed 拒绝、
  空文本拒绝、同文本内容幂等、`request_id` 幂等、崩溃重放（事件已落快照未推进）。
- `tests/unit/test_prepare_contribution.py` 扩展：无反应 → form 强制 clarifying_question 且 prompt 含 `(none)`；
  有反应 → form 沿用 proposal、prompt 含原话；Artifact id 后缀；`reaction=None` 且 `reactions` 为空时 prompt
  除新增块外与改动前一致。
- `tests/unit/test_cli_connect.py` 扩展：`--reaction` 文本/JSON；空白拒绝；多机会 + 反应拒绝；ready + 新反应
  → 重生成；ready 无反应 → 返回旧成果；`connect daily --json` 含 `reactions`。
- prompt contract test：`prepare-contribution.md` 占位符与 `.format()` 参数集合一致（新增 `user_reaction`）。
- `skills/peer-discovery/evals/cases.yaml` 新增：首选以具体问题收尾；禁止「你怎么看 / 要不要做」收尾；
  实质回答 → `prepare --reaction`；「先准备吧」→ 不传 `--reaction` 并说明只得澄清问题。

## 10. 验收

1. `finch connect daily` 经 Skill 呈现后，首选机会以一个具体的、指向用户经历的问题收尾。
2. 回答后 `finch connect prepare --opportunity <id> --reaction "…"` 的正文含至少一句 `[reaction]`，且不含
   反应里没说过的第一人称事实。
3. 不回答直接 `prepare` 得到的成果 kind 为 `reply_draft`（clarifying_question 形式），正文只有观察 + 一个问题。
4. 对 ready 机会再传新 `--reaction` 会重生成；不传则返回原成果。
5. `uv run pytest` / `uv run ruff check .` / `uv run mypy src` 全绿。

## 11. 两周观察（人工，不建指标）

每天用 `docs/trial-run.md` 现有节奏记三项：是否回答了反应问题、成稿是否保留了 `[reaction]` 句、
是否发出并得到回应。两周后判断三件事：是否更容易开始表达；成稿是否保留了自己的经验和判断；
是否产生了值得继续的交流。结论决定是否把反应步骤扩展到 `assess --url` 与 `follow-up`。

## 12. 后续（不在本 spec）

- 反应步骤扩展到 `connect assess --url`（指定帖子）与 `connections follow-up`（对方回应后）。
- 「通过选择呈现个人视角」：浏览列表选人时记录选择理由。
- 反应文本累积后，是否值得由用户主动把某些经历 `profile add` 进 practice profile（仍由用户发起）。
