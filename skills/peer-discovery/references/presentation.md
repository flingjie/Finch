# peer-discovery 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
今天发现 12 位同行候选，最值得先连的是：

**@alice — 在做 Agent 关系记忆** 〔最推荐〕

共同话题是长期同行关系与互动上下文。对方最近在讨论「发现人之后如何记住聊过什么」，和你正在做的 connection-first 方向重叠，有明确可贡献的实践问题，不是纯新闻或推广。

下一步上下文：围绕「互动反思要落成什么事实」开口，避免先推销 Finch。

另外两条可选：

2. @bob — 写过失败案例复盘，适合交换实验设计
3. @cara — 主题接近但互动历史浅，先观察再开口

其余更像弱信号，暂不优先：4… 

回复「准备互动 1」「展开 2」或「换一批」。
```

素材来自 `finch connect daily` 的同行段，或 `finch peers list` / `show` 决策卡（谁 / 为什么值得连 / 下一步上下文 / 共同话题）。

## 用户下一轮 → CLI

- `准备互动 N` → `uv run finch connect prepare`（或对该 peer 相关帖子 `connect create --input <url>`），再进入 interaction-preparation 呈现
- `展开 N` → `uv run finch peers show {peer_id}`，用人话补共同主题与下一步上下文
- `换一批` → 从本次未展示同行再挑最多 3 条；没有则给 `uv run finch peers list`
