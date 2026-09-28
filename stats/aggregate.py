#!/usr/bin/env python3
"""生图数量统计 - 数据适配层 / Image-count stats aggregator.

职责边界:
- 只读取 data/ledger/*.json 中的 batch ledger（不含密钥, secret_values_included=false）,
  聚合成统一的统计记录 records, 写出给前端面板使用的 stats-data.js。
- 不读取 .env / notes/api.md / configs/sources.internal.yaml / data/raw response / logs。
- 不修改任何路由核心逻辑。

统一记录契约 (schemaVersion = image-stats/v1):
    {
      "line":         "snano" | "simage",
      "requestType":  "text_to_image" | "image_to_image",
      "provider":     <provider lane id>,
      "imageCount":   int,   # 产出图片数
      "successCount": int,
      "failedCount":  int,
      "createdAt":    ISO8601 时间字符串
    }

前端只消费 records, 因此 mock 数据和真实 ledger 数据走同一条出口, 接真实数据时
无需改前端: 运行 `python stats/aggregate.py` 即可用真实 ledger 覆盖 stats-data.js。

用法:
    python stats/aggregate.py              # 从 data/ledger 聚合真实数据
    python stats/aggregate.py --mock       # 生成 mock 数据(用于 UI 设计/演示)
    python stats/aggregate.py --out path   # 指定输出文件(默认 stats/stats-data.js)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import random
from collections import defaultdict

SCHEMA_VERSION = "image-stats/v2"

# 两条 operator-facing 产线的默认 provider lane。
# 这是 line 归属的唯一权威来源, 与 configs/routing-presets.yaml 中 snano/simage 预设保持一致。
LINE_LANES = {
    "snano": [
        "apiyi-nano-banana-pro-4k",
        "laozhang-nano-banana-pro-4k-i2i",
        "apimart-gemini-i2i",
    ],
    "simage": [
        "apiyi-simage-gpt-image-2",
        "laozhang-simage-gpt-image-2",
        "apimart-gpt-image-2-i2i",
    ],
}

REQUEST_TYPES = ("text_to_image", "image_to_image")

_LANE_TO_LINE = {lane: line for line, lanes in LINE_LANES.items() for lane in lanes}

# --- mock 专用: 运行质量维度的合成参数 (仅用于 --mock; 真实 ledger 接入由 Codex 侧完善) ---
# 分辨率分布权重 (生图常见 1K / 2K / 4K 三档; 专业线偏 4K)。
SIZE_WEIGHTS = (("4K", 0.45), ("2K", 0.4), ("1K", 0.15))
# 各分辨率的基准出图延迟 (毫秒), 越高分辨率越慢。
SIZE_BASE_LATENCY = {"1K": 6000, "2K": 11000, "4K": 22000}


def _station_of(provider: str) -> str:
    """从 provider lane 取中转站前缀: apiyi / laozhang / apimart。"""
    return provider.split("-", 1)[0]

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_LEDGER_DIR = os.path.join(REPO_ROOT, "data", "ledger")
# 前端 (Vite app) 从 public/ 读取该 JSON; 契约 image-stats/v1。
DEFAULT_OUT = os.path.join(HERE, "app", "public", "stats-data.json")


def classify_line(provider: str) -> str | None:
    """把 provider lane 归到 snano / simage 产线; 未知 lane 返回 None。"""
    if provider in _LANE_TO_LINE:
        return _LANE_TO_LINE[provider]
    p = provider.lower()
    # 兼容启发式: 便于未来新增同族 lane / 各中转站命名细微差异时仍能归线。
    if "nano-banana" in p or "gemini" in p:
        return "snano"
    # 注意: 真实 lane 名可能是 *-openai-images (如 apiyi-primary-openai-images,
    # laozhang-openclaw-openai-images), 不一定含 "gpt-image"。必须覆盖 openai/image 系。
    if "simage" in p or "gpt-image" in p or "openai-image" in p or "openai_image" in p:
        return "simage"
    return None


def _normalize_request_type(raw: str | None) -> str | None:
    if raw in REQUEST_TYPES:
        return raw
    if raw in ("t2i", "txt2img", "text2image"):
        return "text_to_image"
    if raw in ("i2i", "img2img", "image2image"):
        return "image_to_image"
    return None


def _normalize_size(raw) -> str | None:
    """归一分辨率档位到 1K / 2K / 4K (生图常见三档); 其它原样大写返回。"""
    if not raw:
        return None
    s = str(raw).strip().upper()
    return s or None


def build_records_from_ledger(ledger_dir: str) -> list[dict]:
    """从 batch ledger 的 items 聚合统计记录 (契约 image-stats/v2)。

    每个 (batch, line, requestType, provider, size) 桶汇总为一条记录:
      - imageCount   = 该桶内成功 item 的 artifact 图片总数
      - successCount = status == success 的 item 数
      - failedCount  = 其余 item 数
      - latencyMs    = 成功 item 的平均出图延迟
      - fallbackCount= 发生过多 provider 尝试 (回退/重试) 的 item 数
      - station/size = 中转站前缀 / 分辨率档位
      - createdAt    = 该 batch 的 recorded_at

    安全: 只读非敏感字段, 不读取 raw_response_path / .env / sources.internal,
    不落 request_contract.source (含本地绝对路径), 不输出任何密钥。
    """
    buckets: dict[tuple, dict] = {}
    for path in sorted(glob.glob(os.path.join(ledger_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                ledger = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if ledger.get("dry_run"):
            continue
        created_at = ledger.get("recorded_at")
        items = ledger.get("items") or []
        for item in items:
            provider = item.get("selected_provider")
            if not provider:
                continue
            line = classify_line(provider)
            request_type = _normalize_request_type(item.get("request_type"))
            if line is None or request_type is None:
                continue
            contract = item.get("request_contract") or {}
            size = _normalize_size(contract.get("size")) or "—"
            key = (created_at, line, request_type, provider, size)
            rec = buckets.setdefault(
                key,
                {
                    "line": line,
                    "requestType": request_type,
                    "provider": provider,
                    "station": _station_of(provider),
                    "size": size,
                    "imageCount": 0,
                    "successCount": 0,
                    "failedCount": 0,
                    "latencyMs": 0,
                    "fallbackCount": 0,
                    "createdAt": created_at,
                    "_latSum": 0,
                    "_latN": 0,
                },
            )
            # 回退: 多个 provider 被尝试过 = 至少一次回退/重试。
            attempted = item.get("attempted_providers") or []
            if len(set(attempted)) > 1:
                rec["fallbackCount"] += 1
            if item.get("status") == "success":
                rec["successCount"] += 1
                rec["imageCount"] += len(item.get("artifact_paths") or [])
                lat = item.get("latency_ms")
                if isinstance(lat, (int, float)) and lat > 0:
                    rec["_latSum"] += lat
                    rec["_latN"] += 1
            else:
                rec["failedCount"] += 1
                # 脱敏错误类别透传 (路由 ProviderErrorCategory 枚举值, 非原始报错)。
                cat = item.get("error_category")
                if cat:
                    rec["errorKind"] = str(cat)

    records: list[dict] = []
    for rec in buckets.values():
        if rec["_latN"]:
            rec["latencyMs"] = round(rec["_latSum"] / rec["_latN"])
        else:
            rec.pop("latencyMs", None)  # 无延迟样本则不写该字段, 前端降级。
        rec.pop("_latSum", None)
        rec.pop("_latN", None)
        records.append(rec)
    return records


def build_mock_records(seed: int = 20260626) -> list[dict]:
    """生成确定性 mock 数据, 覆盖 今日 / 最近7天 / 更早 三个时间窗, 便于 UI 时间切换演示。"""
    rng = random.Random(seed)
    today = dt.datetime(2026, 6, 26, 9, 0, tzinfo=dt.timezone.utc)
    records: list[dict] = []

    # 各 lane 的相对产能权重, 让面板看起来更真实(不同中转站产出有差异)。
    lane_weight = {
        "apiyi-nano-banana-pro-4k": 1.4,
        "laozhang-nano-banana-pro-4k-i2i": 1.0,
        "apimart-gemini-i2i": 0.6,
        "apiyi-simage-gpt-image-2": 1.2,
        "laozhang-simage-gpt-image-2": 0.9,
        "apimart-gpt-image-2-i2i": 0.5,
    }

    # 覆盖最近 21 天, 每天为每条 lane 各请求类型生成 0~N 条桶记录。
    for day_offset in range(0, 21):
        day = today - dt.timedelta(days=day_offset)
        # 今日数据更密集, 越往前略稀疏。
        density = 1.0 if day_offset == 0 else (0.8 if day_offset < 7 else 0.45)
        for line, lanes in LINE_LANES.items():
            for provider in lanes:
                for request_type in REQUEST_TYPES:
                    if rng.random() > density:
                        continue
                    base = lane_weight[provider] * (8 if request_type == "text_to_image" else 5)
                    success = max(0, int(rng.gauss(base, base * 0.4)))
                    if success == 0 and rng.random() < 0.6:
                        continue
                    failed = int(rng.random() * max(1, success) * 0.18)
                    images = success + int(rng.random() * success * 0.3)  # 部分请求多图
                    created = day - dt.timedelta(
                        hours=rng.randint(0, 8), minutes=rng.randint(0, 59)
                    )
                    # 运行质量合成维度。
                    size = rng.choices(
                        [s for s, _ in SIZE_WEIGHTS], weights=[w for _, w in SIZE_WEIGHTS]
                    )[0]
                    lat_base = SIZE_BASE_LATENCY[size] * (1.2 if request_type == "image_to_image" else 1.0)
                    latency_ms = max(2000, int(rng.gauss(lat_base, lat_base * 0.22)))
                    # 回退: 偶发, 失败越多越可能触发。
                    fallback = 1 if rng.random() < 0.12 + failed * 0.05 else 0
                    # 并发效率 (批次内同时起跑的 item 数, 越高越并行)。占位字段, 待真实墙钟接入。
                    conc = round(max(1.0, rng.gauss(8.0, 2.4)), 1)
                    # 脱敏错误类别 (mock): 仅失败桶才有, 演示日志类别 + 复制给 AI。
                    error_kind = None
                    if failed > 0:
                        error_kind = rng.choices(
                            ["auth", "rate_limit", "timeout", "upstream_5xx", "invalid_artifact", "connection", "unsupported"],
                            weights=[3, 4, 5, 3, 4, 2, 2],
                        )[0]
                    rec = {
                        "line": line,
                        "requestType": request_type,
                        "provider": provider,
                        "station": _station_of(provider),
                        "size": size,
                        "imageCount": images,
                        "successCount": success,
                        "failedCount": failed,
                        "latencyMs": latency_ms,
                        "fallbackCount": fallback,
                        "concFactor": conc,
                        "createdAt": created.isoformat().replace("+00:00", "Z"),
                    }
                    if error_kind:
                        rec["errorKind"] = error_kind
                    records.append(rec)
    return records


def build_payload(records: list[dict], source: str) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "source": source,
        "lineLanes": LINE_LANES,
        "records": records,
    }


def write_data_json(records: list[dict], source: str, out_path: str) -> None:
    """写出前端消费的 stats-data.json (image-stats/v1)。"""
    payload = build_payload(records, source)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生图数量统计数据聚合")
    parser.add_argument("--mock", action="store_true", help="生成 mock 数据而非读取真实 ledger")
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR, help="ledger 目录")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出 stats-data.js 路径")
    args = parser.parse_args()

    if args.mock:
        records = build_mock_records()
        source = "mock"
    else:
        records = build_records_from_ledger(args.ledger_dir)
        source = "ledger"

    write_data_json(records, source, args.out)
    total_images = sum(r["imageCount"] for r in records)
    print(
        f"[{source}] wrote {len(records)} records, "
        f"{total_images} images -> {os.path.relpath(args.out, REPO_ROOT)}"
    )


if __name__ == "__main__":
    main()
