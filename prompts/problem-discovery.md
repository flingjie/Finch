# 问题发现（Problem Discovery）

你从真实讨论/材料中提取「真实问题」卡片。宁可少提，也不编造。

输入是 JSON 数组，每个元素是一条原始材料（`source_ref` + `url` + `text`）。

## 输出格式（严格 JSON）

```json
{
  "problems": [
    {
      "title": "Agent 失败后难以复现",
      "user_type": "Agent 应用开发者",
      "task": "长时间运行的 Agent",
      "trigger": "工具调用异常",
      "consequence": "需要人工恢复执行",
      "current_solutions": ["手动重跑", "人工检查执行日志"],
      "evidence": [
        {
          "source_ref": "github:issue:123",
          "quote": "用户原文摘录（必须逐字取自输入 text）",
          "claim": "用户报告恢复执行异常",
          "evidence_level": "e1",
          "experience": "first_hand | second_hand | unknown",
          "is_marketing": false,
          "author": ""
        }
      ],
      "hypotheses": ["缺少可复现的失败测试环境"],
      "unknowns": ["发生频率", "影响成本"],
      "next_action": "interview"
    }
  ],
  "eval_failed": false,
  "skip_reason": ""
}
```

## 规则

- `problems` 只包含你能从输入材料里**确实找到**的问题；没有就返回空数组。
- `title` 非空；`user_type` 描述「谁遇到这个问题」。
- `evidence` 的每一项 `source_ref` 必须是输入里真实存在的 source_ref；`quote` 必须逐字取自
  对应 `text`，不能改写、不能凭空生成。没有可回溯的证据就留空 `evidence`。
- **事实与假设分开**：用户明确说出的归 `evidence`（带 quote）；你的推断归 `hypotheses`；
  不清楚的归 `unknowns`。不要把推测写进 `evidence`。
- 不把评论数量当作用户数量；不把技术讨论自动转成商业需求；不把产品宣传当作用户需求证据。
- `experience` 只在有明确第一人称/转述线索时填 first_hand / second_hand，否则 unknown；
  自宣/营销内容 `is_marketing=true`。
- 你只做语义抽取，不写任何总分或排名分数。

输入材料（JSON）：

```
{candidates_json}
```
