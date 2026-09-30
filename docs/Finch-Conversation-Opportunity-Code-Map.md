# Finch 交流机会系统 —— 现有实现代码映射

> **历史文档（2026-09-30 阶段 0）**。下文描述的三层 engagement 管线 / `InteractionProposal` / 未接线的 `ConnectionOpportunity` **已被 `src/finch/opportunities/` 聚合取代**。以当前代码与 `CLAUDE.md` 为准；本文仅保留决策追溯，勿再按其中模块路径实施。
>
> 本文是《Finch 交流机会驱动系统》实施计划的阶段 0 交付物：把当时仓库的真实模块映射固化下来。所有行号对应 2026-09-30 早期工作树。

## 0. 三层现状（同一「发现」问题的三个实现层）

「peer-discovery / 交流机会」并非单一模块，而是三层叠加：

| 层 | 目录 | 职责 | 状态 |
|---|---|---|---|
| interaction track | `src/finch/engagement/` | search → score → opportunity → proposals → guard → evidence → metrics | 活跃（`connect prepare` / `generate_proposals` 用） |
| people-first | `src/finch/peers/` | `PeerProfile`、`CreatorEvidence`、`score_person`、50 人分层推荐 | 活跃（每日主链路用） |
| connection judgment | `src/finch/connections/` + `src/finch/discovery/daily.py` | `ConnectionOpportunity`、`craft_reply`、`assess_connection` | **已实现但未接线（死代码）** |

实际每日链路：
```
sources sync → CreatorEvidenceService → score_person
  → select_daily_recommendations(50人分层)
  → opportunities_from_artifacts(浏览列表)
  → DiscoverySnapshot
```
每人的「连接判断」只有通过 `connect prepare`（走 `generate_proposals`）才可达；`ConnectionOpportunity` 路径无任何调用方。

---

## 1. 机会判断在哪层执行（模型 vs 代码）

### 1.1 判断产出点
- `Opportunity.why_relevant` / `opening`：由 **LLM** 在 `score_posts`（`src/finch/engagement/scoring.py:160`）内产出，schema 为 `ConversationScoreInput`（`scoring.py:35`）。确定性 fallback 在 `scored_post_to_opportunity`（`src/finch/engagement/opportunity.py:190`）与 `flow.py` 的后选择回填。
- `ConnectionOpportunity`（`their_problem` / `user_contribution` / `why_now` / `why_not` / `min_action` / `decision`）：独立 LLM prompt `prompts/connection-opportunity.md`，经 `assess_connection`（`src/finch/discovery/daily.py:292`）产出 schema `ConnectionOpportunityDraft`。**无调用方**；`run_daily_discovery` 初始化 `result.connections = []` 后从不填充。`build_connection_opportunity`（`src/finch/connections/service.py:86`）是确定性 fallback，`opportunity_id = conn_<uuid>`（`service.py:96`）**非确定性**。

### 1.2 各阶段分工表

| 阶段 | 文件 | 确定性（Python） | LLM（codex exec） |
|---|---|---|---|
| Search | `engagement/search.py` | 查询构建、排除、去重、预算切分 | 无 |
| Peer 聚合 | `engagement/peer_aggregation.py` | `aggregate_by_peer` | 无 |
| 关系评分 | `engagement/relationship.py` | `compute_peer_value`/`compute_relationship_value` 关键词启发式 | 无 |
| 帖子评分 | `engagement/scoring.py` | `weighted_total`、`prefilter_posts`、`rank_candidates` | `score_posts` → `ConversationScoreInput` |
| 机会构建/选择 | `engagement/opportunity.py` | `scored_post_to_opportunity`、`quality_gate`、`select_opportunity_set`、`assign_next_action`、`infer_evidence_status` | 无 |
| 提案 | `engagement/proposals.py` | `choose_action`、`generation_key_for`、`ready_gate_blocks` | `generate_proposals` → `ProposalItem` |
| 门禁 | `engagement/guard.py` | `evaluate_execution` 纯函数（无 I/O/LLM） | 无 |
| 证据提取 | `engagement/evidence_upgrade.py` | `promote_to_personal` | `extract_conversation_evidence` → `ExtractedEvidenceItem` |
| 指标 | `engagement/metrics.py` | 全部纯函数 | 无 |
| 人物证据 | `peers/evidence_service.py` | `evidence_id_for`、过滤 | `CreatorEvidenceService.assess` → `CreatorEvidenceItem` |

