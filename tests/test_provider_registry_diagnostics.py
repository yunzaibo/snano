from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.models import GenerationRequest
from router.core.provider_registry import (
    build_adapter,
    provider_capability_registry,
    provider_pool_metadata_for,
    provider_startup_diagnostics,
)
from router.core.provider_contracts import check_provider_capability


class ProviderRegistryDiagnosticsTests(unittest.TestCase):
    def test_provider_defaults_do_not_include_secret_values(self) -> None:
        with patch.dict(os.environ, {"MIR_GPTGE_API_KEY": "super-secret-test-key"}, clear=False):
            diagnostics = provider_startup_diagnostics()

        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn("super-secret-test-key", serialized)
        self.assertTrue(any(row["provider"] == "gptge" and row["key_present"] for row in diagnostics))
        self.assertTrue(all("provider_group" in row for row in diagnostics))
        self.assertTrue(all("api_key_env" in row for row in diagnostics))

    def test_professional_group_providers_are_visible_to_diagnostics(self) -> None:
        diagnostics = provider_startup_diagnostics()
        providers = {row["provider"] for row in diagnostics}

        self.assertIn("laozhang", providers)
        self.assertIn("apiyi", providers)

    def test_relay_user_header_env_is_attached_without_exposing_secret(self) -> None:
        with patch.dict(os.environ, {
            "MIR_GPTGE_API_KEY": "test-key",
            "MIR_GPTGE_API_USER": "55582",
        }, clear=False):
            adapter = build_adapter("gptge")

        self.assertEqual(adapter.extra_headers, {"X-Api-User": "55582"})

    def test_vectorengine_uses_ai_base_url_and_user_header_env(self) -> None:
        with patch.dict(os.environ, {
            "MIR_VECTORENGINE_API_KEY": "test-key",
            "MIR_VECTORENGINE_API_USER": "320994",
        }, clear=False):
            adapter = build_adapter("vectorengine_compat")

        self.assertEqual(adapter.base_url, "https://api.vectorengine.ai")
        self.assertEqual(adapter.extra_headers, {"New-Api-User": "320994"})

    def test_lane_config_builds_adapter_without_exposing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  gptge-main:
    kind: relay
    baseUrl: https://api.gpt.ge
    vendorGroup: gptge-group
    sourceTier: daily
    enabled: true
accounts:
  gptge-primary:
    station: gptge-main
    enabled: true
credentials:
  gptge-key3:
    account: gptge-primary
    secretRef:
      type: env
      name: MIR_GPTGE_KEY3_API_KEY
    authScheme: bearer
    headers:
      X-Api-User:
        type: env
        name: MIR_GPTGE_API_USER
    enabled: true
routes:
  gptge-openai-images:
    station: gptge-main
    adapterFamily: openai_images
    path: /v1/images/generations
    enabled: true
lanes:
  gptge-key3-main-openai-images:
    provider: gptge
    providerGroup: gptge-group
    sourceTier: daily
    station: gptge-main
    account: gptge-primary
    credential: gptge-key3
    route: gptge-openai-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 1
    priority: 20
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_GPTGE_KEY3_API_KEY": "lane-secret",
                "MIR_GPTGE_API_USER": "55582",
            }, clear=False):
                diagnostics = provider_startup_diagnostics()
                registry = provider_capability_registry(["gptge-key3-main-openai-images"])
                pool_metadata = provider_pool_metadata_for("gptge-key3-main-openai-images")
                adapter = build_adapter("gptge-key3-main-openai-images")

        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn("lane-secret", serialized)
        self.assertTrue(any(row["provider"] == "gptge-key3-main-openai-images" and row["key_present"] for row in diagnostics))
        self.assertTrue(any(row["provider"] == "gptge-key3-main-openai-images" and row["source_tier"] == "daily" for row in diagnostics))
        lane_diag = next(row for row in diagnostics if row["provider"] == "gptge-key3-main-openai-images")
        self.assertEqual(lane_diag["station_id"], "gptge-main")
        self.assertEqual(lane_diag["account_id"], "gptge-primary")
        self.assertEqual(lane_diag["credential_id"], "gptge-key3")
        self.assertEqual(lane_diag["route_id"], "gptge-openai-images")
        self.assertEqual(lane_diag["pool_identity"], "gptge-group/gptge-main/gptge-primary")
        self.assertEqual(pool_metadata["pool_identity"], "gptge-group/gptge-main/gptge-primary")
        self.assertEqual(pool_metadata["credential_id"], "gptge-key3")
        self.assertEqual(registry["gptge-key3-main-openai-images"].provider, "gptge-key3-main-openai-images")
        self.assertEqual(registry["gptge-key3-main-openai-images"].max_reference_images, 0)
        self.assertEqual(adapter.provider_name, "gptge-key3-main-openai-images")
        self.assertEqual(adapter.base_url, "https://api.gpt.ge")
        self.assertEqual(adapter.extra_headers, {"X-Api-User": "55582"})
        self.assertEqual(adapter.endpoint_path, "/v1/images/generations")
        self.assertEqual(adapter._endpoint_url(), "https://api.gpt.ge/v1/images/generations")

    def test_lane_pool_identity_can_use_account_group_or_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  gptge-main:
    kind: relay
    baseUrl: https://api.gpt.ge
    vendorGroup: gptge-group
