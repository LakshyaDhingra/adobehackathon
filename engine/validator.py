"""Hard-constraint enforcement and the public quality evaluator.

``enforce_hard_constraints`` is the last step of every layout: whatever the
solver produced, the result is forced to satisfy the rules in RULES.md
(ids preserved and in order, nothing off-canvas - rotation included, pictures in
proportion, integer-pixel text sizes).  ``evaluate_layout_quality`` is the
independent auditor from ``engine.qa`` (kept under its historical name for the
tests and the studio UI).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from engine.analyzer import AnalyzedDesign, clean_element_copy
from engine.qa import audit
from engine.typography import measure_text_block


def contain_element(el: Dict[str, Any], source: Dict[str, Any], W: float, H: float) -> None:
    """Contain both the declared box and rotated footprint without distortion."""
    w, h = max(0.0, float(el["width"])), max(0.0, float(el["height"]))
    cx, cy = float(el["x"]) + w / 2, float(el["y"]) + h / 2
    if el["type"] == "image" and source.get("fit", "contain") != "cover" and min(source["width"], source["height"]) > 0:
        ratio = source["width"] / source["height"]
        if h and abs(w / h - ratio) > 1e-6:
            w = h * ratio
    angle = math.radians(float(el.get("rotation", 0) or 0))
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    ew, eh = max(w, w*c + h*s), max(h, w*s + h*c)
    factor = min(1.0, W / max(ew, 1e-9), H / max(eh, 1e-9))
    w, h, ew, eh = w*factor, h*factor, ew*factor, eh*factor
    cx = min(max(cx, ew / 2), W - ew / 2)
    cy = min(max(cy, eh / 2), H - eh / 2)
    el.update(x=round(cx-w/2, 4), y=round(cy-h/2, 4), width=round(w, 4), height=round(h, 4))
    if el["type"] == "text":
        el["font_size"] = max(1, int(round(float(el.get("font_size", 12)) * factor)))
        # A final box clamp can shorten a reflowed card. Refit the actual text
        # after that clamp so glyphs cannot spill beyond a geometrically valid
        # box on micro banners. Content and all style spans remain unchanged.
        def fits(size: int) -> bool:
            metrics = measure_text_block(el, size, el["width"])
            return not metrics.overflows_width and metrics.visual_height <= el["height"] + 0.5
        if el["font_size"] > 1 and not fits(el["font_size"]):
            lo, hi = 1, el["font_size"] - 1
            best = 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if fits(mid):
                    best, lo = mid, mid + 1
                else:
                    hi = mid - 1
            el["font_size"] = best


def enforce_hard_constraints(
    ad: AnalyzedDesign,
    source: Dict[str, Any],
    W: float,
    H: float,
) -> List[Dict[str, Any]]:
    """Return every source element, in source order, strictly inside [0,W]x[0,H]
    with non-background pictures kept at their source aspect ratio."""
    src_by_id = {el["id"]: el for el in source["elements"]}
    bg_ids = {el["id"] for el in ad.backgrounds}
    edge_pad = 0.0065 * min(W, H)

    all_units = (
        list(ad.corner_overlays)
        + ([ad.cta_unit] if ad.cta_unit is not None else [])
        + [u for row in ad.badge_rows for u in row]
        + list(ad.other_units)
    )
    for u in all_units:
        if not u.elements:
            continue
        ux0, uy0, ux1, uy1 = float("inf"), float("inf"), float("-inf"), float("-inf")
        for m in u.elements:
            mw = max(1.0, float(m.get("width", 1.0)))
            mh = max(1.0, float(m.get("height", 1.0)))
            mx, my = float(m["x"]), float(m["y"])
            mrot = float(m.get("rotation", 0.0) or 0.0)
            if abs(mrot) > 0.5:
                rad = math.radians(abs(mrot))
                ew = max(mw, mw * abs(math.cos(rad)) + mh * abs(math.sin(rad)))
                eh = max(mh, mw * abs(math.sin(rad)) + mh * abs(math.cos(rad)))
            else:
                ew, eh = mw, mh
            mcx, mcy = mx + mw / 2.0, my + mh / 2.0
            ux0 = min(ux0, mcx - ew / 2.0)
            uy0 = min(uy0, mcy - eh / 2.0)
            ux1 = max(ux1, mcx + ew / 2.0)
            uy1 = max(uy1, mcy + eh / 2.0)
        px = edge_pad if (ux1 - ux0) + 2.0 * edge_pad <= W else 0.0
        py = edge_pad if (uy1 - uy0) + 2.0 * edge_pad <= H else 0.0
        dx = (px - ux0) if ux0 < px else ((W - px - ux1) if ux1 > W - px else 0.0)
        dy = (py - uy0) if uy0 < py else ((H - py - uy1) if uy1 > H - py else 0.0)
        if abs(dx) > 1e-4 or abs(dy) > 1e-4:
            for m in u.elements:
                m["x"] = float(m["x"]) + dx
                m["y"] = float(m["y"]) + dy

    for eid, el in ad.all_elements_by_id.items():
        src_el = src_by_id[eid]
        w = max(1.0, float(el.get("width", 1.0)))
        h = max(1.0, float(el.get("height", 1.0)))

        # pictures keep their source proportions (cover/background images may crop)
        if el.get("type") == "image" and eid not in bg_ids and el.get("fit", "contain") != "cover":
            src_ar = float(src_el["width"]) / max(1.0, float(src_el["height"]))
            if abs(w / h - src_ar) / max(0.01, src_ar) > 0.01:
                cx, cy = float(el["x"]) + w / 2.0, float(el["y"]) + h / 2.0
                w = h * src_ar
                if w > W:
                    w = W
                    h = w / src_ar
                if h > H:
                    h = H
                    w = h * src_ar
                el["x"], el["y"] = round(cx - w / 2.0, 2), round(cy - h / 2.0, 2)

        w = min(W, max(1.0, w))
        h = min(H, max(1.0, h))
        rot = float(el.get("rotation", 0.0) or 0.0)
        x, y = float(el["x"]), float(el["y"])
        if abs(rot) > 0.5:
            rad = math.radians(abs(rot))
            ext_w = max(w, w * abs(math.cos(rad)) + h * abs(math.sin(rad)))
            ext_h = max(h, w * abs(math.sin(rad)) + h * abs(math.cos(rad)))
            px = edge_pad if eid not in bg_ids and ext_w + 2.0 * edge_pad <= W else 0.0
            py = edge_pad if eid not in bg_ids and ext_h + 2.0 * edge_pad <= H else 0.0
            cx = min(max(x + w / 2.0, px + ext_w / 2.0), max(px + ext_w / 2.0, W - px - ext_w / 2.0))
            cy = min(max(y + h / 2.0, py + ext_h / 2.0), max(py + ext_h / 2.0, H - py - ext_h / 2.0))
            x, y = cx - w / 2.0, cy - h / 2.0
        x = max(0.0, min(W - w, x))
        y = max(0.0, min(H - h, y))

        el["x"], el["y"] = round(x, 2), round(y, 2)
        el["width"], el["height"] = round(w, 2), round(h, 2)
        if el.get("type") == "text":
            el["font_size"] = max(1, int(round(float(el.get("font_size", 12)))))

    out_elements: List[Dict[str, Any]] = []
    for eid in ad.source_order_ids:
        el = ad.all_elements_by_id.get(eid) or clean_element_copy(src_by_id[eid])
        contain_element(el, src_by_id[eid], W, H)
        out_elements.append(el)
    return out_elements


def evaluate_layout_quality(source: Dict[str, Any], output: Dict[str, Any]) -> Dict[str, Any]:
    """Independent rubric-style audit (see ``engine.qa.audit``)."""
    return audit(source, output)
