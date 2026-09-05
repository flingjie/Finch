# Reddit 搜索接入 opencli 设计文档

> 状态：已获用户批准（2026-09-05）。
> 目标：把互动轨道的 `RedditPostSearchProvider` 占位实现替换为真实 opencli 搜索，使其与
> X/Twitter 一样通过 opencli 只读封装拉取帖子，并流入既有的通用评分 → 提案 → 审批队列。

## 1. 范围

- 仅实现 **`opencli reddit search`** 一个只读命令（互动轨道唯一需要的搜索入口）。
  不实现 `hot` / `subreddit` / `read` / `user` 等命令（YAGNI，后续需要时再加）。
- 不新增任何 Reddit 写 / 执行路径：Reddit 帖子可进入 `DRAFT_REPLY` / `DRAFT_QUOTE` 候选与
  人工审批队列，但**没有**对应的发送适配器（发送仍被 denylist 阻断，见 §6）。
- 下游 `scoring` → `proposals` → 审批队列对 `ExternalPost` 已平台无关，本次不改动其逻辑，
  仅新增 Reddit 数据入口。

## 2. 背景与现状

- `engagement/search.py` 现有 `RedditPostSearchProvider` 是占位实现：`available()` 恒为
  `False`，`search()` 抛 `ProviderUnavailableError`。
- opencli（1.8.7）已内置 `reddit` 适配器，`opencli reddit search <query>` 输出列为：

  ```
  id, title, subreddit, author, score, comments, url, created_utc, selftext,
  post_hint, url_overridden_by_dest, preview_image_url, gallery_urls
  ```

  写命令（comment / reply / save / upvote / subscribe / login）需 denylist。

- Reddit 与 Twitter 的字段差异：
  - 文本拆为 `title` + `selftext`（正文，链接/图片帖常为空），无单一 `text`；
  - `created_utc` 是 Unix epoch 秒（整数），非 Twitter 的 RFC 2822 字符串；
  - 热度为 `score`（点赞数）+ `comments`（评论数），非 Twitter 的 `likes` / `views`。

## 3. 架构决策

采用「新建 `src/finch/reddit/` 模块镜像 `twitter/`」方案（用户已选 A）：

- 忠实于既有「每平台一个 adapter 模块」约定，每个单元可独立测试；
- 不动任何已工作的 Twitter 代码与测试，风险面最小；
- `_browser_flags` / 错误分类等约 30 行重复可接受；待出现第三个站点时再抽取共享 opencli 基类。

### 3.1 文件布局

```
src/finch/reddit/
  __init__.py
  models.py          # RedditPost + 类型化错误
  opencli_client.py  # RedditOpenCliClient（allowlist/denylist/_call/search）
```

不设 `normalizer.py`：互动轨道不消费噪音过滤/去重（去重在 `search.py::dedupe` 统一做）。

## 4. `reddit/models.py`

- `RedditPost(BaseModel)`，字段：`id, title, subreddit, author, score, comments, url,
  created_utc, selftext`。
  - 数值/时间容错：`score` / `comments` 若为字符串则转 int（失败为 0）；`selftext` 若为
    `None` 则转 `""`；`created_utc` 兼容 int / str / float。
- `published_at() -> datetime | None`：`datetime.fromtimestamp(created_utc, tz=UTC)`；
  失败返回 `None`（**禁止伪造时间**，与 Twitter 同一不变量）。
- `content() -> str`：`title`，若 `selftext` 非空则 `title + "\n\n" + selftext[:500]`；
  空 selftext 退化为仅标题（链接/图片帖常见）。
  - 模块常量 `_SELFTEXT_MAX_CHARS = 500`（截断上限，可调）。
- 类型化错误镜像 Twitter：`RedditError`（基类，`error_code`）、`RedditSourceUnavailable`
  （未登录 / bridge 离线）、`RedditRateLimited`（限流）、`RedditCommandBlocked`
  （denylist 命中）。

## 5. `reddit/opencli_client.py`

- `_ALLOWLIST`：只读命令 `search, hot, subreddit, frontpage, home, popular, user, read,
  saved, upvoted, subscribed, subreddit-info, whoami, user-posts, user-comments`。
- `_DENYLIST`：写命令 `comment, reply, save, upvote, subscribe, login`。
- `_check_allowlist(argv)`：`" ".join(argv[1:3])` 得 `"reddit search"` 等；命中 denylist
  或不在 allowlist → `RedditCommandBlocked`（与 Twitter 同款防御性双层校验）。
