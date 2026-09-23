# Finch 主动对话与观点引导 — 设计规格

日期：2026-09-23
状态：已确认，待进入 implementation plan。
代码基线：`main` `9a7528e29fa359780b16a72e8046a3c85cbcd734`（实施时须比较最新 HEAD；本 spec 撰写时 HEAD 为 `80332a0`，比基线多一个与本功能无关的 `gh_client` URL 编码提交）。

## 1. 目标与完成定义

Finch 在宿主 Codex 会话中像熟悉用户项目的同行：先完成任务并自然说明结果，再主动分析一个值得讨论的线索；围绕关键假设、反例与适用条件帮助用户形成观点，最后提出一个最小验证行动。

本次改变交互行为，继续沿用「Skill + 确定性 Python 领域服务」。保持跨行业连接与灵感助理的产品定位，讨论服务于发现、连接、实践和表达。对话轮数不成为新的北极星指标。

完整交付分两部分：

1. **对话体验（P0）**：现有任务入口自然表达、主动延伸、按分歧决定追问或收束。
2. **持续理解（P1）**：可检索的讨论摘要，区分用户立场、试探想法、AI 推测与未解决问题；历史引用可追溯。

先交付 P0 即可验证体验，但 P1 完成前不得宣称已支持完整跨会话记忆。

## 2. 已确认的交互决策

| 维度 | 实施要求 |
|---|---|
| 当前问题 | 避免日志式输出；完成指令后能发现值得深聊的线索 |
| 开聊方式 | 先展开一轮有价值的分析，再邀请用户补充经验或判断 |
| 延伸优先级 | 优先选择可能动摇现有判断、帮助形成观点的线索 |
| 挑战方式 | 直接指出薄弱假设，给出反例或明确标注的假设情境，再邀请回应 |
| 补充查证 | 先说明证据缺口、查证范围和它能解决的分歧，等用户决定 |
| 追问与收束 | 只追问可能改变结论的关键分歧；没有新关键点就总结 |
| 历史关联 | 主动联系过去观点，指出变化或可能的矛盾，请用户确认现在的判断 |
| 讨论产出 | 当前观点、成立条件、一个最小验证行动；执行由用户决定 |
| 主动频率 | 每次完成用户任务都尝试找一个延伸点；无价值线索不硬凑 |
| 语气篇幅 | 同行式、直接自然；默认两三段，需要时展开 |

「每次任务」指一个用户意图完成，不是每次 CLI 调用。讨论收束本身不再递归触发一个新讨论。用户说「只给结果」「先不聊」「停」时遵循当轮指令。

## 3. 当前代码实际具备什么（已对代码核验）

| 当前位置 | 已有实现 | 本次差距与处理 |
|---|---|---|
| `skills/_shared/agent-presentation.md` | 要求隐藏技术 ID、点名首选、呈现结论/决策卡/操作 | 已有良好基础，但固定结构偏报告体，且含数量冲突（8–12 张 / 3 条 / 10 条并存）；改成自然表达原则，删除共享层的业务数量规定 |
| `skills/topic-dialogue/SKILL.md` | 同行角色、七种推进动作、来源隔离 | 独立显式触发；默认用户先表达、4–6 轮、每轮必问、challenge 仅用户显式触发；改成任务后可衔接、先分析、按分歧收束 |
| `topic-dialogue/references/presentation.md` 与 `dialogue-contract.md` | 状态不展示，但收尾要求 YAML | 内部结构保留，用户默认看到自然语言总结 |
| `topic-dialogue/references/turn-strategies.md` | 澄清、经历、因果、边界、反例、后果、证据 | 保留动作库，取消机械轮次与每轮必问约束 |
| `src/finch/cli.py` | 多数主命令已有 `--json`；文本展示含状态和 ID | 不重写全部 CLI；由 Codex 解释结果；仅补齐必要的数据与呈现副作用接口 |
| `src/finch/conversations/` | 真实同行的 ConversationThread、承诺、实验、观察笔记 | 不能复用为用户与 Finch 的讨论记忆，否则污染关系事实和指标 |
| `src/finch/practice/models.py` | 表达训练的首稿、诊断、修改、最终表达 | 不扩成万能对话模型 |
| `src/finch/content/jobs.py` + `ideas/service.py` | `AuthorPosition` 立场确认与 `position_revisions`（append-only）修订史 | 复用已确认观点与历史；不以讨论结束代替 `ideas confirm` |
| `src/finch/storage/workspace.py` | 原子写、Pydantic 读写、文件 Workspace | 新增讨论摘要时复用，不引入数据库或向量库 |
| `skills/micro-experiment/SKILL.md` | 输入限定 CollisionCard | 普通讨论收尾只建议小验证，不把所有讨论强塞进 CollisionCard |
| `AGENTS.md`、`CLAUDE.md`、产品契约 | Codex 是智能节点，Python 管状态；topic-dialogue 不进入默认流程 | 增加自然语言交互入口说明，明确「主动邀请讨论」与「自动执行领域流程」的区别 |

