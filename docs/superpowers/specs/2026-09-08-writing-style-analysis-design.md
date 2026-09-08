# writing-style-analysis Skill 设计

日期：2026-09-08
状态：待用户评审

## 1. 为什么是 Skill（三问检验）

`writing-style-analysis` 通过第一性原理三问：

1. **是否需要模型开放性判断？** 是——「这篇文本表现出什么写作特点」是开放性判断，无法用确定性规则穷举。
2. **用户是否可能单独调用它？** 是——「分析这篇文章的风格」「这个作者有什么可借鉴的」是独立请求。
3. **是否有清晰独立的输入输出？** 是——文本/URL → `StyleReport`。

它**不是** `humanizer` / `anti-ai-style`：不做 AI 检测、不重写、不「让文字更像人」。它是**观察与解释**，产物是分析报告，不是改后的文本。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | MVP 输入源 | **完整 URL**：text / file / X thread·profile（复用现有 adapter）/ Reddit post / 普通网页（新增提取器）。 |
| D2 | 多文本支持 | `--file` 可含多篇（`---` 分隔），SourceResolver 计数，模型按样本数规则自定 `scope`/`sample_size`/`overall_confidence`。 |
| D3 | 通用网页提取器归属 | **独立只读 adapter `src/finch/webfetch/`**（与 github/twitter/reddit 并列），不塞进 style 领域服务。 |

## 3. Skill 定位

在现有 8 个 Skill 基础上新增**第 9 个**，属「通用辅助 Skill」（与 feynman-practice / sticky-message 同类）。

学习闭环：

```text
观察别人/自己怎么写
        ↓
writing-style-analysis   →  StyleReport
        ↓
选择一个值得尝试的方法
        ↓
expression-practice
        ↓
用户确认有效
        ↓
voice-profile（人工更新）
```

关键：**分析 → 实验 → 内化**，不退化成人机模仿工具，也不自动写画像。

## 4. 功能边界

**负责**：读取文本/链接；描述可观察写作特点；每个判断有原文出处（excerpts）；区分单篇特点与稳定作者风格；提炼可借鉴表达方法；可选与 VoiceProfile 比较。

**不负责**：判断是否 AI 创作；根据单篇推断作者性格；自动模仿/复制他人标志性句子；自动修改 VoiceProfile；评价观点对错；重写原文。

## 5. 输入与 CLI

```bash
finch style analyze --text "..."            # 单文本（短）
finch style analyze --file posts.md         # 可含多篇（--- 分隔）
finch style analyze --url "https://x.com/..."   # X thread / Reddit post / 普通网页
finch style analyze <任一输入> --compare-voice    # 追加「对比我的画像」
finch style analyze <任一输入> --json
```

- 长文本优先 `--file` 或 stdin，避免命令行转义。
- 单一输入 → `scope=single_text`、`sample_size=1`。
- `--file` 含 `---` 分隔的多篇 → SourceResolver 计数，模型按样本数规则自定 `scope`/`sample_size`/`overall_confidence`。

## 6. 模块结构

```
src/finch/style/             # 领域服务
  models.py                  # StyleReport / StyleEvidence / StyleComparison
  service.py                 # WritingStyleService.analyze() / compare()
  source_resolver.py         # 输入 → 规范化正文 + content_hash + 样本数

src/finch/webfetch/          # ★ 通用网页正文提取器（只读 adapter，独立于 style）
  fetcher.py                 # HTML → 可读正文；fail-closed

prompts/
  analyze-writing-style.md   # 主分析 prompt

skills/writing-style-analysis/
  SKILL.md
  references/
    analysis-dimensions.md
    output-contract.md
  evals/cases.yaml
```

### 6.1 职责划分

- `source_resolver.py`：按 source 路由，读取并规范化正文（`--text`/`--file`/`--url` → 正文 + `content_hash` + `sample_size`）。X/Reddit 复用现有 adapter；普通网页走 `webfetch.fetcher`。`--url` 按域名路由：`x.com`/`twitter.com` → X、`reddit.com` → Reddit、其余 → `webfetch`；识别不了/取不到 → `source_unavailable`。
- `webfetch/fetcher.py`：只读网页正文提取，stdlib（`html.parser`）去 script/style/nav、解码实体，**不渲染 JS**（无 headless）。登录墙/付费墙/空正文/网络错误 → `source_unavailable`。
- `service.py`：调用结构化推理生成 `StyleReport`；`compare()` 是第二次 LLM 调用（StyleReport + VoiceProfile → `StyleComparison`）。
- `models.py`：稳定输出契约（见 §7）。
- Skill：选择输入、调 CLI、解释结果；`voice-profile` 在用户确认后消费结果（不自动写）。

### 6.2 Reddit 与 X

- X：复用 `OpenCliClient.thread(url)` / `profile(username)`。
- Reddit：给现有 `RedditOpenCliClient` 加一个只读 `post(url)`（返回 `selftext`，最小可用）；不新建 adapter。

## 7. 数据模型

