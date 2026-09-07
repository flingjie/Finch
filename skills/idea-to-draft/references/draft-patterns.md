# 草稿写法（draft patterns）

idea-to-draft 只依据 Content Job 语境写正文。下面是把「已确认立场」安全地扩写成一篇中文原创草稿的判据。

## 立场原样表达，不改变

- `author_position.claim / decision / tradeoff` 已由人工确认，草稿必须**逐条表达**，不得替换、软化为近义表述、或夹带相反立场。
- 正文只做「把立场讲清楚」的扩写：给出读者问题 → 立场（decision）→ 取舍（tradeoff）→ 一个具体例子或增量。
- 要推翻立场须重新走确认流程（`finch ideas` 状态机），绝不在草稿层改写。
- **scoping-only 是允许的**：把绝对结论改写为条件结论、缩小适用范围（如"稳定后仍需代码化"
  → "稳定后，再把需要确定性/幂等保障的部分代码化"）不算软化或改变立场；禁止的是推翻
  decision/tradeoff 的方向或夹带相反立场。

## 边界：known / inferred / unknown

- `boundaries.known` 内可作事实断言；`inferred` 必须带边界语言（「看起来 / 可能 / 据我们所测 / 在这次实现下」）；`unknown` 不写。
- 禁止把 `inferred` / `unknown` 写成第一人称亲历（「我踩过这个坑」必须有对应证据）。
- 没有依据就不写；宁可不写某个细节，也不补造数字、指标、经历或来源。

## 口吻与篇幅

- 原创日记（original）：第一人称、实践者口吻，记录「我做了什么、为什么、结果如何、学到什么」。
- 先讲事实（可追溯的结论），再讲增量，最后可有可无地抛一个具体问题。
- 不夸大、不吹嘘、不写「this is amazing」之类空洞表态。
- 篇幅短：一条增量讲清楚。

## scope 限定表达边界

- `general`：给出可迁移判断，必须同时有直接工程证据和真实讨论上下文。
- `bounded_lesson`：明确限定「在这次实现/这个规模下」，不做行业普遍化。
- `build_log`：只说明做了什么、为什么、结果和未知项。
- `reply`：接住对方观点，并提供一项明确新增价值。

## 硬失败（fail-closed）

- Safety 门禁是硬门禁：命中 `secret_detected` / `invented_personal_experience` / `unsupported_metric` → 草稿丢弃（`needs_input`），不被平均分掩盖。
- 证据门禁（Evidence）在本 Skill 不启用（idea 草稿无证据卡）；若未来引入证据卡，`hard_fail` 同样独立于平均分、fail-closed。
- 聚合去向由代码算（`aggregate_checks`），绝不信任模型输出的「通过」。
