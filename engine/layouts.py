"""Responsive reflow layouts for CHAMELEON.

Reflow topologies share one solver:

  * ``layout_vertical_stack``   - portrait / square / story / narrow skyscraper
  * ``layout_split_landscape``  - hero on one side, copy on the other
  * ``layout_ultra_wide_banner``- leaderboard-style horizontal strips
  * ``layout_compact_banner``   - dense banners with a shared footer

``layout_proportional`` instead keeps the source composition (uniform scale,
spare length shared out like the source's own gaps) for near-same shapes.

Design principles (all scale-invariant - nothing is tuned to one canvas size):

1. **Type is anchored to the source, not to pixels.**  Every font size is
   ``source_size * K * s`` where ``K = sqrt(target_area / source_area)`` and
   ``s`` is a single global *type scale* found by search.  Because one ``s``
   multiplies every role, the designer's hierarchy ratios (headline : subhead :
   label) survive the resize.
2. **Copy first, hero gets the rest.**  For a candidate ``s`` all copy blocks
   are measured with the real Skia font metrics and word-wrap; whatever height
   is left over goes to the hero art (bounded relative to its source size).  The
   largest ``s`` that still gives the hero a decent share wins; remaining slack
   then grows the hero and ``s`` together instead of becoming padding, up to
   about the source's own content density.
2b. **Pictures are sized by their visible pixels.**  Transparent asset padding
   is not content: logos and products are measured, spaced and collision
   checked on their opaque bounds (``engine.visible``), and only the padded box
   is kept on canvas.
3. **Readability floors per role.**  Every text role has a minimum size derived
   from the canvas.  Floors are only relaxed (``degrade`` levels) when the
   content genuinely cannot fit, so tiny canvases degrade gracefully instead of
   overlapping.
4. **Buttons and badges are uniformly scaled copies** of the designer's pieces
   (same padding ratio, same corner style); labels are optically centred with
   Skia ascent/descent.
5. Blocks are ordered by the **source reading order**, with logo/ribbons pinned
   to the header and legal text / watermarks pinned to the footer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.analyzer import AnalyzedDesign, CompositeUnit
from engine.typography import (
    fit_font_to_width,
    measure_text_block,
    readable_floor,
    single_line_width,
)
from engine.visible import NO_INSETS, Insets, box_from_visible, visible_fraction

# ----------------------------------------------------------------------------
# Tunables (all relative - see module docstring)
# ----------------------------------------------------------------------------

S_MAX = 1.30          # largest global type scale tried
S_MIN = 0.20          # smallest
S_STEP = 0.05
S_PREF_MIN = 0.60     # don't shrink type below this just to enlarge the hero
S_GROW_MAX = 1.6      # spare room may lift the type scale this far (role ceilings still apply)
S_GROW_STEP = 0.025
GROW_KEEP = 0.10      # share of a column left as breathing room before growth stops
HERO_CAP = 1.35       # hero height vs. its area-scaled source size...
HERO_CAP_GROW = 2.0   # ...and how far spare room may enlarge it
MAX_DEGRADE = 9
DEGRADE_STEP = 0.5   # floors relax in half-steps so we give up as little legibility as needed

# Per-role readable floor (multiple of ``readable_floor``) and ceiling
# (fraction of the canvas' short side).
ROLE_FLOOR = {"headline": 1.5, "cta": 1.15, "sub": 1.0, "badge": 1.0, "legal": 1.0, "label": 1.0}
ROLE_CEIL = {"headline": 0.16, "cta": 0.075, "sub": 0.075, "badge": 0.055, "legal": 0.03, "label": 0.07}
# How fast each role's floor may be relaxed per degrade level.
# Preferred maximum line count per role: type shrinks (down to its floor) before
# a block wraps into a tall, ragged column.
ROLE_MAX_LINES = {"headline": 5, "sub": 6, "cta": 2}
ROLE_RELAX = {"headline": 0.10, "cta": 0.08, "sub": 0.16, "badge": 0.17, "legal": 0.17, "label": 0.15}


class LayoutDoesNotFit(ValueError):
    """A candidate cannot accommodate this design; try another topology."""

S_LIST = [round(S_MAX - i * S_STEP, 3) for i in range(int(round((S_MAX - S_MIN) / S_STEP)) + 1)]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class Ctx:
    ad: AnalyzedDesign
    W: float
    H: float
    K: float
    floor: float
    src_fs: Dict[str, float]
    ceil_boost: float = 1.0
    degrade: float = 0.0
    min_font: float = 0.0
    headline_item: Optional["TextItem"] = None
    hero_cap: float = HERO_CAP
    # visible logo height bounds, as shares of the short side (banners raise them)
    logo_min: float = 0.085
    logo_max: float = 0.11

    @property
    def short(self) -> float:
        return min(self.W, self.H)


def make_ctx(ad: AnalyzedDesign, W: float, H: float, ceil_boost: float = 1.0) -> Ctx:
    K = math.sqrt(max(1.0, W * H) / max(1.0, ad.src_w * ad.src_h))
    src_fs = dict(ad.src_font_sizes)
    return Ctx(ad=ad, W=W, H=H, K=K, floor=readable_floor(W, H), src_fs=src_fs, ceil_boost=ceil_boost)


def role_bounds(ctx: Ctx, role: str) -> Tuple[float, float]:
    """(floor, ceiling) in px for a text role at the current degrade level."""
    f = ctx.floor * ROLE_FLOOR[role]
    if ctx.degrade:
        f *= max(0.12, 1.0 - ROLE_RELAX[role] * ctx.degrade)
    else:
        f = max(f, float(math.ceil(0.88 * ctx.floor)))
    f = max(2.0, f, ctx.min_font)
    c = max(f, ROLE_CEIL[role] * ctx.short * ctx.ceil_boost)
    return f, c


# ----------------------------------------------------------------------------
# Backgrounds & decoratives
# ----------------------------------------------------------------------------


def _place_backgrounds(ad: AnalyzedDesign, W: float, H: float) -> None:
    """Full-bleed backgrounds cover the whole canvas (``fit: cover`` crops)."""
    for bg in ad.backgrounds:
        bg["x"] = 0.0
        bg["y"] = 0.0
        bg["width"] = round(W, 2)
        bg["height"] = round(H, 2)


def _place_decoratives(ad: AnalyzedDesign, W: float, H: float) -> None:
    """Ambient shapes keep their relative size and corner anchoring."""
    min_dim = min(W, H)
    src_min = max(1.0, min(ad.src_w, ad.src_h))
    scale = min_dim / src_min
    for dec in ad.decoratives:
        ow, oh = float(dec["width"]), float(dec["height"])
        ar = ow / max(1.0, oh)
        ocx = float(dec["x"]) + ow / 2.0
        ocy = float(dec["y"]) + oh / 2.0
        nx, ny = ocx / ad.src_w, ocy / ad.src_h
        # A shape that bled off the source canvas must now sit fully inside it;
        # size it by the part the viewer actually saw, so a corner arc does not
        # come back as a full disc of the same diameter.
        vis_w = max(0.0, min(float(dec["x"]) + ow, ad.src_w) - max(float(dec["x"]), 0.0))
        vis_h = max(0.0, min(float(dec["y"]) + oh, ad.src_h) - max(float(dec["y"]), 0.0))
        seen = math.sqrt(vis_w * vis_h / max(1.0, ow * oh))

        th = _clamp(oh * seen * scale, 4.0, min_dim * 0.68)
        tw = th * ar
        if tw > W * 0.42:
            tw = W * 0.42
            th = tw / max(0.05, ar)
        if dec.get("corner_radius"):
            dec["corner_radius"] = round(float(dec["corner_radius"]) * (th / max(1.0, oh)), 2)

        if nx < 0.35:
            x = 0.0
        elif nx > 0.65:
            x = max(0.0, W - tw)
        else:
            x = _clamp(nx * W - tw / 2.0, 0.0, max(0.0, W - tw))
        if ny < 0.35:
            y = 0.0
        elif ny > 0.65:
            y = max(0.0, H - th)
        else:
            y = _clamp(ny * H - th / 2.0, 0.0, max(0.0, H - th))
        dec["x"], dec["y"] = round(x, 2), round(y, 2)
        dec["width"], dec["height"] = round(tw, 2), round(th, 2)


# ----------------------------------------------------------------------------
# Low-level placement helpers
# ----------------------------------------------------------------------------


def _align_x(ctx: "Ctx", x: float, w: float, iw: float) -> float:
    """Left edge for an item of width `iw` inside the column [x, x+w], following
    the design's dominant text alignment."""
    align = ctx.ad.dominant_align
    if align == "left":
        return x
    if align == "right":
        return x + w - iw
    return x + (w - iw) / 2.0


def _rotated_extents(w: float, h: float, angle_deg: float) -> Tuple[float, float]:
    rad = math.radians(abs(angle_deg))
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    return max(w, w * c + h * s), max(h, w * s + h * c)


def _commit_image(
    ctx: "Ctx",
    img: Dict[str, Any],
    vx: float,
    vy: float,
    vw: float,
    vh: float,
    anchor: str = "center",
    slot: Optional[Tuple[float, float, float, float]] = None,
) -> None:
    """Write an image box whose *visible* pixels fill (vx, vy, vw, vh).

    The transparent margin may overlap neighbours but not leave the canvas.
    The visible picture may slide anywhere inside ``slot`` (x0, y0, x1, y1 -
    the space its layout reserved, default: exactly the requested rectangle)
    to keep the box on canvas; only if that is impossible does it shrink about
    its anchor (the aligned edge horizontally, the centre vertically), down to
    60%. Anything left over is a small shift by the hard-constraint pass."""
    ins = ctx.ad.insets.get(img["id"], NO_INSETS)
    fw, fh = visible_fraction(ins)
    sx0, sy0, sx1, sy1 = slot if slot is not None else (vx, vy, vx + vw, vy + vh)
    sx0, sy0 = min(sx0, vx), min(sy0, vy)
    sx1, sy1 = max(sx1, vx + vw), max(sy1, vy + vh)

    def place_axis(v: float, size: float, box: float, lo_in: float, hi_in: float, s0: float, s1: float, D: float):
        lo = max(s0, lo_in * box)
        hi = min(s1, D - hi_in * box) - size
        return (min(max(v, lo), hi), True) if lo <= hi + 1e-6 else (v, False)

    k = 1.0
    nx, ny, nw, nh = vx, vy, vw, vh
    while True:
        nw, nh = vw * k, vh * k
        want_x = vx if anchor == "left" else (vx + vw - nw if anchor == "right" else vx + (vw - nw) / 2.0)
        want_y = vy + (vh - nh) / 2.0
        nx, okx = place_axis(want_x, nw, nw / fw, ins[0], ins[2], sx0, sx1, ctx.W)
        ny, oky = place_axis(want_y, nh, nh / fh, ins[1], ins[3], sy0, sy1, ctx.H)
        if (okx and oky) or k <= 0.6 + 1e-9:
            break
        k = max(0.6, k - 0.04)
    bx, by, bw, bh = box_from_visible(ins, nx, ny, nw, nh)
    img["x"], img["y"] = round(bx, 2), round(by, 2)
    img["width"], img["height"] = round(bw, 2), round(bh, 2)


