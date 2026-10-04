# interaction-preparation 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
为你选了这条机会，先做这个切口吗？

**来自你的那句**：你说「最难的是不知道任务到底跑没跑，后来加了幂等键才敢重试」——方法卡的第三步就从这里来。
**对象与话题**：@alice 在问「同一任务重跑为何结果不同」，和你处理过的调用超时直接对得上。
**你能补充**：把「先判断是否执行过 → 再决定重试」作为最小抓手。
**来源**：https://x.com/alice/status/1

方法卡正文：
> 适用处境：… 输入：… 步骤：… [reaction] 我当时先加了幂等键才敢重试 … 输出与判断：… 限制：…

状态：待审，未运行。回复「采用」「改：…」或「跳过」。
```

无反应时第一行改为：「没有你的反应，这次只准备了一个澄清问题」，正文只有一个具体观察 + 一个问题。

素材来自 `finch connect prepare --opportunity <id>`（正文 + `source_refs` + `execution_status`）。
主文不贴 `opp_*` / `art_*` id。

回复草稿默认只突出一条正文（一个重点）；选用的方法、聚焦点与风格版本经 `--json` 按需查看，
不在主文里展开分析。

## 用户下一轮 → CLI

- `采用` → 用户在原平台亲自发送，再 `uv run finch connections record --person <person_id>
  --url <url> --body "<正文>" --opportunity <id>` 登记事实。
- `演示跑通了` → `uv run finch connect artifact-status --opportunity <id> --artifact <id>
  --execution ran_ok [--real-material] [--note …]`
- `对方回应了：…` → `uv run finch connections follow-up --opportunity <id> --reply-body "…"`
- `改：…` 改的是你自己的经历或判断 → `uv run finch connect prepare --opportunity <id>
  --reaction "<新原话>"`（追加一条反应并重新生成）；只是改措辞 → 直接按指令改正文后重新生成，
  或走 `finch drafts revise`（若正文走草稿流）。
- `跳过` → 结束本机会，不登记。
