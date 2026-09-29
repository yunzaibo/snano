#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_SIMAGE_PROVIDERS = [
    "apiyi-simage-gpt-image-2-5-flare",
]
SIZE_TO_MIN_LONG_EDGE = {
    "1k": 1024,
    "2k": 2048,
    "4k": 4096,
}


def resolve_project_root() -> Path:
    candidates: list[Path] = []
    for env_name in ("SIMAGE_PROJECT_ROOT", "SNANO_PROJECT_ROOT"):
        env_root = os.environ.get(env_name)
        if env_root:
            candidates.append(Path(env_root).expanduser())
    home = Path.home()
    candidates.extend([
        Path(__file__).resolve().parents[3],
        home / "Desktop" / "sshfile" / "snano",
        home / "Documents" / "New project" / "snano",
        home / "Documents" / "snano",
    ])
    for candidate in candidates:
        if (
            (candidate / "router" / "main.py").exists()
            and (candidate / "configs" / "routing-presets.yaml").exists()
        ):
            return candidate.resolve()
    searched = "\n".join(f"- {candidate}" for candidate in candidates)
    raise SystemExit(
        "Simage project root not found. Set SIMAGE_PROJECT_ROOT or install the "
        f"project folder in one of these locations:\n{searched}"
    )


PROJECT_ROOT = resolve_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def project_python() -> str:
    candidates: list[Path] = []
    env_python = os.environ.get("SIMAGE_PYTHON")
    if env_python:
        candidates.append(Path(env_python).expanduser())
    candidates.extend([
        PROJECT_ROOT / ".venv" / "bin" / "python",
        Path("/tmp/snano-py312/bin/python"),
        Path.home() / ".local" / "bin" / "python3.12",
    ])
    for venv_python in candidates:
        if not (venv_python.exists() and os.access(venv_python, os.X_OK)):
            continue
        probe = subprocess.run(
            [str(venv_python), "-c", "import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return str(venv_python)
    return sys.executable


def ensure_supported_python() -> None:
    if sys.version_info >= (3, 10):
        return
    target = project_python()
    if Path(target).resolve() == Path(sys.executable).resolve():
        raise SystemExit("Simage requires Python 3.10+ because the router uses dataclass(slots=True).")
    os.execv(target, [target, __file__, *sys.argv[1:]])


ensure_supported_python()


from router.snano_dispatch import (  # noqa: E402
    apply_dimension_defaults_to_items,
    infer_image_count_from_prompt,
    load_manifest_items,
    max_workers_for_batch,
    normalize_image_size,
)
from router.core.simage_selection import MODEL_CHOICES  # noqa: E402


def load_env_file(path: Path) -> dict[str, str]:
    env = os.environ.copy()
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[len("export "):].strip()
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            value = shlex.split(value)[0] if value else ""
        except ValueError:
            value = value.strip("\"'")
        if key:
            env[key] = value
    return env


def bypass_proxy_for_apiyi(env: dict[str, str]) -> dict[str, str]:
    """APIyi TLS breaks through the local HTTP proxy; route it directly."""
    updated = dict(env)
    no_proxy_values = []
    for key in ("NO_PROXY", "no_proxy"):
        raw = updated.get(key)
        if raw:
            no_proxy_values.extend(part.strip() for part in raw.split(",") if part.strip())
    for host in ("api.apiyi.com", "vip.apiyi.com", "api.morewater.vip"):
        if host not in no_proxy_values:
            no_proxy_values.append(host)
    no_proxy = ",".join(no_proxy_values)
    updated["NO_PROXY"] = no_proxy
    updated["no_proxy"] = no_proxy
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        updated.pop(key, None)
    return updated


def write_manifest(path: Path, items: list[dict[str, Any]]) -> None:
    manifest = {
        "jobType": "manifest",
        "profile": "simage-professional-2k",
        "items": items,
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_model_selection_flags(
    items: list[dict[str, Any]],
    *,
    model: str,
    intent: str,
) -> list[dict[str, Any]]:
    """Carry explicit Agent routing decisions into each structured request."""
    for item in items:
        metadata = item.setdefault("metadata", {})
        if model != "auto":
            metadata["simageModel"] = model
        else:
            metadata.setdefault("simageModel", "auto")
        if intent != "auto":
            metadata["simageIntent"] = intent
        else:
            metadata.setdefault("simageIntent", "auto")
    return items


def min_long_edge_for_size(size: str | None, explicit_min_long_edge: int | None) -> int | None:
    if explicit_min_long_edge is not None:
        return explicit_min_long_edge
    normalized = str(size or "2K").strip().lower()
    if normalized in SIZE_TO_MIN_LONG_EDGE:
        return SIZE_TO_MIN_LONG_EDGE[normalized]
    match = re.fullmatch(r"(\d+)x(\d+)", normalized)
    return max(int(match.group(1)), int(match.group(2))) if match else None


def build_items(
    *,
    prompt: str,
    references: list[Path],
    count: int,
    project_profile: str | None,
    aspect_ratio: str | None,
    size: str | None,
    min_long_edge: int | None,
    aspect_strict: bool | None,
    min_short_edge: int | None,
    min_short_edge_mode: str | None,
) -> list[dict[str, Any]]:
    reference_paths = [str(ref) for ref in references]
    request_type = "image_to_image" if reference_paths else "text_to_image"
    resolved_size = size or "2K"
    items: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        metadata = {
            "source": "simage-skill",
            "variantIndex": index,
            "variantTotal": count,
        }
        if reference_paths:
            metadata["requireFullReferenceLock"] = True
        items.append({
            "id": f"simage-variant-{index:03d}",
            "requestType": request_type,
            "prompt": prompt,
            "referenceImages": reference_paths,
            "quality": "high",
            "routingPolicy": "fallback",
            "metadata": metadata,
        })
    return apply_dimension_defaults_to_items(
        items,
        project_profile=project_profile,
        aspect_ratio=aspect_ratio,
        size=resolved_size,
        min_long_edge=min_long_edge_for_size(resolved_size, min_long_edge),
        aspect_strict=aspect_strict,
        min_short_edge=min_short_edge,
        min_short_edge_mode=min_short_edge_mode,
    )


def run_batch(
    *,
    manifest_path: Path,
    run_root: Path,
    env: dict[str, str],
    max_workers: int,
    dry_run: bool,
    preset: str,
    routing_policy: str | None,
) -> dict[str, Any]:
    cmd = [
        project_python(),
        "-m",
        "router.main",
        "explain-plan" if dry_run else "run",
        str(manifest_path),
        "--preset",
        preset,
        "--max-workers",
        str(max_workers),
        "--max-retries-per-provider",
        "0",
        "--batch-dir",
        str(run_root / "batches"),
        "--ledger-dir",
        str(run_root / "ledger"),
        "--output-dir",
        str(run_root / "artifacts"),
        "--perf-log-file",
        str(run_root / "perf.jsonl"),
        "--provider-state-file",
        str(run_root / "provider-health.json"),
    ]
    if routing_policy:
        cmd.extend(["--routing-policy", routing_policy])
    started_at = time.monotonic()
    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "stdout": completed.stdout,
        }
    summary_path = payload.get("summary_path")
    if summary_path and Path(summary_path).exists():
        try:
            disk_summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            if disk_summary.get("items"):
                payload["items"] = disk_summary["items"]
        except (OSError, json.JSONDecodeError):
            pass
    payload.update({
        "simage_elapsed_ms": elapsed_ms,
        "manifest_path": str(manifest_path),
        "returncode": completed.returncode,
        "max_workers": max_workers,
        "preset": preset,
        "routing_policy_override": routing_policy,
    })
    return payload


def _launch_stats_panel() -> int:
    """子命令 `view`: 显式启动「显影台」统计面板 (不出图), 聚焦 simage 产线。
    避免随出图自动弹窗造成误触。面板路径可用 SNANO_STATS_PANEL 覆盖。"""
    import os as _os, subprocess as _sp
    candidates = [
        _os.environ.get("SNANO_STATS_PANEL"),
        str(PROJECT_ROOT / "stats" / "panel.sh"),
        _os.path.expanduser("~/.codex/skills/_snano-stats/panel.sh"),
    ]
    for p in candidates:
        if p and _os.path.exists(p):
            print(f"→ 启动显影台统计面板 (simage): {p}")
            try:
                return int(_sp.run(["bash", p, "auto", "simage"], check=False).returncode)
            except Exception as exc:  # noqa: BLE001
                print(f"面板启动失败: {exc}")
                return 1
    print("未找到统计面板 panel.sh; 可设环境变量 SNANO_STATS_PANEL 指向它。")
    return 1


def main() -> int:
    # 子命令: `view` / `panel` 显式启动统计面板, 不进入出图流程。
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] in ("view", "panel", "--view"):
        return _launch_stats_panel()

    parser = argparse.ArgumentParser(description="Run Simage professional 2K+ GPT-Image-2 routing.")
    parser.add_argument("--reference", action="append", default=[], help="Optional reference image path. Repeat for multiple references.")
    parser.add_argument("--prompt", help="Image generation prompt")
    parser.add_argument("--manifest", help="Existing manifest to run through the simage preset")
    parser.add_argument("--output-root", required=True, help="Output folder")
    parser.add_argument("--count", type=int, default=None, help="图片数量；未指定时从提示词识别，否则默认为 1")
    parser.add_argument("--max-workers", type=int, default=None, help="Maximum parallel workers; default is total item count")
    parser.add_argument("--preset", default="simage", help="Routing preset used for the unified Simage batch")
    parser.add_argument("--routing-policy", default=None, help="Override routing policy, e.g. fallback or health_aware_load_balance")
    parser.add_argument("--model", choices=MODEL_CHOICES, default="auto", help="模型：auto、flare、sunburst 或完整模型 ID")
    parser.add_argument("--intent", choices=("auto", "speed", "precision"), default="auto", help="任务意图：auto、speed、precision")
    parser.add_argument("--project-profile", default=None, help="Dimension defaults profile, e.g. amazoncar")
    parser.add_argument("--aspect-ratio", default=None, help="Optional target ratio, e.g. 16:9. Omit for auto.")
    parser.add_argument("--size", default="2K", type=normalize_image_size, help="尺寸：1K、2K、4K 或 2048x2048")
    parser.add_argument("--min-long-edge", type=int, default=None, help="Hard minimum long edge, default 2048")
    parser.add_argument("--min-short-edge", type=int, default=None, help="Optional soft/strict short-edge floor")
    parser.add_argument("--min-short-edge-mode", default=None, help="Short-edge mode label, default soft")
    parser.add_argument("--aspect-strict", action="store_true", help="Treat aspect-ratio mismatch as a hard artifact failure")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.manifest:
        source_manifest, items = load_manifest_items(args.manifest)
        manifest_size = args.size or "2K"
        items = apply_dimension_defaults_to_items(
            items,
            project_profile=args.project_profile,
            aspect_ratio=args.aspect_ratio,
            size=manifest_size,
            min_long_edge=min_long_edge_for_size(manifest_size, args.min_long_edge),
            aspect_strict=True if args.aspect_strict else None,
            min_short_edge=args.min_short_edge,
            min_short_edge_mode=args.min_short_edge_mode,
        )
    else:
        if args.count is None:
            args.count = infer_image_count_from_prompt(args.prompt) or 1
        if args.count < 1:
            raise SystemExit("--count must be >= 1")
        if not args.prompt:
            raise SystemExit("--prompt is required unless --manifest is provided")
        references = []
        for raw_ref in args.reference:
            ref = Path(raw_ref).expanduser().resolve()
            if not ref.exists():
                raise SystemExit(f"reference image not found: {ref}")
            references.append(ref)
        items = build_items(
            prompt=args.prompt,
            references=references,
            count=args.count,
            project_profile=args.project_profile,
            aspect_ratio=args.aspect_ratio,
            size=args.size,
            min_long_edge=args.min_long_edge,
            aspect_strict=True if args.aspect_strict else None,
            min_short_edge=args.min_short_edge,
            min_short_edge_mode=args.min_short_edge_mode,
        )
        source_manifest = None

    items = apply_model_selection_flags(items, model=args.model, intent=args.intent)

    output_root = Path(args.output_root).expanduser().resolve()
    run_id = f"{time.strftime('simage_%Y%m%dT%H%M%SZ', time.gmtime())}_{uuid.uuid4().hex[:8]}"
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    explicit_source_config = os.environ.get("MIR_SOURCES_CONFIG_FILE")
    env = load_env_file(PROJECT_ROOT / ".env.internal")
    env = bypass_proxy_for_apiyi(env)
    env["MIR_SOURCES_CONFIG_FILE"] = explicit_source_config or str(PROJECT_ROOT / "configs" / "sources.apiyi.yaml")
    env["MIR_ROUTING_PRESETS_FILE"] = str(PROJECT_ROOT / "configs" / "routing-presets.yaml")

    root_manifest = run_root / "simage-input-manifest.json"
    write_manifest(root_manifest, items)
    batch_max_workers = max_workers_for_batch(items, requested_workers=args.max_workers)
    batch_summary = run_batch(
        manifest_path=root_manifest,
        run_root=run_root,
        env=env,
        max_workers=batch_max_workers,
        dry_run=args.dry_run,
        preset=args.preset,
        routing_policy=args.routing_policy,
    )
    ok = bool(batch_summary.get("ok")) and int(batch_summary.get("returncode") or 0) == 0
    final_summary = {
        "ok": ok,
        "dry_run": bool(args.dry_run),
        "run_root": str(run_root),
        "source_manifest": source_manifest,
        "input_manifest": str(root_manifest),
        "providers": sorted({provider for item in batch_summary.get("items", []) for provider in item.get("planned_providers", [])}) or DEFAULT_SIMAGE_PROVIDERS,
        "model_selection": [
            item.get("request_contract", {}).get("model_selection")
            for item in batch_summary.get("items", [])
            if item.get("request_contract", {}).get("model_selection")
        ],
        "total_items": len(items),
        "request_type": items[0].get("requestType") if items else None,
        "size": args.size,
        "max_workers": batch_max_workers,
        "preset": args.preset,
        "routing_policy_override": args.routing_policy,
        "success_count": int(batch_summary.get("success_count") or 0),
        "failure_count": int(batch_summary.get("failure_count") or 0),
        "batch_summary": batch_summary,
    }
    summary_path = run_root / "simage-summary.json"
    summary_path.write_text(json.dumps(final_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    final_summary["summary_path"] = str(summary_path)
    print(json.dumps(final_summary, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0 if ok else 1

    # 统计同步 (best-effort): 把本次 ledger 汇入中立统计中心并刷新面板数据。
    # 任何异常都吞掉, 绝不影响出图主流程。可用 SNANO_STATS_SYNC 覆盖 sync 脚本路径。
    try:
        import os as _os, sys as _sys, subprocess as _sp
        for _cand in (
            _os.environ.get("SNANO_STATS_SYNC"),
            str(PROJECT_ROOT / "stats" / "sync_run.py"),
            _os.path.expanduser("~/.codex/skills/_snano-stats/sync_run.py"),
        ):
            if _cand and _os.path.exists(_cand):
                _sp.run([_sys.executable, _cand, str(run_root)], timeout=120, check=False)
                break
    except Exception:
        pass

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