def _center_label(label: Dict[str, Any], sx: float, sy: float, sw: float, sh: float, fs: int) -> None:
    """Optically centre a label inside the shape rectangle (sx, sy, sw, sh)."""
    pad = 0.34 * fs
    align = label.get("text_align", "left")
    if align == "center":
        bx, bw = sx, sw
    else:
        bx, bw = sx + pad, max(1.0, sw - 2.0 * pad)
    inner = max(1.0, sw - 2.0 * pad) if align == "center" else bw
    m = measure_text_block(label, fs, inner)
    lh = float(label.get("line_height", 1.2) or 1.2)
    # centre of the glyph mass (cap top .. just below baseline) for n lines
    ink_center = m.ascent - 0.31 * fs + (m.num_lines - 1) * fs * lh / 2.0
    by = sy + sh / 2.0 - ink_center
    label["font_size"] = fs
    label["x"], label["width"] = round(bx, 2), round(bw, 2)
    label["y"] = round(by, 2)
    label["height"] = round(math.ceil(m.visual_height + 2.0), 2)


# ----------------------------------------------------------------------------
# Composite units (buttons, badges, ribbons)
# ----------------------------------------------------------------------------


def _compaction(ctx: Ctx) -> float:
    """How far pill heights may shrink toward their label-height minimum."""
    return max(0.45, 1.0 - 0.13 * ctx.degrade)


@dataclass
class UnitSize:
    w: float
    h: float
    fs: int
    lines: int = 1
    q: float = 0.0  # uniform scale (complex groups only)


def _unit_label_text_w(unit: CompositeUnit, fs: float) -> float:
    return single_line_width(unit.label_el, fs) if unit.label_el is not None else 0.0


def _size_card_reflow(
    ctx: Ctx, unit: CompositeUnit, q0: float, max_w: float, max_h: Optional[float]
) -> UnitSize:
    """Re-wrap a panel-with-text card to the width available instead of shrinking
    it: fonts keep a readable size, the panel grows/shrinks in height to suit."""
    panel = unit.shape_el
    texts = sorted((m for m in unit.elements if m.get("type") == "text"), key=lambda m: float(m["y"]))
    fl, _ = role_bounds(ctx, "sub")
    fl = 0.9 * fl
    px0 = max(0.0, min(float(m["x"]) - float(panel["x"]) for m in texts))
    py0 = max(0.0, float(texts[0]["y"]) - float(panel["y"]))
    gaps0 = [
        max(0.0, float(texts[i + 1]["y"]) - (float(texts[i]["y"]) + float(texts[i]["height"])))
        for i in range(len(texts) - 1)
    ]
    fs0 = [ctx.src_fs.get(m["id"], float(m.get("font_size", 12))) for m in texts]
    w = min(max_w, unit.width * max(1.0, q0))
    q = q0
    plan: Dict[str, Any] = {}
    total = 0.0
    for _ in range(40):
        fs = [max(int(math.ceil(fl)), int(round(f0 * q))) for f0 in fs0]
        pad_x = max(0.5 * min(fs), px0 * q)
        pad_y = max(0.4 * min(fs), py0 * q)
        inner = max(10.0, w - 2 * pad_x)
        heights, gaps = [], []
        for m, f in zip(texts, fs):
            f = fit_font_to_width(m, f, inner, min_font_size=3)
            heights.append(float(math.ceil(measure_text_block(m, f, inner).visual_height + 0.06 * f)))
        fs = [fit_font_to_width(m, f, inner, min_font_size=3) for m, f in zip(texts, fs)]
        gaps = [max(0.2 * min(fs), g * q) for g in gaps0]
        total = 2 * pad_y + sum(heights) + sum(gaps)
        plan = {"texts": texts, "fs": fs, "pad_x": pad_x, "pad_y": pad_y, "inner": inner,
                "heights": heights, "gaps": gaps}
        floor_stop = fl if max_h is None else max(3.0, 0.55 * fl)
        if max_h is None or total <= max_h or q * min(fs0) * 0.92 < floor_stop:
            break
        q *= 0.92
    if max_h is not None and total > max_h and plan:
        content_h = sum(plan["heights"])
        spare = max(0.0, max_h - content_h)
        n_g = len(plan["gaps"])
        plan["pad_y"] = spare * 0.3 / 2.0
        plan["gaps"] = [spare * 0.4 / max(1, n_g)] * n_g
        total = content_h + 2 * plan["pad_y"] + sum(plan["gaps"])
    unit.plan = plan
    return UnitSize(w, total, 0, q=-1.0)


def size_complex(
    ctx: Ctx, unit: CompositeUnit, fs_pref: float, max_w: float, max_h: Optional[float], role: str
) -> UnitSize:
    """Cards, icon+caption groups, tilted stickers: scale the whole group uniformly.
    The scale follows the area-preserving anchor, is lifted until its smallest
    text clears a readable size, and is capped by the space available."""
    texts = [m for m in unit.elements if m.get("type") == "text"]
    fl, _ = role_bounds(ctx, role if role in ROLE_FLOOR else "sub")
    q = fs_pref / max(1.0, unit.orig_label_fs)
    q_floor = max((0.85 * fl / max(1.0, ctx.src_fs.get(m["id"], 12.0)) for m in texts), default=0.0)
    q = max(q, q_floor)
    cap = min(max_w / max(1.0, unit.width), (max_h / max(1.0, unit.height)) if max_h else 1e9, 1.6 * ctx.K)
    if unit.is_card and not abs(unit.rotation) > 1.0 and min(q, cap) < 0.97 * q_floor:
        # uniform scaling would make the card's text unreadable: re-wrap it instead
        return _size_card_reflow(ctx, unit, max(q, q_floor), max_w, max_h)
    unit.plan = None
    q = max(0.02, min(q, cap))
    return UnitSize(unit.width * q, unit.height * q, 0, q=q)


def _commit_card_reflow(unit: CompositeUnit, x: float, y: float, sz: UnitSize) -> None:
    plan = unit.plan or {}
    panel = unit.shape_el
    panel["x"], panel["y"] = round(x, 2), round(y, 2)
    panel["width"], panel["height"] = round(sz.w, 2), round(sz.h, 2)
    if panel.get("corner_radius"):
        panel["corner_radius"] = round(min(float(panel["corner_radius"]), sz.h / 2.0), 2)
    cy = y + plan["pad_y"]
    for m, f, h, g in zip(plan["texts"], plan["fs"], plan["heights"], plan["gaps"] + [0.0]):
        m["font_size"] = int(f)
        m["x"], m["width"] = round(x + plan["pad_x"], 2), round(plan["inner"], 2)
        m["y"], m["height"] = round(cy, 2), round(h, 2)
        cy += h + g


def _commit_complex(unit: CompositeUnit, x: float, y: float, sz: UnitSize) -> None:
    if sz.q < 0:
        _commit_card_reflow(unit, x, y, sz)
        return
    q = sz.w / max(1.0, unit.width)
    for m in unit.elements:
        mx, my = float(m["x"]), float(m["y"])
        mw, mh = float(m["width"]), float(m["height"])
        nx, ny = x + (mx - unit.x) * q, y + (my - unit.y) * q
        nw, nh = mw * q, mh * q
        if m.get("type") == "text":
            fs0 = int(round(float(m.get("font_size", 12))))
            lines0 = measure_text_block(m, fs0, mw).num_lines
            fs = max(2, int(round(fs0 * q)))
            while fs > 2 and measure_text_block(m, fs, nw).num_lines > lines0:
                fs -= 1
            mt = measure_text_block(m, fs, nw)
            m["font_size"] = fs
            nh = max(nh, float(math.ceil(mt.visual_height)))
        elif m.get("corner_radius"):
            m["corner_radius"] = round(float(m["corner_radius"]) * q, 2)
        m["x"], m["y"], m["width"], m["height"] = round(nx, 2), round(ny, 2), round(nw, 2), round(nh, 2)


def size_unit(
    ctx: Ctx,
    unit: CompositeUnit,
    fs_pref: float,
    max_w: float,
    max_h: Optional[float],
    role: str,
) -> UnitSize:
    """Size a button/badge as a uniformly scaled copy of the designer's original.

    The label font is searched downward from ``fs_pref``; the pill keeps the
    source padding ratio and only compresses once it hits ``max_w``/``max_h``."""
    if unit.complex:
        return size_complex(ctx, unit, fs_pref, max_w, max_h, role)
    fl, ce = role_bounds(ctx, role)
    label = unit.label_el
    sw0, sh0 = max(1.0, unit.width), max(1.0, unit.height)

    if label is None:
        # Shape / image only unit: plain uniform scale.
        q = min(max_w / sw0, (max_h or 1e9) / sh0, ctx.K)
        return UnitSize(sw0 * q, sh0 * q, 0)

    fs0 = max(1.0, unit.orig_label_fs)
    tw0 = single_line_width(label, fs0)
    padx0 = max(0.45 * fs0, (sw0 - tw0) / 2.0)
    lo = int(math.ceil(fl))
    top = max(lo, int(_clamp(math.floor(fs_pref), math.ceil(fl), math.floor(ce))))

    for fs in range(top, lo - 1, -1):
        q = fs / fs0
        tw = single_line_width(label, fs)
        w_min = tw + 2 * max(0.5 * fs, 0.6 * padx0 * q)
        h_min = 1.65 * fs
        w = min(sw0 * q, max_w)
        h = max(sh0 * q * _compaction(ctx), h_min)
        if max_h is not None and h > max_h:
            h = max_h
        if w >= w_min - 0.01 and h >= h_min - 0.01:
            return UnitSize(max(w, w_min), h, fs)

    # Could not keep one line at the floor: allow two lines when there is room.
    fs = max(lo, 4)
    inner = max(1.0, max_w - 2 * 0.5 * fs)
    m = measure_text_block(label, fs, inner)
    if not m.overflows_width and m.num_lines <= 2:
        h = max(sh0 * (fs / fs0), (m.num_lines * 1.2 + 0.5) * fs)
        if max_h is not None:
            h = min(h, max(max_h, 1.2 * fs * m.num_lines))
        return UnitSize(max_w, h, fs, m.num_lines)
    # Last resort: smallest font that fits the available width on one line.
    fs_fit = fit_font_to_width(label, fs, max_w - fs, min_font_size=3)
    # fit_font_to_width prevents overlong *words*, not multiple wrapped lines.
    # Reserve their measured height rather than leaving a multi-line label
    # outside a single-line button on very narrow canvases.
    fs_fit = max(3, fs_fit)
    metrics = measure_text_block(label, fs_fit, max(1.0, max_w - 0.68 * fs_fit))
    height = max(1.65 * fs_fit, metrics.visual_height + 0.5 * fs_fit)
    return UnitSize(max_w, max_h or height, fs_fit)


