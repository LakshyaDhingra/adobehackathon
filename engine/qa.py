"""Independent layout audit for CHAMELEON outputs.

This is a *local* approximation of the rubric in the brief (section 2.5 and
RULES.md): no unwanted collisions, everything readable, brand intact, looks
intentional. It never looks at how a layout was produced - it only compares
the returned design against the source - so it can be used both to grade the
engine (tests, fuzzing) and to choose between candidate layouts at runtime.

Text is measured with the same Skia font metrics and word-wrap routine the
renderer uses (``engine.typography.measure_text_block``), so "this text
overflows its box" means the same thing here as in the rendered PNG.

Calibrated against the real renderer:
  * the renderer does NOT clip text to its box height - overflow spills out;
  * a single over-long word overflows its line instead of being broken;
  * line pitch is ``font_size * line_height`` and line 0's baseline sits
    ``ascent`` below the box top.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from engine.typography import measure_text_block, readable_floor
from engine.visible import visible_rect
from renderer.converter.colorutil import parse_rgba

Rect = Tuple[float, float, float, float]  # x0, y0, x1, y1

# Properties that make up the brand / content and must never be altered by a
# resize (stage-one rule: "treat content and font family as fixed").
IMMUTABLE_FIELDS = (
    "type",
    "content",
    "font_family",
    "font_weight",
    "font_style",
    "color",
    "style_runs",
    "asset_id",
    "fit",
    "shape_type",
    "fill_color",
    "stroke_color",
    "text_align",
    "line_height",
    "letter_spacing",
    "group_id",
    "z_index",
    "opacity",
)

# Penalty weights (score starts at 100).
_P_ID = 100.0
_P_BRAND = 20.0
_P_OFFCANVAS = 15.0
_P_AR = 15.0
_P_TEXT_OFF = 10.0
_P_OVERFLOW_W = 10.0
_P_OVERFLOW_H = 6.0
_P_TINY = 6.0
_P_COMPOSITE = 10.0
_P_UNDERSIZED = 8.0
_P_HIERARCHY = 4.0
_P_EDGE = 2.0


def _rect_area(r: Rect) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _inter(a: Rect, b: Rect) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def _rotated_rect(x: float, y: float, w: float, h: float, rot: float) -> Rect:
    if abs(rot) < 0.01:
        return (x, y, x + w, y + h)
    rad = math.radians(rot)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    ew, eh = w * c + h * s, w * s + h * c
    cx, cy = x + w / 2.0, y + h / 2.0
    return (cx - ew / 2.0, cy - eh / 2.0, cx + ew / 2.0, cy + eh / 2.0)


def eff_opacity(el: Dict[str, Any]) -> float:
    op = float(el.get("opacity", 1.0) if el.get("opacity") is not None else 1.0)
    if el.get("type") == "shape":
        op *= parse_rgba(el.get("fill_color"), default=(0, 0, 0, 1.0))[3]
    return op


def text_ink_rect(el: Dict[str, Any], rotate: bool = True) -> Rect:
    """Where the glyphs of a text element really land (not its declared box).
    ``rotate=False`` returns the ink in the element's own (unrotated) frame."""
    x, y = float(el["x"]), float(el["y"])
    w, h = float(el["width"]), float(el["height"])
    fs = float(el.get("font_size", 12))
    m = measure_text_block(el, fs, w)
    lh = float(el.get("line_height", 1.2) or 1.2)
    ink_w = min(m.max_line_width, max(w, m.max_line_width))
    align = el.get("text_align", "left")
    if align == "center":
        x0 = x + (w - ink_w) / 2.0
    elif align == "right":
        x0 = x + w - ink_w
    else:
        x0 = x
    top = y + max(0.0, m.ascent - 0.78 * fs)
    bottom = y + (m.num_lines - 1) * fs * lh + m.ascent + 0.22 * fs
    rect = (x0, top, x0 + ink_w, bottom)
    rot = float(el.get("rotation", 0.0) or 0.0)
    if rotate and abs(rot) > 0.01:
        # Rotate the ink about the *box* centre, like the renderer does.
        cx, cy = x + w / 2.0, y + h / 2.0
        rad = math.radians(rot)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        rcx, rcy = (rect[0] + rect[2]) / 2.0 - cx, (rect[1] + rect[3]) / 2.0 - cy
        a = math.radians(rot)
        ncx = cx + rcx * math.cos(a) - rcy * math.sin(a)
        ncy = cy + rcx * math.sin(a) + rcy * math.cos(a)
        rw, rh = rect[2] - rect[0], rect[3] - rect[1]
        ew, eh = rw * c + rh * s, rw * s + rh * c
        rect = (ncx - ew / 2.0, ncy - eh / 2.0, ncx + ew / 2.0, ncy + eh / 2.0)
    return rect