accounts:
  gptge-primary:
    station: gptge-main
    accountGroup: paid-pool-a
credentials:
  gptge-key3:
    account: gptge-primary
    secretRef:
      type: env
      name: MIR_GPTGE_KEY3_API_KEY
routes:
  gptge-openai-images:
    station: gptge-main
    adapterFamily: openai_images
    path: /v1/images/generations
lanes:
  gptge-key3-main-openai-images:
    provider: gptge
    providerGroup: gptge-group
    station: gptge-main
    account: gptge-primary
    credential: gptge-key3
    route: gptge-openai-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    enabled: true
  gptge-key3-dedicated-route:
    provider: gptge
    providerGroup: gptge-group
    station: gptge-main
    account: gptge-primary
    credential: gptge-key3
    route: gptge-openai-images
    poolIdentity: gptge-group/gptge-main/key3-dedicated-route
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_GPTGE_KEY3_API_KEY": "lane-secret",
            }, clear=False):
                diagnostics = provider_startup_diagnostics()
                grouped = provider_pool_metadata_for("gptge-key3-main-openai-images")
                explicit = provider_pool_metadata_for("gptge-key3-dedicated-route")

        rows = {row["provider"]: row for row in diagnostics}
        self.assertEqual(grouped["account_group"], "paid-pool-a")
        self.assertEqual(grouped["pool_identity"], "gptge-group/gptge-main/paid-pool-a")
        self.assertEqual(rows["gptge-key3-main-openai-images"]["account_group"], "paid-pool-a")
        self.assertEqual(rows["gptge-key3-main-openai-images"]["pool_identity"], "gptge-group/gptge-main/paid-pool-a")
        self.assertEqual(explicit["pool_identity"], "gptge-group/gptge-main/key3-dedicated-route")

    def test_lane_route_path_overrides_openai_images_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  gptge-main:
    kind: relay
    baseUrl: https://api.gpt.ge
    vendorGroup: gptge-group
routes:
  gptge-custom-images:
    station: gptge-main
    adapterFamily: openai_images
    path: custom/images/generations
credentials:
  gptge-key3:
    secretRef:
      type: env
      name: MIR_GPTGE_KEY3_API_KEY
lanes:
  gptge-key3-custom-images:
    provider: gptge
    providerGroup: gptge-group
    credential: gptge-key3
    route: gptge-custom-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_GPTGE_KEY3_API_KEY": "lane-secret",
            }, clear=False):
                adapter = build_adapter("gptge-key3-custom-images")

        self.assertEqual(adapter.endpoint_path, "custom/images/generations")
        self.assertEqual(adapter._endpoint_url(), "https://api.gpt.ge/custom/images/generations")

    def test_vectorengine_lane_can_use_openai_images_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  vectorengine-main:
    kind: relay
    baseUrl: https://api.vectorengine.ai
    vendorGroup: vectorengine-group
routes:
  vectorengine-openai-images:
    station: vectorengine-main
    adapterFamily: openai_images
    path: /v1/images/generations
credentials:
  vectorengine-key:
    secretRef:
      type: env
      name: MIR_VECTORENGINE_TEST_API_KEY
    headers:
      New-Api-User:
        type: env
        name: MIR_VECTORENGINE_API_USER