核验结论（撰写本 spec 时对代码逐项确认）：

- `agent-presentation.md` 确实并存「8–12 张」「3 条」「10 条」三处数量，且均属共享层业务数量，应删除。
- `topic-dialogue/SKILL.md` 硬编码「4–6 轮」「每轮必须一个问题」（硬约束）「challenge 仅用户显式触发」；`dialogue-contract.md` 收尾固定输出 YAML 块。
- `AGENTS.md` 目前极简，无「先读共享策略」的读取路径，P0-A 的读取路径为真实缺口。
- `ContentJob` 确有 `AuthorPosition` + `position_revisions`（append-only），`ContentJobStatus` 为 `PROPOSED → CONFIRMED → DRAFTED`/`SKIPPED`；`IdeaService.revise_position` 追加修订史。`DialogueNote.position_status=user_confirmed` 与 `ContentJobStatus.CONFIRMED` 是两条边界，必须保持分离。
- `connect daily --json` 分支提前 `return`，未执行 `_record_presentations` / `_record_person_presentations`；JSON 缺失文本分支会打印的 `snapshot.created_at` 与 stale 提示。`connect more --json` / `connect person --json` 同理不写曝光/selected。

**第一版不新增一个与 topic-dialogue 重叠的对话 Skill。** 新增共享对话策略文件，扩展现有 topic-dialogue；已有业务 Skill 负责完成任务，共享策略负责如何接话。

## 4. 交互分层与触发方式

### 4.1 各层职责

| 层 | 负责 | 不负责 |
|---|---|---|
| 业务 Skill | 理解任务、读取当前结果、调用现有领域命令 | 复制一套对话状态机 |
| `_shared/dialogue-policy.md`（新增） | 选择延伸点、分析先行、查证边界、停止条件 | 执行抓取、生成正式草稿、写关系事实 |
| `topic-dialogue`（改造） | 用户接话后的连续讨论与自然收尾 | 自动确认作者立场、自动外发 |
| Codex 当前会话 | 语言组织、语义判断、当轮意图与选项映射 | 充当无限自主运行的后台程序 |
| Python 领域服务 | 现有业务执行；P1 新增摘要校验、持久化、修订与检索 | 决定每一句话、给所有用户回答打分 |

优先使用已有结果与本地上下文，不为每次回复额外启动一个 `codex exec`。在宿主 Codex 会话里完成分析与表达。

### 4.2 在 Codex 中如何确保生效

新增共享文件不会自动被执行，必须补齐读取路径：

- 根 `AGENTS.md` 增加短节：使用 Finch 帮用户发现、连接、讨论、表达时，先读共享呈现/对话策略，再读目标 Skill。代码维护、测试日志不套用产品话术。
- 高频 Skill 的 SKILL.md 明确引用共享策略，并要求执行完成后做一次延伸点检查。
- `topic-dialogue` description 增加「回应 Finch 上轮延伸问题、继续刚才的判断讨论」等触发语义。
- 用户回应延伸点时，沿当前上下文进入 topic-dialogue，不要求重新输入 Skill 名或重述话题。
- 根入口与已安装 Skill 两条路径都验收；同步现有用户安装方式，不能只改仓库文件就声称用户环境已更新。
- 如使用目录复制安装，应把 `_shared` 一起同步；文档写清相对路径解析方式。验收实际可读路径，不能假定存在自动发现机制。

