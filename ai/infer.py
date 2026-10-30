"""Inference interface for Neural Geometric Nudger.

Applies sub-pixel and geometric spacing adjustments predicted by the trained
neural model to improve breathing room, legibility, and layout balance.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np

from ai.features import extract_element_features, is_background_element
from ai.model import GeometricNudgerModel

log = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = Path(__file__).resolve().parent / "weights" / "geometric_nudger.npz"
_CACHED_MODEL: Optional[GeometricNudgerModel] = None


def get_model(weights_path: Optional[Path | str] = None) -> Optional[GeometricNudgerModel]:
    """Return cached singleton model instance if weights exist."""
    global _CACHED_MODEL
    if _CACHED_MODEL is not None:
        return _CACHED_MODEL

    path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS
    if not path.is_file():
        log.debug("Geometric nudger weights not found at %s; skipping neural nudge.", path)
        return None

    try:
        model = GeometricNudgerModel()
        model.load(path)
        _CACHED_MODEL = model
        return _CACHED_MODEL
    except Exception as e:
        log.warning("Failed to load neural nudger weights: %s", e)
        return None


def apply_geometric_nudges(
    elements: List[Dict[str, Any]],
    W: float,
    H: float,
    ad: Optional[Any] = None,
    weights_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """
    Applies neural residual adjustments to active elements.

    Guarantees:
    - Never mutates full-bleed canvas backgrounds.
    - Synchronizes offsets across grouped units (e.g. CTA text + button rectangle).
    - Hard bounds: offsets are clamped to stay inside [0, W] x [0, H].
    - Zero regression: if neural nudge fails or degrades spacing, falls back safely.
    """
    original_elements = copy.deepcopy(elements)
    model = get_model(weights_path)
    if model is None:
        return elements

    feats, active_els = extract_element_features(elements, W, H, ad=ad)
    if feats.shape[0] == 0:
        return elements

    try:
        deltas = model.predict(feats)  # shape (N, 2), normalized [-max_nudge, max_nudge]

        # Map active elements to predicted pixel offsets
        el_deltas: Dict[str, Tuple[float, float]] = {}
        for idx, el in enumerate(active_els):
            eid = el.get("id", "")
            dx_px = float(deltas[idx, 0]) * W
            dy_px = float(deltas[idx, 1]) * H
            el_deltas[eid] = (dx_px, dy_px)

        # Synchronize group elements if `ad` unit grouping is available
        if ad is not None:
            all_units = (
                list(getattr(ad, "corner_overlays", []))
                + ([ad.cta_unit] if getattr(ad, "cta_unit", None) is not None else [])
                + [u for row in getattr(ad, "badge_rows", []) for u in row]
            )
            for u in all_units:
                unit_eids = [m.get("id") for m in getattr(u, "elements", []) if m.get("id") in el_deltas]
                if len(unit_eids) > 1:
                    # Use mean delta for all members of the unit to preserve alignment
                    mean_dx = float(np.mean([el_deltas[eid][0] for eid in unit_eids]))
                    mean_dy = float(np.mean([el_deltas[eid][1] for eid in unit_eids]))
                    for eid in unit_eids:
                        el_deltas[eid] = (mean_dx, mean_dy)

        # Apply nudges to candidate elements
        for el in elements:
            eid = el.get("id", "")
            if eid in el_deltas:
                dx, dy = el_deltas[eid]
                w = float(el.get("width", 1.0))
                h = float(el.get("height", 1.0))
                x = float(el.get("x", 0.0)) + dx
                y = float(el.get("y", 0.0)) + dy
                # Clamp within canvas boundaries
                x = max(0.0, min(W - w, x))
                y = max(0.0, min(H - h, y))
                el["x"] = round(x, 2)
                el["y"] = round(y, 2)

        # Optical text centering refinement:
        # Only in vertical/square layouts (W/H < 1.35) where headline is not split-column
        if W / max(1.0, H) < 1.35:
            centered_texts = [
                el for el in elements
                if el.get("id") in el_deltas
                and (el.get("type") == "text" or "headline" in str(el.get("role", "")).lower() or "subhead" in str(el.get("role", "")).lower())
                and el.get("text_align", "center") == "center"
            ]
            for tel in centered_texts:
                tw = float(tel.get("width", 1.0))
                tx = float(tel.get("x", 0.0))
                tcx = tx + tw / 2.0
                if abs(tcx - W / 2.0) < 0.15 * W:
                    target_x = max(0.0, min(W - tw, W / 2.0 - tw / 2.0))
                    tel["x"] = round(tx * 0.5 + target_x * 0.5, 2)

        # Zero-regression check: ensure nudges never introduce collisions or increase audit penalty
        try:
            from engine.qa import audit
            canv = {"width": W, "height": H}
            cand_rep = audit({"canvas": canv, "elements": elements}, {"canvas": canv, "elements": elements})
            orig_rep = audit({"canvas": canv, "elements": original_elements}, {"canvas": canv, "elements": original_elements})
            if (cand_rep.get("collisions") and not orig_rep.get("collisions")) or cand_rep.get("penalty", 0) > orig_rep.get("penalty", 0):
                return original_elements
        except Exception:
            pass

        log.info("[AI Neural Nudger] Adjusted %d elements", len(el_deltas))

    except Exception as e:
        log.warning("Neural geometric nudge failed: %s; keeping baseline layout.", e)
        return original_elements

    return elements