- `_parse_posts(stdout)`：解析 JSON 列表 / 单条 dict 为 `list[RedditPost]`，单条解析失败
  不中断整批（与 `_parse_tweets` 同款）。
- `_browser_flags()`：`["--window", OPENCLI_WINDOW|background, "--site-session", "persistent"]`。
- `_call(argv, timeout=60.0)`：`_run`（复用 `finch.github.gh_client._run`）→ 非零退出时按
  stderr 分类：`not logged in` / `login`、`bridge` / `daemon` → `RedditSourceUnavailable`；
  `rate` / `too many` → `RedditRateLimited`；其余 → `RedditError`。
- `RedditOpenCliClient.search(query, *, sort="relevance", limit=20) -> list[RedditPost]`：
  `opencli reddit search <query> --sort relevance --limit N -f json`。
- 客户端**仅**暴露 `search()` 一个方法（与 §1 范围一致）；不提供 `version()` / `doctor()`，
  `finch diagnose` 本次不扩展 Reddit 探针。

## 6. Provider 与接线

- `RedditPostSearchProvider`（`engagement/search.py`）：
  - `platform = "reddit"`，`available() -> True`（运行期健康问题在 `search` 中以类型化异常暴露）；
  - `__init__(client: RedditOpenCliClient | None = None)`，默认 `RedditOpenCliClient()`（与
    `XPostSearchProvider` 同款 DI）；
  - `search()` 把每条 `RedditPost` 映射为 `ExternalPost`：
    - `id=post.id`，`platform="reddit"`，`url=post.url`，
      `author_id=post.author`，`author_name=post.author`，
      `content=post.content()`，`published_at=post.published_at()`（`None` 则跳过），
      `metrics={"upvotes": post.score, "comments": post.comments}`，
      `matched_topics=[query]`。
- `flow.py::_build_providers`：`platform == "reddit"` → `RedditPostSearchProvider(reddit_opencli)`。
- `flow.py::run_discovery_engagement_flow`：新增可选参数
  `reddit_opencli: RedditOpenCliClient | None = None`，透传给 `_build_providers`；默认构造真实
  客户端。
- `cli.py`：与 Twitter 对称地构造并传入 `RedditOpenCliClient()`。
- `scoring.py`：`_POPULARITY_KEYS` 增加 `"upvotes"`（热度排序 tiebreaker 需识别 Reddit 点赞
  键；否则只看到 `comments` 而丢失点赞信号）。两行改动。

## 7. 不变量与错误处理

- **无自动发布**：adapter 只读；Reddit 写命令 denylist（defense in depth）。
- **外部 ≠ 证据**：搜索到的 Reddit 帖子保持 `ExternalPost`，绝不直接升级为 personal 证据；
  仅经验证的 `ConversationEvidence` 可经 `promote_to_personal` 提升。
- **确定性总分**：Reddit 帖子的 `total` 仍由 `scoring.weighted_total` 在代码中计算，LLM 输出
  不含 `total`。
- **故障隔离**：单条 Reddit 查询失败以 `PostSearchFailure` 记录（`search_engagement_posts`
  内按 query 捕获），绝不中断 X provider 或整轮；`available()=False` 仍记录
  `query=None` 的「provider not enabled」失败。

## 8. 测试

- `tests/unit/test_reddit_models.py`：解析、`published_at()` unix→UTC、`content()` 截断 /
  空 selftext、字符串数值容错。
- `tests/unit/test_reddit_client.py`：allowlist/denylist、`_parse_posts`、search argv、
  错误分类（镜像 `test_opencli_client.py`）。
- 契约测试 + fixture `tests/fixtures/opencli/reddit-search.json`：捕获一条真实
  `opencli reddit search -f json` 输出，锁定 schema（`tests/contract/` 新增
  `test_reddit_models.py`，skipif fixture 不存在）。
- 更新 `tests/unit/test_engagement_search.py`：Reddit provider 现为映射而非抛
  `ProviderUnavailableError`。
- 更新 `tests/unit/test_engagement_flow.py`：注入 `FakeRedditOpenCli`；原「reddit not
  enabled」断言改为「reddit 返回帖子 / reddit 失败但 x 仍工作」。

## 9. 范围外（明确不做）

- Reddit 回复 / 评论 / 点赞的**发送**适配器（审批通过后也不会自动发布；需独立 denylist 写
  任务另行处理）。
- `hot` / `subreddit` / `read` / `user` 等命令，以及共享 opencli 基类抽取（待第三个站点出现）。
- `created_utc` 之外的 `post_hint` / `preview_image_url` / `gallery_urls` 等媒体字段映射。
