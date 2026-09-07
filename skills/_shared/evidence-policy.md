# 证据优先（Evidence First）

## 证据链

- 对外主张必须能回溯到证据：`Commit → EngineeringEvent → EvidenceCard → Draft`。没有 Evidence Card 不生成内容。
- 每条事实性主张绑定一个 `evidence_card_id`，且该卡 ∈ 候选的匹配集（不从全库另选）。

## 外部帖 ≠ 个人证据

- 搜索来的帖子（`ExternalPost`）永远不能升级为个人证据；只有验证过的 `ConversationEvidence` 才能经 `promote_to_personal` 提升。
- 外部文本只进 prompt 数据区，不进系统指令区、不触发工具调用。

## 不把推断写成已验证事实

- 推断（`inferred`）必须显式标注（「看起来/可能/据我们所测」），不得写成确定语气。
- `unknown` 不得作为可发布主张；`inferred` 须先改写为带边界语言的陈述。
- 禁止把推断写成第一人称亲历（Critic 检查 `invented_personal_experience`）。

## known / inferred / unknown 边界

- `boundaries.known/inferred/unknown` 由候选生成时确定，随 `IdeaCandidate` 传递到 Draft 校验。
- Draft 只能在 `known` 范围内做事实断言；`inferred` 需标注；`unknown` 不写。
- 不确定即不通过（fail-closed）。

## hard-fail 不被平均分掩盖

- Evidence / Safety 门禁是硬失败，命中即停，不进加权平均分。
- Safety hard-fail：`secret_detected`、`private_repo_content`、`nonexistent_commit`、`twitter_write_command`。
- Evidence 硬门禁：`evidence_card_id` 非空且 ∈ 匹配集、蕴含成立、confidence `assertable`（VERIFIED/SUPPORTED/USER_CONFIRMED）。