lanes:
  vectorengine-compat-main:
    provider: vectorengine_compat
    providerGroup: vectorengine-group
    station: vectorengine-main
    credential: vectorengine-key
    route: vectorengine-openai-images
    model: gemini-3.1-flash-image-preview
    capabilityBucket: T
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_VECTORENGINE_TEST_API_KEY": "lane-secret",
                "MIR_VECTORENGINE_API_USER": "320994",
            }, clear=False):
                adapter = build_adapter("vectorengine-compat-main")

        self.assertEqual(adapter.provider_name, "vectorengine-compat-main")
        self.assertEqual(adapter.base_url, "https://api.vectorengine.ai")
        self.assertEqual(adapter.endpoint_path, "/v1/images/generations")
        self.assertEqual(adapter._endpoint_url(), "https://api.vectorengine.ai/v1/images/generations")
        self.assertEqual(adapter.extra_headers, {"New-Api-User": "320994"})

    def test_professional_i2i_lane_contracts_are_provider_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            image_path = Path(tmp) / "ref.jpg"
            image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  laozhang-main:
    kind: relay
    baseUrl: https://api.laozhang.ai
    vendorGroup: laozhang-group
    sourceTier: professional
  rightcodes-main:
    kind: relay
    baseUrl: https://www.right.codes/draw
    vendorGroup: rightcodes-group
    sourceTier: professional
  apimart-main:
    kind: relay
    baseUrl: https://api.apimart.ai
    vendorGroup: apimart-group
    sourceTier: professional
credentials:
  laozhang-key:
    secretRef:
      type: env
      name: MIR_LAOZHANG_API_KEY
  rightcodes-key:
    secretRef:
      type: env
      name: MIR_RIGHTCODES_API_KEY
  apimart-key:
    secretRef:
      type: env
      name: MIR_APIMART_API_KEY
routes:
  laozhang-gemini-native:
    station: laozhang-main
    adapterFamily: gemini_generate_content
    path: /v1beta/models/{model}:generateContent
  rightcodes-openai-images:
    station: rightcodes-main
    adapterFamily: openai_images
    path: /v1/images/generations
  apimart-openai-images:
    station: apimart-main
    adapterFamily: openai_images
    path: /v1/images/generations
lanes:
  laozhang-nano-banana-pro-4k-i2i:
    provider: laozhang
    providerGroup: laozhang-group
    station: laozhang-main
    credential: laozhang-key
    route: laozhang-gemini-native
    model: gemini-3-pro-image-preview
    capabilityBucket: IM
    maxReferenceImages: 14
    enabled: true
  rightcodes-nano-banana-pro-i2i:
    provider: rightcodes
    providerGroup: rightcodes-group
    station: rightcodes-main
    credential: rightcodes-key
    route: rightcodes-openai-images
    model: nano-banana-pro
    imageContract:
      requestSizeMode: passthrough
      defaultSize: 1024x1024
      responseFormat: url
      imageUrlsField: image
    capabilityBucket: I1
    maxReferenceImages: 1
    enabled: true
  apimart-gemini-i2i:
    provider: apimart
    providerGroup: apimart-group
    station: apimart-main
    credential: apimart-key
    route: apimart-openai-images
    model: gemini-3-pro-image-preview
    imageContract:
      requestSizeMode: aspect_ratio
      defaultSize: 1:1
      defaultResolution: 4K
      pollPath: /v1/tasks/{task_id}
      pollQuery: language=zh
      imageUrlsField: image_urls
    asyncRemote: true
    capabilityBucket: IM
    maxReferenceImages: 14
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_LAOZHANG_API_KEY": "lane-secret",
                "MIR_RIGHTCODES_API_KEY": "lane-secret",
                "MIR_APIMART_API_KEY": "lane-secret",
            }, clear=False):
                registry = provider_capability_registry([
                    "laozhang-nano-banana-pro-4k-i2i",
                    "rightcodes-nano-banana-pro-i2i",
                    "apimart-gemini-i2i",
                ])
                laozhang = build_adapter("laozhang-nano-banana-pro-4k-i2i")
                rightcodes = build_adapter("rightcodes-nano-banana-pro-i2i")
                apimart = build_adapter("apimart-gemini-i2i")

                request = GenerationRequest(
                    job_type="manifest",
                    profile="test",
                    prompt="edit",
                    request_type="image_to_image",
                    reference_images=[str(image_path)],
                    aspect_ratio="1:1",
                    size="4K",
                )
                laozhang_body = laozhang.build_request(request)
                rightcodes_body = rightcodes.build_request(request)
                apimart_body = apimart.build_request(request)

        self.assertEqual(registry["laozhang-nano-banana-pro-4k-i2i"].max_reference_images, 14)
        self.assertEqual(registry["rightcodes-nano-banana-pro-i2i"].max_reference_images, 1)
        self.assertEqual(registry["apimart-gemini-i2i"].max_reference_images, 14)
        self.assertIn("contents", laozhang_body)
        self.assertEqual(
            laozhang_body["generationConfig"]["imageConfig"],
            {"aspectRatio": "1:1", "imageSize": "4K"},
        )
        self.assertIn("inlineData", laozhang_body["contents"][0]["parts"][1])
        self.assertIn("image", rightcodes_body)
        self.assertNotIn("image_url", rightcodes_body)
        self.assertIn("image_urls", apimart_body)
        self.assertEqual(apimart_body["resolution"], "4K")

    def test_apimart_enters_full_lock_only_when_source_contract_supports_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  apimart-main:
    kind: relay
    baseUrl: https://api.apimart.ai
    vendorGroup: apimart-group
    sourceTier: professional
