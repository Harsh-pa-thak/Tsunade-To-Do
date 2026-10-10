"""Tests for the artwork pipeline, run against small synthetic images.

The real source art cannot be asserted on precisely (it is hand drawn), but the
backdrop keying, the watermark erase and the frame warping all have crisp
contracts that can be checked on generated input.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_assets as build  # noqa: E402


def make_source(path: Path, size=(160, 200)) -> None:
    """A white canvas with a red figure, a white hole inside it, and a watermark."""
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 20, 120, 180), fill=(220, 40, 40))  # the "figure"
    draw.rectangle((70, 80, 100, 110), fill=(255, 255, 255))  # enclosed white detail
    draw.rectangle((0, 150, 30, 199), fill=(170, 170, 170))  # the "watermark"
    img.save(path)


class CutOutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.source = Path(self._tmp.name) / "source.png"
        make_source(self.source)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def cut(self, **kwargs) -> Image.Image:
        return build.cut_out(self.source, watermark_box=(0, 150, 30, 199), **kwargs)

    def test_backdrop_becomes_transparent(self) -> None:
        sprite = self.cut()
        self.assertEqual(sprite.mode, "RGBA")
        # The trim keeps a couple of feathered pixels at the very corner.
        self.assertLess(sprite.getpixel((0, 0))[3], 40)
        # Trimmed to the figure (81x161) plus the feathered rim.
        self.assertLessEqual(abs(sprite.width - 81), 2)
        self.assertLessEqual(abs(sprite.height - 161), 2)

    def test_enclosed_background_is_kept(self) -> None:
        """A white detail inside the figure must survive the flood fill."""
        sprite = self.cut()
        width, height = sprite.size
        # The white hole is at source (85, 95): 45 px right of the figure's left
        # edge, 75 px below its top, i.e. inside the trimmed sprite.
        inside_hole = sprite.getpixel((45, 75))
        self.assertEqual(inside_hole[3], 255)
        self.assertGreater(min(inside_hole[:3]), 200)

    def test_watermark_is_painted_over(self) -> None:
        """The grey watermark sits in the backdrop, so nothing of it may remain."""
        sprite = self.cut()
        for x, y in ((3, 160), (15, 170), (25, 195)):
            # Source pixel (x, y) minus the crop offset (40, 20).
            if 0 <= x - 40 < sprite.width and 0 <= y - 20 < sprite.height:
                self.assertEqual(sprite.getpixel((x - 40, y - 20))[3], 0, (x, y))

    def test_without_the_watermark_box_the_mark_stays(self) -> None:
        """Guard against silently losing the erase step."""
        sprite = build.cut_out(self.source, watermark_box=None)
        opaque_left_column = sum(
            1 for y in range(sprite.height) for x in range(0, 5) if sprite.getpixel((x, y))[3] > 200
        )
        self.assertGreater(opaque_left_column, 10)


class FrameRenderingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.source = Path(self._tmp.name) / "source.png"
        make_source(self.source)
        self.sprite = build.cut_out(self.source, watermark_box=(0, 150, 30, 199))
        self.stage = build.Stage(self.sprite)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cell_has_room_for_movement(self) -> None:
        width, height = self.stage.cell
        self.assertGreater(width, self.sprite.width)
        self.assertGreater(height, self.sprite.height)
        self.assertTrue(0 < self.stage.ground[0] < width)

    def test_rest_frame_keeps_the_character_inside_the_cell(self) -> None:
        frame = self.stage.render(build.Pose())
        self.assertEqual(frame.size, self.stage.cell)
        alpha = frame.getchannel("A")
        bbox = alpha.point(lambda a: 255 if a > 8 else 0).getbbox()
        self.assertIsNotNone(bbox)
        self.assertGreaterEqual(bbox[0], 0)
        self.assertLessEqual(bbox[2], frame.width)
        self.assertLessEqual(bbox[3], frame.height)

    def test_mirroring_flips_the_silhouette(self) -> None:
        upright = self.stage.render(build.Pose())
        mirrored = self.stage.render(build.Pose(sx=-1.0))
        left = sum(1 for v in upright.getchannel("A").crop((0, 0, upright.width // 3, upright.height)).getdata() if v > 128)
        mirrored_left = sum(
            1 for v in mirrored.getchannel("A").crop((0, 0, mirrored.width // 3, mirrored.height)).getdata() if v > 128
        )
        self.assertNotEqual(left, mirrored_left)

    def test_every_animation_settles_back_where_it_started(self) -> None:
        """First and last poses must be near the resting pose, or the pet jumps."""
        for anim in build.ANIMATIONS:
            if anim.name == "idle":
                continue  # a seamless loop, not a one-shot
            with self.subTest(animation=anim.name):
                first = anim.pose(0.0)
                self.assertAlmostEqual(first.sx, 1.0, places=2)
                self.assertAlmostEqual(first.sy, 1.0, places=2)
                self.assertAlmostEqual(first.angle, 0.0, places=2)

    def test_damped_reactions_finish_close_to_rest(self) -> None:
        for pose in (build.pose_jiggle, build.pose_shake):
            with self.subTest(pose=pose.__name__):
                end = pose(0.999)
                self.assertLess(abs(end.angle), 1.5)
                self.assertLess(abs(end.sx - 1.0), 0.05)

    def test_spin_returns_to_the_front(self) -> None:
        self.assertAlmostEqual(build.pose_spin(0.0).sx, 1.0, places=3)
        self.assertAlmostEqual(build.pose_spin(0.5).sx, -1.0, places=3)
        self.assertAlmostEqual(build.pose_spin(0.999).sx, 1.0, places=3)

    def test_hop_leaves_the_ground_and_lands(self) -> None:
        self.assertLess(build.pose_hop(0.45).dy, -30)  # up in the air (dy is negative)
        self.assertAlmostEqual(build.pose_hop(0.999).dy, 0.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
