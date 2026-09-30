---
name: peer-discovery
description: >
  从公开讨论（Twitter/X、GitHub、V2EX、公众号、小红书）中发现值得长期交流的跨行业实践者，
  给出一条首选交流机会（对象与话题、双方参与理由、最小贡献、可见结果、成本与未知）+
  可按请求展开的 50 人分层浏览 + PeerProfile 候选。无 question 也工作；指定对象与问题
  探索复用同一发现管线。判断一个人是否值得了解——是否有持续创造、一手经验或可核实的实践，
  有无值得了解/交流的具体理由；新闻 / 融资 / 纯推广 / 仅 bio 转发的内容降级为待了解。
  用于「今天有什么值得交流的」「这个作品背后的人」「其他行业怎么处理 X」类请求。
---

# peer-discovery

从公开讨论中发现值得交流的机会。职责：在预算内刷新或读取快照，产出 **一条首选交流机会** +
**可按请求展开的 50 人分层浏览**（同一快照投影），真实回复/承诺独立呈现、不占发现名额。
首选可为空：没有值得优先投入的讨论就明说原因，不硬凑、不编造困难。覆盖有实践证据的
跨行业实践者（含闭源/业务场景自述，须标注）；候选不足如实显示覆盖缺口，不用低质量结果凑数。
深度准备默认最多 5 位（deep_prepare_limit），由用户选中后交给 `interaction-preparation` /
`finch connect prepare --opportunity`。自由发现不要求先提出问题；未知用途的可信发现可保留为意外发现。

本 Skill 只调用 Finch CLI，不复制业务逻辑。关系粗筛与机会选择的确定性总分由 Python 计算，
LLM 不输出最终 `total`。不因商业线索挤掉所有普通同行。

## CLI

- 首页：`finch connect daily`（默认读最新快照，展示 3 个重点 + 真实跟进；不因过期隐式抓取）
- 刷新：`finch connect daily --refresh`（有界刷新后展示）
- 浏览：`finch connect daily --view browse`（同一快照的 50 人分层列表）
- 问题探索：`finch connect daily --question "其他行业如何处理责任交接？" --refresh`
- 查看指定对象：`finch connect person <person_id>`（只读）
- 查看某人物完整证据：`finch connect person <person_id>`（只读，不生成互动准备）
- 准备互动：`finch connect prepare --opportunity <id>`（可重复；每次最多 5；**必须选中**）
- 指定帖子：`finch connect assess --url <url> [--question …]`（入口 2）
- 保存启发：`finch inspirations save --text "…" [--source <ref>]`
- 反馈：`finch connect feedback --file feedback.json`
- 回应接续：`finch connections follow-up --opportunity <id> --reply-body "…"`

## 产出契约

- `Opportunity`（新聚合）：`why_me` / `why_continue` / `entry_kind` / `proposal`
  （含 `cost_note`）/ `open_questions` / `evidence_refs` / 生命周期状态
- `PeerProfile`：身份、主题重叠、可选 `current_work` / 实践证据引用 / `evidence_status`

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。
完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束），延伸不得抢占交付物。

- **首选机会**：默认先给一条（0–1 条，可为空），用两三段自然语言回答六问——
  ① 对象与话题（具体的人/线程 + 可访问来源）② 用户为何在意（指向当前问题或好奇，含依据）
  ③ 对方为何可能接话（可补充空间，不保证回复）④ 最小贡献（明确产物与边界）
  ⑤ 可见结果（完成后能展示/比较/检验的东西）⑥ 成本与未知（粗略投入 + 影响判断的未知）。
  以「先做这个切口吗？」收尾，接受自然语言接受/修改/拒绝；不暴露内部命令与状态枚举。
- **浏览列表**（`--view browse`，按请求提供）：50 人分层，每张固定五要素：
  1. **正在做什么**（附实践来源）
  2. **为何值得了解**（对应问题或探索方向；自由发现可写「新视角在于…，用途未知」）
  3. **具体观察或可补充的经验**（不强制；诚实提问即可，声称亲历才需个人证据）
  4. **建议下一步**（reply / ask / try / repro / case / observe）
  5. **时间与不确定性**（预计分钟、缺上下文或身份待核）
- **不要**为整表生成完整回复；仅选中后最多准备 **5** 条（默认建议 1–3 位深入）。
- 内部保留序号 → `opportunity_id` / `snapshot_id`，换一批后不能选错人。

## 边界

- 用户点名具体人（handle / 主页）时，这不是发现请求 → `interaction-preparation` 的 `connect prepare`。不要用今日机会名单顶替。
- 用户给出具体帖子/链接「我能补充什么」→ `finch connect assess --url`，不是全平台刷新。
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
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束
