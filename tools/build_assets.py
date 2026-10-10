#!/usr/bin/env python3
"""Build the Tsunade desktop-pet artwork.

Takes ``assets/source/tsunade.png`` (the plain illustration), removes the white
backdrop, and renders every animation as

* a horizontal sprite sheet (``assets/sheet_<name>.png``) used by the Qt app, and
* an animated GIF (``assets/anim_<name>.gif``) that can be shared/viewed anywhere.

All animation is generated from the single still image: each frame is an affine
warp (scale / rotate / mirror / translate) of the cut-out sprite with a soft
ground shadow composited underneath, so the pet breathes, jiggles, hops and
spins around without any hand-drawn frames.

Usage:  python3 tools/build_assets.py
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "source" / "tsunade.png"
OUT_DIR = ROOT / "assets"

# --- sprite preparation -----------------------------------------------------

# The source art has a small vertical "ANIMEPCUN" watermark baked into the
# bottom-left corner on the white backdrop. It sits well away from the figure,
# so it is simply painted over before the backdrop is keyed out.
WATERMARK_BOX = (0, 750, 62, 997)  # left, top, right, bottom
BACKGROUND_MIN_LIGHT = 190  # a backdrop pixel must be this bright in every channel
BACKGROUND_TOLERANCE = 26  # ...and this close (Chebyshev) to pure white
BOTTOM_FADE = 0.045  # fraction of the sprite height that fades into the desktop
#: Alpha above which a pixel counts as "part of her" when building the window
#: shape the app uses to let clicks through the empty area.
MASK_ALPHA = 8

# --- output geometry --------------------------------------------------------

# Sprite height in px inside a cell. Chosen so the pet stays crisp on HiDPI
# displays: the app shows her about 420 logical px tall, which is ~672 device
# px on a 1.6x screen - so the art is authored a little larger than that.
RENDER_HEIGHT = 720
#: Motion amounts below are written for a 480 px tall sprite; this keeps the
#: feel identical if the art is ever re-rendered at another size.
PX = RENDER_HEIGHT / 480.0
CELL_PAD_X = 0.075  # horizontal room for rotation / mirroring
CELL_PAD_TOP = 0.16  # room above her head for the hop and the head shake
CELL_PAD_BOTTOM = 0.03  # room below the ground line for the shadow


@dataclass(frozen=True)
class Pose:
    """One animation frame, expressed as an affine warp of the sprite.

    The sprite is always anchored at the cell's ground point (bottom-centre);
    every value here is applied around ``pivot`` on top of that anchor.
    """

    sx: float = 1.0  # horizontal scale (negative mirrors, used for the turn-around)
    sy: float = 1.0  # vertical scale
    angle: float = 0.0  # degrees, positive = counter-clockwise
    dx: float = 0.0  # px
    dy: float = 0.0  # px, negative moves her up
    pivot: str = "ground"  # "ground" (feet) or "head"
    shadow: float = 1.0  # shadow scale
    shadow_alpha: float = 1.0  # shadow opacity


# ---------------------------------------------------------------------------
# tiny 3x3 matrix helpers (affine transforms)
# ---------------------------------------------------------------------------


def mat_identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mat_translate(x: float, y: float) -> list[list[float]]:
    m = mat_identity()
    m[0][2], m[1][2] = x, y
    return m


def mat_scale(sx: float, sy: float) -> list[list[float]]:
    m = mat_identity()
    m[0][0], m[1][1] = sx, sy
    return m


def mat_rotate(degrees: float) -> list[list[float]]:
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def mat_invert(m: list[list[float]]) -> list[list[float]]:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("singular transform")
    inv = [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]
    return inv


# ---------------------------------------------------------------------------
# background removal
# ---------------------------------------------------------------------------


def load_source(
    path: Path = SOURCE, watermark_box: tuple[int, int, int, int] | None = WATERMARK_BOX
) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if watermark_box:
        ImageDraw.Draw(img).rectangle(watermark_box, fill=(255, 255, 255))
    return img


def background_mask(img: Image.Image) -> bytearray:
    """Flood-fill the white backdrop inwards from the image border.

    Only the backdrop's connected component is removed, so white details inside
    the figure (pearls, highlights) survive.
    """
    w, h = img.size
    px = img.load()

    def is_backdrop(x: int, y: int) -> bool:
        r, g, b = px[x, y]
        if min(r, g, b) < BACKGROUND_MIN_LIGHT:
            return False
        return max(abs(r - 255), abs(g - 255), abs(b - 255)) <= BACKGROUND_TOLERANCE

    mask = bytearray(w * h)
    stack: list[tuple[int, int]] = []
    for x in range(w):
        stack.append((x, 0))
        stack.append((x, h - 1))
    for y in range(h):
        stack.append((0, y))
        stack.append((w - 1, y))

    while stack:
        x, y = stack.pop()
        if mask[y * w + x] or not is_backdrop(x, y):
            continue
        left = x
        while left > 0 and not mask[y * w + left - 1] and is_backdrop(left - 1, y):
            left -= 1
        right = x
        while right < w - 1 and not mask[y * w + right + 1] and is_backdrop(right + 1, y):
            right += 1
        row = y * w
        for xx in range(left, right + 1):
            mask[row + xx] = 1
        for xx in range(left, right + 1):
            for ny in (y - 1, y + 1):
                if 0 <= ny < h and not mask[ny * w + xx] and is_backdrop(xx, ny):
                    stack.append((xx, ny))
    return mask


def cut_out(
    path: Path = SOURCE, watermark_box: tuple[int, int, int, int] | None = WATERMARK_BOX
) -> Image.Image:
    """Return the figure as a trimmed RGBA image with a soft, matte-correct edge."""
    src = load_source(path, watermark_box)
    w, h = src.size
    mask = background_mask(src)

    alpha = Image.frombytes("L", (w, h), bytes(255 if not m else 0 for m in mask))
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.7))
    # The backdrop is white; pull the white matte out of the semi-transparent
    # rim so no pale halo is left behind.
    alpha_data = alpha.tobytes()
    src_data = src.tobytes()
    out = bytearray(w * h * 4)
    for i, a in enumerate(alpha_data):
        o = i * 4
        if a == 0:
            continue
        out[o + 3] = a
        if a == 255:
            out[o : o + 3] = src_data[i * 3 : i * 3 + 3]
            continue
        f = a / 255.0
        for c in range(3):
            v = (src_data[i * 3 + c] - 255.0 * (1.0 - f)) / f
            out[o + c] = 0 if v < 0 else (255 if v > 255 else int(v))

    sprite = Image.frombytes("RGBA", (w, h), bytes(out))
    bbox = sprite.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    sprite = sprite.crop(bbox)
    return soft_bottom(sprite)


def soft_bottom(sprite: Image.Image) -> Image.Image:
    """Fade the last few pixels of the sprite so she melts into the desktop."""
    w, h = sprite.size
    fade = max(6, int(h * BOTTOM_FADE))
    alpha = sprite.getchannel("A").copy()
    grad = Image.new("L", (1, fade))
    for y in range(fade):
        grad.putpixel((0, y), int(255 * (1.0 - y / (fade - 1)) ** 1.4))
    band = alpha.crop((0, h - fade, w, h))
    band = Image.frombytes("L", (w, fade), bytes(min(p, g) for p, g in zip(band.tobytes(), grad.resize((w, fade)).tobytes())))
    alpha.paste(band, (0, h - fade))
    sprite.putalpha(alpha)
    return sprite


def bleed_edges(image: Image.Image) -> Image.Image:
    """Spread edge colours across the transparent area, alpha untouched.

    Rotating or scaling an RGBA image resamples straight (non-premultiplied)
    alpha, so whatever colour sits under a transparent pixel is dragged into the
    silhouette. Leaving that black paints a dark fringe around her, which is
    very visible once the app scales the art to the size on screen. Filling the
    empty area with the neighbouring art colours fixes it once, here.
    """
    keep = image.getchannel("A").point(lambda a: 255 if a > 0 else 0)
    filled = image.convert("RGB")
    # Each round reaches roughly `radius` px further out; doubling the radius
    # covers the whole canvas in a handful of cheap box blurs.
    radius = 4
    spread = 0
    limit = max(image.size)
    while spread < limit:
        filled = Image.composite(filled, filled.filter(ImageFilter.BoxBlur(radius)), keep)
        spread += radius
        radius = min(radius * 2, 128)
    out = filled.convert("RGBA")
    out.putalpha(image.getchannel("A"))
    return out


def make_shadow(width: int, height: int) -> Image.Image:
    """A soft elliptical contact shadow (black, alpha only)."""
    w = max(16, int(width))
    h = max(10, int(height))
    img = Image.new("L", (w, h), 0)
    inset_x = w * 0.05
    inset_y = h * 0.20
    ImageDraw.Draw(img).ellipse((inset_x, inset_y, w - 1 - inset_x, h - 1 - inset_y), fill=215)
    img = img.filter(ImageFilter.GaussianBlur(max(1.2, h * 0.22)))
    shadow = Image.new("RGBA", (w, h), (18, 12, 20, 0))
    shadow.putalpha(img)
    return shadow


# ---------------------------------------------------------------------------
# frame rendering
# ---------------------------------------------------------------------------


class Stage:
    """Composites one :class:`Pose` into a fixed animation cell."""

    def __init__(self, sprite: Image.Image) -> None:
        self.sprite = bleed_edges(sprite)
        sw, sh = sprite.size
        pad_x = int(sw * CELL_PAD_X) + 12
        pad_top = int(sh * CELL_PAD_TOP)
        pad_bottom = int(sh * CELL_PAD_BOTTOM)
        self.cell = (sw + 2 * pad_x, sh + pad_top + pad_bottom)
        self.ground = (self.cell[0] / 2.0, self.cell[1] - pad_bottom)
        self.pad_bottom = pad_bottom
        self.shadow = make_shadow(sw * 0.78, max(10, sw * 0.155))
        # Frames are composited onto a canvas whose empty pixels already carry
        # the nearby art colours, so no frame ever ends up with black under a
        # transparent pixel.
        seed = Image.new("RGBA", self.cell, (0, 0, 0, 0))
        seed.alpha_composite(self.sprite, (round(self.ground[0] - sw / 2), round(self.ground[1] - sh)))
        self.background = bleed_edges(seed)

    def warp(self, image: Image.Image, pose: Pose, anchor: tuple[float, float]) -> Image.Image:
        """Place ``image`` bottom-centre at ``anchor`` and apply the pose."""
        iw, ih = image.size
        gx, gy = self.ground
        pivot = (gx, gy) if pose.pivot == "ground" else (gx, gy - self.sprite.size[1] * 0.86)
        sx = pose.sx
        if abs(sx) < 0.06:  # avoid a degenerate, infinitely thin transform
            sx = 0.06 if sx >= 0 else -0.06
        m = mat_translate(*anchor)
        m = mat_mul(m, mat_translate(-pivot[0], -pivot[1]))
        m = mat_mul(m, mat_scale(sx, pose.sy))
        m = mat_mul(m, mat_rotate(pose.angle))
        m = mat_mul(m, mat_translate(pivot[0], pivot[1]))
        m = mat_mul(m, mat_translate(pose.dx, pose.dy))
        m = mat_mul(m, mat_translate(-iw / 2.0, -ih))
        inv = mat_invert(m)
        data = (inv[0][0], inv[0][1], inv[0][2], inv[1][0], inv[1][1], inv[1][2])
        return image.transform(self.cell, Image.AFFINE, data, resample=Image.Resampling.BICUBIC)

    def render(self, pose: Pose) -> Image.Image:
        canvas = self.background.copy()
        if pose.shadow_alpha > 0.01 and pose.shadow > 0.01:
            shadow = self.warp(self.shadow, Pose(sx=pose.shadow, sy=pose.shadow, shadow_alpha=1.0), self.ground)
            if pose.shadow_alpha < 1.0:
                a = shadow.getchannel("A").point(lambda v: int(v * pose.shadow_alpha))
                shadow.putalpha(a)
            canvas.alpha_composite(shadow)
        canvas.alpha_composite(self.warp(self.sprite, pose, self.ground))
        return canvas


# ---------------------------------------------------------------------------
# the animation library
# ---------------------------------------------------------------------------


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def pose_idle(t: float) -> Pose:
    """Slow breathing with a gentle sway; loops seamlessly."""
    breathe = math.sin(2.0 * math.pi * t)
    return Pose(
        sx=1.0 - 0.008 * breathe,
        sy=1.0 + 0.014 * breathe,
        angle=0.9 * math.sin(2.0 * math.pi * t + 0.6),
        dy=-1.2 * PX * (1.0 - math.cos(2.0 * math.pi * t)) / 2.0,
        shadow=1.0 + 0.012 * breathe,
    )


def pose_jiggle(t: float) -> Pose:
    """The poke wobble: a damped spring that settles back to rest."""
    damp = math.exp(-3.4 * t)
    wob = math.sin(2.0 * math.pi * 2.3 * t)
    squash = 0.055 * damp * math.sin(2.0 * math.pi * 3.1 * t)
    return Pose(
        sx=1.0 + squash,
        sy=1.0 - squash,
        angle=6.0 * damp * wob,
        dy=-5.0 * PX * damp * abs(math.sin(2.0 * math.pi * 1.5 * t)),
        shadow=1.0 - 0.10 * damp * abs(wob),
        shadow_alpha=1.0 - 0.25 * damp * abs(wob),
    )


def pose_spin(t: float) -> Pose:
    """A full turn around: the sprite narrows to an edge, mirrors, and returns."""
    u = smoothstep(t)
    yaw = 2.0 * math.pi * u
    return Pose(
        sx=math.cos(yaw),
        sy=1.0 + 0.02 * math.sin(math.pi * u),
        angle=1.6 * math.sin(2.0 * math.pi * u),
        dy=-7.0 * PX * math.sin(math.pi * u) ** 2,
        shadow=1.0 - 0.18 * math.sin(math.pi * u) ** 2,
        shadow_alpha=1.0 - 0.2 * math.sin(math.pi * u) ** 2,
    )


def pose_hop(t: float) -> Pose:
    """A little jump: crouch, stretch, hang, land."""
    if t < 0.18:  # anticipation squash
        k = smoothstep(t / 0.18)
        return Pose(sx=1.0 + 0.07 * k, sy=1.0 - 0.09 * k, dy=6.0 * PX * k, shadow=1.0 + 0.05 * k)
    if t < 0.72:  # airborne
        k = (t - 0.18) / 0.54
        height = math.sin(math.pi * k)
        return Pose(
            sx=1.0 - 0.05 * height,
            sy=1.0 + 0.08 * height,
            dy=-58.0 * PX * height,
            angle=2.5 * math.sin(math.pi * k * 2.0),
            shadow=1.0 - 0.45 * height,
            shadow_alpha=1.0 - 0.45 * height,
        )
    k = smoothstep((t - 0.72) / 0.28)  # landing squash
    return Pose(
        sx=1.0 + 0.06 * (1.0 - k),
        sy=1.0 - 0.075 * (1.0 - k),
        dy=5.0 * PX * (1.0 - k),
        shadow=0.55 + 0.45 * k,
        shadow_alpha=0.55 + 0.45 * k,
    )


def pose_shake(t: float) -> Pose:
    """A quick head shake around the shoulders."""
    damp = math.exp(-2.8 * t)
    return Pose(
        angle=5.5 * damp * math.sin(2.0 * math.pi * 3.0 * t),
        dx=4.0 * PX * damp * math.sin(2.0 * math.pi * 3.0 * t),
        dy=-2.0 * PX * damp * abs(math.sin(2.0 * math.pi * 2.0 * t)),
        pivot="head",
        shadow=1.0 - 0.04 * damp,
    )


@dataclass(frozen=True)
class Animation:
    name: str
    label: str
    frames: int
    frame_ms: int
    loop: bool
    pose: object  # callable(t) -> Pose


ANIMATIONS = [
    Animation("idle", "Idle breathing", 24, 90, True, pose_idle),
    Animation("jiggle", "Poke wobble", 18, 50, False, pose_jiggle),
    Animation("spin", "Turn around", 30, 50, False, pose_spin),
    Animation("hop", "Happy hop", 20, 55, False, pose_hop),
    Animation("shake", "Head shake", 16, 50, False, pose_shake),
]

REACTIONS = ["jiggle", "spin", "hop", "shake"]


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------


def sheet_path(name: str) -> Path:
    return OUT_DIR / f"sheet_{name}.png"


def gif_path(name: str) -> Path:
    return OUT_DIR / f"anim_{name}.gif"


def build_sheet(frames: list[Image.Image]) -> Image.Image:
    cell = frames[0].size
    sheet = Image.new("RGBA", (cell[0] * len(frames), cell[1]), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        sheet.paste(frame, (i * cell[0], 0))
    return sheet


def build_gif(frames: list[Image.Image], durations: list[int], path: Path) -> None:
    """Write a transparent animated GIF (index 255 is the see-through colour)."""
    palette_frames = []
    for frame in frames:
        alpha = frame.getchannel("A")
        # Quantise only what will be visible, so the palette is not spent on the
        # (invisible) fill colours behind her.
        visible = alpha.point(lambda a: 255 if a >= 110 else 0)
        rgb = Image.new("RGB", frame.size, (0, 0, 0))
        rgb.paste(frame.convert("RGB"), mask=visible)
        p = rgb.quantize(colors=255, method=Image.Quantize.FASTOCTREE)
        p.paste(255, alpha.point(lambda a: 255 if a < 110 else 0))
        p.putpalette(p.getpalette() + [10, 10, 12])
        palette_frames.append(p)
    palette_frames[0].save(
        path,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        transparency=255,
        disposal=2,
        optimize=False,
    )


def make_icon(sprite: Image.Image) -> Image.Image:
    w, h = sprite.size
    side = int(w * 0.72)
    left = max(0, (w - side) // 2)
    head = sprite.crop((left, 0, left + side, min(h, side)))
    icon = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    icon.alpha_composite(head, (0, 0))
    return icon.resize((128, 128), Image.Resampling.LANCZOS)


def main() -> int:
    if not SOURCE.exists():
        print(f"source art missing: {SOURCE}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sprite = cut_out()
    sw, sh = sprite.size
    if sh != RENDER_HEIGHT:
        scale = RENDER_HEIGHT / sh
        sprite = sprite.resize((max(1, round(sw * scale)), RENDER_HEIGHT), Image.Resampling.LANCZOS)

    stage = Stage(sprite)
    print(f"cut-out sprite: {sprite.size[0]}x{sprite.size[1]}  cell: {stage.cell[0]}x{stage.cell[1]}")
    print(f"ground point: {stage.ground}")

    sprite.save(OUT_DIR / "tsunade_cutout.png")

    meta = {
        "version": 1,
        "generator": "tools/build_assets.py",
        "source": str(SOURCE.relative_to(ROOT)),
        "render_height": RENDER_HEIGHT,
        "cell": {"w": stage.cell[0], "h": stage.cell[1]},
        "ground": {"x": round(stage.ground[0], 2), "y": round(stage.ground[1], 2)},
        "static": "tsunade_sprite.png",
        "icon": "tsunade_icon.png",
        "reactions": REACTIONS,
        "animations": {},
    }

    # The resting pose, with its shadow baked in, is what the pet shows between
    # animations and before the very first one starts.
    rest = stage.render(Pose())
    rest.save(OUT_DIR / "tsunade_sprite.png")
    make_icon(sprite).save(OUT_DIR / "tsunade_icon.png")

    # The app gives the window the shape of her silhouette so that clicks on the
    # empty area reach the desktop. Animations move her (a hop, a lean, the
    # shadow squashing), so the shape has to cover every frame - otherwise the
    # window manager clips her mid-jump.
    silhouette = rest.getchannel("A")

    for anim in ANIMATIONS:
        frames = [stage.render(anim.pose(i / anim.frames)) for i in range(anim.frames)]
        for frame in frames:
            silhouette = ImageChops.lighter(silhouette, frame.getchannel("A"))
        build_sheet(frames).save(sheet_path(anim.name))
        build_gif(frames, [anim.frame_ms] * anim.frames, gif_path(anim.name))
        meta["animations"][anim.name] = {
            "label": anim.label,
            "sheet": f"sheet_{anim.name}.png",
            "gif": f"anim_{anim.name}.gif",
            "frames": anim.frames,
            "frame_ms": anim.frame_ms,
            "loop": anim.loop,
            "reactions": anim.name in REACTIONS,
        }
        print(f"  {anim.name:<7} {anim.frames:>2} frames  {anim.frame_ms} ms  {sheet_path(anim.name).name} + {gif_path(anim.name).name}")

    mask = Image.new("RGBA", stage.cell, (255, 255, 255, 0))
    mask.putalpha(silhouette.point(lambda value: 255 if value > MASK_ALPHA else 0))
    mask.save(OUT_DIR / "tsunade_mask.png")
    meta["mask"] = "tsunade_mask.png"
    covered = mask.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
    print(f"window shape: {mask.size[0]}x{mask.size[1]}, coverage bbox {covered}, covers every frame")

    (OUT_DIR / "sheets.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {OUT_DIR / 'sheets.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
