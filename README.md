# Finch

Finch 是一个**跨行业连接与灵感助理**。它帮你发现不同领域中持续创造、分享一手经验的人，理解值得了解或交流的原因，延续真实对话并积累关系上下文，保存由观察、交流和实践引发的新问题、新视角与方法；公开表达（形成自己的观点、写成像自己的内容）是可选出口。

Finch 记录对话事实、明确承诺与线程内问题/使用反馈笔记；不做跨来源痛点聚类或产品机会评分（那是 builderDNA），也不做产业战略（Quinn）。工具收款权威记录属于具体工具项目。

> 完整定义见 [`docs/product-contract.md`](docs/product-contract.md)。Finch 独立运行，不依赖任何其他项目（如 builderDNA）。

当前架构是 **Skill + 领域服务**（不是 Graph Runtime，也不是 LLM agent loop）：Skill 覆盖认知任务，状态机、存储、幂等、评分、Critic 门禁都留在确定性 Python 领域服务。`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 两条循环

- **连接主循环**：发现同行/使用者 → 准备互动 → 记录关系 → 继续对话。这是 Finch 每天首先呈现的东西（`finch connect daily`）。
- **表达复利循环**：从实践与对话形成观点 → 写成像自己的内容 → 吸引更多同行。它服从连接目标。

北极星指标：**每周新增或加深多少个「有上下文、可继续」的同行关系**。粉丝数、发帖数、草稿数都不是核心指标。PeerProfile 不是 CRM lead；Opportunity 不是商业机会。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init [--prune]             # 初始化 var/ 文件工作区
uv run finch diagnose                    # 探测 gh / opencli 可用性

# —— 连接主循环 ——
uv run finch connect daily [--refresh] [--view home|browse] [--question "…"]  # 首页 0–1 首选机会；browse 为 50 人分层（5/15/30）
uv run finch connect today --limit 10    # 别名：connect daily 首页（纯读）
uv run finch connect person <person_id>  # 查看某人完整证据与档案（只读）
uv run finch connect prepare --opportunity ID --reaction "你的一句反应"   # 深度准备（默认每次最多 5；不传 --reaction 只得澄清问题）
uv run finch connect assess --url <url>  # 对指定讨论做一次机会评估
uv run finch connect feedback --file feedback.json
uv run finch peers list / show <peer_id> # 同行档案与关系上下文
uv run finch connections today           # 今日承诺面（需回应 / 兑现）
uv run finch connections record --person <peer_id> --url <url> --body "..."   # 用户亲自发布后登记
uv run finch connections follow-up --opportunity ID   # 接续对方回应
uv run finch people shortlist --today    # 同 connections today
uv run finch conversations list --needs-follow-up / show / follow-up / ingest / note / commit / defer / close
uv run finch sources doctor / sync       # 只读采集（twitter|reddit|github|v2ex|weixin|xiaohongshu）

# —— 灵感笔记 ——
uv run finch inspirations save --text "想保留的启发" [--source <ref>] [--origin …]
uv run finch inspirations list / show <id> / note <id> --text "…" / archive <id>
uv run finch collisions generate / weekly
uv run finch experiments start <collision_id> --action "…" --observe "…" --stop "…"

# —— 表达复利循环 ——
uv run finch ideas commit / create / list / show / confirm / revise-position / skip
uv run finch drafts write / create / show / revise
uv run finch review list / show / approve / revise / skip
uv run finch voice show / approve-example / reject-example / revoke-example / propose
uv run finch weekly
uv run finch learn <draft_id>            # 记录发布反馈
uv run finch practice start --idea <id> --attempt "<首稿>"
uv run finch style analyze --text "<文本>" | --file posts.md | --url "<链接>"
uv run finch community context / list / run
uv run finch dialogue save / search / show / forget
uv run finch github reflect
uv run finch twitter search / import-bookmarks / diagnose
uv run finch context                     # 每日 / 待办只读投影
```

## 自然语言用法

- 「只看结果」——完成当前任务，不做延伸讨论。
- 「继续讨论」——回应 Finch 上轮延伸问题，进入连续讨论。
- 「查看依据」——要求展示某条结论的来源。
- 「先不查」——停止待查证动作。
- 「别记这段」——跳过当前讨论摘要的保存。

## 从同行到观点

