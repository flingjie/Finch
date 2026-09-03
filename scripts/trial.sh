#!/usr/bin/env bash
# Finch 两周试运行：每日执行一次。
# 1. 运行每日 Graph（finch run daily），产出 Daily Brief。
# 2. 列出需要人工补立场的 Content Jobs（HITL 门：finch jobs list --status needs_input）。
# 3. 列出待审核草稿（finch review list）。
# 每周另执行 `finch run weekly` 得到七项内容指标与「继续/调整/停止」建议。
set -euo pipefail

cd "$(dirname "$0")/.."

finch run daily
echo
echo "== Content Jobs 需补立场 (needs_input) =="
finch jobs list --status needs_input
echo
echo "== 待审核草稿 =="
finch review list