def commit_unit(unit: CompositeUnit, x: float, y: float, sz: UnitSize) -> None:
    """Write the unit's geometry back to its elements."""
    if unit.complex:
        _commit_complex(unit, x, y, sz)
        return
    sh, label = unit.shape_el, unit.label_el
    w, h = sz.w, sz.h
    if sh is not None:
        orig_cr = float(sh.get("corner_radius", 0.0) or 0.0)
        orig_h = max(1.0, unit.height)
        sh["x"], sh["y"] = round(x, 2), round(y, 2)
        sh["width"], sh["height"] = round(w, 2), round(h, 2)
        if orig_cr > 0:
            sh["corner_radius"] = round(h / 2.0, 2) if orig_cr >= 0.42 * orig_h else round(orig_cr * h / orig_h, 2)
        if label is not None:
            _center_label(label, x, y, w, h, sz.fs)
        for m in unit.elements:  # any further members (e.g. an icon) ride along proportionally
            if m is sh or m is label:
                continue
            _scale_member(unit, m, x, y, w, h)
    else:
        for m in unit.elements:
            _scale_member(unit, m, x, y, w, h)
            if m.get("type") == "text":
                m["font_size"] = max(3, int(round(float(m.get("font_size", 12)) * (w / max(1.0, unit.width)))))


def _scale_member(unit: CompositeUnit, m: Dict[str, Any], x: float, y: float, w: float, h: float) -> None:
    ow, oh = max(1.0, unit.width), max(1.0, unit.height)
    m["x"] = round(x + (float(m["x"]) - unit.x) / ow * w, 2)
    m["y"] = round(y + (float(m["y"]) - unit.y) / oh * h, 2)
    m["width"] = round(float(m["width"]) / ow * w, 2)
    m["height"] = round(float(m["height"]) / oh * h, 2)


# ----------------------------------------------------------------------------
# Items - things the column solver stacks
# ----------------------------------------------------------------------------


class Item:
    kind = "item"
    flex = False
    key: float = 0.0  # source reading order (cy)

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:  # pragma: no cover
        raise NotImplementedError

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def content_width(self, ctx: Ctx, w: float) -> float:
        """Width of the visible content at the last ``size`` call (0 = ignore)."""
        return 0.0


class TextItem(Item):
    kind = "text"

    def __init__(self, el: Dict[str, Any], role: str, key: float):
        self.el, self.role, self.key = el, role, key
        self.fs = 12
        self.h = 12.0

    def _compute(self, ctx: Ctx, s: float, w: float) -> Tuple[int, float]:
        fl, ce = role_bounds(ctx, self.role)
        pref = ctx.src_fs.get(self.el["id"], 12.0) * ctx.K * s
        fs = _clamp(pref, fl, ce)
        # Keep the designer's hierarchy: copy never outgrows the headline.
        head = ctx.headline_item
        if head is not None and head is not self and self.role != "headline":
            hfs, _ = head._compute(ctx, s, w)
            fs = max(fl, min(fs, 0.92 * hfs))
        fs = fit_font_to_width(self.el, fs, w, min_font_size=3)
        m = measure_text_block(self.el, fs, w)
        cap = ROLE_MAX_LINES.get(self.role)
        if cap is not None:
            while m.num_lines > cap and fs > fl:
                fs -= 1
                m = measure_text_block(self.el, fs, w)
        return int(fs), float(math.ceil(m.visual_height + 0.06 * fs))

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        self.fs, self.h = self._compute(ctx, s, w)
        return w, self.h

    def content_width(self, ctx: Ctx, w: float) -> float:
        if self.role == "legal":
            return 0.0
        return min(w, measure_text_block(self.el, self.fs, w).max_line_width)

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        bw = w
        if self.role in ("headline", "sub") and w >= 120.0:
            m = measure_text_block(self.el, self.fs, w)
            if m.num_lines >= 2 and not m.overflows_width:
                lo, hi = max(60.0, 0.58 * w), w
                for _ in range(9):
                    mid = (lo + hi) / 2.0
                    mb = measure_text_block(self.el, self.fs, mid)
                    if not mb.overflows_width and mb.num_lines == m.num_lines:
                        hi = mid
                    else:
                        lo = mid
                cand = min(w, float(math.ceil(hi + 4.0)))
                if measure_text_block(self.el, self.fs, cand).num_lines == m.num_lines:
                    bw = cand
        align = self.el.get("text_align", "left")
        bx = x if align == "left" else (x + w - bw if align == "right" else x + (w - bw) / 2.0)
        self.el["x"], self.el["y"] = round(bx, 2), round(y, 2)
        self.el["width"], self.el["height"] = round(bw, 2), round(max(h, self.h), 2)
        self.el["font_size"] = int(self.fs)


class UnitItem(Item):
    kind = "unit"

    def __init__(self, unit: CompositeUnit, role: str, key: float, max_w_frac: float = 0.92):
        self.unit, self.role, self.key = unit, role, key
        self.max_w_frac = max_w_frac
        self.sz = UnitSize(10, 10, 10)

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        label = self.unit.label_el
        pref = (self.unit.orig_label_fs if label is not None else 12.0) * ctx.K * s
        max_h = 0.80 * ctx.H if self.unit.is_card else None
        self.sz = size_unit(ctx, self.unit, pref, w * self.max_w_frac, max_h, self.role)
        return self.sz.w, self.sz.h

    def content_width(self, ctx: Ctx, w: float) -> float:
        return self.sz.w

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        commit_unit(self.unit, _align_x(ctx, x, w, self.sz.w), y, self.sz)


class BadgeRowItem(Item):
    """Several badge units sharing one label size.  They sit in a single row if
    they fit at a readable size, otherwise wrap into two rows, otherwise stack."""

    kind = "badges"

    def __init__(self, row: List[CompositeUnit], key: float):
        self.row, self.key = row, key
        self.sizes: List[UnitSize] = []
        self.rows: List[List[int]] = []
        self.gap = 6.0
        self.vgap = 4.0

    def _natural(self, ctx: Ctx, fs: int, fs0: List[float]) -> List[UnitSize]:
        comp = _compaction(ctx)
        out: List[UnitSize] = []
        for u, f0 in zip(self.row, fs0):
            q = fs / f0
            tw = _unit_label_text_w(u, fs)
            wmin = tw + 2 * max(0.5 * fs, 0.6 * max(0.45 * f0, (u.width - _unit_label_text_w(u, f0)) / 2.0) * q)
            out.append(UnitSize(max(wmin, u.width * q), max(u.height * q * comp, 1.65 * fs), fs))
        return out

    @staticmethod
    def _fit_row(sizes: List[UnitSize], idx: List[int], w: float, gap: float, mins: List[float]) -> bool:
        """Squeeze the padding of the badges in `idx` so the row fits in `w`."""
        total = sum(sizes[i].w for i in idx) + gap * (len(idx) - 1)
        if total <= w:
            return True
        if sum(mins[i] for i in idx) + gap * (len(idx) - 1) > w:
            return False
        free = w - gap * (len(idx) - 1) - sum(mins[i] for i in idx)
        wants = {i: max(0.0, sizes[i].w - mins[i]) for i in idx}
        tot = max(1e-6, sum(wants.values()))
        for i in idx:
            sizes[i].w = mins[i] + min(wants[i], free * wants[i] / tot)
        return True

    def _try(self, ctx: Ctx, fs: int, fs0: List[float], w: float, max_rows: int):
        sizes = self._natural(ctx, fs, fs0)
        mins = [_unit_label_text_w(u, fs) + 2 * 0.5 * fs for u in self.row]
        n = len(sizes)
        for n_rows in range(1, max_rows + 1):
            # greedy even split into n_rows consecutive groups
            per = math.ceil(n / n_rows)
            groups = [list(range(i, min(n, i + per))) for i in range(0, n, per)]
            trial = [UnitSize(z.w, z.h, z.fs) for z in sizes]
            if all(self._fit_row(trial, g, w, self.gap, mins) for g in groups):
                hh = max(z.h for z in trial)
                for z in trial:
                    z.h = hh
                return trial, groups, hh
        return None

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        fl, ce = role_bounds(ctx, "badge")
        n = len(self.row)
        fs0 = [max(1.0, u.orig_label_fs) for u in self.row]
        pref = _clamp(float(sum(fs0)) / n * ctx.K * s, fl, ce)
        self.gap = _clamp(w * 0.02, 3.0, 0.03 * ctx.short + 3)
        self.vgap = max(2.0, 0.6 * self.gap)
        lo = int(math.ceil(fl))
        top = max(lo, int(math.floor(pref)))
        for max_rows in (1, 2):
            for fs in range(top, lo - 1, -1):
                res = self._try(ctx, fs, fs0, w, max_rows) if (max_rows == 1 or n > 1) else None
                if res is None:
                    continue
                self.sizes, self.rows, hh = res
                n_rows = len(self.rows)
                if max_rows == 2 and n_rows < 2:
                    continue
                total_h = hh * n_rows + self.vgap * (n_rows - 1)
                row_w = max(sum(self.sizes[i].w for i in g) + self.gap * (len(g) - 1) for g in self.rows)
                return min(w, row_w), total_h
        # Stack vertically, one badge per line.
        self.sizes = []
        self.rows = [[i] for i in range(n)]
        tot_h = 0.0
        for u in self.row:
            z = size_unit(ctx, u, max(fl, pref), w * 0.94, None, "badge")
            self.sizes.append(z)
            tot_h += z.h
        return w, tot_h + self.vgap * (n - 1)

    def content_width(self, ctx: Ctx, w: float) -> float:
        return max(
            (sum(self.sizes[i].w for i in g) + self.gap * (len(g) - 1) for g in self.rows), default=0.0
        )

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        row_fs = min(z.fs for z in self.sizes)
        cy = y
        for g in self.rows:
            row_h = max(self.sizes[i].h for i in g)
            total = sum(self.sizes[i].w for i in g) + self.gap * (len(g) - 1)
            cx = _align_x(ctx, x, w, total)
            for i in g:
                z = self.sizes[i]
                if len(self.rows) < len(self.row) or len(g) == 1:
                    z.fs = row_fs if len(g) > 1 else z.fs
                commit_unit(self.row[i], cx, cy + (row_h - z.h) / 2.0, z)
                cx += z.w + self.gap
            cy += row_h + self.vgap