def ink_rect(el: Dict[str, Any], assets_dir: str | Path | None = None) -> Rect:
    """Where an element's visible pixels land: glyphs for text, the opaque part
    of an image (transparent asset padding is not content), the box otherwise."""
    if el.get("type") == "text":
        return text_ink_rect(el)
    rot = float(el.get("rotation", 0.0) or 0.0)
    if el.get("type") == "image" and abs(rot) < 0.01:
        return visible_rect(el, assets_dir) if assets_dir is not None else visible_rect(el)
    return _rotated_rect(float(el["x"]), float(el["y"]), float(el["width"]), float(el["height"]), rot)


def is_background(el: Dict[str, Any], cw: float, ch: float, min_z: float) -> bool:
    if el.get("type") not in ("image", "shape"):
        return False
    area = float(el["width"]) * float(el["height"])
    big = area >= 0.65 * cw * ch
    covers = (
        float(el["width"]) >= 0.85 * cw and float(el["height"]) >= 0.85 * ch
        and float(el["x"]) <= 0.08 * cw and float(el["y"]) <= 0.08 * ch
    )
    low = float(el.get("z_index", 0) or 0) <= min_z + 1
    return low and ((el.get("fit") == "cover" and big) or covers)


def is_decorative(el: Dict[str, Any], cw: float, ch: float) -> bool:
    if el.get("type") != "shape" or el.get("group_id"):
        return False
    bleeds = (
        float(el["x"]) < -1 or float(el["y"]) < -1
        or float(el["x"]) + float(el["width"]) > cw + 1
        or float(el["y"]) + float(el["height"]) > ch + 1
    )
    return eff_opacity(el) <= 0.35 or bleeds


def _source_composites(src: Sequence[Dict[str, Any]], cw: float, ch: float) -> List[Tuple[str, str]]:
    """(shape_id, text_id) pairs the designer meant to stay together: explicit
    groups plus un-grouped text sitting inside a foreground shape."""
    pairs: List[Tuple[str, str]] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for el in src:
        if el.get("group_id"):
            groups.setdefault(str(el["group_id"]), []).append(el)
    seen = set()
    for members in groups.values():
        shapes = [m for m in members if m.get("type") in ("shape", "image")]
        texts = [m for m in members if m.get("type") == "text"]
        if shapes and texts:
            sh = max(shapes, key=lambda m: float(m["width"]) * float(m["height"]))
            for t in texts:
                pairs.append((sh["id"], t["id"]))
                seen.add(t["id"])
    for sh in src:
        if sh.get("type") != "shape" or sh.get("group_id") or is_decorative(sh, cw, ch):
            continue
        if float(sh["width"]) * float(sh["height"]) >= 0.30 * cw * ch:
            continue
        for t in src:
            if t.get("type") != "text" or t["id"] in seen or t.get("group_id"):
                continue
            tcx = float(t["x"]) + float(t["width"]) / 2.0
            tcy = float(t["y"]) + float(t["height"]) / 2.0
            t_rect = (float(t["x"]), float(t["y"]), float(t["x"]) + float(t["width"]), float(t["y"]) + float(t["height"]))
            s_rect = (float(sh["x"]), float(sh["y"]), float(sh["x"]) + float(sh["width"]), float(sh["y"]) + float(sh["height"]))
            if (
                float(sh["x"]) <= tcx <= float(sh["x"]) + float(sh["width"])
                and float(sh["y"]) <= tcy <= float(sh["y"]) + float(sh["height"])
                and _inter(t_rect, s_rect) / max(1.0, _rect_area(t_rect)) >= 0.6
                and eff_opacity(sh) > 0.35
                and float(t.get("z_index", 0) or 0) >= float(sh.get("z_index", 0) or 0)
            ):
                pairs.append((sh["id"], t["id"]))
                seen.add(t["id"])
    return pairs


def min_readable_px(W: float, H: float) -> float:
    """Smallest font size that still counts as readable on this canvas."""
    return readable_floor(W, H)