credentials:
  apimart-key:
    secretRef:
      type: env
      name: MIR_APIMART_API_KEY
routes:
  apimart-openai-images:
    station: apimart-main
    adapterFamily: openai_images
    path: /v1/images/generations
lanes:
  apimart-gemini-single-ref:
    provider: apimart
    providerGroup: apimart-group
    station: apimart-main
    credential: apimart-key
    route: apimart-openai-images
    model: gemini-3-pro-image-preview
    imageContract:
      requestSizeMode: aspect_ratio
      defaultSize: 1:1
      defaultResolution: 4K
      imageUrlsField: image
    capabilityBucket: I1
    maxReferenceImages: 1
    enabled: true
  apimart-gemini-i2i:
    provider: apimart
    providerGroup: apimart-group
    station: apimart-main
    credential: apimart-key
    route: apimart-openai-images
    model: gemini-3-pro-image-preview
    imageContract:
      requestSizeMode: aspect_ratio
      defaultSize: 1:1
      defaultResolution: 4K
      imageUrlsField: image_urls
    capabilityBucket: IM
    maxReferenceImages: 14
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_APIMART_API_KEY": "lane-secret",
            }, clear=False):
                diagnostics = {
                    row["provider"]: row
                    for row in provider_startup_diagnostics()
                    if row.get("provider") in {"apimart-gemini-single-ref", "apimart-gemini-i2i"}
                }
                registry = provider_capability_registry([
                    "apimart-gemini-single-ref",
                    "apimart-gemini-i2i",
                ])

        request = GenerationRequest(
            job_type="manifest",
            profile="test",
            prompt="edit",
            request_type="image_to_image",
            reference_images=["1.png", "2.png", "3.png", "4.png", "5.png"],
            metadata={"requireFullReferenceLock": True},
        )

        single_contract = diagnostics["apimart-gemini-single-ref"]["image_contract"]
        full_lock_contract = diagnostics["apimart-gemini-i2i"]["image_contract"]

        self.assertEqual(single_contract["imageUrlsField"], "image")
        self.assertEqual(diagnostics["apimart-gemini-single-ref"]["max_reference_images"], 1)
        self.assertFalse(check_provider_capability(registry["apimart-gemini-single-ref"], request).supported)

        self.assertEqual(full_lock_contract["imageUrlsField"], "image_urls")
        self.assertGreaterEqual(diagnostics["apimart-gemini-i2i"]["max_reference_images"], 5)
        self.assertTrue(check_provider_capability(registry["apimart-gemini-i2i"], request).supported)

    def test_internal_snano_lanes_have_ninety_slot_batch_capacity(self) -> None:
        config = Path("configs/sources.shared.example.yaml").resolve()
        with patch.dict(os.environ, {
            "MIR_SOURCES_CONFIG_FILE": str(config),
            "MIR_APIYI_API_KEY": "lane-secret",
            "MIR_LAOZHANG_API_KEY": "lane-secret",
            "MIR_APIMART_API_KEY": "lane-secret",
        }, clear=False):
            registry = provider_capability_registry([
                "apiyi-nano-banana-pro-4k",
                "laozhang-nano-banana-pro-4k-i2i",
                "apimart-gemini-i2i",
            ])
            adapters = {
                name: build_adapter(name)
                for name in [
                    "apiyi-nano-banana-pro-4k",
                    "laozhang-nano-banana-pro-4k-i2i",
                    "apimart-gemini-i2i",
                ]
            }

        self.assertEqual(
            {name: cap.max_concurrency for name, cap in registry.items()},
            {
                "apiyi-nano-banana-pro-4k": 30,
                "laozhang-nano-banana-pro-4k-i2i": 30,
                "apimart-gemini-i2i": 30,
            },
        )
        self.assertEqual(sum(cap.max_concurrency for cap in registry.values()), 90)
        self.assertEqual(
            {name: adapter.capability().max_concurrency for name, adapter in adapters.items()},
            {
                "apiyi-nano-banana-pro-4k": 30,
                "laozhang-nano-banana-pro-4k-i2i": 30,
                "apimart-gemini-i2i": 30,
            },
        )
        self.assertEqual(adapters["apiyi-nano-banana-pro-4k"].preflight_timeout_seconds, 8.0)
        self.assertEqual(adapters["laozhang-nano-banana-pro-4k-i2i"].preflight_timeout_seconds, 8.0)
        self.assertEqual(adapters["apimart-gemini-i2i"]._poll_deadline_seconds(), 150)

    def test_lane_route_path_template_overrides_gemini_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  aifast-vip:
    kind: relay
    baseUrl: https://chat.aifast.site/v1
    vendorGroup: aifast-group
