---
name: material-library
description: >
  围绕用户的素材库（Notion 权威来源）记录、浏览、搜索、选中并讨论素材，逐步形成观点。
  用于「记一下…」「保存这个素材」「看看最近的素材」「查一下素材」「聊聊这条素材」
  「把这条提炼成 idea」等请求。Notion 是权威来源，本地只存读取缓存、待同步队列与
  讨论工作状态；Finch 只追加正文，不重写用户内容。
  只做素材的记录/读取/讨论/提炼；不点评写法（→ article_analysis）、
  不改写成草稿（→ idea-to-draft，须先确认判断）、不自动发布。
---

# material-library

帮用户随时记录有感触的想法或事件，之后找回素材、关联已有经验、展开讨论，逐步形成
自己的观点。Notion 是主要记录与编辑入口；Finch 是读取、讨论、分析入口。

## 定位

- **权威来源**：Notion 素材库（数据库）是唯一权威；本地只存读取缓存、待同步操作队列
  与讨论工作状态，不另建一套需要用户维护的素材库。
- **只追加不重写**：Finch 只在页面末尾追加「Finch 讨论记录」区；用户原文与用户改过的
  讨论块都不覆盖、不恢复。
- **先保存后分析**：用户说「记一下…」先保存，用户选择「聊聊这条」后再进入讨论；不自动
  追问，不把一句随手记扩展成完整观点。

## 执行（CLI）

调用 `finch materials`，不复制业务逻辑：

```bash
finch materials doctor                     # 核对权限与属性名（只读）
finch materials capture --title ... --body-text ... [--url ...] [--tag ...] [--reflection ...]
finch materials read <page_id> [--json]     # 引用素材前先读
finch materials list [--tag ...] [--discussed] [--json]
finch materials search <关键词> [--limit 3] [--json]
finch materials sync [--full] [--json]
finch materials queue [--drain] [--all] [--json]
finch materials record-discussion --page-id ... --judgment ... [--proposal ...] [--question ...] [--action ...]
finch materials promote <page_id> --core-point ... [--reader-problem ...] [--why ...]
finch materials usage <page_id> [--json]
```

- 引用某条素材前先 `finch materials read <page_id>`，确认拿到的是最新缓存。
- 保存/回写先落队列；`--drain` 立即同步。状态 `pending`（本地暂存待同步）与
  `succeeded`（已写入 Notion）须如实区分，超时不得报成功。
- 页面布局见 `references/notion-page-layout.md`；输出契约见 `references/output-contract.md`。

## 输入回退（codex 触发）

读链接型请求无链接时，复用对话里最近的、语义相关的 Notion 页 id 或 URL；有歧义先确认。

## 讨论（复用 topic-dialogue）

用户选择「聊聊这条」后，进入 `topic-dialogue`（不写关系记录）。讨论结束后：

- 用户选择保留的总结用 `finch dialogue save`（讨论摘要记忆）；
- 需要写回素材页的部分用 `finch materials record-discussion`（追加 Finch 讨论块 +
  已讨论标记）。
- 只同步用户要求保留的部分；讨论本身不触发全文聊天归档。

## 提升为观点候选

仅用户明确要求「把这个提炼成 idea」时，`finch materials promote <page_id> --core-point ...`。
结果为 `proposed`（非 `confirmed`），复用现有 idea 链路与幂等规则，保留 Notion 来源引用
（`_shared/idea-contract.md`）。重复提炼同一素材同一主张复用原记录。

## 边界

| 用户说 | 转交 |
|---|---|
| 先跟我讨论这条素材 | `topic-dialogue`（讨论后 `finch materials record-discussion`） |
| 保存这个观点 / 形成观点候选 | `idea-discovery` 或 `finch materials promote`（结果 `proposed`） |
| 生成草稿 / 帮我写 | `idea-to-draft`（先确认判断） |
| 这篇文章讲了什么 | `content-summary`（`finch summaries`） |
| 这条还能写什么角度 | `article-angle-discovery`（`finch angles`） |
| 分析写法 | `article_analysis`（`finch article analyze`） |

## 证据边界（硬约束）

- 个人感触是用户自述，不自动标成已证实事实（`_shared/evidence-policy.md`）。
- 外部素材 ≠ 个人证据；进入 idea 时 `evidence_status=unverified`、`facts=[]`，用户可后续升级。
- 无自动发布：素材保存不触发对外互动、不建 ContentJob、不生成草稿。

## CLI（持久化）

不得直接写 workspace YAML；用 `finch materials` 命令。讨论摘要用 `finch dialogue`。

## 参考

- `references/notion-page-layout.md` — 用户区 / Finch 区标记与块形状
- `references/output-contract.md` — 快照与讨论回写的字段契约
- `_shared/dialogue-policy.md` — 任务完成后的延伸点、授权边界、收束与停止条件
- `_shared/idea-contract.md` — IdeaCandidate 统一契约
- `_shared/evidence-policy.md` — 证据边界
- `_shared/agent-presentation.md` — 完成后如何对用户说话