def audit(
    source: Dict[str, Any],
    output: Dict[str, Any],
    assets_dir: str | Path | None = None,
) -> Dict[str, Any]:
    """Score `output` against `source`. Returns a dict with ``score`` (0-100),
    structured ``issues`` and the legacy per-check keys used by the tests/UI."""
    sw, sh = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    W, H = float(output["canvas"]["width"]), float(output["canvas"]["height"])
    src_els = source["elements"]
    out_els = output["elements"]
    src_by_id = {e["id"]: e for e in src_els}
    out_by_id = {e["id"]: e for e in out_els}

    issues: List[Dict[str, Any]] = []
    penalty = 0.0

    def flag(code: str, ids: Sequence[str], pen: float, detail: str = "") -> None:
        nonlocal penalty
        penalty += pen
        issues.append({"code": code, "ids": list(ids), "penalty": round(pen, 1), "detail": detail})

    # --- identity -----------------------------------------------------------
    id_match = (
        set(src_by_id) == set(out_by_id)
        and len(src_els) == len(out_els)
        and len(out_by_id) == len(out_els)
    )
    if not id_match:
        flag("id_mismatch", sorted(set(src_by_id) ^ set(out_by_id)), _P_ID, "ids added/removed/duplicated")
        return _pack(issues, penalty, id_match, [], 0.0, [], [], [])

    # --- brand / content fidelity ------------------------------------------
    for e in out_els:
        s = src_by_id[e["id"]]
        for f in IMMUTABLE_FIELDS:
            if s.get(f) != e.get(f):
                flag("brand_changed", [e["id"]], _P_BRAND, f"{f}: {s.get(f)!r} -> {e.get(f)!r}")

    # --- bounds ---------------------------------------------------------------
    off_canvas: List[str] = []
    for e in out_els:
        x0, y0 = float(e["x"]), float(e["y"])
        x1, y1 = x0 + float(e["width"]), y0 + float(e["height"])
        if min(x0, y0) < -0.5 or x1 > W + 0.5 or y1 > H + 0.5:
            off_canvas.append(e["id"])
            flag("off_canvas", [e["id"]], _P_OFFCANVAS)
            continue
        rr = _rotated_rect(x0, y0, x1 - x0, y1 - y0, float(e.get("rotation", 0.0) or 0.0))
        if rr[0] < -0.5 or rr[1] < -0.5 or rr[2] > W + 0.5 or rr[3] > H + 0.5:
            off_canvas.append(e["id"])
            flag("off_canvas_rotated", [e["id"]], _P_OFFCANVAS)

    # --- image proportions ----------------------------------------------------
    max_ar_err = 0.0
    for e in out_els:
        if e.get("type") != "image" or e.get("fit", "contain") == "cover":
            continue
        s = src_by_id[e["id"]]
        sar = float(s["width"]) / max(1e-6, float(s["height"]))
        oar = float(e["width"]) / max(1e-6, float(e["height"]))
        err = abs(oar - sar) / max(0.01, sar) * 100.0
        max_ar_err = max(max_ar_err, err)
        if err > 5.0:
            flag("image_distorted", [e["id"]], _P_AR, f"{err:.1f}% aspect error")

    # --- classify ------------------------------------------------------------
    min_z = min((float(e.get("z_index", 0) or 0) for e in src_els), default=0.0)
    bg_ids = {e["id"] for e in src_els if is_background(e, sw, sh, min_z)}
    dec_ids = {e["id"] for e in src_els if is_decorative(e, sw, sh)}
    composites = _source_composites(src_els, sw, sh)
    comp_pairs = {frozenset(p) for p in composites}
    # "drop-shadow" duplicates (same words, same size, nudged) are one visual unit
    texts_src = [e for e in src_els if e.get("type") == "text" and not e.get("group_id")]
    for a_ in texts_src:
        for b_ in texts_src:
            if a_["id"] < b_["id"] and a_.get("content") == b_.get("content") and abs(
                float(a_.get("font_size", 0)) - float(b_.get("font_size", 0))
            ) < 0.01 and abs(float(a_["x"]) - float(b_["x"])) <= 0.2 * float(a_.get("font_size", 1)) \
                    and abs(float(a_["y"]) - float(b_["y"])) <= 0.2 * float(a_.get("font_size", 1)):
                comp_pairs.add(frozenset((a_["id"], b_["id"])))

    # --- text fit & readability ----------------------------------------------
    overflows: List[str] = []
    tiny: List[str] = []
    ink: Dict[str, Rect] = {}
    floor_px = min_readable_px(W, H)
    for e in out_els:
        if e.get("type") == "text":
            fs = float(e.get("font_size", 12))
            m = measure_text_block(e, fs, float(e["width"]))
            r = text_ink_rect(e)
            ink[e["id"]] = r
            if m.overflows_width:
                overflows.append(e["id"])
                flag("text_overflow_width", [e["id"]], _P_OVERFLOW_W, f"line {m.max_line_width:.0f}px > box {float(e['width']):.0f}px")
            elif text_ink_rect(e, rotate=False)[3] > float(e["y"]) + float(e["height"]) + max(2.0, 0.12 * fs):
                overflows.append(e["id"])
                flag("text_overflow_height", [e["id"]], _P_OVERFLOW_H, "glyphs spill below declared box")
            if r[0] < -0.5 or r[1] < -0.5 or r[2] > W + 0.5 or r[3] > H + 0.5:
                flag("text_ink_off_canvas", [e["id"]], _P_TEXT_OFF)
            if fs < 0.88 * floor_px and not (float(e.get("opacity", 1.0) or 0) <= 0.01):
                tiny.append(e["id"])
                flag("text_too_small", [e["id"]], _P_TINY if fs >= 5 else _P_TINY * 1.5, f"{fs:.0f}px < {0.88 * floor_px:.0f}px")
        else:
            ink[e["id"]] = ink_rect(e, assets_dir)

    # --- composite integrity (button label stays on its shape) ---------------
    for shape_id, text_id in composites:
        sh_el, tx_el = out_by_id[shape_id], out_by_id[text_id]
        sr = _rotated_rect(float(sh_el["x"]), float(sh_el["y"]), float(sh_el["width"]),
                           float(sh_el["height"]), float(sh_el.get("rotation", 0.0) or 0.0))
        tr = ink[text_id]
        ta = max(1.0, _rect_area(tr))
        if _inter(sr, tr) / ta < 0.9:
            flag("label_off_shape", [shape_id, text_id], _P_COMPOSITE)

    # --- collisions -----------------------------------------------------------
    def invisible(e: Dict[str, Any]) -> bool:
        return eff_opacity(e) <= 0.01

    fg = [
        e for e in out_els
        if e["id"] not in bg_ids and e["id"] not in dec_ids and not invisible(e)
    ]
    collisions: List[Tuple[str, str, float]] = []
    for i in range(len(fg)):
        for j in range(i + 1, len(fg)):
            a, b = fg[i], fg[j]
            if a.get("group_id") and a.get("group_id") == b.get("group_id"):
                continue
            if frozenset((a["id"], b["id"])) in comp_pairs:
                continue
            ra, rb = ink[a["id"]], ink[b["id"]]
            inter = _inter(ra, rb)
            if inter <= 0:
                continue
            frac = inter / max(1.0, min(_rect_area(ra), _rect_area(rb)))
            if frac <= 0.02:
                continue
            # An overlap the designer clearly intended (one element sitting
            # mostly inside another in the source) is not a collision.
            sa, sb = src_by_id[a["id"]], src_by_id[b["id"]]
            s_ra, s_rb = ink_rect(sa, assets_dir), ink_rect(sb, assets_dir)
            s_inter = _inter(s_ra, s_rb)
            s_min = max(1.0, min(_rect_area(s_ra), _rect_area(s_rb)))
            if s_inter / s_min > 0.5 and not (
                {"text"} == {a.get("type"), b.get("type")}
            ):
                continue
            collisions.append((a["id"], b["id"], round(frac, 3)))
            flag("collision", [a["id"], b["id"]], min(18.0, 5.0 + 25.0 * frac), f"{frac:.0%} overlap")

    # --- hierarchy: bigger source text must not end up smaller than smaller text
    label_ids = {t for _, t in composites}
    loose_texts = [e for e in src_els if e.get("type") == "text" and not e.get("group_id")
                   and e["id"] not in bg_ids and e["id"] not in label_ids]
    for i in range(len(loose_texts)):
        for j in range(len(loose_texts)):
            a, b = loose_texts[i], loose_texts[j]
            if float(a["font_size"]) >= 1.4 * float(b["font_size"]):
                fa = float(out_by_id[a["id"]]["font_size"])
                fb = float(out_by_id[b["id"]]["font_size"])
                if fa < fb:
                    flag("hierarchy_inverted", [a["id"], b["id"]], _P_HIERARCHY)
    # ...and a button/badge label must not outshout the headline, unless the
    # label is only being held at the readable floor
    if loose_texts:
        head = max(loose_texts, key=lambda t: float(t["font_size"]))
        h_out = float(out_by_id[head["id"]]["font_size"])
        for lid in sorted(label_ids):
            lab = src_by_id[lid]
            l_out = float(out_by_id[lid]["font_size"])
            if float(lab.get("font_size", 0)) < float(head["font_size"]) and l_out > h_out and l_out > floor_px:
                flag("hierarchy_inverted", [head["id"], lid], _P_HIERARCHY, "label larger than headline")

    # --- undersized vs. naive uniform scaling --------------------------------
    s_contain = min(W / sw, H / sh)
    if loose_texts:
        big = max(loose_texts, key=lambda t: float(t["font_size"]))
        want = float(big["font_size"]) * s_contain
        got = float(out_by_id[big["id"]]["font_size"])
        if got < 0.55 * want and want > floor_px:
            flag("undersized_headline", [big["id"]], _P_UNDERSIZED, f"{got:.0f}px vs naive-scale {want:.0f}px")

    # --- margins ---------------------------------------------------------------
    edge = 0.012 * min(W, H)
    for e in fg:
        r = ink[e["id"]]
        if e.get("type") == "shape" and not e.get("group_id"):
            continue
        if r[0] < edge * 0.5 or r[1] < edge * 0.5 or r[2] > W - edge * 0.5 or r[3] > H - edge * 0.5:
            flag("edge_hugging", [e["id"]], _P_EDGE)

    return _pack(issues, penalty, id_match, off_canvas, max_ar_err, collisions, overflows, tiny)


