# 输出契约

ArticleReport：id / source_type / source_ref / content_hash（确定性，代码填）
+ expression_task / audience_change / techniques / effectiveness
+ transferable_methods（2–3）/ clarity_cost_reductions（0–3，可空；ASD-STE100-inspired，
  含 excerpt / method / reader_effect / mini_exercise / 可选 rule_id CL*）/ limitations
+ **style**（嵌套 StyleBlock，默认空列表；非 Optional）。

StyleBlock：scope / overall_confidence + 七维 StyleEvidence 列表（opening、structure、
rhythm、word_choice、stance、concreteness、reader_relationship）+ signature_patterns /
transferable_techniques / potential_weaknesses / experiments_for_me。

禁止 total / 数值评分字段。默认写入 Workspace `article_reports`；`--no-save` 跳过落库。
表达方法库是独立的 `expression_methods` 集合，仅当用户通过 `finch methods save` 选中某条 `transferable_methods` 后才写入；与 VoiceProfile / PracticeProfile 无关。
