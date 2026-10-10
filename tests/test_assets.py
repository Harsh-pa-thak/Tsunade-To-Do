"""Sanity checks on the generated artwork (Pillow only, no display needed).

These fail loudly if ``assets/`` drifts out of sync with the app - for example
after swapping in different source art without re-running the build tool.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


class GeneratedArtworkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.meta = json.loads((ASSETS / "sheets.json").read_text(encoding="utf-8"))
        cls.cell = (cls.meta["cell"]["w"], cls.meta["cell"]["h"])

    def test_expected_animations_exist(self) -> None:
        names = set(self.meta["animations"])
        self.assertLessEqual({"idle", "jiggle", "spin", "hop", "shake"}, names)
        self.assertTrue(set(self.meta["reactions"]) <= names)

    def test_idle_is_a_loop_and_reactions_are_single_shots(self) -> None:
        self.assertTrue(self.meta["animations"]["idle"]["loop"])
        for name in self.meta["reactions"]:
            self.assertFalse(self.meta["animations"][name]["loop"], name)

    def test_sheets_match_their_frame_counts(self) -> None:
        for name, spec in self.meta["animations"].items():
            with self.subTest(animation=name):
                with Image.open(ASSETS / spec["sheet"]) as sheet:
                    self.assertEqual(sheet.size, (self.cell[0] * spec["frames"], self.cell[1]))
                    self.assertEqual(sheet.mode, "RGBA")
                    self.assertGreater(spec["frames"], 1)
                    self.assertGreaterEqual(spec["frame_ms"], 20)

    def test_frames_differ_from_each_other(self) -> None:
        """An animation whose frames are identical would look broken on screen."""
        for name, spec in self.meta["animations"].items():
            with self.subTest(animation=name):
                with Image.open(ASSETS / spec["sheet"]) as sheet:
                    first = sheet.crop((0, 0, self.cell[0], self.cell[1]))
                    middle = sheet.crop(
                        (self.cell[0] * (spec["frames"] // 2), 0, self.cell[0] * (spec["frames"] // 2 + 1), self.cell[1])
                    )
                    self.assertNotEqual(first.tobytes(), middle.tobytes())

    def test_static_sprite_and_icon(self) -> None:
        sprite = Image.open(ASSETS / self.meta["static"])
        self.assertEqual(sprite.size, self.cell)
        self.assertEqual(sprite.mode, "RGBA")
        icon = Image.open(ASSETS / self.meta["icon"])
        self.assertEqual(icon.size, (128, 128))

    def test_window_mask_covers_every_frame(self) -> None:
        """If the shape misses a frame, the window manager clips her mid-hop."""
        mask_name = self.meta.get("mask")
        self.assertIsNotNone(mask_name, "the asset pack has no window-shape mask")
        mask = Image.open(ASSETS / mask_name).convert("RGBA").getchannel("A")
        self.assertEqual(mask.size, self.cell)
        poses = [Image.open(ASSETS / self.meta["static"]).convert("RGBA").getchannel("A")]
        for spec in self.meta["animations"].values():
            sheet = Image.open(ASSETS / spec["sheet"]).convert("RGBA").getchannel("A")
            poses += [
                sheet.crop((i * self.cell[0], 0, (i + 1) * self.cell[0], self.cell[1]))
                for i in range(spec["frames"])
            ]
        for index, pose in enumerate(poses):
            with self.subTest(frame=index):
                # Anything visible in the frame but not in the shape would be cut off.
                spill = ImageChops.subtract(pose, mask).point(lambda v: 255 if v > 8 else 0).getbbox()
                self.assertIsNone(spill, f"frame {index} sticks out of the window shape")

    def test_empty_area_carries_colours_not_black(self) -> None:
        """Black under a transparent pixel turns into a dark rim once scaled."""
        for name in (self.meta["static"], self.meta["animations"]["hop"]["sheet"]):
            with self.subTest(asset=name):
                image = Image.open(ASSETS / name).convert("RGBA")
                width, height = image.size
                empty = image.crop((0, 0, min(width, 400), min(height, 200)))
                pixels = list(empty.getdata())
                transparent = [p for p in pixels if p[3] == 0]
                self.assertTrue(transparent, "expected some transparent pixels")
                dark = [p for p in transparent if max(p[:3]) < 12]
                self.assertLess(
                    len(dark) / len(transparent),
                    0.2,
                    f"{len(dark)}/{len(transparent)} transparent pixels are still black",
                )

    def test_corners_are_transparent_and_she_actually_covers_the_middle(self) -> None:
        sprite = Image.open(ASSETS / self.meta["static"]).convert("RGBA")
        width, height = sprite.size
        alpha = sprite.getchannel("A")
        for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            self.assertLess(alpha.getpixel(point), 12, f"background left at {point}")
        opaque = sum(1 for value in alpha.get_flattened_data() if value > 128)
        coverage = opaque / (width * height)
        self.assertGreater(coverage, 0.15, "sprite looks too empty")
        self.assertLess(coverage, 0.80, "the backdrop was probably not removed")

    def test_no_stray_floating_specks(self) -> None:
        """Leftover backdrop remnants show up as small islands of alpha."""
        sprite = Image.open(ASSETS / self.meta["static"]).convert("RGBA")
        alpha = sprite.getchannel("A")
        width, height = sprite.size
        opaque = sum(1 for value in alpha.get_flattened_data() if value > 200)
        bbox = alpha.point(lambda a: 255 if a > 8 else 0).getbbox()
        self.assertIsNotNone(bbox)
        box_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        self.assertGreater(opaque / box_area, 0.25)

    def test_gifs_exist_and_animate(self) -> None:
        for name, spec in self.meta["animations"].items():
            with self.subTest(animation=name):
                with Image.open(ASSETS / spec["gif"]) as gif:
                    self.assertGreaterEqual(getattr(gif, "n_frames", 1), spec["frames"])


if __name__ == "__main__":
    unittest.main()