这是一套当前会话内的行为约定，不代表 Codex 会在用户不发消息时主动推送，也不新增定时任务。

## 5. 每轮对话规则

### 5.1 先交付，再延伸

普通任务默认两三段：先给结果与意义，再分析一个重要线索，最后视需要问一个具体问题。列表、对比、三张社区卡等任务保留用户需要的结构；「两三段」约束的是讲解，不截断用户要求的交付物。

每次最多选择一个延伸点，选择依据依次为：与当前任务直接相关；能改变判断或行动；有材料支持；尚未讨论清楚。它可以是反例、范围变化、历史观点变化、证据不足，不能只有空泛的「值得深入」。

没有证据支持挑战时，提出带条件的可能性或自然结束。不推测用户从未表达过的立场，不对每次「保存成功」强加哲学讨论。

### 5.2 挑战的最低内容

挑战应让用户看见：哪条假设薄弱、反例如何影响结论、目前建议保留或收窄什么判断。再问一个用户回答后可能改变结论的问题。

真实反例必须有可读取来源。构造的反例写成「假设……」，不得伪装成已发生案例。检索到的他人经验不转成用户个人证据。

### 5.3 区分授权范围

| 情况 | 行为 |
|---|---|
| 用户明确要求「搜索/刷新/读取这个链接」 | 按原任务范围完成，不重复询问 |
| 为完成原请求所必要的已授权读取 | 正常执行 |
| 任务完成后，为新延伸出的争议追加调查 | 先说明缺口、来源范围、预计规模、可能改变的判断，等用户决定 |
| 用户说「查一下」且唯一待确认计划明确 | 执行该计划，不再重复确认 |
| 用户说「有道理」 | 不能同时解释为同意观点、批准查证和批准执行实验 |
| 用户拒绝、延期或转移话题 | 停止待查证动作，不换个工具偷偷继续 |

证据计划保持短小：`要验证的假设 + 来源范围 + 预算/停止条件 + 对结论的影响`。MVP 由 Skill 遵循授权边界；不是能拦截任意 Codex 工具调用的全局权限系统。

### 5.4 收束

取消默认 4–6 轮与每轮必须一个问题。每轮最多一个问题；没有关键分歧可以零问题收尾。关键问题是「回答是否会改变当前结论或下一步」，不是能否继续聊出更多细节。

默认总结三个内容：用户当前认可的观点、适用条件/剩余缺口、一个小验证建议。尚未形成观点就明确说尚未形成，不代替用户定论。最小验证说明行动、观察信号和什么结果会推翻假设；仅建议，不自动执行。

用户换任务立即跟随，不强制插入旧话题总结。需要保留的内容后续写成摘要，不占用前台一大段输出。

### 5.5 内部状态与自然话术分开

内部可维护 `topic / current_judgment / assumptions / open_gap / pending_check / last_extension`；不展示 YAML、轮次、策略名、技术 ID。只有用户明确要求导出结构或排查时才展开。

失败时说明影响范围与可选下一步，例如「这次没读到帖子正文，我只能根据已有摘要判断」，不能把失败压缩成假成功。

## 6. 跨会话记忆：薄扩展

### 6.1 先复用，再补缺口

P0 使用当前会话与现有 `ContentJob.position_revisions`，有真实来源才引用过去立场。没有可读历史时直接说无法确认，不凭模型印象伪造原话。

P1 新增 `src/finch/dialogue/`，仅存讨论检查点。ConversationThread 继续代表真实同行互动；PracticeSession 继续代表表达练习；ContentJob 继续代表表达链路的作者立场。

### 6.2 最小数据模型（新增，不是现有字段）

一个 `DialogueNote` 对应一个讨论主题记录；单个 YAML 保存当前摘要与追加式修订列表，避免同时维护观点库、会话库和事件总线。

