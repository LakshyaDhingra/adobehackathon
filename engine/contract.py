"""Schema and content contract shared by the CLI, studio and release verifier."""
from __future__ import annotations

import math
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any
import json

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
GEOMETRY_FIELDS = {"x", "y", "width", "height", "rotation", "font_size", "corner_radius", "stroke_width"}


@lru_cache(maxsize=1)
def schema_validator() -> Draft202012Validator:
    schema = json.loads((ROOT / "chameleon_design_schema_v0.9.json").read_text())
    return Draft202012Validator(schema)


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return True


def validate_canvas(canvas: dict) -> None:
    if not isinstance(canvas, dict) or set(canvas) != {"width", "height"}:
        raise ValueError("Canvas must contain width and height")
    for key in ("width", "height"):
        n = canvas[key]
        if isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) or n < 1 or n != int(n):
            raise ValueError(f"Canvas {key} must be a positive integer")


def validate_design(design: dict) -> None:
    if not _finite(design):
        raise ValueError("Design contains non-finite numbers")
    error = next(schema_validator().iter_errors(design), None)
    if error is not None:
        location = '.'.join(map(str, error.absolute_path)) or 'design'
        raise ValueError(f"{location}: {error.message}")
    ids = [e["id"] for e in design["elements"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Element ids must be unique")


def contract_problems(source: dict, output: dict, target: dict | None = None) -> list[str]:
    try:
        validate_design(output)
    except (ValueError, TypeError) as exc:
        return [str(exc)]
    problems = []
    if target is not None and output["canvas"] != target:
        problems.append("Output canvas does not match target_canvas")
    if Counter(e["id"] for e in source["elements"]) != Counter(e["id"] for e in output["elements"]):
        return problems + ["Element ids added, removed, or duplicated"]
    by_id = {e["id"]: e for e in source["elements"]}
    W, H = output["canvas"]["width"], output["canvas"]["height"]
    for e in output["elements"]:
        src = by_id[e["id"]]
        for key in set(src) | set(e):
            if key not in GEOMETRY_FIELDS and (key in src) != (key in e):
                problems.append(f"{e['id']}: changed field {key}")
            elif key not in GEOMETRY_FIELDS and src.get(key) != e.get(key):
                problems.append(f"{e['id']}: changed {key}")
        rad = math.radians(float(e.get("rotation", 0) or 0))
        w, h = e["width"], e["height"]
        ew, eh = abs(math.cos(rad))*w + abs(math.sin(rad))*h, abs(math.sin(rad))*w + abs(math.cos(rad))*h
        cx, cy = e["x"] + w/2, e["y"] + h/2
        if min(e["x"],e["y"],cx-ew/2,cy-eh/2) < -0.05 or max(e["x"]+w,cx+ew/2) > W+0.05 or max(e["y"]+h,cy+eh/2) > H+0.05:
            problems.append(f"{e['id']}: outside canvas (including rotation)")
        if e["type"] == "image" and src.get("fit", "contain") != "cover" and min(src["width"], src["height"]) > 0:
            # Compare cross-products to tolerate only coordinate rounding.
            if abs(w * src["height"] - h * src["width"]) > 0.03 * max(src["width"], src["height"]):
                problems.append(f"{e['id']}: changed image aspect ratio")
    return problems
