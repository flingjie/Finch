# Finch Connection-First Refactor — Implementation Plan

> 来源 spec：`/Users/lingjiefan/Downloads/Finch-Connection-First-Refactor-Plan.md`（本文将其 8 阶段分解为可执行的 TDD 任务）。
> 分支：`refactor/connection-first`。每个 Task 结束都跑测试并 commit。

**Goal:** 将 Finch 从「证据驱动内容生成」重定位为「同行连接与个人表达系统」，以 `finch connect daily` 为主入口。

**Architecture:** 保持 Skill + 领域服务。新增 `peers/`、`conversations/` 关系领域；把 engagement 的「候选建议」与「已发生互动」拆为两个事实；`inbox/` 收敛为只读统一投影；CLI 以 `connect`/`peers`/`conversations` 替换 `scout`/`engagement`。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel/SQLite、Typer、Alembic、pytest。

## Global Constraints（verbatim 自 spec §2）

- Finch 独立运行，不接入/调用/依赖 builderDNA；不共享数据库、状态文件、包、Skill、CLI、数据契约。
- 不为未来集成增加 adapter/handoff/export/import 抽象。
- 保留 `finch` 统一 CLI；不恢复通用 Graph Runtime。
- LLM 只生成候选与语义判断；状态、幂等、去重、评分汇总、发布门禁由 Python 负责。
- 公开回复/引用/私信/原创默认需人工批准；`guard.evaluate_execution` 非批准不返回 success。
- 外部帖子/对话不得伪装成用户个人经验。

---

## Phase 0 — 产品契约（docs only）

**Task 0.1:** 重写 `README.md` — 首要目标=同行连接，内容生成降级；Skill 数量与实际目录一致。
**Task 0.2:** 更新 `CLAUDE.md` — 明确各 Skill 与领域服务边界；`Draft`/`ContentJob` 不再作为顶层核心对象。
**Task 0.3:** 新增 `docs/product-contract.md` — 核心闭环、对象所有权、非目标；「连接主循环 + 表达复利循环」。
**Task 0.4:** 清理 `docs/` 旧计划与 README 中「原创/互动同等优先」表述；明确不以粉丝/发帖/草稿数为核心指标。

验收：README、CLI 帮助、Skill 描述同一套产品语言；无 builderDNA 依赖（已确认满足）。

## Phase 1 — 关系领域

**Files:**
- Create `src/finch/peers/models.py`（`PeerProfile` + `PlatformIdentity` + `RelationshipStage`）
- Create `src/finch/peers/service.py`（按 `platform + author_id` 幂等归一化）
- Create `src/finch/conversations/models.py`（`ConversationThread` + `ThreadStatus`）
- Create `src/finch/conversations/service.py`
- Modify `src/finch/engagement/models.py`（新增 `InteractionRecord`、`ContributionType`；`InteractionProposal` 复用 `InteractionCandidate` 语义并补 `peer_id` 等字段）
- Modify `src/finch/storage/repositories.py`（新增 `PeerRepository`、`InteractionRecordRepository`、`ConversationThreadRepository`）
- Add alembic migration + 唯一约束（`platform + author_id`；proposal `peer+source+action+prompt_version`）
- Test `tests/unit/test_peer_models.py` / `test_peer_service.py` / `test_conversation_service.py` / `test_peer_repositories.py` / `test_interaction_record_repository.py`

验收：同一作者跨多帖只一个平台身份；Proposal/实际互动/结果三事实分离；删除或重跑搜索不丢关系上下文。

## Phase 2 — 同行发现

**Files:**
- Rename `skills/conversation-scout/` → `skills/peer-discovery/`（含 references/evals）
- Modify `src/finch/engagement/flow.py` → 拆为 search / peer-aggregation / relationship-scoring / opportunity-scoring（新增 `peer_value` 评分：topic_overlap + practical_depth + contribution_space + continuity_potential − repetition_penalty − promotion_risk）
- 限每轮推荐人数与每人帖数；一平台失败不取消其他平台

## Phase 3 — 连接优先 CLI

**Commands:** `finch connect daily/prepare/approve/reject/record`、`finch peers list/show`、`finch conversations list/show/follow-up`。
**Adjust:** 删除 `finch scout`、`finch engagement` 旧入口（一次性替换）；`inbox/` 只读投影。
**Files:** Modify `src/finch/cli.py`；Modify `src/finch/inbox/service.py`。

验收：daily 可进入人物/帖子/草稿/对话；未批准内容无法进入执行态；同一 proposal 不重复记录互动。

## Phase 4 — 对话成为观点来源

**Files:** Modify `skills/idea-discovery/references/conversation-signals.md`；`finch ideas create --conversation` 读完整 `ConversationThread`；AuthorIdea 来源白名单；`idea-to-draft` 加 `communication_goal` + `recommended_format`（reply/quote/short post/thread/DM/do-not-publish）。

## Phase 5 — VoiceProfile 闭环

**Files:** Modify `finch voice approve-example`（接受亲写/最终修订文本）；记录初稿→终版 diff；提出变更→用户确认→写入；他人风格分析只出 `StyleReport`。

## Phase 6 — 反馈与周复盘

**Files:** Modify `src/finch/engagement/metrics.py` → 关系质量指标；`learn/weekly.py` 只做确定性聚合；`skills/weekly-reflection/` 定性解读。新指标：meaningful_interactions / continued_conversations / repeat_peers / new_relevant_peers / ideas_from_conversations / collaboration_signals / stale_conversations。

## Phase 7 — 清理与 E2E

删除被 connect/peers/conversations 替换的旧 CLI 与测试；删除「无证据才走互动分支」历史逻辑；`ContentJob` 仅作 AuthorIdea 状态实现内部能力；README Skill 数与目录一致；E2E 覆盖重复运行/部分平台失败/LLM 超时/拒绝/修订后旧批准失效/未验证观点不得升级。
