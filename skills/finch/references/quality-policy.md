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

### 置信度语义（`ClaimConfidence`，计划 Task 1.2）

| Confidence | 对外表达规则 |
|---|---|
| VERIFIED | 可作为事实断言，必须有直接来源 |
| SUPPORTED | 可作为有范围的事实陈述 |
| USER_CONFIRMED | 仅人工确认后产生 |
| INFERRED | 必须使用"这表明/在这次实现中/可能"等边界语言 |
| UNKNOWN | 不得作为可发布主张 |

`assertable` = VERIFIED / SUPPORTED / USER_CONFIRMED；INFERRED 须先改写为带边界语言
的陈述，UNKNOWN 不得作为可发布主张。

禁止 LLM 自行产出 `USER_CONFIRMED`：加载模型输出后由 Python 强制降级
（`evidence.models.sanitize_model_confidence`，模型产出的 `USER_CONFIRMED` → `SUPPORTED`），
只有人工确认路径才能写入 `USER_CONFIRMED`。


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
