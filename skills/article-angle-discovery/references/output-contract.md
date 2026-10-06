# 输出契约

AngleBrief：id / source_type / source_ref / content_hash / coverage（确定性，代码填）
+ source_summary + angles（shortlist，默认 3）+ recommended_index + recommendation_reason
+ outline（推荐方向提纲）+ evidence_gaps（推荐方向需补充证据）+ smallest_validation_action。

SourceSummary：main_point（一句话主旨）/ key_claims / author_advice / scope（作者明说限定）
/ gaps（原文未回答的问题/主要缺口，选题由缺口触发）。

AngleCard（选题卡）：title / main_angles（引用角度库名称）/ target_reader / thesis（中心主张）
/ incremental_value（相对原文增量）/ increment_basis / opening_scene / evidence_gaps
/ reader_action / writing_status。

`increment_basis` 取值（对应 research.distinguish）：
`source_claim`（重申原文主张）/ `verified_fact`（可核实事实）/ `inference`（推理外推，未验证）
/ `hypothetical_example`（假设场景）。缺省按 `inference`（保守，不越界声称）。

禁止 total / 数值评分字段。默认写入 Workspace `angle_briefs`；`--no-save` 跳过落库。
`recommended_index` 指向 `angles`（0-based），推荐方向的深挖字段（outline / evidence_gaps /
smallest_validation_action）在顶层。原文是外部证据，永远不得写成作者亲历；实践记录是材料不是证明。
