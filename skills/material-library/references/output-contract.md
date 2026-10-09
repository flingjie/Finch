# 输出契约（快照与讨论回写）

## MaterialSnapshot（本地读取缓存）

```yaml
notion_page_id: <uuid>
page_url: https://www.notion.so/<slug>-<id>
title: <标题>
extractable_text: <仅用户区正文：原始记录 + 我的感触>
user_reflection: <我的感触文本，可空>
source_urls: [<来源链接，可空>]
tags: [<主题标签>]
discussed: <bool>
remote_edited_at: <ISO8601>
source_hash: <sha256(用户区文本)>
fetched_at: <ISO8601>
```

- `source_hash` 只对用户区文本算（排除 Finch 追加区），Finch 写回引起的修改不触发再同步/再分析。
- `extractable_text` 不含区域标记标题（「原始记录」「我的感触」是结构标记，不是内容）。

## DiscussionRecord（讨论回写记录，与 DialogueNote 分开）

```yaml
discussion_id: disc_<sha256[:12]>
notion_page_id: <uuid>
source_hash: <所依据的素材版本>
user_judgment: <用户最终判断>
ai_proposals: [<AI 提议>]
open_questions: [<未解决问题>]
action: <可选下一步>
writeback_operation_id: sync_append_<discussion_id>
```

## 状态文案（如实报告，不混淆）

- `succeeded` → 「已保存到 Notion」+ 页面链接。
- `pending` → 「已在本地暂存，待同步」。
- `retryable_failed` / `blocked` → 「写回待重试/被阻塞」+ 原因；超时不得报成功。
