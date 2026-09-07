# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 读取 GitHub Commit/PR/Issue/测试证据，通过 `opencli` 搜索与读取 Twitter/X 内容，把工程实践与公共技术讨论提炼成「Idea」，再由人工确认立场、生成草稿，最终必须经人工审核才能发布。

当前架构是 **Skill + 领域服务**（不再是 Graph Runtime）：`commit-to-idea` / `search-to-idea` 提炼候选 → `IdeaService` 落库为 `ContentJob` → 人工确认 → `idea-to-draft` 生成草稿 → `DraftService` + Critic 审查 → 人工审核。`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init [--prune]             # 初始化 var/ 与数据库；--prune 清理孤儿表
uv run finch diagnose                    # 探测 gh / opencli 可用性

uv run finch ideas commit [--repo R] [--since 7d]   # 从最近 Commit 提炼 idea 候选
uv run finch ideas search [--topic T]               # 从公开讨论提炼 idea 候选
uv run finch ideas list                              # 列出全部候选（ContentJob）
uv run finch ideas confirm <id>                      # 确认立场：proposed → confirmed
uv run finch ideas skip <id> --reason <理由>          # 跳过候选

uv run finch drafts create <id>          # 从已确认 idea 生成草稿（经 Critic，不自动发布）
uv run finch drafts show <draft_id>
uv run finch drafts revise <draft_id> --instruction "<指令>"

uv run finch review list                          # 列出待审核的原创草稿
uv run finch review show <draft_id>
uv run finch review approve <draft_id>            # 采用（不自动发布）
uv run finch review revise <draft_id> --instruction "<指令>"
uv run finch review skip <draft_id> --reason <理由>

uv run finch engagement list                      # 列出待审核的互动候选
uv run finch engagement show <id>
uv run finch engagement approve <id>              # 批准（不自动发布）

uv run finch weekly                      # 周复盘
uv run finch learn <draft_id> --url <URL> --metrics '<JSON>' --outcome '<JSON>' --learning '<文本>'
                                          # 记录已发布草稿的反馈，供 weekly 汇总
```

## 从 Idea 到 Draft

```bash
# 1. 从公开讨论提炼 idea 候选（落库为 ContentJob，状态 proposed）
uv run finch ideas search --topic "agent evals"

# 2. 查看候选
uv run finch ideas list

# 3. 确认立场（proposed → confirmed）
uv run finch ideas confirm <id>

# 4. 从已确认 idea 生成草稿（idea-to-draft → DraftService → Critic，不自动发布）
uv run finch drafts create <id>

# 5. 人工审核
uv run finch review list
uv run finch review approve <draft_id>
```

候选状态机：`PROPOSED → CONFIRMED → DRAFTED`，或 `PROPOSED/CONFIRMED → SKIPPED`。

## Skill 架构

```
skills/
  commit-to-idea/   从 Commit/PR/Issue/测试变化提炼 IdeaCandidate → finch ideas commit
  search-to-idea/   从公开讨论提炼 IdeaCandidate → finch ideas search
  idea-to-draft/    已确认 idea → Draft + CriticReport → finch drafts create
  _shared/          idea-contract / evidence-policy / author-position / quality-policy / voice-guide

src/finch/
  ideas/            IdeaService（状态机）+ CommitService + SearchService
  drafts/           DraftService（已确认 idea → 草稿）
  idea/             finch drafts 复用的纯函数（rewrite_idea / idea_checker_suite）
  content/          ContentJob、writer、critic 检查器、voice profile
  inbox/            原创 + 互动轨道的投影与决策（InboxDecisionService）
  learn/            反馈数据模型 + weekly 周复盘
  evidence/         Commit → EngineeringEvent → EvidenceCard
  github/ twitter/  只读 adapter（gh / opencli）
  storage/          SQLite via SQLModel
```

详见 `docs/` 与 `skills/`。历史开发计划见 `docs/Finch-Codex-Development-Plan.md`（Graph 架构，已由 Skill 架构取代）。
