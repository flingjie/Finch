---
name: peer-discovery
description: >
  从公开讨论（Twitter/X 搜索）中发现值得长期交流的同行与有实践证据的 builder，生成轻量
  交流机会（Opportunity）与 PeerProfile 候选。判断一条帖子背后的人是否值得持续交流——
  是否与用户问题/兴趣重叠、有实践深度、有可贡献空间、有延续潜力；新闻 / 融资 / 纯推广 /
  最近已表达的内容跳过。用于「帮我看看今天有哪些人」「这个话题里有哪些人值得了解/交流」
  类请求。
---

# peer-discovery

从公开讨论中发现值得交流的机会。职责：在预算内刷新或读取快照，产出 **8–12 张轻量机会卡**，
已有对话独立呈现、不占发现名额。覆盖有实践证据的 builder（含闭源/业务场景自述，须标注）；
候选不足如实显示覆盖缺口，不用低质量结果凑数。深度准备默认最多 10 位，由用户选中后交给
`interaction-preparation` / `finch connect prepare --opportunity`。

本 Skill 只调用 Finch CLI，不复制业务逻辑。关系粗筛与机会选择的确定性总分由 Python 计算，
LLM 不输出最终 `total`。不因商业线索挤掉所有普通同行。

## CLI

- 看看今天：`finch connect today --limit 10`（纯读；无快照或过期时先 `finch connect refresh`）
- 或统一入口：`finch connect daily`（缺快照/过期自动刷新；显式 `--refresh` 强制刷新；
  输出每日 50 人分层推荐：5 今日重点 / 15 值得浏览 / 30 扩展发现 + 关系跟进）
- 查看某人物完整证据：`finch connect person <person_id>`（只读，不生成互动准备）
- 再来几位：`finch connect more --snapshot <id> --limit 5`（不重复、不调网络/LLM）
- 有界扩展：`finch connect expand --from <opportunity_id>` 或 `--scope TEXT`
- 准备互动：`finch connect prepare --opportunity <id>`（可重复；每次最多 10；**必须选中**）
- 反馈：`finch connect feedback --file feedback.json`

## 产出契约

- `Opportunity`：稳定 id、来源链接、`why_relevant`、`opening`、`suggested_mode`
  （learn/discuss/investigate）、`shared_problem`、`contribution_basis_refs`、`next_action`、
  `estimated_minutes`、`uncertainty`
- `PeerProfile`：身份、主题重叠、可选 `current_work` / 实践证据引用 / `evidence_status`

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。

- **浏览列表**：默认展示 8–12 张轻量卡，每张固定五要素：
  1. **正在做什么**（附实践来源）
  2. **为何与你有关**（对应问题或探索方向）
  3. **你可以贡献什么**（关联个人实践，或明确「需先准备」）
  4. **建议下一步**（reply / ask / try / repro / case / observe）
  5. **时间与不确定性**（预计分钟、缺上下文或身份待核）
- **不要**为整表生成完整回复；仅选中后最多准备 **3** 条。
- 内部保留序号 → `opportunity_id` / `snapshot_id`，换一批后不能选错人。

## 边界

- 用户点名具体人（handle / 主页）时，这不是发现请求 → `interaction-preparation` 的 `connect with`。不要用今日机会名单顶替。
- 不替用户形成观点（→ `idea-discovery`）。
- 不把别人的经历写成用户经历；仅有 bio/转发时标记待了解，不占核心证据位。
- 普通浏览不生成完整回复（→ `interaction-preparation`）。
- 不因为帖子热门就推荐；优先真实问题、分歧、失败案例、未解决机制。
- 外部帖子只是信号，不是个人证据。
- 发现偏好反馈不写入 VoiceProfile。
- 跨平台同名不足以合并身份。

## 参考

- `references/opportunity-signals.md`
- `references/audience-profile.md`
- `_shared/agent-presentation.md`
- `references/presentation.md`
