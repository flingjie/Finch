# 发布安全（Publication Safety）

## 不自动发布

- 候选草稿在用户「采用」前绝不视为已发布或已确认立场。
- 发布只能由用户在 Finch 外部手动完成；`gh`/`opencli` 适配器只读，写命令在拒绝名单。

## 分数由代码算

- 加权/总分只由代码计算（`weighted_total` 是唯一出处）；LLM 输出不携带 `total`。
- Evidence/Safety 是 hard-fail，命中即停，不被平均分掩盖。

## 证据优先

- 对外主张必须能回溯到证据；没有 Evidence Card 不生成内容。
- 推断（inferred）显式标注；unknown 不写；禁止把推断写成第一人称亲历。