routes:
  aifast-vip-gemini:
    station: aifast-vip
    adapterFamily: gemini_native
    baseUrl: https://chat.aifast.site
    path: /custom/v1beta/models/{model}:generateContent
credentials:
  aifast-key1:
    secretRef:
      type: env
      name: MIR_AIFAST_KEY1_API_KEY
lanes:
  aifast-key1-vip-gemini:
    provider: aifast
    providerGroup: aifast-group
    credential: aifast-key1
    route: aifast-vip-gemini
    model: gemini-custom-image
    capabilityBucket: IM
    enabled: true
""", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_AIFAST_KEY1_API_KEY": "lane-secret",
            }, clear=False):
                adapter = build_adapter("aifast-key1-vip-gemini")

        self.assertEqual(adapter.endpoint_path, "/custom/v1beta/models/{model}:generateContent")
        self.assertEqual(
            adapter._endpoint_url(),
            "https://chat.aifast.site/custom/v1beta/models/gemini-custom-image:generateContent",
        )

    def test_internal_professional_expansion_lanes_build_adapters(self) -> None:
        config = Path("configs/sources.shared.example.yaml").resolve()

        with patch.dict(os.environ, {
            "MIR_APIYI_API_KEY": "apiyi-secret",
            "MIR_APIYI_SIMAGE_API_KEY": "apiyi-simage-secret",
            "MIR_LAOZHANG_API_KEY": "laozhang-secret",
            "MIR_SOURCES_CONFIG_FILE": str(config),
            "MIR_RIGHTCODES_API_KEY": "rightcodes-secret",
            "MIR_RIGHTCODES_GEMINI_API_KEY": "rightcodes-gemini-secret",
            "MIR_APIMART_API_KEY": "apimart-secret",
        }, clear=False):
            diagnostics = provider_startup_diagnostics()
            rightcodes = build_adapter("rightcodes-gemini-images")
            rightcodes_native = build_adapter("rightcodes-gemini-native")
            rightcodes_images = build_adapter("rightcodes-gpt-image-2-images")
            apimart = build_adapter("apimart-gpt-image-2-images")
            apiyi_snano = build_adapter("apiyi-nano-banana-pro-4k")
            apiyi_simage = build_adapter("apiyi-simage-gpt-image-2")
            laozhang_simage = build_adapter("laozhang-simage-gpt-image-2")
            apimart_snano = build_adapter("apimart-gemini-images")

        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn("apiyi-secret", serialized)
        self.assertNotIn("apiyi-simage-secret", serialized)
        self.assertNotIn("laozhang-secret", serialized)
        self.assertNotIn("rightcodes-secret", serialized)
        self.assertNotIn("rightcodes-gemini-secret", serialized)
        self.assertNotIn("apimart-secret", serialized)
        rows = {row["provider"]: row for row in diagnostics}
        self.assertEqual(rows["rightcodes-gemini-native"]["source_tier"], "professional")
        self.assertEqual(rows["rightcodes-gemini-native"]["api_key_env"], "MIR_RIGHTCODES_GEMINI_API_KEY")
        self.assertEqual(rows["rightcodes-gemini-native"]["route_path"], "/v1beta/models/{model}:generateContent")
        self.assertFalse(rows["rightcodes-gemini-native"]["enabled"])
        self.assertEqual(rows["rightcodes-gemini-images"]["source_tier"], "professional")
        self.assertEqual(rows["rightcodes-gemini-images"]["api_key_env"], "MIR_RIGHTCODES_API_KEY")
        self.assertEqual(rows["rightcodes-gemini-images"]["route_path"], "/v1/images/generations")
        self.assertTrue(rows["rightcodes-gemini-images"]["enabled"])
        self.assertEqual(rows["rightcodes-gemini-images"]["model"], "nano-banana-pro")
        self.assertFalse(rows["rightcodes-gemini-chat"]["enabled"])
        self.assertEqual(rows["rightcodes-gpt-image-2-images"]["route_path"], "/v1/images/generations")
        self.assertEqual(rows["rightcodes-gpt-image-2-images"]["image_contract"]["imageUrlsField"], "image")
        self.assertEqual(rows["apiyi-nano-banana-pro-4k"]["source_tier"], "professional")
        self.assertEqual(rows["apiyi-nano-banana-pro-4k"]["api_key_env"], "MIR_APIYI_API_KEY")
        self.assertEqual(rows["apiyi-nano-banana-pro-4k"]["base_url"], "https://api.apiyi.com")
        self.assertEqual(rows["apiyi-nano-banana-pro-4k"]["route_path"], "/v1beta/models/{model}:generateContent")
        self.assertEqual(rows["apiyi-nano-banana-pro-4k"]["model"], "gemini-3-pro-image-preview")
        self.assertEqual(rows["apiyi-nano-banana-pro-4k"]["capability_bucket"], "IM")
        self.assertTrue(rows["apiyi-simage-gpt-image-2"]["enabled"])
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["source_tier"], "professional")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["api_key_env"], "MIR_APIYI_SIMAGE_API_KEY")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["route_path"], "/images/edits")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["model"], "gpt-image-2")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["capability_bucket"], "IM")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["max_reference_images"], 16)
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["image_contract"]["textToImagePath"], "/images/generations")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["image_contract"]["requestFormat"], "multipart")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["image_contract"]["referenceField"], "image")
        self.assertEqual(rows["apiyi-simage-gpt-image-2"]["image_contract"]["suppressFields"], ["aspect_ratio"])
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["source_tier"], "professional")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["api_key_env"], "MIR_LAOZHANG_API_KEY")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["route_path"], "/images/edits")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["model"], "gpt-image-2")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["capability_bucket"], "IM")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["max_reference_images"], 5)
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["image_contract"]["textToImagePath"], "/images/generations")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["image_contract"]["requestFormat"], "multipart")
        self.assertEqual(rows["laozhang-simage-gpt-image-2"]["image_contract"]["referenceField"], "image")
        self.assertEqual(
            rows["laozhang-simage-gpt-image-2"]["image_contract"]["suppressFields"],
            ["resolution", "aspect_ratio"],
        )
        self.assertEqual(rows["apimart-gpt-image-2-images"]["source_tier"], "professional")
        self.assertEqual(rows["apimart-gpt-image-2-images"]["api_key_env"], "MIR_APIMART_API_KEY")
        self.assertEqual(rows["apimart-gpt-image-2-images"]["route_path"], "/v1/images/generations")
        self.assertEqual(rows["apimart-gpt-image-2-images"]["image_contract"]["defaultResolution"], "2k")
        self.assertEqual(rows["apimart-gpt-image-2-images"]["image_contract"]["pollQuery"], "language=zh")
        self.assertEqual(rows["apimart-gpt-image-2-images"]["image_contract"]["pollInitialDelaySeconds"], 15)
        self.assertEqual(rows["apimart-gpt-image-2-i2i"]["capability_bucket"], "IM")
        self.assertEqual(rows["apimart-gpt-image-2-i2i"]["max_reference_images"], 16)
        self.assertEqual(rows["apimart-gpt-image-2-i2i"]["image_contract"]["defaultSize"], "1:1")
        self.assertEqual(rows["apimart-gpt-image-2-i2i"]["image_contract"]["pollQuery"], "language=zh")
        self.assertEqual(rows["apimart-gpt-image-2-i2i"]["image_contract"]["pollInitialDelaySeconds"], 15)
        self.assertEqual(rows["apimart-gemini-images"]["source_tier"], "professional")
        self.assertEqual(rows["apimart-gemini-images"]["api_key_env"], "MIR_APIMART_API_KEY")
        self.assertEqual(rows["apimart-gemini-images"]["route_path"], "/v1/images/generations")
        self.assertEqual(rows["apimart-gemini-images"]["model"], "gemini-3-pro-image-preview")
        self.assertEqual(rows["apimart-gemini-images"]["image_contract"]["defaultResolution"], "2K")
        self.assertEqual(apiyi_snano.provider_name, "apiyi-nano-banana-pro-4k")
        self.assertEqual(apiyi_snano.base_url, "https://api.apiyi.com")
        self.assertEqual(apiyi_snano.endpoint_path, "/v1beta/models/{model}:generateContent")
        self.assertEqual(
            apiyi_snano._endpoint_url(),
            "https://api.apiyi.com/v1beta/models/gemini-3-pro-image-preview:generateContent",
        )
        self.assertEqual(apiyi_snano.model_name, "gemini-3-pro-image-preview")
        self.assertEqual(apiyi_simage.provider_name, "apiyi-simage-gpt-image-2")
        self.assertEqual(apiyi_simage.model_name, "gpt-image-2")
        self.assertEqual(laozhang_simage.provider_name, "laozhang-simage-gpt-image-2")
        self.assertEqual(laozhang_simage.model_name, "gpt-image-2")
        self.assertEqual(laozhang_simage.endpoint_path, "/images/edits")
        self.assertEqual(rightcodes.provider_name, "rightcodes-gemini-images")
        self.assertEqual(rightcodes.base_url, "https://www.right.codes/draw")
        self.assertEqual(rightcodes.endpoint_path, "/v1/images/generations")
        self.assertEqual(rightcodes._endpoint_url(), "https://www.right.codes/draw/v1/images/generations")
        self.assertEqual(rightcodes.model_name, "nano-banana-pro")
        self.assertEqual(rightcodes_native.provider_name, "rightcodes-gemini-native")
        self.assertEqual(rightcodes_native.base_url, "https://right.codes/gemini")
        self.assertEqual(rightcodes_images.provider_name, "rightcodes-gpt-image-2-images")
        self.assertEqual(rightcodes_images.base_url, "https://www.right.codes/draw")
        self.assertEqual(rightcodes_images.endpoint_path, "/v1/images/generations")
        self.assertEqual(rightcodes_images._endpoint_url(), "https://www.right.codes/draw/v1/images/generations")
        self.assertEqual(rightcodes_images.model_name, "gpt-image-2")
        self.assertEqual(apimart.provider_name, "apimart-gpt-image-2-images")
        self.assertEqual(apimart.base_url, "https://api.apimart.ai")
        self.assertEqual(apimart.endpoint_path, "/v1/images/generations")
        self.assertEqual(apimart._endpoint_url(), "https://api.apimart.ai/v1/images/generations")
        self.assertEqual(apimart.model_name, "gpt-image-2")
        self.assertEqual(apimart_snano.provider_name, "apimart-gemini-images")
        self.assertEqual(apimart_snano.base_url, "https://api.apimart.ai")
        self.assertEqual(apimart_snano.endpoint_path, "/v1/images/generations")
        self.assertEqual(apimart_snano._endpoint_url(), "https://api.apimart.ai/v1/images/generations")
        self.assertEqual(apimart_snano.model_name, "gemini-3-pro-image-preview")

    def test_apiyi_simage_image2_oss_lane_is_schedulable_when_requested(self) -> None:
        config = Path("configs/sources.shared.example.yaml").resolve()
        with patch.dict(os.environ, {
            "MIR_SOURCES_CONFIG_FILE": str(config),
            "MIR_APIYI_SIMAGE_API_KEY": "secret-apiyi-simage-key",
        }, clear=False):
            registry = provider_capability_registry(["apiyi-simage-gpt-image-2"])

        self.assertIn("apiyi-simage-gpt-image-2", registry)
        self.assertTrue(registry["apiyi-simage-gpt-image-2"].enabled)


if __name__ == "__main__":
    unittest.main()
