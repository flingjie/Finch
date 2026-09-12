# Finch

Finch 是一个**同行连接与个人表达系统**。它帮你发现值得长期交流的同行，理解对方正在解决的问题，准备有价值的互动，延续对话并积累关系上下文，最终从实践与交流中形成自己的观点，写出像自己的内容。

> 完整定义见 [`docs/product-contract.md`](docs/product-contract.md)。Finch 独立运行，不依赖任何其他项目（如 builderDNA）。

当前架构是 **Skill + 领域服务**（不是 Graph Runtime，也不是 LLM agent loop）：Skill 覆盖认知任务，状态机、存储、幂等、评分、Critic 门禁都留在确定性 Python 领域服务。`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 两条循环

- **连接主循环**：发现同行 → 准备互动 → 记录关系 → 继续对话。这是 Finch 每天首先呈现的东西（`finch connect daily`）。
- **表达复利循环**：从实践与对话形成观点 → 写成像自己的内容 → 吸引更多同行。它服从连接目标。

北极星指标：**每周新增或加深多少个「有上下文、可继续」的同行关系**。粉丝数、发帖数、草稿数都不是核心指标。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init [--prune]             # 初始化 var/ 文件工作区
uv run finch diagnose                    # 探测 gh / opencli 可用性

# —— 连接主循环 ——
uv run finch connect refresh             # 有界刷新发现池 + 快照
uv run finch connect today --limit 10    # 纯读：8–12 轻量机会 + 需跟进对话
uv run finch connect daily [--refresh]   # 默认同 today；缺快照/过期或 --refresh 时刷新
uv run finch connect more --snapshot ID --limit 5
uv run finch connect prepare --opportunity ID   # 深度准备（默认每次最多 3）
uv run finch connect feedback --file feedback.json
uv run finch peers list / show <peer_id> # 同行档案与关系上下文
uv run finch connect approve / reject / edit / record
uv run finch conversations list --needs-follow-up / show / follow-up / ingest / defer / close

# —— 表达复利循环 ——
uv run finch ideas commit / create / list / show / confirm / revise-position / skip
uv run finch drafts create / show / revise
uv run finch review list / show / approve / revise / skip
uv run finch voice show / approve-example / reject-example / propose
uv run finch weekly
uv run finch practice start --idea <id> --attempt "<首稿>"
uv run finch style analyze --text "<文本>" | --file posts.md | --url "<链接>"
uv run finch github reflect
uv run finch twitter search / import-bookmarks / diagnose
```

## 从同行到观点

```bash
# 1. 发现交流机会（轻量卡，无整表草稿）
uv run finch connect daily --refresh

# 2. 选中后深度准备（批准 ≠ 已发布）
uv run finch connect prepare --opportunity <opportunity_id>
uv run finch connect approve <proposal_id>

# 3. 记录真实互动（含系统外导入），收到回复后跟进
uv run finch connect record <proposal_id> --url <url>
uv run finch conversations ingest --peer <peer_id> --url <url> --body "..." --attested
uv run finch conversations follow-up <conversation_id>

# 4. 从对话形成观点候选（确认立场 → 草稿 → 人工审核）
uv run finch ideas create --conversation <id>
uv run finch ideas confirm <id>
uv run finch drafts create <id>
uv run finch review approve <draft_id>
```

观点状态机：`PROPOSED → CONFIRMED → DRAFTED`，或 `PROPOSED/CONFIRMED → SKIPPED`。修订历史 append-only；草稿生成 ≠ 观点已证实。

## Skill 架构

```
skills/
  peer-discovery/        公开讨论 → Opportunity（8–12 轻量）+ PeerProfile
  interaction-preparation/ 选中后深度准备互动建议（默认 ≤3）
  conversation-follow-up/  按真实触发信号恢复对话并提出下一步
  idea-discovery/        Commit/用户片段/已验证对话 → AuthorIdea
  idea-to-draft/         已确认观点 → Draft + CriticReport
  voice-profile/         个人表达画像（只从用户认可样本更新）
  weekly-reflection/     关系质量/观点形成/表达反馈复盘
  # —— 独立训练工具（不进入默认流水线）——
  expression-practice/   表达训练
  writing-style-analysis/ 分析他人写作风格（只读，不写画像）
  feynman-practice/      费曼技巧
  sticky-message/        检查想法是否清晰易记
  _shared/               idea-contract / evidence-policy / author-position / expression-contract / publication-safety
```

```
src/finch/
  peers/           PeerProfile 关系领域（按 platform+author_id 幂等归一化）
  conversations/   ConversationThread / 跟进触发 / Commitment
  engagement/      Opportunity / InteractionProposal / InteractionRecord / 发现流水线
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
  github/ twitter/ 只读 adapter（gh / opencli）
  storage/         文件 Workspace（YAML / Markdown / JSONL，原子写）
```

详见 `docs/` 与 `skills/`。