class HeroRowItem(Item):
    """One or more product images laid side by side (or wrapped to 2 rows in narrow slots)."""

    kind = "hero"
    flex = True

    def __init__(self, imgs: List[Dict[str, Any]], key: float, src_h: float, ctx_ad: AnalyzedDesign):
        self.imgs = imgs
        self.key = key
        self.ins: List[Insets] = [ctx_ad.insets.get(i["id"], NO_INSETS) for i in imgs]
        fr = [visible_fraction(n) for n in self.ins]
        # Everything below is in *visible* pixels: transparent padding is not content.
        self.ars = [
            ctx_ad.src_boxes[i["id"]][2] * fw / max(1.0, ctx_ad.src_boxes[i["id"]][3] * fh)
            for i, (fw, fh) in zip(imgs, fr)
        ]
        self.src_h = max((ctx_ad.src_boxes[i["id"]][3] * fh for i, (_, fh) in zip(imgs, fr)), default=src_h)
        # the padded box must still fit the canvas
        self.box_h_frac = min((fh for _, fh in fr), default=1.0)
        self.pos: List[Tuple[float, float, float, float]] = []
        self.frac = 0.62  # max share of the region height (side columns use ~0.94)

    def _gap(self, w: float) -> float:
        return 0.04 * w if len(self.imgs) > 1 else 0.0

    def _two_row_ar(self) -> Tuple[int, float]:
        k = math.ceil(len(self.imgs) / 2)
        return k, max(sum(self.ars[:k]), sum(self.ars[k:]))

    def max_h(self, ctx: Ctx, w: float, rh: float) -> float:
        gap = self._gap(w)
        by_w = (w * 0.94 - gap * (len(self.imgs) - 1)) / max(0.05, sum(self.ars))
        per_cap = min(self.src_h * ctx.K * ctx.hero_cap, ctx.H * self.box_h_frac)
        if len(self.imgs) >= 2 and rh > 1.3 * w:
            k, row_ar = self._two_row_ar()
            hh2 = min(per_cap, (w * 0.94 - gap * (k - 1)) / max(0.05, row_ar))
            if hh2 > 1.2 * min(by_w, per_cap):
                return max(8.0, min(2.08 * hh2, rh * self.frac, ctx.H * 0.92))
        return max(8.0, min(by_w, rh * self.frac, per_cap))

    def min_h(self, ctx: Ctx, w: float, rh: float) -> float:
        frac = max(0.04, 0.09 - 0.01 * ctx.degrade)
        return max(10.0, min(frac * rh, self.max_h(ctx, w, rh)))

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:  # flexible - sized by solver
        return w, 0.0

    def fit(self, w: float, h: float, align: str = "center") -> Tuple[float, float]:
        gap = self._gap(w)
        n = len(self.imgs)
        hh = min(h, (w - gap * (n - 1)) / max(0.05, sum(self.ars)))
        if n >= 2:
            k, row_ar = self._two_row_ar()
            vgap = min(gap, 0.06 * h)
            hh2 = min((h - vgap) / 2.0, (w - gap * (k - 1)) / max(0.05, row_ar))
            if hh2 > 1.2 * hh:
                self.pos = []
                total_h = 2.0 * hh2 + vgap
                y0 = (h - total_h) / 2.0
                max_uw = 0.0
                for r_idx, idxs in enumerate((range(0, k), range(k, n))):
                    r_widths = [hh2 * self.ars[i] for i in idxs]
                    uw = sum(r_widths) + gap * (len(r_widths) - 1)
                    max_uw = max(max_uw, uw)
                    rx = 0.0 if align == "left" else (w - uw if align == "right" else (w - uw) / 2.0)
                    ry = y0 + r_idx * (hh2 + vgap)
                    for wd in r_widths:
                        self.pos.append((rx, ry, wd, hh2))
                        rx += wd + gap
                return max_uw, total_h
        widths = [hh * a for a in self.ars]
        used_w = sum(widths) + gap * (len(widths) - 1)
        self.pos = []
        x = 0.0 if align == "left" else (w - used_w if align == "right" else (w - used_w) / 2.0)
        for wd in widths:
            self.pos.append((x, (h - hh) / 2.0, wd, hh))
            x += wd + gap
        return used_w, hh

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float, align: Optional[str] = None) -> None:
        align = align or ctx.ad.dominant_align
        self.fit(w, h, align)
        n = len(self.imgs)
        for img, (dx, dy, wd, hh) in zip(self.imgs, self.pos):
            _commit_image(ctx, img, x + dx, y + dy, wd, hh, align if n == 1 else "center",
                          slot=(x, y, x + w, y + h) if n == 1 else None)


class LogoItem(Item):
    kind = "logo"

    def __init__(self, img: Dict[str, Any], key: float, ctx_ad: AnalyzedDesign):
        self.img, self.key = img, key
        bx = ctx_ad.src_boxes[img["id"]]
        fw, fh = visible_fraction(ctx_ad.insets.get(img["id"], NO_INSETS))
        # w/h/ar describe the visible mark, not its transparent padding
        self.ar = bx[2] * fw / max(1.0, bx[3] * fh)
        self.src_h = bx[3] * fh
        self.box_h_frac = fh
        self.w = self.h = 10.0

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        # The brand mark is protected: it follows the type scale only loosely and
        # keeps a canvas-relative minimum until the layout is truly desperate.
        lo = max(10.0, ctx.logo_min * ctx.short * max(0.5, 1.0 - 0.1 * ctx.degrade))
        h = _clamp(self.src_h * ctx.K * (0.55 + 0.45 * min(1.0, s)), lo, max(lo, ctx.logo_max * ctx.short))
        h = min(h, ctx.H * self.box_h_frac)
        wd = h * self.ar
        max_w = min(w * 0.9, (0.36 if ctx.W >= 520 else 0.7) * ctx.W)
        if wd > max_w:
            wd, h = max_w, max_w / self.ar
        self.w, self.h = wd, h
        return wd, h

    def content_width(self, ctx: Ctx, w: float) -> float:
        return self.w

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        _commit_image(ctx, self.img, _align_x(ctx, x, w, self.w), y + (h - self.h) / 2.0, self.w, self.h,
                      ctx.ad.dominant_align, slot=(x, y, x + w, y + h))


class OverlayItem(Item):
    """A corner ribbon / sticker (possibly rotated)."""

    kind = "overlay"

    def __init__(self, unit: CompositeUnit, key: float):
        self.unit, self.key = unit, key
        self.sz = UnitSize(10, 10, 10)
        self.ext = (10.0, 10.0)

    def fit(self, ctx: Ctx, max_ext_w: float, max_ext_h: float, s: float = 1.0) -> Tuple[float, float]:
        u = self.unit
        sw0, sh0 = max(1.0, u.width), max(1.0, u.height)
        q = ctx.K * 0.9 * min(1.0, s)
        label = u.label_el
        if label is not None:
            fl, ce = role_bounds(ctx, "badge")
            q = max(q, fl / max(1.0, u.orig_label_fs))
            if not u.complex and u.shape_el is not None:
                fs0 = max(1.0, u.orig_label_fs)
                tw0 = single_line_width(label, fs0)
                padx0 = max(0.45 * fs0, (sw0 - tw0) / 2.0)
                lo = max(3, int(math.ceil(fl)))
                top = max(lo, int(_clamp(round(fs0 * q), lo, math.floor(ce))))
                rad = math.radians(abs(u.rotation))
                c, sn = abs(math.cos(rad)), abs(math.sin(rad))
                comp = _compaction(ctx)
                for fs in range(top, 2, -1):
                    qf = fs / fs0
                    tw = single_line_width(label, fs)
                    w_min = tw + 2.0 * max(0.5 * fs, 0.35 * padx0 * qf)
                    h_min = 1.65 * fs
                    h_nat = max(sh0 * qf * comp, h_min)
                    h_lim = max_ext_h
                    if c > 1e-6:
                        h_lim = min(h_lim, (max_ext_h - w_min * sn) / c)
                    if sn > 1e-6:
                        h_lim = min(h_lim, (max_ext_w - w_min * c) / sn)
                    if h_lim < h_min - 0.01:
                        continue
                    h = min(h_nat, h_lim)
                    w_cap = max_ext_w
                    if c > 1e-6:
                        w_cap = min(w_cap, (max_ext_w - h * sn) / c)
                    if sn > 1e-6:
                        w_cap = min(w_cap, (max_ext_h - h * c) / sn)
                    if w_cap >= w_min - 0.01:
                        w = min(max(w_min, sw0 * qf), w_cap)
                        self.sz = UnitSize(w, h, fs)
                        self.ext = _rotated_extents(w, h, u.rotation)
                        return self.ext
        ew, eh = _rotated_extents(sw0 * q, sh0 * q, u.rotation)
        shrink = min(1.0, max_ext_w / max(1.0, ew), max_ext_h / max(1.0, eh))
        q *= shrink
        w, h = sw0 * q, sh0 * q
        fs = int(max(3, round(u.orig_label_fs * q))) if label is not None else 0
        self.sz = UnitSize(w, h, fs, q=q if u.complex else 0.0)
        self.ext = _rotated_extents(w, h, u.rotation)
        return self.ext

    def place_center(self, cx: float, cy: float) -> None:
        commit_unit(self.unit, cx - self.sz.w / 2.0, cy - self.sz.h / 2.0, self.sz)

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        return self.fit(ctx, w, 0.14 * ctx.H, s)

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        self.place_center(x + w / 2.0, y + h / 2.0)


class HeaderItem(Item):
    """Logo (centred) plus corner overlays at the top of a stack."""

    kind = "header"

    def __init__(self, logo: Optional[LogoItem], overlays: List[OverlayItem]):
        self.logo, self.overlays = logo, overlays
        self.mode = "row"
        self.o_pos: List[Tuple[OverlayItem, float, float]] = []
        self.h = 0.0
        self.logo_xy = (0.0, 0.0)

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        W = ctx.W
        lw, lh = (self.logo.size(ctx, s, w) if self.logo else (0.0, 0.0))
        side_cap = 0.30 * W if W >= 520 else 0.72 * W
        o_cap_h = 0.13 * ctx.H
        exts = [(o, o.fit(ctx, side_cap, o_cap_h, s)) for o in self.overlays]
        left = [e for o, e in exts if self._is_left(ctx, o)]
        right = [e for o, e in exts if not self._is_left(ctx, o)]
        lwid = max((e[0] for e in left), default=0.0)
        rwid = max((e[0] for e in right), default=0.0)
        half_free = w / 2.0 - lw / 2.0 - 0.025 * W
        crowded = len(left) > 1 or len(right) > 1
        row_ok = W >= 480 and lwid <= half_free and rwid <= half_free and not crowded
        if not row_ok and not crowded and exts and W >= 240 and half_free > 0.12 * W and ctx.degrade >= 0.5:
            # narrow canvas: squeeze the ribbon(s) beside the logo instead of stacking
            fl_badge, _ = role_bounds(ctx, "badge")
            tight = [(o, o.fit(ctx, half_free, o_cap_h, s)) for o in self.overlays]
            if all(o.sz.fs == 0 or o.sz.fs >= 0.85 * fl_badge for o, _ in tight):
                exts, row_ok = tight, True
            else:
                exts = [(o, o.fit(ctx, side_cap, o_cap_h, s)) for o in self.overlays]
        gap = 0.012 * ctx.H
        if row_ok or not exts:
            self.mode = "row"
            self.h = max([lh] + [e[1] for _, e in exts])
        else:
            self.mode = "stack"
            self.h = lh + sum(e[1] for _, e in exts) + gap * (len(exts) + (1 if lh else 0) - 1)
        self._exts = exts
        self._gap = gap
        self._lw, self._lh = lw, lh
        return w, self.h

    @staticmethod
    def _is_left(ctx: Ctx, o: OverlayItem) -> bool:
        return (o.unit.cx / max(1.0, ctx.ad.src_w)) < 0.5

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        W = ctx.W
        if self.mode == "row":
            if self.logo:
                self.logo.place(ctx, x, y, w, self._lh)
            for o, (ew, eh) in self._exts:
                cx = x + ew / 2.0 if self._is_left(ctx, o) else x + w - ew / 2.0
                o.place_center(cx, y + eh / 2.0)
        else:
            cy = y
            for o, (ew, eh) in self._exts:
                o.place_center(x + w / 2.0, cy + eh / 2.0)
                cy += eh + self._gap
            if self.logo:
                self.logo.place(ctx, x, cy, w, self._lh)


