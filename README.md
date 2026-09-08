# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 读取 GitHub Commit/PR/Issue/测试证据，通过 `opencli` 搜索与读取 Twitter/X 内容，把工程实践与公共技术讨论提炼成「Idea」或「交流机会」，再由人工确认立场、生成草稿，最终必须经人工审核才能发布。

当前架构是 **Skill + 领域服务**（不再是 Graph Runtime）：8 个 Skill 覆盖认知任务（idea 发现 / 交流侦察 / 表达训练 / 草稿代写 / 声音画像 / 周复盘 / 费曼 / 凝练消息），状态机、存储、幂等、评分、Critic 门禁都留在确定性 Python 领域服务。`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init [--prune]             # 初始化 var/ 与数据库；--prune 清理孤儿表
uv run finch diagnose                    # 探测 gh / opencli 可用性

# —— idea 发现 ——
uv run finch ideas commit [--repo R] [--since 7d]   # 从最近 Commit 提炼 idea 候选
uv run finch ideas create --text "<片段>"            # 从用户片段提炼 idea 候选
uv run finch ideas create --conversation <id>         # 从已验证 ConversationEvidence 提炼
uv run finch ideas create --opportunity <id>          # 把 scout 机会转成 idea
uv run finch ideas list / show / confirm / revise-position / skip ...

# —— 交流侦察 ——
uv run finch scout search [--topic T]    # 从公开讨论提炼交流机会（Opportunity）
uv run finch scout list / show ...

# —— 表达训练 ——
uv run finch practice start --idea <id> --attempt "<首稿>"
uv run finch practice diagnose / save / finish / show ...

# —— 草稿 ——
uv run finch drafts create <id>          # 从已确认 idea 生成草稿（经 Critic，不自动发布）
uv run finch drafts show <draft_id>
uv run finch drafts revise <draft_id> --instruction "<指令>"

# —— 人工审核 ——
uv run finch review list / show / approve / revise / skip ...
uv run finch engagement list / show / approve / reject / edit / metrics ...

# —— 声音与复盘 ——
uv run finch voice show / approve-example <draft_id> / reject-example <draft_id> --reason "<理由>"
uv run finch weekly                      # 定性周复盘（指标由代码算，解读由 LLM 做）
uv run finch learn <draft_id> --url <URL> --metrics '<JSON>' --outcome '<JSON>' --learning '<文本>'
                                          # 记录已发布草稿的反馈，供 weekly 汇总
```

## 从 Idea 到 Draft

```bash
# 1. 从公开讨论提炼交流机会（落库为 Opportunity）
uv run finch scout search --topic "agent evals"

# 2. 把机会转成 idea 候选（落库为 ContentJob，状态 proposed）
uv run finch ideas create --opportunity <id>
#    或直接从 commit / 用户片段：finch ideas commit / finch ideas create --text "..."

# 3. 查看候选
uv run finch ideas list

# 4. 确认立场（proposed → confirmed）
uv run finch ideas confirm <id>

# 5. 从已确认 idea 生成草稿（idea-to-draft → DraftService → Critic，不自动发布）
uv run finch drafts create <id>

# 6. 人工审核
uv run finch review list
uv run finch review approve <draft_id>
```

候选状态机：`PROPOSED → CONFIRMED → DRAFTED`，或 `PROPOSED/CONFIRMED → SKIPPED`。

## Skill 架构

```
skills/
  idea-discovery/        Commit/PR/测试 + 用户片段 + ConversationEvidence → IdeaCandidate（finch ideas commit / create）
  conversation-scout/    公开讨论帖子 → Opportunity → finch scout search
  expression-practice/   表达训练（诊断+追问，skill 驱动 + finch practice 落库）
  idea-to-draft/         已确认 idea → Draft + CriticReport → finch drafts create（Assist 模式）
  voice-profile/         个人表达画像（finch voice）
  weekly-reflection/     定性周复盘（finch weekly）
  feynman-practice/      费曼技巧（检查理解）
  sticky-message/        检查想法是否清晰易记
  _shared/               idea-contract / evidence-policy / author-position / expression-contract / publication-safety

src/finch/
  ideas/            IdeaService（状态机）+ CommitService + FragmentService + OpportunityService
  drafts/           DraftService（已确认 idea → Draft + CriticReport）
  practice/         PracticeService（expression-practice 会话）
  idea/             finch drafts 复用的纯函数（rewrite_idea / idea_checker_suite）
  content/          ContentJob、writer、critic 检查器、voice profile
  inbox/            原创 + 互动轨道的投影与决策（InboxDecisionService）
  learn/            Feedback 模型 + weekly_analysis 指标 + WeeklyReflectionService 定性复盘
  evidence/         Commit → EngineeringEvent → EvidenceCard
  github/ twitter/  只读 adapter（gh / opencli）
  storage/          SQLite via SQLModel
```

详见 `docs/` 与 `skills/`。历史开发计划见 `docs/Finch-Codex-Development-Plan.md`（Graph 架构，已由 Skill 架构取代）。
