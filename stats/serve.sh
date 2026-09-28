#!/usr/bin/env bash
# 一键启动「显影台」统计面板 (开发模式, 含自动刷新)。
#   ./stats/serve.sh         # 用 mock 数据 (默认)
#   ./stats/serve.sh real    # 从 data/ledger 聚合真实数据
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-mock}"
PYTHON_BIN="${SNANO_STATS_PYTHON:-python3}"
PORT="${SNANO_STATS_PORT:-5173}"

echo "→ 生成统计数据 (${MODE})…"
if [ "$MODE" = "real" ]; then
  "$PYTHON_BIN" "$HERE/aggregate.py"
else
  "$PYTHON_BIN" "$HERE/aggregate.py" --mock
fi

cd "$HERE/app"
if [ "${SNANO_STATS_DEV:-0}" = "1" ] && command -v npm >/dev/null 2>&1; then
  if [ ! -d node_modules ]; then
    echo "→ 首次运行, 安装依赖 (npm install)…"
    npm install
  fi
  echo "→ 启动 dev 服务 (Ctrl-C 退出)…"
  exec npm run dev -- --host 127.0.0.1 --port "$PORT"
fi

if [ -d dist ]; then
  echo "→ 启动静态面板 (Ctrl-C 退出)…"
  cd dist
  exec "$PYTHON_BIN" -m http.server "$PORT" --bind 127.0.0.1
fi

if command -v npm >/dev/null 2>&1; then
  if [ ! -d node_modules ]; then
    echo "→ 首次运行, 安装依赖 (npm install)…"
    npm install
  fi
  echo "→ 启动 dev 服务 (Ctrl-C 退出)…"
  exec npm run dev -- --host 127.0.0.1 --port "$PORT"
fi

echo "未找到 npm，也没有 stats/app/dist 静态面板。请先构建面板或同步 dist。"
exit 1