class FooterItem(Item):
    """Legal copy and corner watermarks pinned to the bottom.  When space is
    desperately tight the watermarks share the legal line instead of stacking."""

    kind = "footer"

    def __init__(self, legal: List[TextItem], wms: List[Dict[str, Any]], ctx_ad: AnalyzedDesign):
        self.legal, self.wms, self.ad = legal, wms, ctx_ad
        self.h = 0.0
        self._wm: List[Tuple[Dict[str, Any], float, float, str]] = []
        self.inline = False
        self._legal_w = 0.0
        self._legal_h = 0.0
        self._wm_h = 0.0
        self._gap = 2.0

    def _wm_sizes(self, ctx: Ctx, w: float, cap_h: Optional[float]) -> List[Tuple[Dict[str, Any], float, float, str]]:
        out = []
        for wm in self.wms:
            bx = self.ad.src_boxes[wm["id"]]
            ar = bx[2] / max(1.0, bx[3])
            hh = _clamp(bx[3] * ctx.K * 0.7, 0.03 * ctx.short, 0.075 * ctx.short)
            hh = max(hh, 8.0)
            if cap_h is not None:
                hh = min(hh, max(6.0, cap_h))
            ww = hh * ar
            if ww > w * 0.4:
                ww, hh = w * 0.4, w * 0.4 / ar
            side = "left" if (bx[0] + bx[2] / 2.0) / max(1.0, self.ad.src_w) < 0.5 else "right"
            if ctx.W < 420:
                side = "center"
            out.append((wm, ww, hh, side))
        return out

    def size(self, ctx: Ctx, s: float, w: float) -> Tuple[float, float]:
        self._gap = 0.008 * ctx.H + 1
        self.inline = bool(self.wms and self.legal and (getattr(self, "force_inline", False) or ctx.degrade >= 2.0))
        if self.inline:
            _, th0 = self.legal[0].size(ctx, min(s, 1.0), w)
            self._wm = self._wm_sizes(ctx, w, th0)
            reserved = sum(ww for _, ww, _, _ in self._wm) + self._gap * len(self._wm)
            self._legal_w = max(40.0, w - reserved)
            self._legal_h = 0.0
            for t in self.legal:
                _, th = t.size(ctx, min(s, 1.0), self._legal_w)
                self._legal_h += th
            self._legal_h += self._gap * (len(self.legal) - 1)
            self._wm_h = max((hh for _, _, hh, _ in self._wm), default=0.0)
            self.h = max(self._legal_h, self._wm_h)
            return w, self.h

        self._legal_w = w
        total = 0.0
        self._legal_h = 0.0
        for t in self.legal:
            _, th = t.size(ctx, min(s, 1.0), w)
            self._legal_h += th
        total += self._legal_h
        if len(self.legal) > 1:
            total += self._gap * (len(self.legal) - 1)
            self._legal_h += self._gap * (len(self.legal) - 1)
        self._wm = self._wm_sizes(ctx, w, None)
        self._wm_h = max((hh for _, _, hh, _ in self._wm), default=0.0)
        if self._wm_h:
            total += self._wm_h + (self._gap if self.legal else 0.0)
        self.h = total
        return w, total

    def place(self, ctx: Ctx, x: float, y: float, w: float, h: float) -> None:
        if self.inline:
            bottom = y + h
            rx = x + w
            for wm, ww, hh, _ in self._wm:
                wm["x"], wm["y"] = round(rx - ww, 2), round(bottom - hh, 2)
                wm["width"], wm["height"] = round(ww, 2), round(hh, 2)
                rx -= ww + self._gap
            cy = bottom - self._legal_h
            for t in self.legal:
                t.place(ctx, x, cy, self._legal_w, t.h)
                cy += t.h + self._gap
            return
        cy = y + h - self._legal_h
        if self._wm_h:
            wm_y = cy - (self._gap if self.legal else 0.0) - self._wm_h
            lx, rx = x, x + w
            for wm, ww, hh, side in self._wm:
                if side == "left":
                    wx = lx
                    lx += ww + self._gap
                elif side == "right":
                    wx = rx - ww
                    rx -= ww + self._gap
                else:
                    wx = x + (w - ww) / 2.0
                wm["x"], wm["y"] = round(wx, 2), round(wm_y + self._wm_h - hh, 2)
                wm["width"], wm["height"] = round(ww, 2), round(hh, 2)
        for t in self.legal:
            t.place(ctx, x, cy, w, t.h)
            cy += t.h + self._gap


# ----------------------------------------------------------------------------
# Column solver
# ----------------------------------------------------------------------------


def _gap_between(a: Item, b: Item, g: float) -> float:
    if a.kind == "text" and b.kind == "text":
        return 0.7 * g
    if a.kind in ("header", "logo") or b.kind == "footer":
        return 1.0 * g
    if b.kind in ("unit", "badges") or a.kind in ("unit", "badges"):
        return 1.25 * g
    return g


def _seq_gaps(items: Sequence[Item], g: float) -> float:
    return sum(_gap_between(items[i - 1], items[i], g) for i in range(1, len(items)))


@dataclass
class ColumnResult:
    s: float
    degrade: float
    hero_h: float = 0.0
    fits: bool = True
    content_w: float = 0.0  # widest visible block at the chosen scale (legal copy excluded)


def solve_column(
    ctx: Ctx,
    rx: float,
    ry: float,
    rw: float,
    rh: float,
    top: Sequence[Item],
    mid: Sequence[Item],
    bot: Sequence[Item],
    hero_target: float = 0.34,
    commit: bool = True,
    grow: bool = True,
) -> ColumnResult:
    """Find the largest type scale that fits, give the leftover to flexible
    (hero) items, and (optionally) commit positions."""
    g = _clamp(0.028 * rh, 0.012 * ctx.short, 0.045 * ctx.short)
    flex = [it for it in mid if it.flex]
    fixed_mid = [it for it in mid if not it.flex]
    zone_gap = g * (1.0 if (top and (mid or bot)) else 0.0) + g * (1.0 if (bot and (mid or top)) else 0.0)

    def measure(s: float) -> Tuple[float, float, float, float]:
        ht = sum(it.size(ctx, s, rw)[1] for it in top)
        hb = sum(it.size(ctx, s, rw)[1] for it in bot)
        hm_fixed = sum(it.size(ctx, s, rw)[1] for it in fixed_mid)
        gaps = _seq_gaps(list(top), g) + _seq_gaps(list(mid), g) + _seq_gaps(list(bot), g) + zone_gap
        return ht, hm_fixed, hb, gaps

    chosen: Optional[Tuple[float, float]] = None
    for deg in [i * DEGRADE_STEP for i in range(0, int(MAX_DEGRADE / DEGRADE_STEP) + 1)]:
        ctx.degrade = deg
        flex_min = sum(f.min_h(ctx, rw, rh) for f in flex)  # type: ignore[attr-defined]
        flex_max = sum(f.max_h(ctx, rw, rh) for f in flex)  # type: ignore[attr-defined]
        target = min(flex_max, hero_target * rh) if flex else 0.0
        good = min(flex_max, 0.22 * rh) if flex else 0.0
        fallback: Optional[float] = None
        good_s: Optional[float] = None
        for s in S_LIST:
            ht, hm, hb, gaps = measure(s)
            room = rh - (ht + hm + hb + gaps)
            if room >= target - 0.5:
                chosen = (deg, s)
                break
            if room >= good - 0.5 and good_s is None:
                good_s = s
            if room >= flex_min - 0.5 and fallback is None:
                fallback = s
            if s <= S_PREF_MIN and (good_s is not None or fallback is not None):
                chosen = (deg, good_s if good_s is not None else fallback)  # type: ignore[arg-type]
                break
        if chosen:
            break
        if fallback is not None:
            chosen = (deg, fallback)
            break
    fits = chosen is not None
    if chosen is None:
        chosen = (float(MAX_DEGRADE), S_MIN)
    deg, s = chosen
    ctx.degrade = deg
    ht, hm, hb, gaps = measure(s)
    room = rh - (ht + hm + hb + gaps)
    flex_max = sum(f.max_h(ctx, rw, rh) for f in flex)  # type: ignore[attr-defined]
    flex_min = sum(f.min_h(ctx, rw, rh) for f in flex)  # type: ignore[attr-defined]
    hero_alloc = min(flex_max, max(flex_min, room)) if flex else 0.0
    if fits and deg == 0 and grow:
        s, hero_alloc = _grow(ctx, measure, rh, s, room, hero_alloc, flex, rw)
        ht, hm, hb, gaps = measure(s)
        room = rh - (ht + hm + hb + gaps)
        flex_max = sum(f.max_h(ctx, rw, rh) for f in flex)  # type: ignore[attr-defined]
        flex_min = sum(f.min_h(ctx, rw, rh) for f in flex)  # type: ignore[attr-defined]
    if flex and room < flex_min:
        hero_alloc = flex_min
        fits = False

    res = ColumnResult(s=s, degrade=deg, hero_h=hero_alloc, fits=fits and room >= -0.5)
    res.content_w = max((it.content_width(ctx, rw) for it in list(top) + list(mid) + list(bot)), default=0.0)
    if not commit:
        return res

    # ---- commit ---------------------------------------------------------------
    flex_maxes = [f.max_h(ctx, rw, rh) for f in flex]  # type: ignore[attr-defined]
    flex_mins = [f.min_h(ctx, rw, rh) for f in flex]  # type: ignore[attr-defined]
    flex_h: Dict[int, float] = {}
    if flex:
        tot_max = max(1e-6, sum(flex_maxes))
        for f, mx, mn in zip(flex, flex_maxes, flex_mins):
            flex_h[id(f)] = max(mn, min(mx, hero_alloc * mx / tot_max))
    heights: Dict[int, float] = {}
    for it in list(top) + list(mid) + list(bot):
        heights[id(it)] = flex_h[id(it)] if it.flex else it.size(ctx, s, rw)[1]

    total_top = sum(heights[id(i)] for i in top) + _seq_gaps(list(top), g)
    total_bot = sum(heights[id(i)] for i in bot) + _seq_gaps(list(bot), g)
    total_mid = sum(heights[id(i)] for i in mid) + _seq_gaps(list(mid), g)
    free = rh - total_top - total_bot - total_mid - zone_gap
    # stretch gaps a little before adding outer breathing room
    stretch = _clamp(free / max(1.0, _seq_gaps(list(mid), g) + 1e-6) * 0.5, 0.0, 0.9) if len(mid) > 1 else 0.0
    mid_gaps = [_gap_between(mid[i - 1], mid[i], g) * (1.0 + stretch) for i in range(1, len(mid))]
    total_mid = sum(heights[id(i)] for i in mid) + sum(mid_gaps)
    zg = g * (1.0 if top else 0.0)
    avail_mid_top = ry + total_top + zg
    mid_slot = rh - total_top - total_bot - zone_gap
    offset = max(0.0, (mid_slot - total_mid) * 0.48)

    y = ry
    for i, it in enumerate(top):
        it.place(ctx, rx, y, rw, heights[id(it)])
        y += heights[id(it)] + (_gap_between(it, top[i + 1], g) if i + 1 < len(top) else 0.0)
    y = avail_mid_top + offset
    for i, it in enumerate(mid):
        it.place(ctx, rx, y, rw, heights[id(it)])
        y += heights[id(it)] + (mid_gaps[i] if i < len(mid_gaps) else 0.0)
    yb = ry + rh - total_bot
    for i, it in enumerate(bot):
        it.place(ctx, rx, yb, rw, heights[id(it)])
        yb += heights[id(it)] + (_gap_between(it, bot[i + 1], g) if i + 1 < len(bot) else 0.0)
    return res


