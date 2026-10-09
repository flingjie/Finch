# Notion 页面布局（toggle 块素材）

素材以 toggle（``<details>``）块存在月度页（Motivation & Vision → Notes → 月度页）里。
一条素材 = 一个 toggle 块，summary 是标题，children 是正文：

```
[月度页]
  ├── toggle 「标题一句话」         ← 素材 1（summary = 标题）
  │     ├── paragraph 发生了什么
  │     ├── heading_3 我的感触      ← 可选
  │     ├── paragraph 为什么触动我
  │     ├── heading_3 Finch 讨论记录 #disc_xxx   ← 讨论后追加
  │     ├── paragraph 判断：…
  │     └── paragraph AI 提议：…
  ├── toggle 「标题一句话」         ← 素材 2
  │     └── …
  └── …
```

规则：

- **只追加不重写**：讨论块永远追加到该 toggle 的 children 末尾；用户原文、用户改过的讨论块
  不覆盖、不恢复。
- **块 id / 讨论 id 管理**：Finch 区靠 `heading_3` 标记（`Finch 讨论记录 #<discussion_id>`）与
  讨论 id 管理，不靠标题文本辨认。
- **部分写入恢复**：写回前全量读该 toggle 的 children、扫标记——无标记→整段追加；有标记但尾部
  缺失→只补缺失尾块；齐全→幂等不重复；标记在但内容对不上（用户已编辑）→不覆盖、不重复追加。
- **无固定格式**：用户可自由写正文；模板只是提示。无法可靠识别「我的感触」时保持为空，不用模型
  猜测填充。
- **已讨论**：由 children 是否含 `Finch 讨论记录` 派生，不单独存字段。