| 字段 | 含义 |
|---|---|
| `id / schema_version / revision` | 记录身份、兼容版本、修订号 |
| `topic / topic_key` | 展示主题与检索标签；标签只召回，不自动合并相似观点 |
| `origin_refs` | 关联任务、社区卡、Opportunity 或已有 idea；附来源类型 |
| `checkpoints[]` | 每次有意义的收束摘要，追加不覆盖 |
| `checkpoint_id / created_at` | 调用方重试幂等键与时间 |
| `user_position` | 用户实际说出或认可的判断；可以为空 |
| `position_status` | `tentative / user_confirmed / unresolved`；只描述讨论认同程度 |
| `confirmation_quote` | 如为 user_confirmed，保留用户认可的原话与可用来源引用 |
| `conditions / open_questions` | 成立条件与尚未解决问题 |
| `assistant_hypotheses` | 与用户立场分开保存的 AI 推测 |
| `suggested_validation` | 建议验证行动；未批准默认只是建议 |
| `change_reason / supersedes_checkpoint_id` | 观点变动原因与被修正版本 |

`position_status=user_confirmed` 不等于 `ContentJobStatus.CONFIRMED`，更不等于观点被事实证实。写草稿继续走已有领域门禁；一次明确且范围清晰的用户确认可被正确路由，避免机械重复询问。

摘要在有实质判断变化或收束时保存，不记录每轮全文、不记录隐藏推理。用户说「这段别记」时跳过；支持删除。默认允许保存区分来源的摘要，但不得自动把 AI 总结升级为用户立场。

### 6.3 CLI（均待新增）

```bash
uv run finch dialogue save --file note.json --expected-revision 0 --json
uv run finch dialogue search --query "skill 代码化" --limit 3 --json
uv run finch dialogue show <id> --json
uv run finch dialogue forget <id> --json
```

命令由 Skill 使用，不要求用户记命令。`save` 经模型校验后原子写入 `<var_dir>/dialogue/<id>.yaml`；同 checkpoint_id 重试不重复追加；版本不符返回冲突并要求重新读取，不能覆盖旧版本。

沿用当前单用户串行写入假设。`atomic_write` 只保证替换原子性，revision 检查并不提供多进程事务保证；本期不并发写同一记录，也不声称解决了跨进程竞态。

检索先用标签/关键词和时间排序，最多返回 3 条相关摘要，由 Codex 判断是否同一条件下的观点变化。读取历史不调用网络。相似文本不自动归并。无需 embedding、向量库或通用记忆框架。

来源没有宿主消息 ID 时，保存实际可得的用户摘录与本地检查点引用，并标明来源局限，不生成虚假的 Codex 对话链接。

### 6.4 本设计明确的三项决策（原 plan 未定）

1. **身份与检索**：`id` 为首次保存时生成的**不透明标识**；`topic_key` 是稳定的检索键。宿主重找某主题记录时走 `search --query <topic>` → `show <id>` → `save --expected-revision <rev>`。**不采用**从 topic 派生的确定性 `id`：plan 要求「相似观点不自动归并」，而近义 topic 的 slug 碰撞会静默合并两条本应独立的记录。
2. **保存触发与确认**：在有意义的收束（形成/改变判断，或真正收束）时自动保存，并附一行提示「已记下这条判断摘要，可说『别记』撤销」。既非完全静默，也非每次保存前询问。
3. **「主动联系过去观点」的读取时机**：仅当讨论映射到可识别的 topic（topic_key 有重叠）时才 `search`，绝不每条消息都查；只读、不联网。指出「矛盾」前必须读原记录并比较成立条件，不得凭同关键词认定自相矛盾。

## 7. CLI 与呈现兼容性

优先复用现有 `--json`，但不能全局机械替换调用：

- `connect daily --json` 当前提前返回，未执行 `_record_presentations` / `_record_person_presentations`。
- `connect more --json` 同样不写曝光；`connect person --json` 不记录 selected。
- `connect daily` 的 JSON 当前缺少文本分支提示的快照时间与 stale 信息，直接改成 JSON 会遗漏新鲜度提示。
- `community discover` 只是生成本地实践上下文和读取周报，不会自己完成公开社区搜索；前台不可声称已经抓取。

实施策略：