def _grow(ctx: Ctx, measure, rh: float, s: float, room: float, hero: float, flex, rw: float):
    """Spend spare column height on the design instead of on padding.

    Half of the slack beyond ``GROW_KEEP`` may enlarge the hero (up to a relaxed
    cap); the type scale then rises in fine steps while the copy, the hero and
    the breathing room still fit. One scale multiplies every role, so the
    designer's hierarchy ratios are unchanged; role ceilings still apply."""
    # keep at least GROW_KEEP free, and more for airy sources: never pack the
    # column much denser than the designer packed the original
    keep = rh * max(GROW_KEEP, 1.0 - min(0.9, ctx.ad.fill_v + 0.10))
    if room - hero <= keep:
        return s, hero
    if flex:
        ctx.hero_cap = HERO_CAP_GROW
        relaxed = sum(f.max_h(ctx, rw, rh) for f in flex)
        hero = min(relaxed, hero + 0.5 * (room - hero - keep))
    best = s
    s2 = s + S_GROW_STEP
    while s2 <= S_GROW_MAX + 1e-9:
        ht, hm, hb, gaps = measure(s2)
        if rh - (ht + hm + hb + gaps) - hero < keep:
            break
        best = s2
        s2 = round(s2 + S_GROW_STEP, 4)
    ht, hm, hb, gaps = measure(best)
    room = rh - (ht + hm + hb + gaps)
    if flex:
        hero = min(sum(f.max_h(ctx, rw, rh) for f in flex), max(hero, room - keep))
    return best, hero


def enforce_label_hierarchy(ad: AnalyzedDesign, W: float, H: float) -> None:
    """Keep button, badge and ribbon labels below the headline, as in the source.

    A headline can be held back by one long word or its line cap while labels
    keep growing with the type scale. Any label that would outgrow
    ``headline x (source ratio, +15% slack)`` shrinks to that size - never below
    the canvas' readable floor - and its pill shrinks about its centre so no
    neighbour moves."""
    head = ad.headline
    if head is None:
        return
    h_src = max(1.0, ad.src_font_sizes.get(head["id"], float(head.get("font_size", 12))))
    h_out = float(head.get("font_size", h_src))
    floor = readable_floor(W, H)

    def cap_for(label: Dict[str, Any]) -> Optional[int]:
        l_src = ad.src_font_sizes.get(label["id"], float(label.get("font_size", 12)))
        if l_src >= h_src:
            return None  # the designer made this label the loudest text
        cap = int(math.floor(max(floor, h_out * min(1.0, 1.15 * l_src / h_src))))
        return cap if float(label.get("font_size", 0)) > cap else None

    units = ([ad.cta_unit] if ad.cta_unit else []) + [u for row in ad.badge_rows for u in row]
    units += list(ad.corner_overlays) + list(ad.other_units)
    for u in units:
        if u.complex:
            # cards / icon groups: their text only gets smaller inside a panel
            # that stays put, so it can only fit better
            for m in u.elements:
                if m.get("type") == "text":
                    cap = cap_for(m)
                    if cap is not None:
                        m["font_size"] = cap
            continue
        if u.label_el is None or u.shape_el is None:
            continue
        cap = cap_for(u.label_el)
        if cap is None:
            continue
        sh = u.shape_el
        r = cap / float(u.label_el["font_size"])
        w, h = float(sh["width"]), float(sh["height"])
        cx, cy = float(sh["x"]) + w / 2.0, float(sh["y"]) + h / 2.0
        tw = single_line_width(u.label_el, cap)
        nw = min(w, max(w * r, tw + 1.0 * cap))
        m_cap = measure_text_block(u.label_el, cap, max(1.0, nw - 0.68 * cap))
        nh = min(h, max(h * r, 1.65 * cap, m_cap.visual_height + 0.5 * cap))
        sh["x"], sh["y"] = round(cx - nw / 2.0, 2), round(cy - nh / 2.0, 2)
        sh["width"], sh["height"] = round(nw, 2), round(nh, 2)
        if sh.get("corner_radius"):
            sh["corner_radius"] = round(float(sh["corner_radius"]) * nh / max(1.0, h), 2)
        _center_label(u.label_el, cx - nw / 2.0, cy - nh / 2.0, nw, nh, cap)
    if ad.cta_text is not None:
        cap = cap_for(ad.cta_text)
        if cap is not None:
            ad.cta_text["font_size"] = cap


def finalize_followers(ad: AnalyzedDesign) -> None:
    """Re-attach drop-shadow style duplicates to the (already placed) primary text."""
    for pid, fl in ad.followers.items():
        prim = ad.all_elements_by_id[pid]
        src_fs = max(1.0, ad.src_font_sizes.get(pid, float(prim.get("font_size", 12))))
        r = float(prim.get("font_size", src_fs)) / src_fs
        for el, dx, dy in fl:
            el["x"] = round(float(prim["x"]) + dx * r, 2)
            el["y"] = round(float(prim["y"]) + dy * r, 2)
            el["width"], el["height"] = prim["width"], prim["height"]
            el["font_size"] = prim["font_size"]
            if "rotation" in prim:
                el["rotation"] = prim["rotation"]


# ----------------------------------------------------------------------------
# Building items from the analysed design
# ----------------------------------------------------------------------------


def _src_cy(ad: AnalyzedDesign, el_id: str) -> float:
    bx = ad.src_boxes[el_id]
    return bx[1] + bx[3] / 2.0


def _hero_rows(ad: AnalyzedDesign) -> List[List[Dict[str, Any]]]:
    """Group hero images that sit side by side in the source into rows."""
    heroes = sorted(ad.hero_images, key=lambda e: (ad.src_boxes[e["id"]][1], ad.src_boxes[e["id"]][0]))
    rows: List[List[Dict[str, Any]]] = []
    for h in heroes:
        hb = ad.src_boxes[h["id"]]
        placed = False
        for row in rows:
            rb = ad.src_boxes[row[0]["id"]]
            ov = min(hb[1] + hb[3], rb[1] + rb[3]) - max(hb[1], rb[1])
            if ov > 0.5 * min(hb[3], rb[3]):
                row.append(h)
                placed = True
                break
        if not placed:
            rows.append([h])
    for row in rows:
        row.sort(key=lambda e: ad.src_boxes[e["id"]][0])
    return rows


def _hero_first(ad: AnalyzedDesign) -> bool:
    """True when the source is a two-column design (hero beside the copy)."""
    if not ad.hero_images:
        return False
    texts = [t for t in ([ad.headline] if ad.headline else []) + ad.subheads]
    if not texts:
        return False
    t_left = min(ad.src_boxes[t["id"]][0] for t in texts)
    t_right = max(ad.src_boxes[t["id"]][0] + ad.src_boxes[t["id"]][2] for t in texts)
    tol = 0.05 * ad.src_w
    for h in ad.hero_images:
        hb = ad.src_boxes[h["id"]]
        if hb[0] + hb[2] <= t_left + tol or hb[0] >= t_right - tol:
            return True
    return False


def _legal_items(ad: AnalyzedDesign) -> List[TextItem]:
    return [TextItem(t, "legal", _src_cy(ad, t["id"])) for t in sorted(ad.legal_texts, key=lambda e: ad.src_boxes[e["id"]][1])]


def _copy_items(ctx: Ctx, include_units: bool = True, include_heroes: bool = True) -> List[Item]:
    """Free (non-pinned) items in source reading order."""
    ad = ctx.ad
    items: List[Item] = []
    hero_first = _hero_first(ad)
    if include_heroes:
        for row in _hero_rows(ad):
            ys = [ad.src_boxes[i["id"]][1] for i in row]
            hs = [ad.src_boxes[i["id"]][3] for i in row]
            it = HeroRowItem(row, -1e9 if hero_first else min(ys) + max(hs) / 2.0, max(hs), ad)
            items.append(it)
    if ad.headline is not None:
        head = TextItem(ad.headline, "headline", _src_cy(ad, ad.headline["id"]))
        ctx.headline_item = head
        items.append(head)
    for sub in ad.subheads:
        items.append(TextItem(sub, "sub", _src_cy(ad, sub["id"])))
    if ad.cta_text is not None:
        items.append(TextItem(ad.cta_text, "cta", _src_cy(ad, ad.cta_text["id"])))
    if include_units:
        for row in ad.badge_rows:
            if any(u.complex for u in row):
                items.extend(UnitItem(u, "badge", u.cy, max_w_frac=0.85) for u in row)
            else:
                items.append(BadgeRowItem(row, sum(u.cy for u in row) / len(row)))
        if ad.cta_unit is not None:
            items.append(UnitItem(ad.cta_unit, "cta", ad.cta_unit.cy))
        for u in ad.other_units:
            items.append(UnitItem(u, "badge", u.cy, max_w_frac=0.85))
    items.sort(key=lambda it: it.key)
    return items


def _overlay_items(ad: AnalyzedDesign) -> List[OverlayItem]:
    return [OverlayItem(u, u.cy) for u in ad.corner_overlays]


# ----------------------------------------------------------------------------
# REGIME 1 - vertical stack
# ----------------------------------------------------------------------------


def layout_vertical_stack(ad: AnalyzedDesign, W: float, H: float, min_font: float = 0.0) -> None:
    """Header / copy and hero in reading order / footer.  With ``min_font`` set
    (the dense variant for small canvases) no text may go below that size: the
    hero and the spacing give way first, and the candidate is rejected if the
    copy still cannot fit."""
    ctx = make_ctx(ad, W, H)
    ctx.min_font = min_font
    _place_backgrounds(ad, W, H)
    _place_decoratives(ad, W, H)

    mx = max(0.06 * W, 0.02 * min(W, H), 4.0)
    my_top = max(0.04 * H, 3.0)
    my_bot = max(0.035 * H, 3.0)
    rw, rh = W - 2 * mx, H - my_top - my_bot

    logo = LogoItem(ad.logo, _src_cy(ad, ad.logo["id"]), ad) if ad.logo is not None else None
    header_logo = logo if (logo and ad.src_boxes[ad.logo["id"]][1] / max(1.0, ad.src_h) < 0.3) else None
    top: List[Item] = []
    overlays = _overlay_items(ad)
    if header_logo or overlays:
        top.append(HeaderItem(header_logo, overlays))
    mid = _copy_items(ctx)
    if logo is not None and header_logo is None:
        mid.append(logo)
        mid.sort(key=lambda it: it.key)
    legal = _legal_items(ad)
    bot: List[Item] = []
    if legal or ad.watermarks:
        bot.append(FooterItem(legal, list(ad.watermarks), ad))
    result = solve_column(ctx, mx, my_top, rw, rh, top, mid, bot, hero_target=0.36)
    if min_font and not result.fits:
        raise LayoutDoesNotFit("Copy cannot fit at the dense minimum")


# ----------------------------------------------------------------------------
# REGIME 1b - proportional (target close to the source's shape)
# ----------------------------------------------------------------------------

PROPORTIONAL_MAX_AR_CHANGE = 1.3  # beyond this aspect change a reflow is needed


