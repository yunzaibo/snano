from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from router.core.artifacts import write_result
from router.core.models import GenerationRequest
from router.core.provider_registry import build_adapter


def default_single_ref() -> list[str]:
    candidate = Path.home() / "Desktop" / "参考图.jpg"
    return [str(candidate)] if candidate.exists() else []


def default_multi_ref() -> list[str]:
    refs: list[str] = []
    for p in [
        Path.home() / "Desktop" / "参考图.jpg",
        Path.home() / "Desktop" / "建筑风格确定" / "ae1e49c8f8750cc712bafd05d5eda7d3.PNG",
        Path.home() / "Desktop" / "网站参考图" / "氛围参考图" / "1.PNG",
    ]:
        if p.exists():
            refs.append(str(p))
    return refs


def normalize_refs(args: argparse.Namespace, mode: str) -> list[str]:
    refs: list[str] = []
    if args.ref:
        refs.extend(args.ref)
    if args.refs:
        refs.extend(args.refs)
    if refs:
        return refs
    if mode == "i2i-single":
        return default_single_ref()
    if mode == "i2i-multi":
        return default_multi_ref()
    return []


def validate_refs(refs: list[str], mode: str) -> list[str]:
    if mode == "t2i":
        return refs
    missing = [p for p in refs if not Path(p).exists()]
    if missing:
        missing_text = "\n".join(f"- {p}" for p in missing)
        raise SystemExit(
            "Reference image paths not found:\n"
            f"{missing_text}\n"
            "Pass current existing files via --ref/--refs instead of relying on old desktop paths."
        )
    if mode == "i2i-single" and len(refs) < 1:
        raise SystemExit("i2i-single requires 1 existing reference image path.")
    if mode == "i2i-multi" and len(refs) < 2:
        raise SystemExit("i2i-multi requires at least 2 existing reference image paths.")
    return refs


def build_request(args: argparse.Namespace) -> GenerationRequest:
    refs = validate_refs(normalize_refs(args, args.mode), args.mode)
    prompt = args.prompt or {
        "t2i": "A premium white ride-on children's car in a warm suburban driveway, grounded tires, commercial product photography, realistic lighting.",
        "i2i-single": "Use the uploaded reference image as the absolute anchor. Preserve the core subject faithfully while placing it into a premium commercial scene with realistic grounding and controlled lighting.",
        "i2i-multi": "Use the uploaded references together: preserve the primary subject from the main reference, borrow scene mood from the secondary references, and keep the subject structure stable.",
    }[args.mode]

    if args.mode == "t2i":
        return GenerationRequest(
            job_type="manifest",
            request_type="text_to_image",
            profile=args.profile,
            prompt=prompt,
            aspect_ratio=args.aspect_ratio,
            size=args.size,
            quality=args.quality,
            metadata={"source": args.source},
        )

    return GenerationRequest(
        job_type="manifest",
        request_type="image_to_image",
        profile=args.profile,
        prompt=prompt,
        reference_images=refs,
        aspect_ratio=args.aspect_ratio,
        size=args.size,
        quality=args.quality,
        metadata={"source": args.source},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    default_perf_log = str(Path(__file__).resolve().parents[1] / "logs" / "performance.jsonl")
    parser.add_argument("provider", help="Provider or configured lane id, for example gptge-key3-main-openai-images")
    parser.add_argument("mode", choices=["t2i", "i2i-single", "i2i-multi"])
    parser.add_argument("--prompt")
    parser.add_argument("--profile", default="generic")
    parser.add_argument("--source", default="manual-verify")
    parser.add_argument("--aspect-ratio", default="4:5")
    parser.add_argument("--size", default="4K")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--refs", nargs="*", default=[])
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parents[1] / "data" / "artifacts"))
    parser.add_argument("--perf-log-file", default=default_perf_log)
    args = parser.parse_args()

    adapter = build_adapter(args.provider)
    request = build_request(args)
    result = adapter.generate(request)
    result = write_result(args.output_dir, args.provider, request, result, perf_log_path=args.perf_log_file)
    print(json.dumps({
        "ok": result.ok,
        "provider": result.provider,
        "model": result.model,
        "artifact_paths": result.artifact_paths,
        "latency_ms": result.latency_ms,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "raw_response_path": result.raw_response_path,
        "reference_images": request.reference_images,
        "source": request.metadata.get("source"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