_LOGO_WORDS = ("logo", "wordmark", "lockup", "brandmark")


def _empty_band(rects: Sequence[Rect], W: float, H: float) -> float:
    """Largest share of the canvas height or width (outer 4% margins ignored)
    that no visible foreground pixel touches."""
    def longest(spans: List[Tuple[float, float]], lo: float, hi: float) -> float:
        best, cur = 0.0, lo
        for a, b in sorted(spans):
            if b <= cur:
                continue
            if a > cur:
                best = max(best, min(a, hi) - cur)
            cur = max(cur, b)
            if cur >= hi:
                break
        return max(best, hi - cur)
    v = longest([(r[1], r[3]) for r in rects], 0.04 * H, 0.96 * H) / H
    h = longest([(r[0], r[2]) for r in rects], 0.04 * W, 0.96 * W) / W
    return max(0.0, v, h)


def fidelity(
    source: Dict[str, Any],
    output: Dict[str, Any],
    assets_dir: str | Path | None = None,
) -> float:
    """0..1: how close each visible element stays to its place *in the frame*
    (canvas-normalised centre) and to its uniformly scaled size, weighted by how
    close the target's shape is to the source's. A designer expects a
    near-identical format to look the same; a radically different one is
    allowed to rearrange freely."""
    W, H = float(output["canvas"]["width"]), float(output["canvas"]["height"])
    sw, sh = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    closeness = max(0.0, 1.0 - abs(math.log((W / H) / (sw / sh))) / math.log(1.5))
    if closeness <= 0:
        return 0.0
    min_z = min((float(e.get("z_index", 0) or 0) for e in source["elements"]), default=0.0)
    out_by_id = {e["id"]: e for e in output["elements"]}
    s_uniform = min(W / sw, H / sh)
    d: List[float] = []
    dev: List[float] = []
    for e in source["elements"]:
        if is_background(e, sw, sh, min_z) or is_decorative(e, sw, sh) or eff_opacity(e) <= 0.01:
            continue
        o = out_by_id[e["id"]]
        a, b = ink_rect(e, assets_dir), ink_rect(o, assets_dir)
        d.append(math.hypot((a[0] + a[2]) / 2 / sw - (b[0] + b[2]) / 2 / W, (a[1] + a[3]) / 2 / sh - (b[1] + b[3]) / 2 / H))
        if e.get("type") == "text":
            ratio = float(o["font_size"]) / max(1e-6, float(e["font_size"]) * s_uniform)
        else:
            ratio = math.sqrt(max(1e-6, _rect_area(b)) / max(1e-6, _rect_area(a))) / s_uniform
        dev.append(abs(math.log(max(1e-6, ratio))))
    if not d:
        return 0.0
    place = max(0.0, 1.0 - (sum(d) / len(d)) / 0.12)
    size = max(0.0, 1.0 - (sum(dev) / len(dev)) / 0.35)
    return closeness * place * size


