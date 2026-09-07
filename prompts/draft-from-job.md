你按 Content job context 里指定的 scope 写一篇中文草稿。按 schema 返回 JSON。
不要读取文件、运行命令或使用任何工具，只依据下方数据作答。
Instructions:
- 只依据 Content job context 里的读者问题、作者立场与核心主张写。
- 不把推断写成第一人称亲历事实；不编造事实、数字或来源。
- 立场（claim/decision/tradeoff）原样表达，不得改写作者已确认的立场。
- claims 保持为空列表（idea 草稿不绑定证据卡）。
- 严格按 scope 选择最小结构：
  - general：给出可迁移判断，必须同时有直接工程证据和真实讨论上下文。
  - bounded_lesson：明确限定“在这次实现/这个规模下”，不做行业普遍化。
  - build_log：只说明做了什么、为什么、结果和未知项。
  - reply：接住对方观点，并提供一项明确新增价值。
- 篇幅短：一条增量讲清楚。

{job_context}
