"""Skia-accurate typography measurement and font-fitting helpers.

Uses the exact same `FontManager` and `wrap_text` algorithm that `cr-renderer`
and `renderer/converter` use at evaluation time, guaranteeing that line wraps,
glyph widths, and vertical ascender/descender bounds match the rendered PNG
down to the pixel.
"""

from __future__ import annotations

import math
from itertools import groupby
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import skia  # type: ignore

from renderer.converter.brand_fonts import load_brand_font_manager
from renderer.converter.wrap import wrap_text

_DEFAULT_LINE_HEIGHT = 1.2
_BOLD_WEIGHTS = {"bold", "semibold", "extrabold", "black"}

_FONT_MANAGER_CACHE: Dict[str, Any] = {}


def get_font_manager(fonts_dir: str | Path = "brand_kit/fonts"):
    """Load and cache the local brand kit FontManager."""
    key = str(fonts_dir)
    if key not in _FONT_MANAGER_CACHE:
        path = Path(fonts_dir)
        if not path.is_dir():
            # Fallback relative to repo root if called from another cwd
            alt = Path(__file__).resolve().parent.parent / "brand_kit" / "fonts"
            if alt.is_dir():
                path = alt
        _FONT_MANAGER_CACHE[key] = load_brand_font_manager(str(path))
    return _FONT_MANAGER_CACHE[key]


def _normalize_weight_style(element: Dict[str, Any]) -> Tuple[str, str]:
    raw_weight = (element.get("font_weight") or "regular").lower()
    is_bold = raw_weight in _BOLD_WEIGHTS
    raw_style = element.get("font_style") or element.get("italic")
    is_italic = raw_style is True or raw_style == "italic"
    weight = "bold" if is_bold else "regular"
    if is_bold and is_italic:
        style = "bolditalic"
    elif is_bold:
        style = "bold"
    elif is_italic:
        style = "italic"
    else:
        style = "regular"
    return weight, style


def readable_floor(W: float, H: float) -> float:
    """Smallest font size (px) that still counts as legible on a W x H canvas."""
    return max(7.0, 0.0095 * min(W, H))


@lru_cache(maxsize=4096)
def _skia_font(fonts_key: str, family: str, weight: str, style: str, size: int):
    fm = get_font_manager(fonts_key)
    ttf_bytes = fm.lookup(family, weight, style)
    typeface = skia.Typeface.MakeFromData(ttf_bytes)
    font = skia.Font(typeface, size)
    m = font.getMetrics()
    return font, -float(m.fAscent), float(m.fDescent)


@lru_cache(maxsize=16384)
def _advances(fonts_key: str, family: str, weight: str, style: str, size: int, text: str) -> Tuple[float, ...]:
    font, _, _ = _skia_font(fonts_key, family, weight, style, size)
    glyphs = font.textToGlyphs(text)
    return tuple(float(w) for w in font.getWidths(glyphs))


class TextMetrics:
    """Exact rendered metrics for a text element at a given font_size and box width."""

    __slots__ = (
        "font_size",
        "box_width",
        "lines",
        "num_lines",
        "max_line_width",
        "visual_height",
        "ascent",
        "descent",
        "overflows_width",
    )

    def __init__(
        self,
        font_size: int,
        box_width: float,
        lines: List[str],
        max_line_width: float,
        visual_height: float,
        ascent: float,
        descent: float,
        overflows_width: bool,
    ) -> None:
        self.font_size = font_size
        self.box_width = box_width
        self.lines = lines
        self.num_lines = len(lines)
        self.max_line_width = max_line_width
        self.visual_height = visual_height
        self.ascent = ascent
        self.descent = descent
        self.overflows_width = overflows_width


