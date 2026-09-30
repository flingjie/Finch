# interaction-preparation 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
为你选了这条机会，先做这个切口吗？

**对象与话题**：@alice 在问「连接之后系统该记住什么」，和你的关系记忆实践直接对得上。
**你能补充**：把「聊过什么 / 新认识 / 下次为何值得继续」三条事实作为最小抓手。
**来源**：https://x.com/alice/status/1

方法卡正文：
> 适用处境：… 输入：… 步骤：… 输出与判断：… 限制：…

状态：待审，未运行。回复「采用」「改：…」或「跳过」。
```

素材来自 `finch connect prepare --opportunity <id>`（正文 + `source_refs` + `execution_status`）。
主文不贴 `opp_*` / `art_*` id。

## 用户下一轮 → CLI

- `采用` → 用户在原平台亲自发送，再 `uv run finch connections record --person <person_id>
  --url <url> --body "<正文>" --opportunity <id>` 登记事实。
- `改：…` → 继续用 `finch drafts revise`（若正文走草稿流）或直接按指令改正文后重新生成。
- `跳过` → 结束本机会，不登记。
