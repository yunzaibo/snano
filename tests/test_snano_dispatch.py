from __future__ import annotations

import unittest

from router.snano_dispatch import (
    DEFAULT_SIMAGE_PROVIDERS,
    DEFAULT_SNANO_PROVIDERS,
    apply_dimension_defaults_to_items,
    assign_items_evenly,
    build_execution_rounds,
    chunk_provider_items,
    default_skill_items_from_prompt,
    default_items_from_prompt,
    infer_image_count_from_prompt,
    max_workers_for_batch,
    normalize_image_size,
)


class SnanoDispatchTests(unittest.TestCase):
    def test_prompt_count_inference_supports_chinese_and_english_without_matching_dimensions(self) -> None:
        self.assertEqual(infer_image_count_from_prompt("生成3张产品图，尺寸2048×2048"), 3)
        self.assertEqual(infer_image_count_from_prompt("生成三张图片"), 3)
        self.assertEqual(infer_image_count_from_prompt("create 4 images"), 4)
        self.assertIsNone(infer_image_count_from_prompt("生成2048×2048的图片"))

    def test_normalize_image_size_accepts_quality_or_explicit_dimensions(self) -> None:
        self.assertEqual(normalize_image_size("1K"), "1K")
        self.assertEqual(normalize_image_size("2048×2048"), "2048x2048")
        with self.assertRaises(ValueError):
            normalize_image_size("800p")

    def test_skill_prompt_without_references_builds_text_to_image_for_both_lines(self) -> None:
        for skill, providers in {
            "snano": DEFAULT_SNANO_PROVIDERS,
            "simage": DEFAULT_SIMAGE_PROVIDERS,
        }.items():
            with self.subTest(skill=skill):
                items = default_skill_items_from_prompt(
                    skill=skill,
                    prompt="create a new poster",
                    count=3,
                )
                assigned = assign_items_evenly(items, providers)

                self.assertEqual([item["requestType"] for item in items], ["text_to_image"] * 3)
                self.assertEqual([item["referenceImages"] for item in items], [[], [], []])
                self.assertEqual(items[0]["metadata"]["source"], f"{skill}-skill")
                self.assertEqual(set(assigned), set(providers))

    def test_skill_prompt_without_count_builds_single_item_for_both_lines(self) -> None:
        for skill in ("snano", "simage"):
            with self.subTest(skill=skill):
                items = default_skill_items_from_prompt(
                    skill=skill,
                    prompt="create a new poster",
                )

                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["metadata"]["variantTotal"], 1)

    def test_skill_prompt_explicit_count_is_used_without_cli_count(self) -> None:
        items = default_skill_items_from_prompt(
            skill="simage",
            prompt="生成三张商品图",
        )
        self.assertEqual(len(items), 3)

    def test_skill_prompt_with_references_builds_image_to_image_for_both_lines(self) -> None:
        refs = ["/tmp/ref-a.jpg", "/tmp/ref-b.jpg"]
        for skill, providers in {
            "snano": DEFAULT_SNANO_PROVIDERS,
            "simage": DEFAULT_SIMAGE_PROVIDERS,
        }.items():
            with self.subTest(skill=skill):
                items = default_skill_items_from_prompt(
                    skill=skill,
                    prompt="keep subject locked",
                    references=refs,
                    per_provider=1,
                )
                assigned = assign_items_evenly(items, providers)

                self.assertEqual(len(items), len(providers))
                self.assertEqual([item["requestType"] for item in items], ["image_to_image"] * len(providers))
                self.assertEqual([item["referenceImages"] for item in items], [refs] * len(providers))
                self.assertTrue(all(item["metadata"]["requireFullReferenceLock"] for item in items))
                self.assertEqual({provider: len(rows) for provider, rows in assigned.items()}, {provider: 1 for provider in providers})

    def test_default_prompt_without_count_builds_single_item(self) -> None:
        items = default_items_from_prompt(
            prompt="keep product locked",
            reference="/tmp/ref.jpg",
            providers=DEFAULT_SNANO_PROVIDERS,
        )
        assigned = assign_items_evenly(items, DEFAULT_SNANO_PROVIDERS)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            {provider: len(rows) for provider, rows in assigned.items()},
            {
                "apiyi-nano-banana-pro-4k": 1,
                "laozhang-nano-banana-pro-4k-i2i": 0,
                "apimart-gemini-i2i": 0,
            },
        )

    def test_per_provider_compatibility_expands_when_explicit(self) -> None:
        items = default_items_from_prompt(
            prompt="keep product locked",
            reference="/tmp/ref.jpg",
            per_provider=5,
            providers=DEFAULT_SNANO_PROVIDERS,
        )

        self.assertEqual(len(items), 15)

    def test_distribution_is_even_for_arbitrary_manifest_items(self) -> None:
        items = [{"id": f"item-{idx:02d}", "prompt": "p"} for idx in range(12)]
        assigned = assign_items_evenly(items, DEFAULT_SNANO_PROVIDERS)

        self.assertEqual(len(assigned["apiyi-nano-banana-pro-4k"]), 4)
        self.assertEqual(len(assigned["laozhang-nano-banana-pro-4k-i2i"]), 4)
        self.assertEqual(len(assigned["apimart-gemini-i2i"]), 4)

    def test_total_count_distributes_across_verified_providers(self) -> None:
        cases = {
            7: [3, 2, 2],
            8: [3, 3, 2],
            10: [4, 3, 3],
            20: [7, 7, 6],
            23: [8, 8, 7],
        }

        for total, expected_counts in cases.items():
            with self.subTest(total=total):
                items = default_items_from_prompt(
                    prompt="keep product locked",
                    reference="/tmp/ref.jpg",
                    count=total,
                    providers=DEFAULT_SNANO_PROVIDERS,
                )
                assigned = assign_items_evenly(items, DEFAULT_SNANO_PROVIDERS)

                self.assertEqual(
                    [len(assigned[provider]) for provider in DEFAULT_SNANO_PROVIDERS],
                    expected_counts,
                )

    def test_default_prompt_items_do_not_force_square_aspect_ratio(self) -> None:
        items = default_items_from_prompt(
            prompt="keep product locked",
            reference="/tmp/ref.jpg",
            count=1,
            providers=DEFAULT_SNANO_PROVIDERS,
        )

        self.assertEqual(items[0]["size"], "2K")
        self.assertNotIn("aspectRatio", items[0])
        self.assertEqual(items[0]["metadata"]["artifactMinWidth"], 2048)
        self.assertEqual(items[0]["metadata"]["artifactMinHeight"], 2048)
        self.assertEqual(items[0]["metadata"]["artifactDimensionMode"], "long_edge")
        self.assertFalse(items[0]["metadata"]["artifactAspectStrict"])
        self.assertEqual(items[0]["metadata"]["artifactMinShortEdgeMode"], "soft")

    def test_dimension_defaults_use_task_then_cli_then_project_then_global(self) -> None:
        profiles = {
            "defaults": {
                "size": "2K",
                "minLongEdge": 2048,
                "aspectRatio": "auto",
                "aspectStrict": False,
            },
            "projectProfiles": {
                "amazoncar": {
                    "aspectRatio": "4:3",
                    "aspectStrict": True,
                }
            },
        }

        project_items = default_items_from_prompt(
            prompt="keep product locked",
            reference="/tmp/ref.jpg",
            count=1,
            providers=DEFAULT_SNANO_PROVIDERS,
            project_profile="amazoncar",
            dimension_profiles=profiles,
        )
        self.assertEqual(project_items[0]["aspectRatio"], "4:3")
        self.assertTrue(project_items[0]["metadata"]["artifactAspectStrict"])

        cli_items = default_items_from_prompt(
            prompt="keep product locked",
            reference="/tmp/ref.jpg",
            count=1,
            providers=DEFAULT_SNANO_PROVIDERS,
            project_profile="amazoncar",
            aspect_ratio="16:9",
            dimension_profiles=profiles,
        )
        self.assertEqual(cli_items[0]["aspectRatio"], "16:9")
        self.assertTrue(cli_items[0]["metadata"]["artifactAspectStrict"])

        manifest_items = apply_dimension_defaults_to_items(
            [{"id": "one", "prompt": "p", "size": "2K", "aspectRatio": "1:1"}],
            project_profile="amazoncar",
            aspect_ratio="16:9",
            size="4K",
            dimension_profiles=profiles,
        )
        self.assertEqual(manifest_items[0]["size"], "2K")
        self.assertEqual(manifest_items[0]["aspectRatio"], "1:1")

    def test_project_profile_makes_existing_task_aspect_ratio_strict(self) -> None:
        profiles = {
            "defaults": {
                "size": "2K",
                "minLongEdge": 2048,
                "aspectRatio": "auto",
                "aspectStrict": False,
            },
            "projectProfiles": {
                "amazoncar": {
                    "aspectRatio": "auto",
                    "aspectStrict": "when_aspect_ratio_present",
                }
            },
        }

        project_items = apply_dimension_defaults_to_items(
            [{"id": "M01-L-hero-kv", "prompt": "p", "size": "4K", "aspectRatio": "16:9"}],
            project_profile="amazoncar",
            dimension_profiles=profiles,
        )
        self.assertTrue(project_items[0]["metadata"]["artifactAspectStrict"])

        generic_items = apply_dimension_defaults_to_items(
            [{"id": "generic", "prompt": "p", "size": "4K", "aspectRatio": "16:9"}],
            dimension_profiles=profiles,
        )
        self.assertFalse(generic_items[0]["metadata"]["artifactAspectStrict"])

    def test_provider_groups_never_exceed_five_items(self) -> None:
        items = [{"id": f"item-{idx:02d}", "prompt": "p"} for idx in range(22)]
        assigned = assign_items_evenly(items, DEFAULT_SNANO_PROVIDERS)
        groups = chunk_provider_items(assigned, group_size=5)

        for provider_groups in groups.values():
            for group in provider_groups:
                self.assertLessEqual(len(group), 5)
        self.assertEqual(
            [len(group) for group in groups["apiyi-nano-banana-pro-4k"]],
            [5, 3],
        )
        self.assertEqual(
            [len(group) for group in groups["laozhang-nano-banana-pro-4k-i2i"]],
            [5, 2],
        )
        self.assertEqual(
            [len(group) for group in groups["apimart-gemini-i2i"]],
            [5, 2],
        )

    def test_execution_rounds_pair_provider_groups_for_parallel_runs(self) -> None:
        items = [{"id": f"item-{idx:02d}", "prompt": "p"} for idx in range(20)]
        rounds = build_execution_rounds(items, DEFAULT_SNANO_PROVIDERS, group_size=5)

        self.assertEqual(len(rounds), 2)
        for execution_round in rounds:
            self.assertEqual({group.provider for group in execution_round}, set(DEFAULT_SNANO_PROVIDERS))
            for group in execution_round:
                self.assertLessEqual(len(group.items), 5)

    def test_execution_rounds_allow_uneven_tail(self) -> None:
        items = [{"id": f"item-{idx:02d}", "prompt": "p"} for idx in range(23)]
        rounds = build_execution_rounds(items, DEFAULT_SNANO_PROVIDERS, group_size=5)

        self.assertEqual(
            [[(group.provider, len(group.items)) for group in execution_round] for execution_round in rounds],
            [
                [
                    ("apiyi-nano-banana-pro-4k", 5),
                    ("laozhang-nano-banana-pro-4k-i2i", 5),
                    ("apimart-gemini-i2i", 5),
                ],
                [
                    ("apiyi-nano-banana-pro-4k", 3),
                    ("laozhang-nano-banana-pro-4k-i2i", 3),
                    ("apimart-gemini-i2i", 2),
                ],
            ],
        )

    def test_batch_max_workers_defaults_to_total_task_count(self) -> None:
        items = [{"id": f"item-{idx:02d}", "prompt": "p"} for idx in range(6)]

        self.assertEqual(max_workers_for_batch(items, requested_workers=None), 6)
        self.assertEqual(max_workers_for_batch(items, requested_workers=0), 6)
        self.assertEqual(max_workers_for_batch(items, requested_workers=4), 4)


if __name__ == "__main__":
    unittest.main()