**结论**：机会判断的语义部分在 `score_posts`（`why_relevant`/`opening`），另有死代码 `ConnectionOpportunity`（`their_problem`/`user_contribution`/`decision`）；前者字段分散、后者从未接线。二者都没有「单一首选机会 + 双方参与理由 + 最小贡献」的统一归属。

---

## 2. 数据模型字段对照

### 2.1 `Opportunity`（`src/finch/engagement/models.py:124`）
`id, peer_id, source_refs, source_excerpt, content_fingerprint, discovered_via, topic_tags, role_tags, tags_inferred, why_relevant, opening, suggested_mode(learn|discuss|investigate), novelty_reason, uncertainty, shared_problem, contribution_basis_refs, next_action(reply|ask|try|repro|case|observe), estimated_minutes, evidence_status, assessed_at, assessment_version, score_total, complementarity, post, related_source_refs`
- 文档明确：**无审批状态机**（docstring `models.py:125`）；`InteractionProposal` 才是用户选中后的深度准备。

### 2.2 `InteractionProposal`（`models.py:70`）
`id(<platform>:<post_id>:<action>), post, score, action(ignore|bookmark|observe_author|draft_reply|draft_quote|draft_dm), draft, intent, source_summary, factual_risks, revised_draft, reject_reason, approval_required, status(proposed|approved|rejected|executed|expired), peer_id, contribution_type(experience|question|addition|counterexample|resource), relationship_context, why_this_person, why_now, expected_conversation_opening, generation_key, outline, value_added, contribution_basis_refs, revision, approval_revision`
- 这是**审批/执行状态机**所在。`revision` + `approval_revision` 支持编辑使旧批准失效。

### 2.3 `ConnectionOpportunity`（`src/finch/connections/service.py:20`，死代码）
`opportunity_id(conn_<uuid>), person_id, peer_id, their_problem, user_contribution, why_now, why_not, min_action, decision(connect|SKIP|defer), user_evidence_refs, their_artifact_ids`
- 字段形状最贴近新规范的 `why_me/why_continue/proposal/decision`；但非确定性 id + 未接线。

### 2.4 `ConversationEvidence`（`models.py:287`）
`id(ce_<interaction_id>_<i>), interaction_id, post_id, origin(恒"conversation"), kind(question|disagreement|hypothesis|experiment), statement, verified`
- 最接近的「入口分类」是 **4 类**，非规范的五类。

### 2.5 `ExternalPost`（`models.py:13`）
`id, platform, url, author_id, author_name, content, published_at, metrics, matched_topics`
- 缺 `retrieved_at`（抓取时间在 `RawArtifact.retrieved_at`，`sources/models.py:93`）。

### 2.6 评分模型
- `ConversationScore`（`models.py:27`）：`relevance, novelty, discussability, practical_evidence, relationship_value, total, reasons` —— `total` 由代码算。
- `ConversationScoreInput`（`scoring.py:35`）：LLM 输出 schema，**故意无 `total`、无 `relationship_value`**。
- `PeerValue`（`relationship.py:57`）六维、`PersonScoreBreakdown`（`peers/scoring.py:20`）六维，均含代码算出的 `total`。

---

## 3. 关键差距表（规范 §5-§14 vs 现状）

| 规范要求 | 现状 | 差距 |
|---|---|---|
| 单一首选机会（0-1 条，可空） | 首页 3 重点 + 50 人浏览（`DailyRecommendationSet`） | 无「首选 0-1」概念 |
| `why_me` / `why_continue` 可审阅判断 | `why_relevant`/`opening`（评分内）或死代码字段 | 字段分散无统一归属 |
| `entry_kind` 五分类 | `ConversationEvidence.kind`(4类) + `SuggestedMode` + 跨域标记 | 无单一 taxonomy |
| 机会生命周期状态机 | `Opportunity` 无状态；`InteractionProposal` 五态 | 无任务级「选定→可审阅→接续」 |
| 证据分层 explicit/inferred/unknown | `ClaimConfidence`(证据管线) / `EvidenceStatus`(engagement) | 三分类未统一到机会证据 |
| 聚合级写锁 + events.jsonl + 恢复 | `Workspace.atomic_write` 唯一机制；无锁/WAL；仅 3 对象有 revision | 缺并发/恢复保障 |
| 内容类型自适应检查 | 固定 8 检查器强制项目锚定 | 缺类型差异 |

---

## 4. `confirm` / `approve` / `record` 的实际含义（澄清误用）

三个「批准」概念必须保持分离：

