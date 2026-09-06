你根据用户的原始想法写一篇中文短内容草稿。按 schema 返回 JSON（只有 body 字段）。
不要读取文件、运行命令或使用任何工具，只依据下方数据作答。
Instructions:
- 保持用户核心观点，用作者自己的口吻表达。
- 观点诚实表述为个人判断/假设，不把推断写成已验证事实。
- 只把 Evidence cards 当作参考上下文（提供具体案例），不编造来源或数字。
- 篇幅短：一段观点 + 一句具体例子或取舍，不写长文。

## 用户想法
{text}

## 核心观点
{core_point}

## 目标读者
{audience}

## 作者立场
claim: {claim}
decision: {decision}
tradeoff: {tradeoff}

## Evidence cards（参考上下文，可选）
{cards}
