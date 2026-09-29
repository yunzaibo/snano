"""Deterministic model selection from explicit, agent-supplied task intent."""
from dataclasses import dataclass

from router.core.models import GenerationRequest


MODEL_LANES = {
    "flare": "apiyi-simage-gpt-image-2-5-flare",
    "sunburst": "apiyi-simage-gpt-image-2-5-sunburst",
}
MODEL_IDS = {name: f"gpt-image-2.5-{name}-vip" for name in MODEL_LANES}
MODEL_CHOICES = ("auto", *MODEL_LANES, *MODEL_IDS.values())


@dataclass(frozen=True)
class ModelSelection:
    model: str
    provider: str
    reason: str


def select_simage_model(request: GenerationRequest, allowed: list[str]) -> ModelSelection:
    model = str(request.metadata.get("simageModel", "auto")).lower()
    intent = str(request.metadata.get("simageIntent", "auto")).lower()
    if model not in MODEL_CHOICES:
        raise ValueError("simageModel must be auto, flare, sunburst, or a supported full model ID")
    if intent not in {"auto", "speed", "precision"}:
        raise ValueError("simageIntent must be auto, speed, or precision")
    if not allowed or any(lane not in MODEL_LANES.values() for lane in allowed):
        raise ValueError("Simage model selection only accepts the configured GPT Image 2.5 lanes")

    if model != "auto":
        selected = next((name for name, full in MODEL_IDS.items() if full == model), model)
        reason = "explicit_model"
    elif len(set(allowed)) == 1:
        selected = next(name for name, lane in MODEL_LANES.items() if lane == allowed[0])
        reason = "explicit_provider"
    elif request.reference_images and intent == "precision":
        selected, reason = "sunburst", "reference_precision_edit"
    elif intent == "speed":
        selected, reason = "flare", "speed_preference"
    else:
        selected, reason = "flare", "default_generation"
    lane = MODEL_LANES[selected]
    if lane not in allowed:
        raise ValueError("The requested Simage model conflicts with the provider restriction")
    return ModelSelection(MODEL_IDS[selected], lane, reason)
