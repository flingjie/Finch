---
name: article-angle-discovery
description: >
  读一篇热门文章，结合读者问题与作者自己的实践，找出几个值得独立成文的角度（选题卡 / 写作 brief），
  并说清每个角度的相对原文增量、目标读者、中心主张、证据缺口与最小验证行动。用于「这篇文章我可以
  从哪里继续写」「帮我找选题」「这篇文章之后我还能写什么」类请求。只产出选角报告，不重述原文
  （→ content-summary）、不点评写法（→ article_analysis）、不生成草稿（→ idea-to-draft / finch drafts）。
  证据不足的新颖观点标为「需先验证」，不因独特而排第一。优先寻找有依据的组合：从原文取一个观点，
  与另一份有依据的材料连接，产生读者用得上的新判断。
---

# article-angle-discovery

读一篇热门文章，找出几个「相对原文有增量、值得独立成文」的角度，并说明为什么值得写、还缺什么材料。
产出是「选题卡 / 写作 brief」，不是把原文换标题重述，也不是按学科各谈一下的伪扩展。

## 执行

`finch angles discover --text/--file/--url [--reader] [--reader-problem] [--author-context]
[--practice-ref ...] [--platform] [--goal] [--prefer-angle ...] [--exclude-angle ...] [--json] [--no-save]`

报告默认落库 Workspace（`angle_briefs`）；`--no-save` 跳过写入。呈现含 brief `id`。
`finch angles show <id>` 回看，`finch angles list` 列出。
发散探索（问题型思维导图，逐轮展开/组合）走 `finch angles map new/show/expand/connect/list`，与 `discover` 平行。

选角流程（一次结构化调用内完成）：
1. 读原文，提炼主旨 / 关键主张 / 作者建议 / 适用范围 / 未回答的缺口。
2. 由缺口触发，生成候选角度（内部发散 6–8）。
3. 寻找组合材料：从原文取一个观点，与另一份有依据的材料（实践/失败记录、其它文章、
   已有方法、领域通识、读者问题）连接，产生新判断；每个组合说清「为什么能连起来」。
4. 筛出 1–3 个**论点确实不同**的方向（可零候选，不硬凑；独特性排最后）。
5. 深入推荐 1 个方向：提纲 + 证据缺口 + 最小验证行动。

## 可视化呈现

一张图不同时承担探索、组合与决策。方向卡片负责探索，局部关系图负责组合，选题卡负责决策。
默认只呈现核心问题与 3–5 个探索方向；用户选择后展开该方向的 2–3 个完整问题。静态图采用纵向
布局（Mermaid 优先 `flowchart TD`），过宽时拆成总览图与分支图，不通过缩小字体容纳更多节点。
方向使用短标签，具体问题保留完整问句；选题细节使用卡片。交互式 HTML 在实际显示尺寸下保持正文
至少 16px，并适配窄屏。输出前检查正常窗口下的可读性，不以全屏放大作为解决方案。

完整规则见 `references/presentation.md`。

## 输入回退（codex 触发）

用户未提供帖子链接时的回退规则见 `_shared/url-fallback.md`。

## 价值判据

相对原文必须至少给读者一种增量：理解增量（现象为什么发生）/ 判断增量（何时适用、如何选）/
行动增量（具体怎么做）/ 观察增量（看见原本忽略的问题）。每条候选角度都要能回答
「原文已经讲了什么？我准备补上什么？这能帮助谁解决什么问题？」。

角度库见 `references/angle-library.md`（22 角度四类）。库可以全面，但每次只深入最合适的几个；
`--prefer-angle` / `--exclude-angle` 可微调。

## 证据边界（硬约束）

- **原文是外部证据**：其主张、例子、结果永远不得写成作者亲历。
- **实践记录是材料不是证明**：`--practice-ref` 提供可用素材，不能自动证明推论；无真实记录时建议实验或明确标为假设场景。
- 区分 `source_claim` / `verified_fact` / `inference` / `hypothetical_example`（`increment_basis` 字段）。
- 证据不足的新颖观点不因独特而排第一，标「需先验证」而非成熟结论。
- 不把热度当真相；不虚构亲历；不强行唱反调；不得把类比当证据。

## 边界

- 不重述原文（→ `finch summarize`）；不点评写法（→ `finch article analyze`）。
- 不生成草稿；选中的 brief 之后由草稿生成技能（`finch drafts write`）写成文章，本 Skill 不做自动 handoff。
- 不把原文主张写成作者亲历（见 `_shared/evidence-policy.md`）；无自动发布。

## 参考

- `references/angle-library.md` — 22 角度四类 + 核心追问 + 适用信号 + 两条约束。
- `references/combination-patterns.md` — 组合方式表 + 桥问题 + 试金石 + 工作示例。
- `references/output-contract.md` — AngleBrief 契约（确定性字段 + 判断字段 + 证据性质标注）。
- `references/presentation.md` — 可视化布局、字号与交互式呈现规则。
- `_shared/evidence-policy.md` — 外部帖 ≠ 个人证据；不把推断写成已验证事实。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话。
- `_shared/url-fallback.md` — 用户未提供帖子链接时，回退到当前对话中最近一次 URL（codex 触发）。