def layout_proportional(ad: AnalyzedDesign, W: float, H: float) -> None:
    """Keep the designer's own composition when the target has nearly the same shape.

    Content scales uniformly by ``s = min(W/sw, H/sh)``.  Along the axis with
    spare length, elements are clustered into bands (overlapping or touching
    pieces, and whole groups, travel together) and the spare length is shared
    out in proportion to the source's own gaps and margins, so whitespace
    rhythm survives instead of turning into letterbox bars."""
    sw, sh = ad.src_w, ad.src_h
    if abs(math.log((W / H) / (sw / sh))) > math.log(PROPORTIONAL_MAX_AR_CHANGE):
        raise LayoutDoesNotFit("Aspect ratio change too large for a proportional layout")
    s = min(W / sw, H / sh)
    vertical = H / sh > W / sw  # spare length runs down the canvas
    _place_backgrounds(ad, W, H)
    bg_ids = {e["id"] for e in ad.backgrounds}
    dec_ids = {e["id"] for e in ad.decoratives}
    follower_ids = {el["id"] for fl in ad.followers.values() for el, _, _ in fl}
    flow = [eid for eid in ad.source_order_ids if eid not in bg_ids | dec_ids | follower_ids]

    def span(eid: str) -> Tuple[float, float]:
        x, y, w, h = ad.src_boxes[eid]
        rot = float(ad.all_elements_by_id[eid].get("rotation", 0.0) or 0.0)
        ew, eh = _rotated_extents(w, h, rot)
        c, ext = (y + h / 2.0, eh) if vertical else (x + w / 2.0, ew)
        return c - ext / 2.0, c + ext / 2.0

    # bands: union of overlapping/touching spans, and of grouped elements
    D_s, D_t = (sh, H) if vertical else (sw, W)
    touch = 0.012 * D_s
    groups: Dict[str, str] = {}
    for eid in flow:
        gid = ad.all_elements_by_id[eid].get("group_id")
        if gid:
            groups.setdefault(str(gid), eid)
    spans = {eid: span(eid) for eid in flow}
    for gid, first in groups.items():  # a group spans the union of its members
        members = [e for e in flow if str(ad.all_elements_by_id[e].get("group_id") or "") == gid]
        lo, hi = min(spans[e][0] for e in members), max(spans[e][1] for e in members)
        for e in members:
            spans[e] = (lo, hi)
    bands: List[List[str]] = []
    band_lo: List[float] = []
    band_hi: List[float] = []
    for eid in sorted(flow, key=lambda e: spans[e]):
        lo, hi = spans[eid]
        if bands and lo <= band_hi[-1] + touch:
            bands[-1].append(eid)
            band_hi[-1] = max(band_hi[-1], hi)
        else:
            bands.append([eid])
            band_lo.append(lo)
            band_hi.append(hi)

    # spare length is shared in proportion to the source gaps (margins included)
    lo_c = [max(0.0, v) for v in band_lo]
    hi_c = [min(D_s, v) for v in band_hi]
    gaps = [max(0.0, lo_c[0])] if bands else []
    gaps += [max(0.0, lo_c[i] - hi_c[i - 1]) for i in range(1, len(bands))]
    gaps += [max(0.0, D_s - hi_c[-1])] if bands else []
    content = sum(max(0.0, h - l) for l, h in zip(lo_c, hi_c)) * s
    g_scale = (D_t - content) / max(1e-6, sum(gaps)) if gaps else 0.0
    new_lo: List[float] = []
    cursor = 0.0
    for i in range(len(bands)):
        cursor += gaps[i] * g_scale
        new_lo.append(cursor)
        cursor += (hi_c[i] - lo_c[i]) * s

    def move(el: Dict[str, Any], along: float) -> None:
        x, y, w, h = ad.src_boxes[el["id"]]
        nx, ny = (x * s, along) if vertical else (along, y * s)
        el["x"], el["y"], el["width"], el["height"] = round(nx, 2), round(ny, 2), round(w * s, 2), round(h * s, 2)
        if el.get("type") == "text":
            fs0 = ad.src_font_sizes.get(el["id"], float(el.get("font_size", 12)))
            el["font_size"] = max(1, int(math.floor(fs0 * s + 1e-6)))
        for key in ("corner_radius", "stroke_width"):
            if el.get(key):
                el[key] = round(float(el[key]) * s, 2)

    for i, band in enumerate(bands):
        for eid in band:
            x, y, _, _ = ad.src_boxes[eid]
            start = y if vertical else x
            move(ad.all_elements_by_id[eid], new_lo[i] + (start - lo_c[i]) * s)
    _place_decoratives(ad, W, H)  # ambient shapes: corner-anchored, sized by what was seen


# ----------------------------------------------------------------------------
# REGIME 2 - split landscape (hero beside copy)
# ----------------------------------------------------------------------------


def layout_split_landscape(ad: AnalyzedDesign, W: float, H: float) -> None:
    if not ad.hero_images:
        layout_vertical_stack(ad, W, H)
        return
    ctx = make_ctx(ad, W, H)
    _place_backgrounds(ad, W, H)
    _place_decoratives(ad, W, H)

    mx = max(0.035 * W, 4.0)
    my = max(0.06 * H, 4.0)
    rows = _hero_rows(ad)
    hero_items = [HeroRowItem(r, 0.0, max(ad.src_boxes[i["id"]][3] for i in r), ad) for r in rows]

    # --- corner overlays ride in the hero column / top corners ----------------
    overlays = _overlay_items(ad)
    ov_boxes: List[Tuple[OverlayItem, str, float, float]] = []
    for o in overlays:
        left = (o.unit.cx / max(1.0, ad.src_w)) < 0.5
        ew, eh = o.fit(ctx, 0.26 * W, 0.20 * H)
        ov_boxes.append((o, "left" if left else "right", ew, eh))

    # --- hero side: mirror the source (hero on the right stays on the right) ----
    hero_right = False
    hb = ad.src_boxes[ad.hero_images[0]["id"]]
    if (hb[0] + hb[2] / 2.0) / max(1.0, ad.src_w) > 0.62 and _hero_first(ad):
        hero_right = True

    for hi in hero_items:
        hi.frac = 0.94
    # The hero owns its whole column here, so it may outgrow its source scale.
    ctx.hero_cap = 1.8

    # Overlays sharing a side stack top-down; the column below them starts lower.
    ov_gap = 0.02 * H
    side_h = {"left": 0.0, "right": 0.0}
    ov_y: Dict[int, float] = {}
    for idx, (o, side, ew, eh) in enumerate(ov_boxes):
        ov_y[idx] = my + side_h[side]
        side_h[side] += eh + ov_gap
    hero_side = "right" if hero_right else "left"
    copy_side = "left" if hero_right else "right"
    lo_h = max(0.0, side_h[hero_side] - ov_gap) if side_h[hero_side] else 0.0
    hero_ry = my + (lo_h + 0.02 * H if lo_h else 0.0)
    hero_rh = H - my - hero_ry

    # width the hero column needs at its natural height, plus breathing room
    hero_w_need = 0.0
    for hi in hero_items:
        hh = hi.max_h(ctx, 0.42 * W, hero_rh)
        hero_w_need = max(hero_w_need, sum(a_ * hh for a_ in hi.ars) + hi._gap(0.42 * W) * (len(hi.ars) - 1))
    hero_col_w = _clamp(hero_w_need * 1.15 + 0.02 * W, 0.14 * W, 0.42 * W)
    gap_cols = 0.03 * W
    text_w_max = min(W - 2 * mx - hero_col_w - gap_cols, 0.66 * W)

    # an overlay on the copy side pushes the copy column down a little
    ro_h = max(0.0, side_h[copy_side] - ov_gap) if side_h[copy_side] else 0.0
    text_ry = my + (ro_h + 0.015 * H if ro_h else 0.0)
    text_rh = H - my - text_ry

    def copy_items() -> Tuple[List[Item], List[Item], List[Item]]:
        mid_ = _copy_items(ctx, include_heroes=False)
        if ad.logo is not None:
            mid_.insert(0, LogoItem(ad.logo, -1e9, ad))
        legal_ = _legal_items(ad)
        bot_: List[Item] = []
        if legal_ or ad.watermarks:
            bot_.append(FooterItem(legal_, list(ad.watermarks), ad))
        return [], mid_, bot_

    # pass 1: solve at full width to learn how wide the copy really is
    t1, m1, b1 = copy_items()
    probe = solve_column(ctx, 0.0, text_ry, text_w_max, text_rh, t1, m1, b1, hero_target=0.0, commit=False)
    text_w = _clamp(probe.content_w * 1.06 + 0.03 * W, min(text_w_max, 0.30 * W), text_w_max)
    x0 = (W - (hero_col_w + gap_cols + text_w)) / 2.0
    # The hero's padded box overhangs its visible pixels; at the canvas edge
    # that overhang needs real room or the picture would have to shrink.
    n_rows = max(1, len(hero_items))
    row_h = (hero_rh - 0.03 * hero_rh * (n_rows - 1)) / n_rows
    edge_need = 0.0
    for hi in hero_items:
        vh = min(row_h, hi.max_h(ctx, hero_col_w, hero_rh))
        idx = -1 if hero_right else 0
        bw = hi.ars[idx] * vh / visible_fraction(hi.ins[idx])[0]
        used = sum(hi.ars) * vh + hi._gap(hero_col_w) * (len(hi.ars) - 1)
        overhang = (hi.ins[idx][2] if hero_right else hi.ins[idx][0]) * bw
        edge_need = max(edge_need, overhang - max(0.0, hero_col_w - used) / 2.0)
    total = hero_col_w + gap_cols + text_w
    if hero_right:
        x0 = min(x0, W - edge_need - total)
        if x0 < mx:
            text_w -= mx - x0
            x0 = mx
    else:
        x0 = max(x0, edge_need)
        if x0 + total > W - mx:
            text_w -= x0 + total - (W - mx)
    if hero_right:
        text_x, hero_x = x0, x0 + text_w + gap_cols
    else:
        hero_x, text_x = x0, x0 + hero_col_w + gap_cols

    # hero column ------------------------------------------------------------
    if hero_items:
        n = len(hero_items)
        ghy = 0.03 * hero_rh
        sub_h = (hero_rh - ghy * (n - 1)) / n
        for i, hi in enumerate(hero_items):
            use_h = min(sub_h, hi.max_h(ctx, hero_col_w, hero_rh))
            hi.place(ctx, hero_x, hero_ry + i * (sub_h + ghy) + (sub_h - use_h) / 2.0, hero_col_w, use_h, align="center")
    for idx, (o, side, ew, eh) in enumerate(ov_boxes):
        on_hero_side = (side == "right") == hero_right
        if on_hero_side:
            cx = hero_x + ew / 2.0 if side == "left" else hero_x + hero_col_w - ew / 2.0
        else:
            cx = text_x + text_w - ew / 2.0 if side == "right" else text_x + ew / 2.0
        o.place_center(cx, ov_y[idx] + eh / 2.0)

    # copy column ------------------------------------------------------------
    top, mid, bot = copy_items()
    solve_column(ctx, text_x, text_ry, text_w, text_rh, top, mid, bot, hero_target=0.0)


# ----------------------------------------------------------------------------
# REGIME 3 - ultra wide / micro height banner
# ----------------------------------------------------------------------------


