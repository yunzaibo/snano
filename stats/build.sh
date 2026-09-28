#!/usr/bin/env bash
# 构建「显影台」静态产物到 stats/app/dist, 可用任意静态服务托管。
#   ./stats/build.sh         # 用 mock 数据 (默认)
#   ./stats/build.sh real    # 从 data/ledger 聚合真实数据
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-mock}"

cd "$HERE/app"
[ -d node_modules ] || { echo "→ 安装依赖…"; npm install; }

echo "→ 生成统计数据 (${MODE})…"
if [ "$MODE" = "real" ]; then
  python3 "$HERE/aggregate.py"
else
  python3 "$HERE/aggregate.py" --mock
fi

echo "→ 构建…"
npm run build
echo "✓ 产物: stats/app/dist  ——  预览: cd stats/app && npm run preview"
