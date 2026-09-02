#!/usr/bin/env bash
# Finch 两周试运行：每日执行一次。
# 运行每日 Graph，产出 Daily Brief，再列出待审核草稿。
set -euo pipefail

cd "$(dirname "$0")/.."

finch run daily
echo
finch review list