def layout_ultra_wide_banner(ad: AnalyzedDesign, W: float, H: float) -> None:
    """Left-to-right columns - [logo / ribbons] [hero] [copy] [CTA] - with the
    spare width shared evenly between the columns."""
    ctx = make_ctx(ad, W, H, ceil_boost=1.8)
    # the short side is the banner height: an 8-11% logo would be a speck
    ctx.logo_min, ctx.logo_max = 0.3, 0.45
    _place_backgrounds(ad, W, H)
    _place_decoratives(ad, W, H)

    pad_x = max(6.0, 0.012 * W)
    pad_y = max(3.0, 0.05 * H)
    inner_h = H - 2 * pad_y
    gap = max(6.0, 0.012 * W)

    # (width, painter) per fixed column; painters take the column's left x.
    columns: List[Tuple[str, float, Any]] = []

    # --- column 1: logo / ribbons ------------------------------------------------
    logo_item = LogoItem(ad.logo, 0.0, ad) if ad.logo is not None else None
    overlays = _overlay_items(ad)
    if logo_item or overlays:
        col1_w = _clamp(0.115 * W, 56.0, 150.0)
        sgap = max(2.0, 0.04 * H)
        n_slots = (1 if logo_item else 0) + len(overlays)
        usable = inner_h - sgap * (n_slots - 1)
        logo_slot_h = usable if not overlays else (usable * 0.5 if logo_item else 0.0)
        ov_slot_h = (usable - logo_slot_h) / max(1, len(overlays)) if overlays else 0.0
        slots: List[Tuple[Any, float]] = []
        if logo_item is not None:
            lw, lh = logo_item.size(ctx, 0.7, col1_w)
            k = min(1.0, col1_w / max(1.0, lw), logo_slot_h / max(1.0, lh))
            logo_item.w, logo_item.h = lw * k, lh * k
            slots.append((logo_item, logo_item.h))
        for o in overlays:
            _, eh = o.fit(ctx, col1_w, ov_slot_h)
            slots.append((o, eh))
        need = sum(h for _, h in slots) + sgap * (len(slots) - 1)

        def paint_col1(x: float, _slots=slots, _need=need, _w=col1_w, _sgap=sgap) -> None:
            cy = pad_y + max(0.0, (inner_h - _need) / 2.0)
            for it, hv in _slots:
                if isinstance(it, OverlayItem):
                    it.place_center(x + _w / 2.0, cy + hv / 2.0)
                else:
                    _commit_image(ctx, it.img, x + (_w - it.w) / 2.0, cy, it.w, it.h,
                                  slot=(x, cy, x + _w, cy + hv))
                cy += hv + _sgap

        columns.append(("brand", col1_w, paint_col1))

    # --- column 2: hero images ----------------------------------------------------
    rows = _hero_rows(ad)
    hero_items = [HeroRowItem(r, 0.0, max(ad.src_boxes[i["id"]][3] for i in r), ad) for r in rows]
    if hero_items:
        row_h = (inner_h - 2.0 * (len(hero_items) - 1)) / len(hero_items)
        need = max(sum(hi.ars) * min(row_h, H * hi.box_h_frac) for hi in hero_items)
        hero_col_w = _clamp(need * 1.06, 30.0, 0.22 * W)

        def paint_hero(x: float, _items=hero_items, _w=hero_col_w) -> None:
            sub_h = (inner_h - 2.0 * (len(_items) - 1)) / len(_items)
            for i, hi in enumerate(_items):
                hi.place(ctx, x, pad_y + i * (sub_h + 2.0), _w, sub_h, align="center")

        columns.append(("hero", hero_col_w, paint_hero))

    # --- column 4: CTA (placed last in reading order, sized now) ------------------
    cta_col: Optional[Tuple[str, float, Any]] = None
    if ad.cta_unit is not None:
        cta_unit_item = UnitItem(ad.cta_unit, "cta", 0.0, max_w_frac=1.0)
        cap_w = _clamp(0.22 * W, 80.0, 0.3 * W)
        for s in S_LIST:
            cta_unit_item.size(ctx, s, cap_w)
            if cta_unit_item.sz.h <= inner_h * 0.62:
                break

        def paint_cta(x: float, _it=cta_unit_item) -> None:
            _it.place(ctx, x, pad_y + (inner_h - _it.sz.h) / 2.0, _it.sz.w, _it.sz.h)

        cta_col = ("cta", cta_unit_item.sz.w, paint_cta)
    elif ad.cta_text is not None:
        cta_text_item = TextItem(ad.cta_text, "cta", 0.0)
        cap_w = _clamp(0.2 * W, 80.0, 0.3 * W)
        for s in S_LIST:
            _, th = cta_text_item.size(ctx, s, cap_w)
            if th <= inner_h * 0.62:
                break
        text_w_used = max(20.0, cta_text_item.content_width(ctx, cap_w))

        def paint_cta_text(x: float, _it=cta_text_item, _w=text_w_used) -> None:
            _it.place(ctx, x, pad_y + (inner_h - _it.h) / 2.0, _w + 2.0, _it.h)

        cta_col = ("cta", text_w_used + 2.0, paint_cta_text)

    # --- column 3: the copy ------------------------------------------------------------
    def copy_items() -> Tuple[List[Item], List[Item], List[Item]]:
        mid_ = [it for it in _copy_items(ctx, include_heroes=False) if not (
            (isinstance(it, UnitItem) and it.unit is ad.cta_unit)
            or (isinstance(it, TextItem) and it.el is ad.cta_text)
        )]
        legal_ = _legal_items(ad)
        bot_: List[Item] = []
        if legal_ or ad.watermarks:
            bot_.append(FooterItem(legal_, list(ad.watermarks), ad))
        return [], mid_, bot_

    fixed_w = sum(w for _, w, _ in columns) + (cta_col[1] if cta_col else 0.0)
    n_fixed = len(columns) + (1 if cta_col else 0)
    copy_w_max = max(60.0, W - 2 * pad_x - fixed_w - gap * n_fixed)
    t1, m1, b1 = copy_items()
    probe = solve_column(ctx, 0.0, pad_y, copy_w_max, inner_h, t1, m1, b1, hero_target=0.0, commit=False)
    copy_w = _clamp(probe.content_w * 1.05 + 4.0, min(copy_w_max, 60.0), copy_w_max)

    # --- compose: share the spare width between the columns -----------------------
    ordered: List[Tuple[str, float, Any]] = list(columns) + [("copy", copy_w, None)]
    if cta_col:
        ordered.append(cta_col)
    n_cols = len(ordered)
    total = sum(w for _, w, _ in ordered)
    base_gaps = gap * (n_cols - 1)
    spare = W - 2 * pad_x - total - base_gaps
    extra_gap = _clamp(spare / max(1, n_cols - 1) * 0.75, 0.0, 0.07 * W) if n_cols > 1 else 0.0
    used = total + base_gaps + extra_gap * (n_cols - 1)
    x = pad_x + max(0.0, (W - 2 * pad_x - used) / 2.0)
    for name, wcol, painter in ordered:
        if name == "copy":
            top, mid, bot = copy_items()
            solve_column(ctx, x, pad_y, wcol, inner_h, top, mid, bot, hero_target=0.0)
        else:
            painter(x)
        x += wcol + gap + extra_gap


def layout_compact_banner(ad: AnalyzedDesign, W: float, H: float, cta_fraction: float = 0.18) -> None:
    """Content-dense banner: shared footer, compact brand lane, roomy copy.

    Uses the same measured items and immutable source content as every other
    layout. This is an additional candidate, so it cannot displace a better
    existing composition. Seven pixels is a preferred minimum, not a claim
    that all arbitrary content can be legible on a micro canvas.
    """
    ctx = make_ctx(ad, W, H, ceil_boost=1.8)
    ctx.min_font = 7.0
    ctx.logo_min, ctx.logo_max = 0.3, 0.45
    _place_backgrounds(ad, W, H)
    _place_decoratives(ad, W, H)
    px, py = max(3.0, 0.008 * W), max(2.0, 0.025 * H)
    gap = max(3.0, 0.006 * W)
    inner_w = W - 2 * px
    footer = FooterItem(_legal_items(ad), list(ad.watermarks), ad)
    footer.force_inline = True
    _, fh = footer.size(ctx, 1.0, inner_w)
    body_h = H - 2 * py - (fh + gap if fh else 0)
    if body_h < 12:
        raise LayoutDoesNotFit("Canvas too short for compact banner")

    # Brand and all hero images share a single lane instead of taking two
    # fixed-width columns away from copy. Ribbons get measured rotated bounds.
    brand_w = min(0.18 * W, max(38.0, body_h * 1.65))
    brand_items: List[Item] = []
    if ad.logo is not None:
        brand_items.append(LogoItem(ad.logo, 0.0, ad))
    for row in _hero_rows(ad):
        brand_items.append(HeroRowItem(row, 0.0, max(ad.src_boxes[e["id"]][3] for e in row), ad))
    brand_items.extend(_overlay_items(ad))
    if not brand_items:
        brand_w = 0.0

    cta: Optional[Item] = None
    if ad.cta_unit is not None:
        cta = UnitItem(ad.cta_unit, "cta", 0.0, max_w_frac=1.0)
    elif ad.cta_text is not None:
        cta = TextItem(ad.cta_text, "cta", 0.0)
    cta_w = W * cta_fraction if cta else 0.0
    reserved = (brand_w + gap if brand_items else 0) + (cta_w + gap if cta else 0)
    copy_w = inner_w - reserved
    if copy_w < 32:
        raise LayoutDoesNotFit("Insufficient copy width")
    mid = [it for it in _copy_items(ctx, include_heroes=False) if not (
        (isinstance(it, UnitItem) and it.unit is ad.cta_unit)
        or (isinstance(it, TextItem) and it.el is ad.cta_text)
    )]
    copy_x = px + (brand_w + gap if brand_items else 0)
    result = solve_column(ctx, copy_x, py, copy_w, body_h, [], mid, [], hero_target=0)
    if not result.fits:
        raise LayoutDoesNotFit("Copy cannot fit at compact banner minimum")

    # Keep a modest but visible product, logo and ribbon. Allocate each item
    # from its measured size; no asset is stretched or hidden.
    ctx.degrade = 0
    overlays = [it for it in brand_items if isinstance(it, OverlayItem)]
    pictures = [it for it in brand_items if not isinstance(it, OverlayItem)]
    slots = [(it, it.fit(ctx, brand_w, body_h * 0.60)[1]) for it in overlays]
    picture_h = body_h - sum(h for _, h in slots) - 2 * len(slots)
    if picture_h < (12 if pictures else 0):
        raise LayoutDoesNotFit("Brand lane cannot fit")
    if pictures:
        cell_w = brand_w / len(pictures)
        for i, it in enumerate(pictures):
            if isinstance(it, LogoItem):
                it.size(ctx, 1.0, cell_w)
            it.place(ctx, px + i * cell_w, py, cell_w, picture_h)
    y = py + (picture_h if pictures else 0) + 2
    for it, height in slots:
        it.place(ctx, px, y, brand_w, height)
        y += height + 2
    if cta is not None:
        _, height = cta.size(ctx, max(0.7, result.s), cta_w)
        if height > body_h:
            raise LayoutDoesNotFit("CTA cannot fit")
        cta.place(ctx, W - px - cta_w, py + (body_h - height) / 2, cta_w, height)
    footer.size(ctx, 1.0, inner_w)
    footer.place(ctx, px, H - py - fh, inner_w, fh)
