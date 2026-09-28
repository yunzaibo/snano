from __future__ import annotations

import unittest

from router.core.job_templates import build_job_from_template, list_job_templates


class JobTemplateTests(unittest.TestCase):
    def test_lists_core_image_generation_templates(self) -> None:
        template_ids = {template["id"] for template in list_job_templates()}

        self.assertIn("text-to-image", template_ids)
        self.assertIn("single-reference", template_ids)
        self.assertIn("multi-reference", template_ids)

    def test_builds_multi_reference_template_manifest(self) -> None:
        job = build_job_from_template(
            "multi-reference",
            prompt="keep the car exact",
            reference_images=["/tmp/a.png", "/tmp/b.png"],
            routing_policy="fidelity_first",
        )

        self.assertEqual(job["jobType"], "manifest")
        self.assertEqual(job["items"][0]["requestType"], "image_to_image")
        self.assertEqual(job["items"][0]["routingPolicy"], "fidelity_first")
        self.assertEqual(len(job["items"][0]["referenceImages"]), 2)


if __name__ == "__main__":
    unittest.main()