def measure_text_block(
    element: Dict[str, Any],
    font_size: float,
    box_width: float,
    fonts_dir: str | Path = "brand_kit/fonts",
) -> TextMetrics:
    """Measure how `cr-renderer` will wrap and draw `element` at `font_size` inside `box_width`."""
    fs = max(1, int(round(font_size)))
    content = str(element.get("content", ""))
    if not content:
        return TextMetrics(fs, box_width, [""], 0.0, float(fs), fs * 0.8, fs * 0.2, False)

    fonts_key = str(fonts_dir)
    get_font_manager(fonts_dir)  # make sure the brand font manager is loaded/cached
    family = element.get("font_family", "Solstice Sans")
    weight, style = _normalize_weight_style(element)
    line_height = float(element.get("line_height", _DEFAULT_LINE_HEIGHT) or _DEFAULT_LINE_HEIGHT)
    letter_spacing = float(element.get("letter_spacing", 0.0) or 0.0)
    capitalize = bool(element.get("capitalize", False))

    # 1. Character widths + greedy wrap, identical to renderer/converter/convert.py
    widths = list(_advances(fonts_key, family, weight, style, fs, content))
    wrapped = wrap_text(content, widths, float(box_width))
    raw_lines = wrapped.split("\n")

    # 2. Rendered line widths and vertical metrics via Skia
    font, ascent, descent = _skia_font(fonts_key, family, weight, style, fs)

    # Wrapping uses the converter's base face, but drawing uses per-character
    # style spans. Measure those actual spans (including colour boundaries,
    # where cr-renderer restarts letter spacing) rather than assuming one face.
    keys = [(weight, style, element.get("color"))] * len(content)
    for run in element.get("style_runs") or []:
        for index in range(max(0, int(run.get("start", 0))), min(len(content), int(run.get("end", len(content))))):
            rw, rs, rc = keys[index]
            bold = (run.get("font_weight", rw) or "regular").lower() in _BOLD_WEIGHTS
            italic = (run.get("font_style") == "italic") if "font_style" in run else (rs in ("italic", "bolditalic"))
            rw = "bold" if bold else "regular"
            rs = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
            keys[index] = (rw, rs, run.get("color", rc))
    max_line_w, offset = 0.0, 0
    for line in raw_lines:
        lw = 0.0
        for key, indices in groupby(range(offset, offset + len(line)), key=lambda i: keys[i]):
            text = ''.join(wrapped[i] for i in indices)
            text = text.upper() if capitalize else text
            if not text:
                continue
            adv = _advances(fonts_key, family, key[0], key[1], fs, text)
            lw += float(sum(adv)) + letter_spacing * max(0, len(adv) - 1)
            _, run_ascent, run_descent = _skia_font(fonts_key, family, key[0], key[1], fs)
            ascent, descent = max(ascent, run_ascent), max(descent, run_descent)
        max_line_w = max(max_line_w, lw)
        offset += len(line) + 1

    num_lines = max(1, len(raw_lines))
    visual_height = (num_lines - 1) * fs * line_height + ascent + descent
    overflows_width = max_line_w > (box_width + 1.5)

    return TextMetrics(
        font_size=fs,
        box_width=box_width,
        lines=raw_lines,
        max_line_width=max_line_w,
        visual_height=visual_height,
        ascent=ascent,
        descent=descent,
        overflows_width=overflows_width,
    )


def fit_font_to_width(
    element: Dict[str, Any],
    max_font_size: float,
    box_width: float,
    min_font_size: float = 4.0,
) -> int:
    """Largest integer font size <= max_font_size at which no single word is wider
    than `box_width` (the renderer never hard-breaks a word, so a too-wide word
    would spill out of its box). Height is *not* considered here."""
    hi = max(1, int(math.floor(max_font_size)))
    lo = max(1, int(math.ceil(min_font_size)))
    if hi <= lo:
        return max(1, hi if measure_text_block(element, hi, box_width).overflows_width is False else lo)
    if not measure_text_block(element, hi, box_width).overflows_width:
        return hi
    while hi - lo > 1:
        mid = (hi + lo) // 2
        if measure_text_block(element, mid, box_width).overflows_width:
            hi = mid
        else:
            lo = mid
    return lo


def single_line_width(element: Dict[str, Any], font_size: float) -> float:
    """Width of the element's content laid out on one line at `font_size`."""
    return measure_text_block(element, font_size, 1.0e6).max_line_width
