"""
CHAMELEON Responsive Layout Engine - `engine/resize.py`

`resize(source, target_canvas)` re-lays-out a finished design for a new canvas:

  1. **Analyse** (`engine.analyzer`): infer each element's role (background,
     logo, hero art, headline, buttons/badges, ribbons, legal copy...) from
     geometry, stacking, grouping and typography - no hard-coded ids.
  2. **Generate candidates** (`engine.layouts`): one layout per plausible
     topology for the target's aspect ratio (vertical stack, split landscape,
     banner strip, compact banner; 7px-minimum variants on small canvases)
     plus a proportional candidate that keeps the source composition when the
     target has nearly the same shape.  Reflows solve a single global type
     scale against exact Skia text metrics and size pictures by their visible
     (non-transparent) pixels; spare room then grows the hero and the type,
     up to the density of the source.  Labels never outgrow the headline.
  3. **Enforce** (`engine.validator`): ids/order preserved, everything inside the
     canvas (rotation included), pictures in proportion.
  4. **Choose**: every candidate is scored by the independent auditor
     (`engine.qa`: collisions, overflow, readability, hierarchy, brand fidelity)
     plus an aesthetic term (visible hero and logo size, legibility, use of
     the canvas, fidelity to the source on near-same formats); the best wins.

If anything raises, the engine falls back to a uniform "contain" scale of the
source so a valid answer is always returned.
"""

from __future__ import annotations

import copy
import logging
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from engine.analyzer import analyze_design
from engine.contract import validate_canvas, validate_design
from engine.layouts import (
    LayoutDoesNotFit,
    enforce_label_hierarchy,
    layout_compact_banner,
    finalize_followers,
    layout_proportional,
    layout_split_landscape,
    layout_ultra_wide_banner,
    layout_vertical_stack,
)
from engine.qa import aesthetic, audit
from engine.validator import contain_element, enforce_hard_constraints

log = logging.getLogger(__name__)

_LAYOUTS: Dict[str, Callable[[Any, float, float], None]] = {
    "vertical": layout_vertical_stack,
    "split": layout_split_landscape,
    "banner": layout_ultra_wide_banner,
    "compact": layout_compact_banner,
    "compact-wide-copy": partial(layout_compact_banner, cta_fraction=0.14),
    "vertical-dense": partial(layout_vertical_stack, min_font=7.0),
    "proportional": layout_proportional,
}


def _topologies(mode: str, W: float, H: float) -> List[str]:
    """Topologies worth trying for this canvas, most natural first.  The
    proportional candidate is always offered; it declines targets whose shape
    differs too much from the source."""
    if mode in _LAYOUTS:
        return [mode]
    return _reflow_topologies(W, H) + ["proportional"]


def _reflow_topologies(W: float, H: float) -> List[str]:
    ar = W / max(1.0, H)
    # Small canvases also try variants that hold every text at a 7px minimum.
    small = W * H < 200_000 or min(W, H) < 260
    if ar >= 3.0 or H < 210:
        return ["banner", "split", "compact", "compact-wide-copy"]
    if ar >= 2.0:
        return ["split", "banner"] + (["compact", "compact-wide-copy"] if small else [])
    if ar >= 1.35:
        return ["split", "vertical"] + (["vertical-dense", "compact"] if small else [])
    if ar >= 0.95:
        return ["vertical", "split"] + (["vertical-dense"] if small else [])
    return ["vertical"] + (["vertical-dense"] if small else [])


def _naive_contain(source: Dict[str, Any], W: int, H: int) -> Dict[str, Any]:
    """Last-resort answer: uniform scale to fit, centred; backgrounds stretched."""
    sw = float(source["canvas"]["width"])
    sh = float(source["canvas"]["height"])
    s = min(W / sw, H / sh)
    ox, oy = (W - sw * s) / 2.0, (H - sh * s) / 2.0
    out: List[Dict[str, Any]] = []
    for el in source["elements"]:
        e = copy.deepcopy(el)
        w, h = float(e.get("width", 1.0)), float(e.get("height", 1.0))
        full = w >= 0.9 * sw and h >= 0.9 * sh
        if full:
            e["x"], e["y"], e["width"], e["height"] = 0.0, 0.0, float(W), float(H)
        else:
            e["x"] = round(ox + float(e.get("x", 0.0)) * s, 2)
            e["y"] = round(oy + float(e.get("y", 0.0)) * s, 2)
            e["width"], e["height"] = round(w * s, 2), round(h * s, 2)
        if e.get("type") == "text":
            e["font_size"] = max(1, int(round(float(e.get("font_size", 12)) * s)))
        contain_element(e, el, W, H)
        out.append(e)
    return {"canvas": {"width": W, "height": H}, "elements": out}


def resize(
    source: Dict[str, Any],
    target_canvas: Dict[str, Any],
    mode: str = "auto",
    assets_dir: str | Path = "brand_kit/assets",
) -> Dict[str, Any]:
    """
    Reflow `source` design onto `target_canvas` with designer intent.

    Args:
        source: {"canvas": {"width": int, "height": int}, "elements": [...]}
        target_canvas: {"width": int, "height": int}
        mode: Layout topology override ("auto", "vertical", "split", "banner",
              "compact", "compact-wide-copy", "vertical-dense", "proportional").
              Defaults to "auto" for automated evaluation.

    Returns:
        Schema-valid design dict {"canvas": target_canvas, "elements": [...]}
    """
    validate_design(source)
    validate_canvas(target_canvas)
    if mode != "auto" and mode not in _LAYOUTS:
        raise ValueError(f"Unknown layout mode: {mode}")
    target_w = int(round(float(target_canvas["width"])))
    target_h = int(round(float(target_canvas["height"])))
    W, H = float(target_w), float(target_h)

    candidates: List[Tuple[float, int, Dict[str, Any]]] = []
    for rank, topo in enumerate(_topologies(mode, W, H)):
        try:
            ad = analyze_design(source, assets_dir)
            _LAYOUTS[topo](ad, W, H)
            enforce_label_hierarchy(ad, W, H)
            finalize_followers(ad)
            out = {
                "canvas": {"width": target_w, "height": target_h},
                "elements": enforce_hard_constraints(ad, source, W, H),
            }
            report = audit(source, out, assets_dir)
            objective = (100.0 - report["penalty"]) + aesthetic(source, out, assets_dir)
            candidates.append((objective, -rank, out))
        except LayoutDoesNotFit:
            continue
        except Exception:  # noqa: BLE001 - a bad candidate must never sink the run
            log.exception("layout %r failed; trying the next candidate", topo)

    if not candidates:
        return _naive_contain(source, target_w, target_h)
    # highest objective wins; ties go to the topology that was listed first
    return max(candidates, key=lambda c: (round(c[0], 1), c[1]))[2]
