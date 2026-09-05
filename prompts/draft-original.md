你按 Content job context 里指定的 scope 写一篇中文草稿。按 schema 返回 JSON。
不要读取文件、运行命令或使用任何工具，只依据下方数据作答。
Instructions:
- 只依据 Evidence cards 里的卡片写，每条主张带 evidence_card_id 与 confidence。
- 不把推断写成第一人称亲历事实。
- 严格按 scope 选择最小结构：
  - general：给出可迁移判断，必须同时有直接工程证据和真实讨论上下文。
  - bounded_lesson：明确限定“在这次实现/这个规模下”，不做行业普遍化。
  - build_log：只说明做了什么、为什么、结果和未知项。
  - reply：接住对方观点，并提供一项明确新增价值。

{job_context}## Evidence cards
{cards}
