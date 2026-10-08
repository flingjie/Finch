# 领域开放：示例 vs 门槛 映射

Finch 从「Agent 工程默认」放开到「由用户当前问题决定领域」。这份清单区分
「Agent/工程」身份出现在代码里的三类位置，供 P1（技能边界与去 engineering 措辞）
与后续验收对照。不是领域分类系统。

## 实际门槛（P1 需去「engineering / 技术」措辞）

这些提示词把内容域写死为工程，会让非工程领域（书法、写作、决策复盘等）被误判或排除：

- `skills/idea-discovery/SKILL.md`（frontmatter + 正文）：「真实**工程**决策或问题」。
- `src/finch/ideas/fragment_service.py`：「a single publishable **engineering** Idea candidate」「Synthesize only a real **engineering** decision…」。
- `src/finch/ideas/divergence.py`：「publishable **engineering** idea」「bundle of traceable **engineering** facts」。
- `skills/peer-discovery/references/opportunity-signals.md`：「可发布的**工程** Idea」「**工程**缺口」。
- `prompts/critique-draft.md`：conversation 维度定义为「public **technical** discussion」。

## 纯示例（换词或保留，非门槛）

- `prompts/opportunity.md`、`prompts/prepare-contribution.md`、`prompts/practice-profile-draft.md` 里的 `agent-100-days`。
- `prompts/summarize-content.md` 的 `worth_asking` 示例（一个 Agent 项目）。

## 独立工具（保留，Agent 只是标签，不成为全局资格门槛）

- `src/finch/settings.py` 的 `RepoDiscoveryAgentTags.keywords`、`RepoDiscoverySettings.queries`、`default_view: all|agent`——`repo_discovery` 是 X 分享的 GitHub 热度榜，Agent 只作标注。
- `src/finch/repos/tagging.py`、`src/finch/repos/models.py`（`TopicTag = AGENT | OTHER | UNKNOWN`）。
- `prompts/extract-engineering-events-batch.md`、`prompts/merge-engineering-events.md`——`finch github reflect` 的用户自有仓库证据管线，与领域开放无关。

## 本就中性（无需改代码）

- `peer-discovery` / `creator-evidence`：代码无 GitHub 或技术门槛，跨领域；`src/finch/peers/person.py` 的 `CreatorEvidenceKind` 是领域中性枚举（creation / first_hand_experience / knowledge_sharing / cross_domain_bridge / conversation_behavior / marketing_or_repost）。
- `VoiceProfile`（`src/finch/content/voice.py`）：只有风格/来源字段，无 audience 或领域字段；更新规则是来源门槛（用户认可样本），不按领域限制，也不自动吸收特定作者口吻。

## 配置

- `finch.example.yaml` 已扩为跨领域示例 + Agent 保留一组；用户本地 `finch.yaml` 是 gitignored，需自行同步。
- `interests.usage_queries` 是死配置（`settings.py` 定义但无任何代码消费），模板已清空并标注。