1. P0 保留现有会影响曝光/选择记录的命令路径，Codex 解释其输出，不原样粘贴日志。其余已确认无相关副作用的入口可用 JSON。
2. P1 若切换发现入口为 JSON，补齐 `snapshot_created_at / stale / refresh_status` 等必要事实，兼容增加字段，不破坏旧键。
3. 新增显式呈现记录入口或复用等价领域服务：只提交实际展示的 snapshot/person/opportunity IDs 与 surface；校验属于快照，稳定 presentation key 去重。selected 与 displayed 分开。
4. 仅在当前答复确实准备展示的项目上记录；宿主没有用户阅读回执时，定义为「已输出到答复」，不得表述为「用户已读」。保留序号到 ID 的快照映射。
5. 自动化/纯机器 JSON 读取继续无曝光副作用。不能通过「所有 JSON 读取都算曝光」补洞。

P1 切换须和曝光契约测试同批提交；若无法完整实现，保持原调用路径，不能把它当作已完成优化。

## 8. 分阶段任务与文件清单

### P0-A：统一规则（首个可评审提交）

- [ ] 新增 `skills/_shared/dialogue-policy.md`：固定本次十项决策、分析先行、零或一个问题、授权范围、停止条件。
- [ ] 改 `skills/_shared/agent-presentation.md`：移除固定「结论/卡/操作」要求及业务数量；保留来源、ID 隐藏、失败诚实表达。
- [ ] 改 `AGENTS.md`、`CLAUDE.md`、`docs/product-contract.md`：明确共享策略读取入口；将 topic-dialogue 定位改成独立可唤起且可从任务结果衔接。
- [ ] 在文档中明确：共享交互策略适用于产品使用，不影响编码任务的测试与错误报告。

验收：不存在「每轮必须提问」「默认固定 4–6 轮」「收尾默认 YAML」等仍会生效的冲突规则。

### P0-B：改造现有 topic-dialogue

- [ ] 更新 `skills/topic-dialogue/SKILL.md` 及三个 references 文件。
- [ ] 开场：已有材料先分析；真正缺少对象或问题时才澄清，避免先问泛泛的「你怎么看」。
- [ ] challenge 可由明确观点和有效反例触发，不再只有用户说「挑战我」才启用；没有用户立场时不能编造被挑战对象。
- [ ] 收尾自然语言呈现；结构契约用于内部整理与后续存储。
- [ ] P0 保留纯会话状态；P1 接入摘要时同步修改原「禁止持久化」的硬约束，保留「禁止写关系事实」。

验收：模糊想法、明确观点、事实争议、用户转向四类对话均能自然推进或结束。

### P0-C：贯穿业务入口

首批修改 `peer-discovery`、`community-scout`、`idea-discovery`、`weekly-reflection` 的 SKILL.md 与呈现 references。随后覆盖 `interaction-preparation`、`conversation-follow-up`、`idea-to-draft`、`voice-profile`，以及其他用户可直接触发的 Skill；逐一检查是否残留更具体但冲突的呈现规则。

- [ ] 完成任务后只做一次延伸点检查，内部无有效点就结束。
- [ ] 保留完整交付，延伸不得抢占用户要求的名单、草稿、报告。
- [ ] 用户接话进入讨论，直接给新任务则执行新任务，不追着旧问题问。
- [ ] 使用现有 ContentJob 历史时注明来源与时间，不自动修订立场。
- [ ] 更新 README：自然语言用法、只看结果、继续讨论、查看依据、先不查、别记这段。
- [ ] 实测仓库使用及安装后的 Skill 使用，确认共享文件真实可达。

P0 完成标准：日常输出和连续讨论行为可用；跨会话仅能引用已有可读记录，局限明确。

### P1-A：讨论摘要与历史关联

新增：

- `src/finch/dialogue/__init__.py`
- `src/finch/dialogue/models.py`
- `src/finch/dialogue/repository.py`
- `src/finch/dialogue/service.py`
- `tests/unit/test_dialogue_memory.py`
- `tests/unit/test_cli_dialogue.py`

修改 `src/finch/cli.py` 注册薄命令，复用 Workspace 与当前配置路径。不新增 runner。更新 topic-dialogue 对持久化的说明及产品对象所有权表。