def aesthetic(
    source: Dict[str, Any],
    output: Dict[str, Any],
    assets_dir: str | Path | None = None,
) -> float:
    """Up to ~29 points for how *designed* a layout looks, beyond being
    collision-free: a prominent hero and a visible logo (opaque pixels, not
    padded boxes), text comfortably above the readable floor, content that uses
    the canvas without dead bands, and - for near-same shapes - staying faithful
    to the source composition. Only used to choose between candidate layouts."""
    W, H = float(output["canvas"]["width"]), float(output["canvas"]["height"])
    sw, sh = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    min_z = min((float(e.get("z_index", 0) or 0) for e in source["elements"]), default=0.0)
    src_by_id = {e["id"]: e for e in source["elements"]}
    skip = {e["id"] for e in source["elements"]
            if is_background(e, sw, sh, min_z) or is_decorative(e, sw, sh)}
    fg = [e for e in output["elements"] if e["id"] not in skip and eff_opacity(e) > 0.01]
    if not fg:
        return 0.0

    # 1. hero prominence: the biggest foreground picture should own a real share
    #    (measured on its opaque pixels - transparent padding is not presence)
    def logo_like(e: Dict[str, Any]) -> bool:
        r = ink_rect(e, assets_dir)
        return any(k in str(e.get("asset_id") or "").lower() for k in _LOGO_WORDS) or (
            (r[2] - r[0]) / max(1.0, r[3] - r[1]) >= 2.5)

    images = [e for e in fg if e.get("type") == "image" and e.get("asset_id")]
    pics = [e for e in images if not logo_like(e)]
    hero = 8.0
    if pics:
        biggest = max(_rect_area(ink_rect(e, assets_dir)) for e in pics)
        src_share = max(_rect_area(ink_rect(src_by_id[e["id"]], assets_dir)) for e in pics) / (sw * sh)
        want = min(0.25, max(0.04, 0.8 * src_share))
        hero = 8.0 * min(1.0, (biggest / (W * H)) / want) ** 0.6

    # 1b. the brand mark stays legible: visible height vs. the short side
    logo = 3.0
    logos = [e for e in images if logo_like(e)]
    if logos:
        r = max((ink_rect(e, assets_dir) for e in logos), key=_rect_area)
        logo = 3.0 * min(1.0, ((r[3] - r[1]) / min(W, H)) / 0.06) ** 0.6

    # 2. text comfortably readable (bigger is better up to 1.8x the floor) and a
    #    headline that actually stands out
    floor = readable_floor(W, H)
    texts = [e for e in fg if e.get("type") == "text"]
    legib = 8.0
    if texts:
        body = sum(min(1.0, float(e["font_size"]) / (1.8 * floor)) for e in texts) / len(texts)
        top = max(float(e["font_size"]) for e in texts)
        legib = 3.0 * body + 5.0 * min(1.0, top / (0.075 * min(W, H)))

    # 3. the composition spans the canvas rather than huddling in a corner
    rects = [ink_rect(e, assets_dir) for e in fg]
    x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
    x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
    span = min(1.0, ((x1 - x0) / W + (y1 - y0) / H) / 1.5)
    cx_off = abs((x0 + x1) / 2.0 - W / 2.0) / W
    cy_off = abs((y0 + y1) / 2.0 - H / 2.0) / H
    fill = 4.0 * span * max(0.0, 1.0 - 2.0 * (cx_off + cy_off))
    # 4. no dead band of empty canvas inside the composition
    dead = 3.0 * min(1.0, max(0.0, _empty_band(rects, W, H) - 0.12) / 0.2)
    # 5. near-same formats should look like the source
    faithful = 8.0 * fidelity(source, output, assets_dir)
    return round(hero + logo + legib + fill - dead + faithful, 2)


def _pack(
    issues: List[Dict[str, Any]],
    penalty: float,
    id_match: bool,
    off_canvas: List[str],
    max_ar_err: float,
    collisions: List[Tuple[str, str, float]],
    overflows: List[str],
    tiny: List[str],
) -> Dict[str, Any]:
    score = max(0.0, round(100.0 - penalty, 1))
    return {
        "score": score,
        # unclipped, so candidates that are all "failing" can still be ranked
        "penalty": round(penalty, 1),
        "issues": issues,
        "id_match": id_match,
        "off_canvas": off_canvas,
        "max_image_ar_error_pct": round(max_ar_err, 3),
        "collisions": collisions,
        "text_overflows": overflows,
        "tiny_text": tiny,
    }