| 命令 | 服务函数 | 行为 | 是否发布/署名 |
|---|---|---|---|
| `finch ideas confirm` | `IdeaService.confirm_position`（`ideas/service.py:165`） | `AuthorPosition` `PROPOSED→CONFIRMED`，不改内容 | 否；仅确认「作者立场值得写」 |
| `finch review approve` | `InboxDecisionService.accept`（`inbox/service.py:206`） | 写 `DecisionRecord(ACCEPT)` + `PublicationIntent`（`approved_content_hash`） | 否；仅记录**发布意图** |
| `finch connect approve` | `InteractionRepository.approve`（`repositories.py:284`） | `InteractionProposal` `PROPOSED→APPROVED` + `approval_revision` | 否；docstring「批准只创建发布意图，不等于已发布」 |
| `finch connect record` | cli `record`（`cli.py:3305`） | 写 `InteractionRecord(outcome="published")`，`verification_status` 留 `UNVERIFIED` | 用户自述事实，非平台验证 |
| `finch connections record` | cli `record`（`cli.py:3404`） | `verification_status=USER_ATTESTED` + 关系阶段升级 | 用户声明，仍非平台验证 |

**结论**：`confirm` 未误用于署名认领；代码强制 LLM 输出的 `USER_CONFIRMED` 降级（`sanitize_model_confidence`，`evidence/models.py:35`）。唯一问题：`drafts create` 硬性要求 `ContentJob.status==CONFIRMED`（`drafts/service.py:155`），使「直接写作请求」无法走短路（规范 §4.2/§4.3 要求修复）。

---

## 5. 写作检查器现状

### 5.1 检查器清单与数量不一致
- `default_checker_suite`（`src/finch/content/critic.py:90`）返回 **8** 个：`EvidenceChecker, DecisionChecker, SpecificityChecker, PortabilityChecker, VoiceChecker, StructureChecker, SafetyChecker, ResponsivenessChecker`（docstring 却写「7 个」）。
- `idea_checker_suite`（`src/finch/idea/service.py:49`）去掉 `EvidenceChecker`，剩 **7** 个；但 `idea-to-draft/SKILL.md` 与 `drafts/service.py` 文档称「6 检查器」。

### 5.2 `PortabilityChecker`（`src/finch/content/checkers/portability.py`）
- 反事实测试：每句「能否原样套用到任何项目」；`overgeneralized`/`boilerplate`/`disclaimer` 三分类（`portability.py:20-24`）。
- 规则强制每句锚定「具体项目细节」（命名系统、数字、决策、取舍、产物、代码路径）——**这正是规范 §1.1「通用观点被规则要求绑定某个项目」的根源**。
- 同时惩罚 meta 免责声明（`_fix_instruction`，`portability.py:62-63`「remove the meta-disclaimer」）。「反复强调边界」的观感来自 writer 被要求 conditionalize（`overgeneralized → conditionalize，不追加免责声明`）而非追加免责声明。

### 5.3 聚合与硬失败
- 聚合（`checkers/aggregate.py:24`）确定性：任一 `hard_fail` 未过 → `reject`；任一 `high` 带 `requires_human_input` 未过 → `needs_input`；其余未过 → `rewrite`；否则 `pass`。LLM 的 `passed` 不被信任。
- 确定性 hard-fail：`SafetyChecker`（secret 扫描 + unsourced-outcome 正则）、`EvidenceChecker`（`validate_draft`）。

### 5.4 措辞修订上限
- `quality_gates.max_rewrite_rounds` 当前默认 **1**（`settings.py:89`）；规范 §7.3 建议「最多两轮」。`_critic_loop`（`drafts/service.py:217`）超轮后保留末版返回。

---

## 6. 可复用的纯正文输出契约（沿用，不新建）

- `DraftBodyOutput`（`src/finch/content/models.py:57`）：仅 `body: str`，由 `write_original_from_job`（`writer.py:116`）使用。
- `RewriteIdeaOutput`（`src/finch/idea/models.py:6`）：仅 `body: str`。
- 二者保证 idea 草稿 body-only（`claims` 恒为空）。

---

## 7. 存储与并发现状

### 7.1 `Workspace`（`src/finch/storage/workspace.py`）
- `atomic_write`（`workspace.py:41`）：同目录临时文件 + `os.replace`，**唯一**一致性机制。**无文件锁、无 revision/WAL/事件日志**（模块 docstring 明示）。
- `append_jsonl`（`workspace.py:88`）：读-重写-原子替换；reader 跳过空白/损坏尾行。

### 7.2 revision 字段分布（仅 3 个对象有乐观并发）
`ConversationThread.revision`、`DialogueNote.revision`、`InteractionProposal.revision`(+`approval_revision`)。
`ContentJob`、`PeerProfile`、`Opportunity`、`InteractionRecord`、`DecisionRecord` **均无**。