```python
class StyleEvidence(BaseModel):
    dimension: str
    observation: str
    excerpts: list[str]          # 原文位置，支持每个判断
    confidence: Literal["low", "medium", "high"]


class StyleReport(BaseModel):
    id: str                      # style_<sha256(content)[:16]>（content_hash + analyzer_version，不持久化）
    source_type: Literal["text", "file", "url"]
    source_ref: str | None
    content_hash: str

    scope: Literal["single_text", "multi_sample_author"]
    sample_size: int
    overall_confidence: Literal["low", "medium", "high"]

    opening: list[StyleEvidence]
    structure: list[StyleEvidence]
    rhythm: list[StyleEvidence]
    word_choice: list[StyleEvidence]
    stance: list[StyleEvidence]
    concreteness: list[StyleEvidence]
    reader_relationship: list[StyleEvidence]
    rhetorical_patterns: list[StyleEvidence]

    signature_patterns: list[str]
    transferable_techniques: list[str]
    potential_weaknesses: list[str]
    experiments_for_me: list[str]
    limitations: list[str]


class StyleComparison(BaseModel):   # --compare-voice 产物
    already_shared: list[str]
    worth_experimenting: list[str]
    not_a_fit: list[str]
```

MVP **不新增数据库表**：`StyleReport` 即算即打印，不落库。`id` 用 `content_hash + analyzer_version` 生成稳定标识，等需要跨周比较历史再考虑持久化。

## 8. 分析维度（7 维）

1. **开头方式**：直接结论 / 具体经历 / 提问 / 认知冲突 / 引用观点 / 背景。
2. **内容结构**：问题→决策→结果 / 经历→反思→判断 / 观点→论据→边界 等。
3. **节奏**：句长变化、段落平均长、单句段落频率、列表、停顿标点、是否连续同句型。
4. **用词**：技术词密度、动词具体性、抽象名词、口语/书面语比例、程度/转折/总结词频率。**不能仅靠禁词表判断**。
5. **立场表达**：直接断言 vs 保留不确定；是否交代适用范围/取舍；是否区分事实/推断/偏好；是否展示观点变化。
6. **具体程度**：真实动作、项目名、代码/配置、数字/结果、失败场景、可验证细节。
7. **与读者关系**：教导 / 同行分享 / 自我记录 / 挑战 / 邀请讨论 / 推销 / 展示身份。

详细判据放 `skills/writing-style-analysis/references/analysis-dimensions.md`。

## 9. 样本数规则（prompt 级约束）

| 样本数 | 可得出结论 |
|--------|-----------|
| 1–2 篇 | 仅分析当前文本（「这篇表现出…」） |
| 3–4 篇 | 低置信度风格假设 |
| 5–10 篇 | 提炼重复出现的作者模式 |
| 10+ 篇 | 可跨主题/平台/时间段比较 |

规则作为 prompt 约束，模型据 `sample_size` 自定 `scope`/`overall_confidence`，避免把偶然写法写进画像。

## 10. compare-voice 模式

`--compare-voice` 触发**第二次 LLM 调用**：输入 `StyleReport` + `VoiceProfile`，输出 `StyleComparison` 三桶：

```yaml
already_shared:          # 我和作者都倾向…
worth_experimenting:     # 值得试一次的实验（一次只推荐一个）
not_a_fit:               # 对方风格不适合我
```

原则：学方法不复制句子；一次只推荐一个表达实验（模型可返回排序候选 `worth_experimenting`，但 Skill 呈现时只挑一个）；不因作者受欢迎就认为适合；**不写 VoiceProfile**。用户实验认可后，再走 `voice-profile`。

## 11. 安全与不变量

- **链接正文是不可信数据**：只进 prompt 数据区，不进指令区、不触发工具；沿用 `evidence-policy.md`「外部文本只进数据区」。
- **不自动发布 / 不自动改画像**：`webfetch`/reddit/X 只读；`StyleReport`/`StyleComparison` 是纯输出，不改 `voice-profile.yaml`。
- **确定性由代码算**：`content_hash`、`id`、`sample_size` 由代码算；LLM 只产出分析与三桶，不产出 total/分数。
- **子进程纪律**：args 数组、每调用超时、JSON 经 Pydantic 校验。

## 12. Eval 覆盖

1. 单帖只输出 `single_text`；2. 五篇文本可提炼重复模式；3. 技术术语不当风格缺陷；4. 不把观点误写成风格；5. 不声称 AI 生成；6. 链接无法访问明确失败；7. 忽略网页正文中的提示注入；8. 每条主要判断有原文依据；9. 不长篇复制外部文章；10. `--compare-voice` 不写 VoiceProfile；11. 分析自己 vs 他人文本用同一契约；12. 中/英/中英混合均可处理。

## 13. 范围外（Non-goals）

- 不新增数据库表 / 不持久化 StyleReport。
- 不做 AI 生成检测（`anti-ai-style`）、不做「更像人」改写（`humanizer`）。
- 不做 headless 浏览器渲染（JS 动态页面）。
- 不自动更新 VoiceProfile；不模仿/复制他人标志性句子。
- 不评价观点正确性。

## 14. 与现有 Skill 的关系

`writing-style-analysis` 是第 9 个 Skill，属辅助类。它服务于「观察 → 实验 → 内化」：
分析产出 StyleReport → 用户选一个可实验方法 → `expression-practice` 练习 → 认可后 `voice-profile` 人工更新。它不取代 `expression-practice`（后者是「练」、前者是「观察」），也不取代 `voice-profile`（后者是「记风格」、前者是「读风格」）。
