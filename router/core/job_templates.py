from __future__ import annotations

from copy import deepcopy
from typing import Any


TEMPLATES: dict[str, dict[str, Any]] = {
    "text-to-image": {
        "id": "text-to-image",
        "label": "Text to image",
        "requestType": "text_to_image",
        "referenceImages": [],
    },
    "single-reference": {
        "id": "single-reference",
        "label": "Single reference image to image",
        "requestType": "image_to_image",
        "referenceImages": ["<reference-image>"],
    },
    "multi-reference": {
        "id": "multi-reference",
        "label": "Multi reference image to image",
        "requestType": "image_to_image",
        "referenceImages": ["<reference-image-1>", "<reference-image-2>"],
    },
}


def list_job_templates() -> list[dict[str, Any]]:
    return [deepcopy(template) for template in TEMPLATES.values()]


def build_job_from_template(
    template_id: str,
    *,
    prompt: str,
    reference_images: list[str] | None = None,
    routing_policy: str = "fallback",
    item_id: str = "item-001",
) -> dict[str, Any]:
    if template_id not in TEMPLATES:
        raise KeyError(f"Unknown job template: {template_id}")
    template = TEMPLATES[template_id]
    refs = list(reference_images if reference_images is not None else template["referenceImages"])
    return {
        "jobType": "manifest",
        "items": [
            {
                "id": item_id,
                "requestType": template["requestType"],
                "prompt": prompt,
                "referenceImages": refs,
                "routingPolicy": routing_policy,
            }
        ],
    }
