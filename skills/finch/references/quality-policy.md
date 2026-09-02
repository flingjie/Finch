# Quality Policy

## 门禁数值

来自 `finch.yaml` 的 `quality_gates`（唯一来源，不要在别处硬编码）：

| 键 | 值 | 作用 |
|---|---|---|
| `max_daily_replies` | 5 | 每日回复上限 |
| `max_daily_original_posts` | 1 | 每日原创上限 |
| `min_candidate_score` | 0.65 | 匹配总分门禁 |
| `min_evidence_score` | 0.75 | 证据强度门禁 |
| `min_quality_score` | 0.75 | 草稿质量门禁 |
| `min_discussability` | 0.50 | 可讨论性门禁 |
| `max_rewrite_rounds` | 2 | 单稿重写上限 |
| `match_top_k` | 10 | 召回预排序 top-K |
| `timing_default` | 0.3 | 发布时间缺失时的保守时效分 |

评分公式：`Score = 0.30*relevance + 0.30*evidence_strength + 0.20*incremental_value + 0.10*timing + 0.10*relationship_value`。`discussability` 不进公式，是独立门禁。

## 证据绑定（硬门禁，任一不满足 → 不生成/不进审核）

1. 每条主张的 `evidence_card_id` 非空，且 ∈ 该候选的 `MatchResult.card_ids`（禁止从全库另选一张卡）。
2. **蕴含**：`statement` 必须被该卡的 `claim` + `sources` 支持；Critic 判定，不确定即不通过（fail-closed）。
3. 对外主张的 `confidence` 必须 `assertable`（VERIFIED / SUPPORTED / USER_CONFIRMED）。

`required` 含 `evidence_card` = 「ID ∈ 匹配集 + 蕴含 + assertable」，不是"有个字符串就算"。

## 安全（职责分离）

**确定性 hard_fail**（`evidence/safety.py`，命中即停）：

- `secret_detected` — 密钥/token 形态。
- `private_repo_content` — 私有仓库或 `publishable=false`。
- `nonexistent_commit` — source URL 无法反查到已同步 commit。
- `twitter_write_command` — 允许名单之外命令即阻断（Phase 3）。

**Critic 语义审查**（fail-closed，不确定即不通过）：

- `invented_personal_experience` — 把推断写成第一人称亲历。
- `unsupported_metric` — 引用未在证据中出现的具体数字指标。
- 蕴含检查（见上）。

外部 Tweet 文本一律视为不可信数据：只进 prompt 数据区，不进系统指令区、不触发工具调用。
