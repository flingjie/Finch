# Finch

Finch 帮助用户从真实使用障碍中发现值得自己解决的问题。问题是默认入口与核心研究对象。产品是已有方案和替代方案；人物是问题当事人、验证伙伴和潜在合作者。需要时再理解已有方案为谁解决什么问题、采用什么做法、有哪些证据与未验证假设，并通过真实试用、具体反馈与公开讨论推进判断。

产品发现、反馈研究与关系能力按问题研究需要调用，不要求先经过产品发现或连接创业者。一个真实问题可以对应多个已有方案，一个产品也可以对应多个问题，二者通过证据或人工确认关联。运行目标是每周至少推进一个有证据支持的投入或放弃决定；实际验证、判断变化和真实反馈分别记录，不汇总总分，不承诺每周必有产出。内容生产不是目的，也不服从连接。

> 完整定义见 [`docs/product-contract.md`](docs/product-contract.md)。Finch 独立运行，不依赖任何其他项目（如 builderDNA）。

当前架构是 **Skill + 领域服务**（不是 Graph Runtime，也不是 LLM agent loop）：状态机、存储、幂等、产品聚类、确定性打分、证据门禁都留在确定性 Python 领域服务。`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 核心闭环

```text
发现问题 → 对照已有方案 → 比较与学习 → 选择行动 → 反馈与连接
```

默认从真实使用障碍进入：社区问题（issue / 讨论）或用户反馈里值得自己解决的问题。产品发现与反馈研究按需要调用，用来对照已有方案和替代方案。

当前已落地「问题发现（Problem Inbox）」作为默认入口；产品发现与方案分析按问题研究需要调用。Product↔Problem 显式关联、Radar、Investigate 与更深的协作在后续轮次。

北极星指标：**每周新增或加深多少个「有上下文、可继续」的创业者关系**；关系之外每周分别观察判断修正、真实行动、自己的判断、持续交流与问题推进（各自记录，不合成总分）。问题推进目标：每周至少推进一个有证据支持的投入或放弃决定；实际验证、判断变化和真实反馈分别记录，不汇总总分，不承诺每周必有产出。收藏数、发现数、试用数、反馈数都不是核心指标。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init                          # 初始化 var/ 文件工作区
uv run finch diagnose                      # 探测 gh / opencli 可用性

# —— 产品发现（按问题研究需要调用） ——
uv run finch products daily [--refresh] [--view home|browse] [--json] [--question "…"]
                                           # 每日 5–10 个产品候选；--refresh 跑发现，否则回放最新快照
uv run finch products list [--json]        # 全部已保存产品（按分数降序）
uv run finch products show <product_id>    # 单个产品完整档案
uv run finch products feedback <product_id> [--refresh] [--json]
                                           # 用户反馈研究（Reddit / X / GitHub）
uv run finch products explore <product_id> [--refresh] [--json]
                                           # 选中一个产品 → 8 点理解（方案分析）

# —— 问题发现（默认入口） ——
uv run finch problem-discovery add --repo <owner/repo> | --url <链接> | --text <正文>
uv run finch problem-discovery discover [--refresh] [--json]
uv run finch problem-discovery list|show|summary|present|last-presented

# —— 人物关系 ——
uv run finch relationships import|add-person|add-event|record-contact|next|weekly

# —— 只读采集（产品/问题发现复用） ——
uv run finch sources doctor / sync         # 跨平台只读采集（twitter|reddit|github|v2ex|weixin|xiaohongshu|…）
uv run finch repos discover [--resume ID] / list --run ID / export --run ID   # X→GitHub 热度榜
uv run finch twitter search / import-bookmarks / diagnose
```

产品发现使用 `finch.yaml` 中独立的 `products.sources` 查询块（与旧 `sources` 隔离）。问题发现使用 `problem_discovery` 配置块。`source_checked`（引用已核对）只表示摘录能回溯到来源，不等于主张被支持，也不等于已复现；复现只能由实验结果更新，属后续轮次。

### 已冻结的旧使命命令

旧「学习 / 连接 / 表达」使命的领域服务已冻结（`hidden=True`），从 `finch --help` 默认面移除，但命令仍可调用、代码与测试保留、数据原样保留，验证新方向后再决定删除。包括：`connect`、`peers`、`connections`、`conversations`、`ideas`、`drafts`、`review`、`voice`、`profile`、`practice`、`article`、`angles`、`methods`、`community`、`dialogue`、`materials`、`problems`、`attempts`、`grounded`、`knowledge`、`collisions`、`experiments`、`inspirations`、`github reflect`、`learn`、`weekly`、`summarize`、`context`。

## 产品发现用法

```bash
# 1. 跑一次产品发现（5–10 个候选；空轮保持空）
uv run finch products daily --refresh --question "最近发布的值得关注的 AI 产品"

# 2. 选中一个产品，得到 8 点理解（方案分析）
uv run finch products explore <product_id>

# 3. 查看或导出候选
uv run finch products list --json
uv run finch products show <product_id>
```

## 架构

```
src/finch/
  products/           Product / Founder / Solution / Feedback + 产品发现 + 方案分析
  problem_discovery/  真实问题抽取与 Problem Inbox
  relationships/      人物任务理解与周度简报
  sources/            跨平台只读抓取（opencli + RawArtifact）
  repos/              X 分享的 GitHub 仓库发现与热度榜（产品发现输入）
  github/ twitter/ reddit/ webfetch/ ingest/  只读 adapter
  storage/            文件 Workspace（YAML / Markdown / JSONL，原子写）
  codex/ llm/         `codex exec` 子进程 + OpenAI 兼容结构化推理
  # —— 已冻结（旧使命，代码与测试保留） ——
  peers/ opportunities/ conversations/ connections/ ideas/ drafts/ content/ practice/
  article/ angle_discovery/ content_summary/ expression_methods/ communities/ grounded/
  problems/ attempts/ knowledge/ materials/ learn/ inbox/ evidence/ collisions/ inspirations/
```

详见 `docs/` 与 `skills/`。
