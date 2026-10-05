# 输出契约

ContentSummary：id / source_type / source_ref / content_hash（确定性，代码填）
+ main_point（一句话主旨）/ key_points（核心要点）/ evidence（关键依据或例子）
/ conditions（条件与限制）。

EvidencePoint：source + content。`source` 取值：`作者`（作者本人观点/例子）、
`引用`（作者引用的外部内容）、`回复`（线程中的回复/评论）、`未标明`（来源不明）。

禁止 total / 数值评分字段。默认写入 Workspace `content_summaries`；`--no-save` 跳过落库。
只做内容摘要，不产写法点评（那是 ArticleReport 的职责，两者共享 SourceResolver）。
