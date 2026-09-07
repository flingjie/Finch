# 质量门禁（Quality Policy）

## Critic 门禁

- 草稿须过 Critic（`finch critique`），未达标不进审核；门禁数值来自 `finch.yaml` 的 `quality_gates`，不要硬编码改动。
- 关键门禁：`min_quality_score`（0.75）、`min_evidence_score`（0.75）、`min_candidate_score`（0.65）、`min_discussability`（0.50）。
- Critic 语义审查 fail-closed：`invented_personal_experience`、`unsupported_metric`、蕴含检查——不确定即不通过。

## 有限 rewrite

- 单稿重写上限 `max_rewrite_rounds`（2）；超过即停，不得无限重写。
- revise 只改表达，不改已确认立场；用户明确指示才重写。

## 不自动发布

- 候选草稿在用户「采用」前绝不视为已发布或已确认立场。
- 发布只能由用户在 Finch 外部手动完成；`gh`/`opencli` 适配器只读，写命令在拒绝名单内。

## 分数由代码算

- 加权/总分只由代码计算（`weighted_total` 是唯一出处）；LLM 输出不携带 `total`。
- Evidence/Safety 是 hard-fail，命中即停，不被平均分掩盖。

## 幂等（generation key）

- idea 用 `idea_generation_key(skill, version, canonical_source_refs, input_fingerprint)`；draft 用 `draft_generation_key(idea_fingerprint, idea_to_draft_version, format, voice_profile_version)`。
- 同 key 重复创建返回已存在对象，不重复落库、不重复生成。
