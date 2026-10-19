"""Where an image's pixels actually land inside its element box.

Brand assets often carry generous transparent padding (the starter bottle and
logo are ~70% empty pixels). Sizing, spacing and collision checks against the
padded box make products and logos look small and loosely spaced, so layouts
and the auditor work with the *visible* rectangle instead. Nothing here alters
an asset: the element box still contains the whole image at its own aspect
ratio; only the transparent margin is allowed to overlap neighbours.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from renderer.converter.assets import asset_path

Insets = Tuple[float, float, float, float]  # left, top, right, bottom as fractions of the box
NO_INSETS: Insets = (0.0, 0.0, 0.0, 0.0)
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "brand_kit" / "assets"
_ALPHA_CUTOFF = 16


def resolve_assets_dir(assets_dir: str | Path | None) -> Path:
    path = Path(assets_dir) if assets_dir else DEFAULT_ASSETS_DIR
    return path if path.is_dir() else DEFAULT_ASSETS_DIR


@lru_cache(maxsize=256)
def _alpha_bbox(path: str) -> Optional[Tuple[int, int, Tuple[int, int, int, int]]]:
    """(width, height, bbox of non-transparent pixels) of an image file."""
    try:
        with Image.open(path) as im:
            rgba = im.convert("RGBA")
            mask = rgba.getchannel("A").point(lambda a: 255 if a > _ALPHA_CUTOFF else 0)
            bbox = mask.getbbox()
            return rgba.width, rgba.height, bbox or (0, 0, rgba.width, rgba.height)
    except Exception:  # noqa: BLE001 - unreadable asset: treat the box as visible
        return None


@lru_cache(maxsize=256)
def _alpha_density(path: str) -> float:
    """Share of pixels inside the visible bbox that are opaque (logos/wordmarks
    have sparse strokes; product photos/illustrations have dense silhouettes)."""
    try:
        with Image.open(path) as im:
            rgba = im.convert("RGBA")
            mask = rgba.getchannel("A").point(lambda a: 255 if a > _ALPHA_CUTOFF else 0)
            bbox = mask.getbbox()
            if not bbox:
                return 1.0
            area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            opaque = mask.crop(bbox).histogram()[255]
            return opaque / area
    except Exception:  # noqa: BLE001
        return 1.0


def visible_fill_density(el: Dict[str, Any], assets_dir: str | Path | None = None) -> float:
    """Opaque pixel density (0..1) inside `el`'s visible bounding box."""
    if el.get("type") != "image":
        return 1.0
    path = asset_path(str(resolve_assets_dir(assets_dir)), el.get("asset_id"))
    return _alpha_density(str(path)) if path is not None else 1.0


def visible_insets(el: Dict[str, Any], assets_dir: str | Path | None = None) -> Insets:
    """Fractional insets of the visible pixels inside `el`'s box, as the renderer
    draws it (``contain`` letterboxes and centres, ``fill`` stretches). Cover
    images, shapes, text and missing assets are fully visible."""
    if el.get("type") != "image" or el.get("fit", "contain") == "cover":
        return NO_INSETS
    path = asset_path(str(resolve_assets_dir(assets_dir)), el.get("asset_id"))
    info = _alpha_bbox(str(path)) if path is not None else None
    if info is None:
        return NO_INSETS
    iw, ih, (x0, y0, x1, y1) = info
    bw, bh = max(1e-6, float(el["width"])), max(1e-6, float(el["height"]))
    if el.get("fit", "contain") == "fill":
        sx, sy, ox, oy = bw / iw, bh / ih, 0.0, 0.0
    else:
        s = min(bw / iw, bh / ih)
        sx = sy = s
        ox, oy = (bw - iw * s) / 2.0, (bh - ih * s) / 2.0
    left, top = (ox + x0 * sx) / bw, (oy + y0 * sy) / bh
    right, bottom = 1.0 - (ox + x1 * sx) / bw, 1.0 - (oy + y1 * sy) / bh
    return (max(0.0, left), max(0.0, top), max(0.0, right), max(0.0, bottom))


def visible_rect(el: Dict[str, Any], assets_dir: str | Path | None = None) -> Tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the visible pixels of an unrotated element."""
    x, y, w, h = float(el["x"]), float(el["y"]), float(el["width"]), float(el["height"])
    l, t, r, b = visible_insets(el, assets_dir)
    return (x + l * w, y + t * h, x + w - r * w, y + h - b * h)


def box_from_visible(insets: Insets, vx: float, vy: float, vw: float, vh: float) -> Tuple[float, float, float, float]:
    """Element box (x, y, w, h) whose visible part is the rectangle (vx, vy, vw, vh)."""
    l, t, r, b = insets
    bw = vw / max(1e-6, 1.0 - l - r)
    bh = vh / max(1e-6, 1.0 - t - b)
    return vx - l * bw, vy - t * bh, bw, bh


def visible_fraction(insets: Insets) -> Tuple[float, float]:
    """(width, height) share of the box that is visible."""
    l, t, r, b = insets
    return max(1e-6, 1.0 - l - r), max(1e-6, 1.0 - t - b)
