"""Scene Graph & Semantic Role Analyzer for CHAMELEON designs.

Infers the structural and visual role of every element purely from its
geometry, stacking order, grouping, opacity, asset properties, and typographic
hierarchy — without relying on hardcoded element IDs.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image

from engine.visible import NO_INSETS, Insets, visible_fill_density, visible_fraction, visible_insets
from renderer.converter.assets import asset_path
from renderer.converter.colorutil import parse_rgba

def clean_element_copy(el: Dict[str, Any]) -> Dict[str, Any]:
    """Lossless deep copy of an element.

    Every key is kept - including ones outside the v0.9 schema that the
    renderer still honours (e.g. ``capitalize``) - so a resize can never
    silently change how an element looks. The engine only ever rewrites
    geometry (x/y/width/height) and ``font_size``.
    """
    return copy.deepcopy(el)


@dataclass
class CompositeUnit:
    """A designer-intended group of elements (e.g., a button/badge shape + text label)."""

    group_id: str
    elements: List[Dict[str, Any]]
    shape_el: Optional[Dict[str, Any]] = None
    label_el: Optional[Dict[str, Any]] = None
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    orig_label_fs: float = 16.0
    # False for the classic "shape + one label" pill/ribbon; True for cards,
    # icon+caption groups, lone rotated text... which are scaled as a whole.
    complex: bool = False
    # Layout plan for a card that is re-wrapped instead of uniformly scaled.
    plan: Optional[Dict[str, Any]] = None

    @property
    def is_card(self) -> bool:
        """A panel with two or more text blocks on it (and nothing else)."""
        texts = [m for m in self.elements if m.get("type") == "text"]
        others = [m for m in self.elements if m.get("type") != "text" and m is not self.shape_el]
        return self.shape_el is not None and len(texts) >= 2 and not others

    @property
    def cx(self) -> float:
        return self.x + self.width / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.height / 2.0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def aspect_ratio(self) -> float:
        return self.width / max(1.0, self.height)


@dataclass
class AnalyzedDesign:
    """Structured decomposition of a source design."""

    src_w: float
    src_h: float
    all_elements_by_id: Dict[str, Dict[str, Any]]
    source_order_ids: List[str]
    # Original (x, y, w, h) of every element, captured before any layout mutates it.
    src_boxes: Dict[str, Tuple[float, float, float, float]] = field(default_factory=dict)
    # Image elements whose asset file is not in the brand kit (rendered as a placeholder).
    missing_ids: Set[str] = field(default_factory=set)
    # "left" / "center" / "right": what the designer's main copy uses.
    dominant_align: str = "center"
    # Original font size of every text element (layouts overwrite font_size in place).
    src_font_sizes: Dict[str, float] = field(default_factory=dict)
    # Drop-shadow style duplicates: primary id -> [(element, dx, dy)] offsets in
    # source px, re-attached to the primary after layout.
    followers: Dict[str, List[Tuple[Dict[str, Any], float, float]]] = field(default_factory=dict)
    # Transparent margins of every image, as fractions of its (aspect-locked) box.
    insets: Dict[str, Insets] = field(default_factory=dict)
    assets_dir: str = ""
    # Share of the source height covered by foreground content: how airy the
    # designer made it. Layouts don't grow content past roughly this density.
    fill_v: float = 1.0

    backgrounds: List[Dict[str, Any]] = field(default_factory=list)
    decoratives: List[Dict[str, Any]] = field(default_factory=list)

    logo: Optional[Dict[str, Any]] = None
    hero_images: List[Dict[str, Any]] = field(default_factory=list)
    watermarks: List[Dict[str, Any]] = field(default_factory=list)

    headline: Optional[Dict[str, Any]] = None
    subheads: List[Dict[str, Any]] = field(default_factory=list)
    cta_text: Optional[Dict[str, Any]] = None
    legal_texts: List[Dict[str, Any]] = field(default_factory=list)

    cta_unit: Optional[CompositeUnit] = None
    badge_rows: List[List[CompositeUnit]] = field(default_factory=list)
    corner_overlays: List[CompositeUnit] = field(default_factory=list)
    other_units: List[CompositeUnit] = field(default_factory=list)


def _build_composite(group_id: str, members: List[Dict[str, Any]]) -> CompositeUnit:
    shapes = [m for m in members if m.get("type") == "shape"]
    texts = [m for m in members if m.get("type") == "text"]
    shape_el = max(shapes, key=lambda s: s["width"] * s["height"]) if shapes else None
    label_el = max(texts, key=lambda t: t.get("font_size", 12)) if texts else None
    orig_fs = float(label_el.get("font_size", 16.0)) if label_el is not None else 16.0

    if shape_el is not None:
        x = float(shape_el["x"])
        y = float(shape_el["y"])
        w = float(shape_el["width"])
        h = float(shape_el["height"])
    else:
        x = min(float(m["x"]) for m in members)
        y = min(float(m["y"]) for m in members)
        x1 = max(float(m["x"]) + float(m["width"]) for m in members)
        y1 = max(float(m["y"]) + float(m["height"]) for m in members)
        w = max(1.0, x1 - x)
        h = max(1.0, y1 - y)

    rot = 0.0
    for m in members:
        r = float(m.get("rotation", 0.0) or 0.0)
        if abs(r) > abs(rot):
            rot = r

    simple = (
        len(texts) == 1 and len(shapes) == 1 and len(members) == 2
    ) or (len(members) == 1 and len(shapes) == 1)
    return CompositeUnit(
        group_id=group_id,
        elements=members,
        shape_el=shape_el,
        label_el=label_el,
        x=x,
        y=y,
        width=w,
        height=h,
        rotation=rot,
        orig_label_fs=orig_fs,
        complex=not simple,
    )


def analyze_design(
    source: Dict[str, Any],
    assets_dir: str | Path = "brand_kit/assets",
) -> AnalyzedDesign:
    """Decompose `source` design into semantic layers and composite units."""
    src_w = float(source["canvas"]["width"])
    src_h = float(source["canvas"]["height"])
    src_area = max(1.0, src_w * src_h)

    elements = [clean_element_copy(el) for el in source["elements"]]
    by_id = {el["id"]: el for el in elements}
    order_ids = [el["id"] for el in elements]

    result = AnalyzedDesign(
        src_w=src_w,
        src_h=src_h,
        all_elements_by_id=by_id,
        source_order_ids=order_ids,
        src_boxes={
            el["id"]: (float(el["x"]), float(el["y"]), float(el["width"]), float(el["height"]))
            for el in elements
        },
        src_font_sizes={
            el["id"]: float(el.get("font_size", 12) or 12) for el in elements if el.get("type") == "text"
        },
    )

    assigned_ids: Set[str] = set()
    min_z = min((el.get("z_index", 0) for el in elements), default=0)

    # 1. Identify full-bleed background elements
    for el in elements:
        if el.get("type") in ("image", "shape") and not el.get("group_id"):
            covers_canvas = (
                el["width"] >= 0.85 * src_w
                and el["height"] >= 0.85 * src_h
                and el["x"] <= 0.08 * src_w
                and el["y"] <= 0.08 * src_h
            )
            is_cover_bg = el.get("fit") == "cover" and (el["width"] * el["height"]) >= 0.65 * src_area
            # Anything that blankets the canvas (a gradient scrim, a texture, a frame)
            # is part of the background, whatever its z-order.
            if (covers_canvas and abs(float(el.get("rotation", 0) or 0)) < 1.0) or (
                is_cover_bg and el.get("z_index", 0) <= min_z + 1
            ):
                result.backgrounds.append(el)
                assigned_ids.add(el["id"])

    # 1b. Layered duplicates ("drop-shadow" text: same words, same size, nudged a few px)
    loose = [e for e in elements if e["id"] not in assigned_ids and not e.get("group_id") and e.get("type") == "text"]
    for hi in sorted(loose, key=lambda e: -float(e.get("z_index", 0) or 0)):
        if hi["id"] in assigned_ids:
            continue
        for lo in loose:
            if lo is hi or lo["id"] in assigned_ids or lo["id"] in result.followers:
                continue
            if (
                lo.get("content") == hi.get("content")
                and lo.get("font_family") == hi.get("font_family")
                and abs(float(lo.get("font_size", 0)) - float(hi.get("font_size", 0))) < 0.01
                and float(lo.get("z_index", 0) or 0) <= float(hi.get("z_index", 0) or 0)
            ):
                dx = float(lo["x"]) - float(hi["x"])
                dy = float(lo["y"]) - float(hi["y"])
                if abs(dx) <= 0.2 * float(hi["font_size"]) and abs(dy) <= 0.2 * float(hi["font_size"]):
                    result.followers.setdefault(hi["id"], []).append((lo, dx, dy))
                    assigned_ids.add(lo["id"])

    # 2. Identify explicit group_id composites
    groups_map: Dict[str, List[Dict[str, Any]]] = {}
    for el in elements:
        if el["id"] in assigned_ids:
            continue
        gid = el.get("group_id")
        if gid:
            groups_map.setdefault(str(gid), []).append(el)
            assigned_ids.add(el["id"])

    composites: List[CompositeUnit] = [
        _build_composite(gid, members) for gid, members in groups_map.items()
    ]

    # 3. Identify implicit shape+text composites (when group_id wasn't set).
    #    A real button/badge is a reasonably opaque shape whose label box sits
    #    (almost) entirely inside it - a faint circle behind a headline is not.
    def _eff_opacity(sh_el: Dict[str, Any]) -> float:
        _, _, _, fa = parse_rgba(sh_el.get("fill_color"), default=(255, 255, 255, 1.0))
        return float(sh_el.get("opacity", 1.0)) * fa

    rem_shapes = [
        el for el in elements
        if el["id"] not in assigned_ids
        and el.get("type") == "shape"
        and _eff_opacity(el) > 0.35
        and (el["width"] * el["height"]) < 0.30 * src_area
    ]
    rem_texts = [
        el for el in elements
        if el["id"] not in assigned_ids and el.get("type") == "text"
    ]
    for sh in rem_shapes:
        sx0, sy0 = float(sh["x"]), float(sh["y"])
        sx1, sy1 = sx0 + float(sh["width"]), sy0 + float(sh["height"])
        contained = []
        for tx in rem_texts:
            if tx["id"] in assigned_ids:
                continue
            tx0, ty0 = float(tx["x"]), float(tx["y"])
            tx1, ty1 = tx0 + float(tx["width"]), ty0 + float(tx["height"])
            inter = max(0.0, min(sx1, tx1) - max(sx0, tx0)) * max(0.0, min(sy1, ty1) - max(sy0, ty0))
            t_area = max(1.0, (tx1 - tx0) * (ty1 - ty0))
            tcx, tcy = (tx0 + tx1) / 2.0, (ty0 + ty1) / 2.0
            if (
                sx0 <= tcx <= sx1 and sy0 <= tcy <= sy1
                and inter / t_area >= 0.6
                and tx.get("z_index", 0) >= sh.get("z_index", 0)
            ):
                contained.append(tx)
        if len(contained) == 1:
            tx = contained[0]
            assigned_ids.add(sh["id"])
            assigned_ids.add(tx["id"])
            composites.append(_build_composite(f"_implicit_{sh['id']}", [sh, tx]))

    # 3b. A lone rotated text (a tilted sticker/tagline) travels as one unit
    for el in elements:
        if (
            el["id"] not in assigned_ids
            and el.get("type") == "text"
            and abs(float(el.get("rotation", 0.0) or 0.0)) > 1.0
        ):
            assigned_ids.add(el["id"])
            composites.append(_build_composite(f"_rot_{el['id']}", [el]))

    # 4. Identify decorative / ambient shapes
    fg_min_z = min(
        (
            el.get("z_index", 0)
            for el in elements
            if el["id"] not in assigned_ids and el.get("type") in ("text", "image")
        ),
        default=999,
    )
    for el in elements:
        if el["id"] in assigned_ids:
            continue
        if el.get("type") == "shape":
            op = float(el.get("opacity", 1.0))
            _, _, _, fill_alpha = parse_rgba(el.get("fill_color"), default=(255, 255, 255, 1.0))
            eff_op = op * fill_alpha
            bleeds = (
                el["x"] < -1.0
                or el["y"] < -1.0
                or (el["x"] + el["width"]) > src_w + 1.0
                or (el["y"] + el["height"]) > src_h + 1.0
            )
            if eff_op <= 0.35 or bleeds or el.get("z_index", 0) < fg_min_z:
                result.decoratives.append(el)
                assigned_ids.add(el["id"])
            else:
                # Standalone foreground shape treated as a single-element composite
                assigned_ids.add(el["id"])
                composites.append(_build_composite(f"_shape_{el['id']}", [el]))

    # 5. Classify composites into Corner Overlays, Badge Rows, and Primary CTA
    non_corner_composites: List[CompositeUnit] = []
    max_comp_h = max((c.height for c in composites), default=0.0)

    for comp in composites:
        nx = comp.cx / src_w
        ny = comp.cy / src_h
        is_rotated = abs(comp.rotation) > 1.0
        is_top_corner = (
            ny < 0.25
            and (nx < 0.30 or nx > 0.70)
            and (len(composites) > 1 and comp.height < max_comp_h * 0.85)
        )
        if is_rotated or is_top_corner:
            result.corner_overlays.append(comp)
        else:
            non_corner_composites.append(comp)

    # Group non-corner composites into horizontal rows if multiple share similar y and height
    used_comp_ids: Set[str] = set()
    non_corner_composites.sort(key=lambda c: (c.y, c.x))
    for i, c1 in enumerate(non_corner_composites):
        if c1.group_id in used_comp_ids:
            continue
        row = [c1]
        for j in range(i + 1, len(non_corner_composites)):
            c2 = non_corner_composites[j]
            if c2.group_id in used_comp_ids:
                continue
            same_y = abs(c1.cy - c2.cy) <= max(c1.height, c2.height) * 0.75
            similar_h = 0.65 <= (c1.height / max(1.0, c2.height)) <= 1.55
            if same_y and similar_h:
                row.append(c2)
        if len(row) >= 2:
            row.sort(key=lambda c: c.x)
            result.badge_rows.append(row)
            for c in row:
                used_comp_ids.add(c.group_id)

    rem_composites = [c for c in non_corner_composites if c.group_id not in used_comp_ids]
    if rem_composites:
        # Prefer composites that actually have a text label as the Primary CTA
        labeled_comps = [c for c in rem_composites if c.label_el is not None]
        if labeled_comps:
            labeled_comps.sort(key=lambda c: (c.height * 2.0 + c.width * 0.2 + c.y * 0.05), reverse=True)
            result.cta_unit = labeled_comps[0]
            for c in rem_composites:
                if c.group_id != result.cta_unit.group_id:
                    result.other_units.append(c)
        else:
            rem_composites.sort(key=lambda c: (c.height * 2.0 + c.y * 0.05), reverse=True)
            result.cta_unit = rem_composites[0]
            if len(rem_composites) > 1:
                result.other_units.extend(rem_composites[1:])

    # 6. Classify Foreground Images (Logo, Hero Product, Watermark/Missing)
    fg_images = [
        el for el in elements
        if el["id"] not in assigned_ids and el.get("type") == "image"
    ]
    for el in fg_images:
        assigned_ids.add(el["id"])

    resolved_assets_dir = Path(assets_dir)
    if not resolved_assets_dir.is_dir():
        alt_dir = Path(__file__).resolve().parent.parent / "brand_kit" / "assets"
        if alt_dir.is_dir():
            resolved_assets_dir = alt_dir

    primary_images: List[Dict[str, Any]] = []
    for img in fg_images:
        aid = img.get("asset_id")
        exists = asset_path(str(resolved_assets_dir), aid) is not None if aid else False
        nx = (float(img["x"]) + float(img["width"]) / 2.0) / src_w
        ny = (float(img["y"]) + float(img["height"]) / 2.0) / src_h
        area_frac = (float(img["width"]) * float(img["height"])) / src_area
        is_bottom_corner_small = ny > 0.78 and (nx < 0.28 or nx > 0.72) and area_frac < 0.045

        if not exists:
            # Keep the element exactly as the designer left it (opacity included):
            # hiding it would silently drop content. It is simply parked as a small,
            # non-colliding corner badge by the layouts.
            result.missing_ids.add(img["id"])
            result.watermarks.append(img)
        elif is_bottom_corner_small:
            result.watermarks.append(img)
        else:
            primary_images.append(img)

    def _native_ar(img: Dict[str, Any]) -> float:
        """Aspect ratio of the asset file itself (falls back to the element box)."""
        aid = img.get("asset_id")
        path = asset_path(str(resolved_assets_dir), aid) if aid else None
        if path is not None:
            try:
                with Image.open(path) as im:
                    return im.width / max(1.0, float(im.height))
            except Exception:  # noqa: BLE001 - unreadable asset: use the box
                pass
        return float(img["width"]) / max(1.0, float(img["height"]))

    result.assets_dir = str(resolved_assets_dir)
    for el in elements:
        if el.get("type") == "image":
            result.insets[el["id"]] = visible_insets(el, resolved_assets_dir)

    def _logo_evidence(im: Dict[str, Any]) -> float:
        """Score how much an image looks like a brand logo/wordmark (>= 2 means yes)."""
        w, h = float(im["width"]), float(im["height"])
        fw, fh = visible_fraction(result.insets.get(im["id"], NO_INSETS))
        vis_ar = (w * fw) / max(1.0, h * fh)
        vis_area_frac = (w * fw * h * fh) / src_area
        nx = (float(im["x"]) + w / 2.0) / src_w
        ny = (float(im["y"]) + h / 2.0) / src_h
        area_frac = (w * h) / src_area
        name = str(im.get("asset_id") or "").lower()
        score = 0.0
        if any(k in name for k in ("logo", "wordmark", "lockup", "brandmark", "emblem")):
            score += 2.5
        if max(w / max(1.0, h), _native_ar(im), vis_ar) >= 1.8:
            score += 1.5          # wide wordmark proportions
        if visible_fill_density(im, resolved_assets_dir) < 0.45:
            score += 1.0          # sparse letterform/icon strokes vs. solid product
        if area_frac < 0.05 or vis_area_frac < 0.03:
            score += 0.5          # small
        if ny < 0.3 or nx < 0.22 or nx > 0.78:
            score += 0.5          # sits where logos sit
        return score

    if primary_images:
        scored = sorted(primary_images, key=_logo_evidence, reverse=True)
        best = scored[0]
        if _logo_evidence(best) >= 2.0:
            result.logo = best
            result.hero_images.extend(im for im in primary_images if im is not best)
        else:
            result.hero_images.extend(primary_images)

    # 7. Classify Ungrouped Text Elements (Headline, Subheads, CTA text, Legal/Footer)
    fg_texts = [
        el for el in elements
        if el["id"] not in assigned_ids and el.get("type") == "text"
    ]
    for el in fg_texts:
        assigned_ids.add(el["id"])

    if fg_texts:
        max_fs = max(float(t.get("font_size", 12)) for t in fg_texts)
        main_texts: List[Dict[str, Any]] = []

        for t in fg_texts:
            fs = float(t.get("font_size", 12))
            ny = (float(t["y"]) + float(t["height"]) / 2.0) / src_h
            _, _, _, alpha = parse_rgba(t.get("color"), default=(255, 255, 255, 1.0))
            eff_alpha = alpha * float(t.get("opacity", 1.0))
            is_legal = (
                ny > 0.78
                and len(fg_texts) >= 2
                and (fs <= 15.0 or fs <= max_fs * 0.38 or eff_alpha <= 0.75)
            )
            if is_legal:
                result.legal_texts.append(t)
            else:
                main_texts.append(t)

        if not main_texts and result.legal_texts:
            main_texts.append(result.legal_texts.pop(0))

        if main_texts:
            headline = max(main_texts, key=lambda t: (float(t.get("font_size", 12)), -float(t["y"])))
            result.headline = headline
            rem_main = [t for t in main_texts if t["id"] != headline["id"]]
            rem_main.sort(key=lambda t: float(t["y"]))

            if result.cta_unit is None and rem_main:
                last_t = rem_main[-1]
                r, g, b, _ = parse_rgba(last_t.get("color"), default=(255, 255, 255, 1.0))
                is_colored = (max(r, g, b) - min(r, g, b)) > 40
                is_bold = (last_t.get("font_weight") or "regular").lower() == "bold"
                if is_colored or (is_bold and len(str(last_t.get("content", ""))) <= 36):
                    result.cta_text = rem_main.pop(-1)

            result.subheads.extend(rem_main)

    ambient = {e["id"] for e in result.backgrounds + result.decoratives}
    spans = sorted(
        (max(0.0, float(e["y"])), min(src_h, float(e["y"]) + float(e["height"])))
        for e in elements if e["id"] not in ambient
    )
    covered, cur = 0.0, 0.0
    for lo, hi in spans:
        lo = max(lo, cur)
        if hi > lo:
            covered += hi - lo
            cur = hi
    result.fill_v = min(1.0, covered / max(1.0, src_h)) if spans else 1.0

    mains = ([result.headline] if result.headline else []) + list(result.subheads)
    if mains:
        votes: Dict[str, float] = {}
        for t in mains:
            votes[t.get("text_align", "left")] = votes.get(t.get("text_align", "left"), 0.0) + float(t.get("font_size", 12))
        result.dominant_align = max(votes, key=votes.get)
    return result
