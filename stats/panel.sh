#!/usr/bin/env bash
# 一键启动「显影台」统计面板, 并自动打开浏览器。
#
#   ./stats/panel.sh                # 自动: 中心目录/仓库有真实 ledger 则用真实, 否则 mock
#   ./stats/panel.sh real           # 强制真实数据
#   ./stats/panel.sh mock           # 强制 mock (看设计)
#   ./stats/panel.sh real snano     # 真实数据 + 启动即聚焦 snano 产线
#   ./stats/panel.sh auto simage    # 自动数据源 + 聚焦 simage
#
# 面板每 15s 轮询 stats-data.json, 所以技能跑完 sync 刷新后, 开着的页面会自动更新。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-auto}"
LINE="${2:-}"

STATS_HOME="${SNANO_STATS_HOME:-$HOME/.codex/skills/_snano-stats}"
CENTRAL_LEDGER="$STATS_HOME/ledger"
REPO_LEDGER="$(dirname "$HERE")/data/ledger"
PYTHON_BIN="${SNANO_STATS_PYTHON:-python3}"
PORT="${SNANO_STATS_PORT:-5173}"

# 解析数据源
resolve_ledger_dir() {
  if compgen -G "$CENTRAL_LEDGER/*.json" >/dev/null 2>&1; then echo "$CENTRAL_LEDGER"; return; fi
  if compgen -G "$REPO_LEDGER/*.json" >/dev/null 2>&1; then echo "$REPO_LEDGER"; return; fi
  echo ""
}

if [ "$MODE" = "mock" ]; then
  echo "→ 生成 mock 数据…"; "$PYTHON_BIN" "$HERE/aggregate.py" --mock
else
  LEDGER_DIR="$(resolve_ledger_dir)"
  if [ -n "$LEDGER_DIR" ]; then
    echo "→ 聚合真实数据 ($LEDGER_DIR)…"
    "$PYTHON_BIN" "$HERE/aggregate.py" --ledger-dir "$LEDGER_DIR"
  elif [ "$MODE" = "real" ]; then
    echo "⚠ 未找到真实 ledger, 退回 mock。"; "$PYTHON_BIN" "$HERE/aggregate.py" --mock
  else
    echo "→ 暂无真实 ledger, 用 mock 占位…"; "$PYTHON_BIN" "$HERE/aggregate.py" --mock
  fi
fi

# 拼启动 URL: #7d 默认范围 + 可选 ?line= 聚焦
URL="http://localhost:${PORT}/"
[ -n "$LINE" ] && URL="${URL}?line=${LINE}"
URL="${URL}#7d"

echo "→ 启动面板: $URL  (Ctrl-C 退出)"
( sleep 2.5; (command -v open >/dev/null && open "$URL") || true ) &

cd "$HERE/app"
if [ "${SNANO_STATS_DEV:-0}" = "1" ] && command -v npm >/dev/null 2>&1; then
  [ -d node_modules ] || { echo "→ 安装依赖…"; npm install; }
  exec npm run dev -- --host 127.0.0.1 --port "$PORT"
fi

if [ -d dist ]; then
  cd dist
exec "$PYTHON_BIN" -m http.server "$PORT" --bind 127.0.0.1
fi

if command -v npm >/dev/null 2>&1; then
  [ -d node_modules ] || { echo "→ 安装依赖…"; npm install; }
  exec npm run dev -- --host 127.0.0.1 --port "$PORT"
fi

echo "未找到 npm，也没有 stats/app/dist 静态面板。请先构建面板或同步 dist。"
exit 1
