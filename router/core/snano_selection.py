"""Deterministic model-variant selection for the Gemini/Nano Banana skill."""
from dataclasses import dataclass

from router.core.models import GenerationRequest


MODEL_LANES = {
    "nano-banana-pro": "apiyi-snano",
    "gemini-3-pro-image": "apiyi-snano-gemini-3-pro-image",
    "gemini-3-pro-image-preview": "apiyi-snano-gemini-3-pro-image-preview",
    "gemini-3-pro-image-preview-c": "apiyi-snano-gemini-3-pro-image-preview-c",
}
MODEL_CHOICES = ("auto", *MODEL_LANES)
NATIVE_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}


def validate_native_dimensions(request: GenerationRequest) -> None:
    if str(request.size or "2K").upper() not in {"1K", "2K", "4K"}:
        raise ValueError("Gemini 原生接口请使用 1K、2K、4K，并单独指定比例；不能直接传像素尺寸")
    if request.aspect_ratio and request.aspect_ratio.lower() != "auto" and request.aspect_ratio not in NATIVE_RATIOS:
        raise ValueError("Gemini 原生接口不支持此比例")


@dataclass(frozen=True)
class SnanoModelSelection:
    model: str
    provider: str
    reason: str


def select_snano_model(request: GenerationRequest, allowed: list[str]) -> SnanoModelSelection:
    model = str(request.metadata.get("snanoModel", "auto")).lower()
    intent = str(request.metadata.get("snanoIntent", "auto")).lower()
    if model not in MODEL_CHOICES:
        raise ValueError("snanoModel must be auto or a configured Gemini/Nano Banana model ID")
    if intent not in {"auto", "speed", "professional"}:
        raise ValueError("snanoIntent must be auto, speed, or professional")
    if not allowed or any(lane not in MODEL_LANES.values() for lane in allowed):
        raise ValueError("Snano model selection only accepts configured Gemini/Nano Banana lanes")

    if model != "auto":
        selected = model
        reason = "explicit_model"
    elif len(set(allowed)) == 1:
        selected = next(name for name, lane in MODEL_LANES.items() if lane == allowed[0])
        reason = "explicit_provider"
    elif intent == "professional":
        selected = "gemini-3-pro-image"
        reason = "professional_intent"
    else:
        selected = "nano-banana-pro"
        reason = "default_generation" if intent == "auto" else "compatibility_preference"

    provider = MODEL_LANES[selected]
    if provider not in allowed:
        raise ValueError("The requested Snano model conflicts with the provider restriction")
    if selected != "nano-banana-pro":
        validate_native_dimensions(request)
    return SnanoModelSelection(model=selected, provider=provider, reason=reason)
