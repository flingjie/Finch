---
name: problem-discovery-shadow
description: >
  Skill-first 影子实验：宿主 Agent 动态研究真实讨论并起草问题卡片，经 Python
  validate-upsert 门禁写入 shadow 命名空间；与 control ``discover`` 对照。用于验证
  Skill-first + Thin Runtime，不替代生产 problem-discovery Skill。
---

# problem-discovery-shadow

宿主（Cursor/Codex）负责研究路径；Finch thin CLI 负责候选池、同步、证据门禁与对照。
**禁止**调用 `finch problem-discovery discover`（那是 control 的 Python LLM 抽取）。

设计见 `docs/superpowers/specs/2026-10-30-skill-first-thin-runtime-design.md`。

## CLI（thin tools）

```bash
uv run finch problem-discovery add --repo <owner/repo> | --url <链接> | --text <正文>
uv run finch problem-discovery candidates --json
uv run finch problem-discovery sync [--source NAME] [-q QUERY]...
uv run finch problem-discovery validate-upsert --shadow --draft -|--file PATH [--json]
uv run finch problem-discovery journal show|append [--shadow] [--json]
uv run finch problem-discovery compare [--json]
uv run finch problem-discovery list|show|summary   # 注意：默认读 control；对照以 compare / shadow repos 为准
```

Shadow 写入目录：`var/problem_discovery/shadow/{problems,runs,journal}/`。

## 流程

1. `candidates --json`：读取 fingerprint、budget_stats、sources。无来源则先 `add` 或 `sync`。
2. 证据不足时：最多再 `sync` **3** 次（可带 `--source` / `-q`），或 `add` 导入指定讨论。
3. 宿主根据 candidates 正文起草 `ProblemsExtraction` JSON（字段同 control：title / scenario / evidence / hypotheses / unknowns）。
4. `validate-upsert --shadow --draft …`：Python `build_problem` 门禁；失败摘录会被丢掉。
5. `journal append` 记录研究决策；`compare --json` 对照最新 control run（需事先跑过 control `discover`）。
6. 筛选呈现复用生产规则：`skills/problem-discovery/references/screening.md`（最多 3 个重点，不写总分）。

## 硬约束

- **不发明证据**：`source_ref` / quote 必须来自 `candidates`；URL 由 Python 覆盖为来源 URL。
- **事实 / 假设 / 未知分列**；无证据时留 unknowns，不编造。
- **预算**：≤3 次 sync；墙钟建议 ≤15 分钟；candidates 已由 Python 裁剪 max_sources / max_chars。
- **停止规则**：预算耗尽或证据仍不足 → 输出 unknowns + 下一步验证建议，停止扩展搜索。
- 只读：不外发、不评论、不私信。
- 不删不改 control inbox。

## 草稿 JSON 形状

```json
{
  "problems": [
    {
      "title": "…",
      "user_type": "…",
      "task": "…",
      "trigger": "…",
      "consequence": "…",
      "current_solutions": [],
      "evidence": [
        {
          "source_ref": "<from candidates.id>",
          "quote": "<verbatim substring>",
          "claim": "…",
          "evidence_level": "e1",
          "experience": "first_hand",
          "is_marketing": false,
          "author": ""
        }
      ],
      "hypotheses": [],
      "unknowns": [],
      "next_action": "observe"
    }
  ]
}
```

## 参考

- `../problem-discovery/references/screening.md`
- `../problem-discovery/references/presentation.md`
- `references/budgets.md`
