#!/usr/bin/env bash
# Finch 每日任务：运行完整每日 Graph，生成 Daily Brief。
set -euo pipefail

exec finch run daily
