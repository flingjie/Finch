# URL 输入回退

用户在 codex 对话中触发读帖类 skill、却未提供帖子链接时：取当前对话中最近一次出现、
且与本请求语义相关的 URL 作为输入。不要盲目套用——若对话中有多个候选 URL，或拿不准
用户指的是哪一篇，先向用户确认链接，再继续。

适用入口：

- 内容阅读（`--text / --file / --url` 三选一）：`finch summarize`、`finch article analyze`、
  `finch angles discover`、`finch angles map new`。
- 指定帖子评估：`finch connect assess`（peer-discovery / interaction-preparation 的入口 2）。

只回退 URL；`--text` / `--file` 不参与回退。