- [ ] 保存、读取、检索、删除、幂等与冲突处理。
- [ ] 缺少用户认可来源不能写成 user_confirmed；允许保留 tentative/unresolved。
- [ ] 历史引用时读原记录，再解释条件变化；不能凭同关键词认定自相矛盾。
- [ ] 失败只影响记忆保存，不阻止当前对话；告知尚未保存，重试不重复。
- [ ] 对 AI 对话完成、模拟 Builder 对话等操作，关系记录与北极星指标均不变。
- [ ] 如需进入观点链路，调用原 IdeaService；不新增平行的观点确认机制。

### P1-B：机器结果与呈现副作用适配

按第 7 节补齐必要字段与显式呈现行为。主要涉及 `src/finch/cli.py`、现有曝光 repository/service 及 `tests/unit/test_presentation.py`、`test_cli_connect.py`、`test_cli_community.py`。不把全 CLI 文案重构绑入本项目。

### P2：真实使用校准

用一周实际使用中的 10–20 段对话检查：挑战有没有依据、问题是否影响结论、是否需要手动阻止追问、是否出现重复确认。调整共享策略与例子，不先写大规模评分平台。

不得把「接话率越高」作为单一优化目标；用户快速完成任务并自然结束同样是成功。

## 9. 必须通过的验收用例

新增 `skills/topic-dialogue/evals/cases.yaml` 存输入场景、上下文、期望行为与禁止行为；自然语言质量使用人工回放或模型辅助评审，不能仅靠字符串测试声称体验达标。

| 场景 | 必须观察到的结果 |
|---|---|
| 有效推荐任务 | 先完整交付，再分析最多一个延伸点；内部 ID 不进入前台 |
| 用户要求 50 人浏览 | 不因「两三段」省略交付；讲解简洁，不生成 50 个讨论问题 |
| 明确观点且有反例 | 指出具体假设与反例关联，再提一个会影响结论的问题 |
| 只有设想反例 | 明确假设性，不宣称真实案例 |
| 没有价值延伸 | 正常结束，不机械问「还需要什么帮助」 |
| 连续讨论已无分歧 | 即使只有两轮也可收束，不凑 4–6 轮 |
| 用户只赞同一条理由 | 不把整套观点标成用户确认 |
| 新争议需要检索 | 提出具体查证计划；用户同意前无新增网络调用 |
| 原任务明确要求搜索 | 直接完成授权范围内的搜索，不重复问许可 |
| 用户同意查证 | 只执行该查证范围；失败不编造证据，不声称问题已解决 |
| 用户拒绝/换话题 | 停止待查证，不纠缠旧问题 |
| 历史观点似乎矛盾 | 引用真实记录，先比较条件，再请用户确认变化 |
| 新会话没有历史 | 不虚构「你上次说过」；历史存在时才能恢复 |
| 保存讨论摘要 | 不写 PeerProfile/InteractionRecord/ConversationThread，不改变关系指标 |
| 建议最小实验 | 有行动与观察标准；用户未选择前不执行，不记录成功 |
| 机器读取与展示 | JSON 读取不计曝光；实际展示只记对应项目；换一批不重复 |
| 过期或失败结果 | 新鲜度/失败范围保留，不能因语言润色被隐藏 |
| 重试与修订冲突 | 同 checkpoint 幂等；旧版本拒绝覆盖；坏文件不静默当作无历史 |

确定性测试重点覆盖存储、门禁、隔离与曝光行为。修改 Python 后执行相关单测，最后按仓库约定执行 `uv run pytest`、`uv run ruff check .`、`uv run mypy src`，区分基线已有失败与本次新增失败。仅文案阶段不为自然语言的每个词编写镜像测试。

## 10. 兼容、上线与回退

- 不删除或改名现有 Skill；不重建 Graph Runtime；不替换数据库或抓取架构。
- 保留现有 CLI 参数和默认业务语义，新增 JSON 字段采用兼容扩展。
- 用户与 Finch 的讨论不得变成真实同行互动，也不得自动改变 VoiceProfile。
- `dialogue/` 新目录不要求迁移旧数据；旧观点记录按需读取。
- P0、P1 分开提交，先跑一轮真实使用再上摘要。回退 P1 时停止新入口和自动写摘要即可，保留文件供恢复，不删除用户记录。
- 若共享策略未在实际安装路径加载，视为未完成；若只演示单独 topic-dialogue，却未覆盖任务后接话，也视为未完成。
