# 选题比较流程

当用户还没说清「为什么这个问题值得写」时，用比较流程帮他从同一素材里选出方向。目标是
低交互成本：默认一次选择、一个可选理由。比较记录可回看（`finch ideas compare-list/show`），
不建品味分数、不自动偏好学习。

## 步骤

1. **确定素材与问题背景**。素材可为 URL、文章、个人观察或现有素材。尽量复用已有上下文
   （关注的问题、目标读者、已有实践）。仅当缺少信息会显著改变方向时才补问一个问题；
   输入太少时先说明不确定性，不得编造用户经历或实验结果。

2. **生成可比较方向**：
   `finch ideas compare --text/--file/--url [--reader] [--reader-problem] [--goal] [--practice-ref]`。
   产出 3 张（信息不足时 1–2 张并解释）同字段方向卡，见 `comparison-card.md`。首轮不生成
   标题、提纲或正文；不能把用户兴趣视为亲历证据。

3. **先比较，再按需推荐**。默认问：
   「你更愿意写哪一个？可以用一句话说明原因，也可以全部放弃。」
   - 只回复编号 → 继续推进，不反复追问。
   - 合并多方向 → `compare-select <id> <序号> <序号> --final-question ...`，先收敛为一个核心问题。
   - 帮我选 → `compare-select <id> --help-me-choose`，给一个建议 + 依据 + 当前缺口；不预选、
     不展示综合分数，不一律偏向实践类。建议与用户选择分开记录。
   - 全部放弃 / 暂存 → `--discard` / `--park`。

4. **材料检查**（`compare-select` 内部执行）。模型给出有依据的判断；程序只校验字段与状态迁移，
   不把「有引用」机械视为「证据足够」：
   - 已有材料支持核心主张 → `ready` → 建 `ContentJob`（复用现有路径）。
   - 缺一个关键场景或例子 → `needs_material` → 补一次关键追问。
   - 关键效果主张待验证 → `needs_validation` → 最小验证行动（问题/动作/观察/何时/不能据此推断）。
   - 用户尚不愿投入 → `undecided` → 暂存或放弃。

5. **写作交接**。`ready` 时经 `IdeaService.create_candidate` 幂等建 `ContentJob`，再按需指向：
   - 写作：`uv run finch ideas confirm <id>` → `uv run finch drafts create <id>`。
   - 结构梳理：`uv run finch practice start --material "..."`。
   用户明确「直接写」→ `finch ideas compare --direct` 或 `finch drafts write`，跳过比较。

## 记录与复盘

`finch ideas compare-list/show` 可回看取舍：只保存用户明确表达的选择理由（`user_reason`），
不替用户推断；AI 推荐与材料评估（`recommendation` / `readiness_reason`）与用户选择分离。
阅读量是补充信息，不作品味代理指标。
