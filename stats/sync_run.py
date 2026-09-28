#!/usr/bin/env python3
"""技能跑完后的统计同步 (collector)。

被 snano-run / simage-run 在 batch 结束时以 best-effort 方式调用:
    python stats/sync_run.py <run_root>

职责:
1) 把该次 run 散落在 <run_root> 下的 batch-ledger.json 复制进"中立统计中心目录"
   ($SNANO_STATS_HOME/ledger, 默认 ~/.codex/skills/_snano-stats/ledger);
   —— 两个技能 project root 不同, 必须汇到同一中心, 面板才看得到两条线。
2) 调 aggregate.py 从中心目录重新聚合 -> 写出面板消费的 stats-data.json
   (同时刷新 app/public 与 app/dist, 两种托管方式都即时更新)。

安全: 只搬运 *ledger*.json (聚合层只读非敏感字段); 不碰 .env / sources.internal /
raw response / 密钥。任何异常都吞掉, 绝不影响出图主流程。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGGREGATE = os.path.join(HERE, "aggregate.py")
PUBLIC_OUT = os.path.join(HERE, "app", "public", "stats-data.json")
DIST_OUT = os.path.join(HERE, "app", "dist", "stats-data.json")

STATS_HOME = os.environ.get(
    "SNANO_STATS_HOME", os.path.expanduser("~/.codex/skills/_snano-stats")
)
LEDGER_DIR = os.path.join(STATS_HOME, "ledger")


def _find_ledgers(run_root: str) -> list[str]:
    """在 run_root 下递归找 batch-ledger.json (兼容 ledger/ 子目录命名)。"""
    pats = ["**/*batch-ledger.json", "**/ledger/*.json", "*batch-ledger.json"]
    found: list[str] = []
    for pat in pats:
        found.extend(glob.glob(os.path.join(run_root, pat), recursive=True))
    return sorted(set(found))


def ingest(run_root: str) -> int:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    n = 0
    for src in _find_ledgers(run_root):
        dst = os.path.join(LEDGER_DIR, os.path.basename(src))
        try:
            shutil.copy2(src, dst)
            n += 1
        except OSError:
            continue
    return n


def refresh() -> None:
    # 中心目录有真实 ledger 就聚合真实数据, 否则保持现状 (不强行覆盖成空)。
    has_real = bool(glob.glob(os.path.join(LEDGER_DIR, "*.json")))
    if not has_real:
        return
    cmd = [sys.executable, AGGREGATE, "--ledger-dir", LEDGER_DIR, "--out", PUBLIC_OUT]
    subprocess.run(cmd, check=False, timeout=120)
    # dist 若已构建, 同步一份, 让静态托管也即时更新。
    if os.path.isdir(os.path.dirname(DIST_OUT)):
        try:
            shutil.copy2(PUBLIC_OUT, DIST_OUT)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    run_root = argv[1] if len(argv) > 1 else "."
    try:
        n = ingest(run_root)
        refresh()
        print(f"[stats-sync] ingested {n} ledger(s) from {run_root} -> {LEDGER_DIR}")
    except Exception as exc:  # noqa: BLE001  — 同步绝不应中断出图
        print(f"[stats-sync] skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