### 7.3 幂等（大量 sha256 稳定 id）
`generation_key`（ContentJob / InteractionProposal）、`content_fingerprint`、`idea_<sha>`、`rec_<sha>`、`thread_<sha>`、`note_<sha>`、`cmt_<sha>`、`dec_<job_id>`、`intent_<source_id>`、`rfb_<sha>`、`pp_<sha>`、`opp_<sha>`。`InteractionRecord` 按 `platform_message_id`/`source_url` 去重（`repositories.py:509`）。

### 7.4 磁盘布局（`var/`）
```
var/
  artifacts/            github_commit_*.yaml
  cache/                extraction_cache.json
  communities/          cards/, profile.yaml, candidates.jsonl, feedback.jsonl, runs.jsonl, steps.jsonl
  conversations/        (threads)
  creator_evidence/
  decisions/            dec_<job_id>.yaml
  dialogue/
  drafts/               draft_<id>/draft.md + critic.jsonl
  evidence/
  explorations/         expl_<id>.yaml
  ideas/                idea_<8hex>.yaml
  interactions/         opportunities/opp_*.yaml, proposals/, discovery-snapshots/, presentations/, snapshots/, records/, evidence/, recommendation-feedback/, run-stats/
  outputs/
  peers/                peer_<12hex>.yaml
  people/               person_<12hex>.yaml + presentations/pp_*.yaml
  projections/          daily-context.json, pending-actions.json
  raw/                  github/ reddit/ twitter/ v2ex/ weixin/ xiaohongshu/
  sources/              artifact_index.jsonl, cursors/, runs/run_<hex>.yaml
```

---

## 8. CLI 表面（命令族）

`src/finch/cli.py`，单一 typer 根 app，18 个子 app。

- `connect`：`refresh` `today` `daily` `person` `record-presented` `more` `expand` `prepare` `feedback` `create` `with` `approve` `reject` `edit` `record`
- `ideas`：`commit` `create` `signals` `choose` `list` `show` `confirm` `revise-position` `skip`
- `drafts`：`create` `show` `revise`
- `review`：`list` `show` `approve` `revise` `skip` `weekly`
- `conversations`：`list` `show` `get` `follow-up` `ingest` `note` `commit` `experiment` `mark-important` `defer` `close`
- `community`：`context` `save` `inspect` `feedback` `list` `run` `runs` `run-trace`
- `peers`：`list` `show` `get`；`people shortlist`；`connections today/record`
- 其他：`github reflect`、`twitter search/import-bookmarks/diagnose`、`sources doctor/sync`、`voice *`、`inspirations *`、`dialogue *`、`practice *`、`style analyze`、`collisions *`、`experiments *`、`init`、`diagnose`、`context`、`learn`、`weekly`

---

## 9. 其他核实结论

- `IdeaService.mark_drafted`（`CONFIRMED→DRAFTED`）是死代码，从未调用 —— `ContentJob` 永远停在 `CONFIRMED`。
- 语音画像 ≥3 样本规则只写在 `skills/voice-profile/extraction-rules.md`，代码未强制；`propose_voice_updates`（`content/voice.py:86`）对单样本 diff 即提取规则，且 `preferred_patterns/avoid_phrases/rhythm_rules` 无 CLI 写入路径（仅手工改 YAML）。
- `review approve` 绑定 `approved_content_hash`，后续 `revise` 使旧批准失效（`inbox/models.py:48`）。

---

## 10. 结论：字段落在哪个对象

规范 §10.1 的「机会」需承载 `why_me/why_continue/entry_kind/proposal/open_questions/status/decision`，现有三个对象都只能部分承载：

- 现有 `engagement.Opportunity`：有 `why_relevant`/`opening`/`shared_problem`/`next_action`，但无状态机、无 `entry_kind`、无 `decision`。
- 死代码 `ConnectionOpportunity`：字段形状最贴近（`their_problem`/`user_contribution`/`why_now`/`min_action`/`decision`），但非确定性 id、未接线。
- `InteractionProposal`：审批状态机所在，但绑定单一帖子/动作，不承载「机会」全生命周期。

因此（已由用户确认）：**忽略现有，重新实现** —— 在 `src/finch/opportunities/` 新建带生命周期状态机的 `Opportunity` 聚合，替换上述三者（保留 `InteractionRecord`/`ConversationThread`/`ContentJob`/`VoiceProfile` 等关系与表达对象）。详见实施计划阶段 3。
