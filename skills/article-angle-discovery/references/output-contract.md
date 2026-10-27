# 输出契约

AngleBrief：id / source_type / source_ref / content_hash / coverage（确定性，代码填）
+ source_summary + angles（shortlist，1–3 个，可为空）+ recommended_index（0-based，可空；空 angles 时为 null）
+ recommendation_reason + outline（推荐方向提纲）+ evidence_gaps（推荐方向需补充证据）+ smallest_validation_action
+ comparison（5 个维度定性比较，非数值）。

SourceSummary：main_point（一句话主旨）/ key_claims / author_advice / scope（作者明说限定）
/ gaps（原文未回答的问题/主要缺口，选题由缺口触发）。

AngleCard（选题卡）：title / main_angles（引用角度库名称）/ target_reader / thesis（中心主张）
/ combination_materials（组合材料，各带来源）/ connection_rationale（连接理由）/ incremental_value（相对原文增量）
/ increment_basis / opening_scene / evidence_gaps / reader_action / writing_status
/ reader_journey（可选，本角度的读者认知变化）/ applicable_form / main_risk。

`ReaderJourney`：initial_belief（读者原有认知，假设须标注）/ reader_problem / cognitive_shift（希望读者重新理解什么）
/ emotional_path（如 好奇/怀疑/理解）/ turning_point（触发变化的事实或经历）/ evidence_refs（真实来源，不得编造）
/ evidence_gaps（转折依据仍缺的证据）/ desired_afterthought（读后留下的问题或行动）。
读者心理是假设，不得冒充调查事实；`turning_point` 必须有 `evidence_refs` 或明确标为待验证。

`ComparisonDimension`：dimension（读者相关性 / 认知新意 / 证据充分度 / 可实践性 / 与作者风格契合度）
/ comparison（一句定性比较，非数值、非总分）。

`CombinationMaterial`：role（`原文观点` / `第二份材料`）/ content（材料内容）/ source（来源：原文 / 实践记录 /
失败记录 / 其它文章 / 已有方法 / 领域通识 / 读者问题）。第二份材料不限于实践记录；每条材料必须标来源。
`connection_rationale`：为什么两份材料能共同解释这个问题（第二份材料补上什么缺口、读者改变什么判断）。

`increment_basis` 取值（对应 research.distinguish）：
`source_claim`（重申原文主张）/ `verified_fact`（可核实事实）/ `inference`（推理外推，未验证）
/ `hypothetical_example`（假设场景）。缺省按 `inference`（保守，不越界声称）。

禁止 total / 数值评分字段。默认写入 Workspace `angle_briefs`；`--no-save` 跳过落库。
`recommended_index` 指向 `angles`（0-based）；空 `angles` 时为 `null`。推荐方向的深挖字段
（outline / evidence_gaps / smallest_validation_action）在顶层。原文是外部证据，永远不得写成作者亲历；
实践记录是材料不是证明；不得把类比当证据。
