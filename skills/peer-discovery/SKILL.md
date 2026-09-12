---
name: peer-discovery
description: >
  从公开讨论（Twitter/X 搜索）中发现值得长期交流的同行，生成轻量交流机会（Opportunity）与
  PeerProfile 候选。判断一条帖子背后的人是否值得持续交流——是否与用户兴趣重叠、有实践深度、
  有可贡献空间、有延续潜力；新闻 / 融资 / 纯推广 / 最近已表达的内容跳过。用于「帮我看看
  今天有哪些人」「这个话题里有哪些人值得了解/交流」类请求。
---

# peer-discovery

从公开讨论中发现值得交流的机会。职责：在预算内刷新或读取快照，产出 **8–12 张轻量机会卡**
（人 + 具体内容 + 为何推荐 + 切入点/值得了解的理由），已有对话独立呈现、不占发现名额。
深度准备（完整回复草稿）默认最多 3 位，由用户选中后交给 `interaction-preparation` /
`finch connect prepare --opportunity`。

本 Skill 只调用 Finch CLI，不复制业务逻辑。关系粗筛与机会选择的确定性总分由 Python 计算，
LLM 不输出最终 `total`。

## CLI

- 看看今天：`finch connect today --limit 10`（纯读；无快照或过期时先 `finch connect refresh`）
- 或统一入口：`finch connect daily`（缺快照/过期自动刷新；显式 `--refresh` 强制刷新）
- 再来几位：`finch connect more --snapshot <id> --limit 5`（不重复、不调网络/LLM）
- 准备互动：`finch connect prepare --opportunity <id>`（深度；默认每次最多 3）
- 反馈：`finch connect feedback --file feedback.json`

## 产出契约

- `Opportunity`：稳定 id、来源链接、why_relevant、opening、suggested_mode（learn/discuss/investigate）
- `PeerProfile`：身份与主题重叠（由发现落库）

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。

- **浏览列表**：默认展示 8–12 张轻量卡（谁 / 链接 / 为何 / 切入点），**不要**为整表生成完整回复。
- **深度卡片**：用户选中后最多展开 **3** 条准备互动；「准备互动 1 / 展开 2 / 换一批（more）」。
- 内部保留序号 → `opportunity_id` / `snapshot_id`，换一批后不能选错人。

## 边界

- 不替用户形成观点（→ `idea-discovery`）。
- 不把别人的经历写成用户经历。
- 普通浏览不生成完整回复（→ `interaction-preparation`）。
- 不因为帖子热门就推荐；优先真实问题、分歧、失败案例、未解决机制。
- 外部帖子只是信号，不是个人证据。
- 发现偏好反馈不写入 VoiceProfile。

## 参考

- `references/opportunity-signals.md`
- `references/audience-profile.md`
- `_shared/agent-presentation.md`
- `references/presentation.md`