```bash
# 1. 发现交流机会（轻量卡，无整表草稿）
uv run finch connect daily --refresh

# 2. 选中后深度准备（生成正文 ≠ 已发布）
uv run finch connect prepare --opportunity <opportunity_id> [--reaction "<你的原话>"]

# 3. 记录真实互动（含系统外导入），收到回复后跟进
uv run finch connections record --person <peer_id> --url <url> --body "..." --opportunity <opportunity_id>
uv run finch conversations ingest --peer <peer_id> --url <url> --body "..." --attested
uv run finch conversations follow-up <conversation_id>

# 4. 从对话形成观点候选（确认立场 → 草稿 → 人工审核）
uv run finch ideas create --conversation <id>
uv run finch ideas confirm <id>
uv run finch drafts create <id>
uv run finch review approve <draft_id>
```

观点状态机：`PROPOSED → CONFIRMED`，或 `PROPOSED/CONFIRMED → SKIPPED`。修订历史 append-only；草稿生成走「待审」（`review`）语义，不改 job 状态，且 ≠ 观点已证实。

## Skill 架构

```
skills/
  peer-discovery/          公开讨论 → 首页 0–1 首选机会 + browse 的 50 人分层（5/15/30）+ PeerProfile
  creator-evidence/        有限工件 → CreatorEvidence（必须引用 artifact_id）
  connection-opportunity/  判断有无真实贡献（无贡献则 SKIP）
  interaction-preparation/ 选中后深度准备互动建议（默认每次最多 5）
  reply-crafting/          公开回复草稿；不发布
  conversation-follow-up/  按真实触发信号恢复对话并提出下一步
  relationship-review/     由互动记录判断关系阶段与是否联系
  idea-discovery/          Commit/用户片段/已验证对话 → ContentJob
  idea-to-draft/           已确认观点 → Draft + CriticReport
  voice-profile/           个人表达画像（只从用户认可样本更新）
  weekly-reflection/       关系质量/观点形成/表达反馈复盘
  collision-lab/           跨领域结构碰撞 → CollisionCard
  micro-experiment/        CollisionCard → 一周内小实验
  # —— 独立训练工具（不进入默认流水线）——
  community-scout/         围绕问题发现并持续参与社区（三入口 + 观察/可参与分层，验证中）
  expression-practice/     表达训练
  writing-style-analysis/  分析他人写作风格（只读，不写画像）
  feynman-practice/        费曼技巧
  sticky-message/          检查想法是否清晰易记
  topic-dialogue/          围绕话题讨论，形成或修正判断（不写关系记录）
  _shared/                 idea-contract / evidence-policy / author-position / expression-contract / publication-safety / agent-presentation / dialogue-policy
```

```
src/finch/
  peers/           PeerProfile / Person / CreatorEvidence（按 platform+author_id 幂等归一化）
  conversations/   ConversationThread / 跟进触发 / Commitment
  connections/     真实互动登记与机会跟进
  dialogue/        讨论摘要（与对话线索、观点分开）
  inspirations/    轻量灵感笔记
  collisions/      跨领域碰撞与一周小实验
  opportunities/   交流机会聚合（评估 / 状态机 / 贡献制作）
  discovery/       每日发现编排（sources → people → 首选机会）
  sources/         跨平台只读抓取（opencli + RawArtifact）
  engagement/      发现快照 / InteractionRecord / 推荐反馈（旧评分管线已移除）
  communities/     社区档案与反馈（无评分）
  ideas/           观点状态机 + Commit/Fragment 服务
  drafts/          DraftService（已确认观点 → Draft + CriticReport）
  practice/        expression-practice 会话
  idea/            finch drafts 复用的纯函数
  content/         ContentJob、writer、critic 检查器、voice profile
  style/           writing-style-analysis（只读）
  webfetch/        通用网页正文提取器（只读 adapter，fail-closed）
  inbox/           连接主循环 + 表达复利循环的只读统一投影与决策
  learn/           Feedback + 周复盘指标 + 定性复盘
  evidence/        Commit → EngineeringEvent → EvidenceCard
  github/ twitter/ reddit/  只读 adapter（gh / opencli）
  storage/         文件 Workspace（YAML / Markdown / JSONL，原子写）
```

详见 `docs/` 与 `skills/`。
