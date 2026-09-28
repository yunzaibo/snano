from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from router.core.artifact_metrics import artifact_metrics, inspect_image_blob, parse_aspect_ratio


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


class ArtifactMetricsTests(unittest.TestCase):
    def test_parses_common_aspect_ratio_strings(self) -> None:
        self.assertEqual(parse_aspect_ratio("2:3"), 2 / 3)
        self.assertEqual(parse_aspect_ratio("16x9"), 16 / 9)
        self.assertIsNone(parse_aspect_ratio("4K"))

    def test_detects_valid_png_dimensions(self) -> None:
        row = inspect_image_blob(PNG_1X1)

        self.assertTrue(row["valid_image"])
        self.assertEqual(row["format"], "png")
        self.assertEqual(row["width"], 1)
        self.assertEqual(row["height"], 1)

    def test_summarizes_invalid_artifact_without_reading_secret_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good.png"
            bad = root / "bad.png"
            good.write_bytes(PNG_1X1)
            bad.write_bytes(b"not an image")

            summary = artifact_metrics([str(good), str(bad)])

            self.assertEqual(summary["artifact_count"], 2)
            self.assertEqual(summary["valid_image_count"], 1)
            self.assertEqual(summary["invalid_image_count"], 1)
            self.assertEqual(summary["formats"], ["png", "unknown"])
            self.assertEqual(summary["min_width"], 1)

    def test_records_requested_aspect_ratio_mismatch_without_invalidating_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wide = root / "wide.png"
            wide.write_bytes(png_header(1600, 900))

            summary = artifact_metrics([str(wide)], expected_aspect_ratio="2:3")

            self.assertEqual(summary["valid_image_count"], 1)
            self.assertEqual(summary["invalid_image_count"], 0)
            self.assertEqual(summary["expected_aspect_ratio"], "2:3")
            self.assertEqual(summary["aspect_ratio_checked_count"], 1)
            self.assertEqual(summary["aspect_ratio_mismatch_count"], 1)
            self.assertFalse(summary["images"][0]["aspect_ratio_matches"])

    def test_records_requested_aspect_ratio_match_with_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portrait = root / "portrait.png"
            portrait.write_bytes(png_header(2000, 3000))

            summary = artifact_metrics([str(portrait)], expected_aspect_ratio="2:3")

            self.assertEqual(summary["aspect_ratio_mismatch_count"], 0)
            self.assertTrue(summary["images"][0]["aspect_ratio_matches"])

    def test_records_ratio_not_checked_when_expected_ratio_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            square = root / "square.png"
            square.write_bytes(png_header(4096, 4096))

            summary = artifact_metrics([str(square)])

            self.assertEqual(summary["aspect_ratio_checked_count"], 0)
            self.assertEqual(summary["aspect_ratio_mismatch_count"], 0)
            self.assertFalse(summary["strict_aspect_ratio_checked"])
            self.assertEqual(summary["strict_aspect_ratio_check_reason"], "expected_aspect_ratio_missing")

    def test_4k_matrix_uses_long_edge_and_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            landscape_4k = root / "landscape-4k.png"
            square_4k = root / "square-4k.png"
            small_landscape = root / "small-landscape.png"
            landscape_4k.write_bytes(png_header(4096, 2304))
            square_4k.write_bytes(png_header(4096, 4096))
            small_landscape.write_bytes(png_header(2048, 1152))

            landscape = artifact_metrics([str(landscape_4k)], expected_aspect_ratio="16:9")
            square = artifact_metrics([str(square_4k)], expected_aspect_ratio="16:9")
            small = artifact_metrics([str(small_landscape)], expected_aspect_ratio="16:9")

            self.assertEqual(landscape["aspect_ratio_mismatch_count"], 0)
            self.assertTrue(landscape["images"][0]["aspect_ratio_matches"])
            self.assertEqual(max(landscape["images"][0]["width"], landscape["images"][0]["height"]), 4096)

            self.assertEqual(square["aspect_ratio_mismatch_count"], 1)
            self.assertFalse(square["images"][0]["aspect_ratio_matches"])
            self.assertEqual(max(square["images"][0]["width"], square["images"][0]["height"]), 4096)

            self.assertEqual(small["aspect_ratio_mismatch_count"], 0)
            self.assertTrue(small["images"][0]["aspect_ratio_matches"])
            self.assertLess(max(small["images"][0]["width"], small["images"][0]["height"]), 4096)


if __name__ == "__main__":
    unittest.main()
