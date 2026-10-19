"""Batch scoring entry point: input/*.json -> output/<task>_output.{json,png}."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from engine.contract import contract_problems, validate_canvas, validate_design
from engine.resize import resize

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
ASSETS_DIR = Path(__file__).resolve().parent.parent / "brand_kit" / "assets"


def self_check(source: dict, output: dict) -> list[str]:
    """Structural contract check, separate from the quality audit and official judge."""
    return contract_problems(source, output)


def render_to_png(design: dict, assets_dir: Path, png_path: Path) -> list[str]:
    """Use the real renderer. A missing renderer fails loudly; no placeholder fallback."""
    from renderer.converter.convert import render_design_to_png
    return render_design_to_png(design, assets_dir, str(png_path))


def main(input_dir: Path | None = None, output_dir: Path | None = None) -> int:
    inputs, outputs = input_dir or INPUT_DIR, output_dir or OUTPUT_DIR
    outputs.mkdir(parents=True, exist_ok=True)
    task_files = sorted(inputs.glob("*.json"))
    if not task_files:
        print(f"No task files found in {inputs}", file=sys.stderr)
        return 1
    failed = False
    for task_path in task_files:
        stem = task_path.stem
        json_path, png_path = outputs / f"{stem}_output.json", outputs / f"{stem}_output.png"
        temp_json, temp_png = outputs / f".{stem}.json.tmp", outputs / f".{stem}.png.tmp"
        try:
            task = json.loads(task_path.read_text())
            source, target = task["source"], task["target_canvas"]
            validate_design(source)
            validate_canvas(target)
            t0 = time.perf_counter()
            output = resize(source, target)
            elapsed = time.perf_counter() - t0
            problems = contract_problems(source, output, target)
            if problems:
                raise ValueError('; '.join(problems))
            warnings = render_to_png(output, ASSETS_DIR, temp_png)
            from PIL import Image
            with Image.open(temp_png) as image:
                if image.size != (target["width"], target["height"]):
                    raise ValueError(f"PNG size {image.size} differs from target canvas")
            temp_json.write_text(json.dumps(output, indent=2, allow_nan=False) + '\n')
            temp_png.replace(png_path)
            temp_json.replace(json_path)
            print(f"[{task_path.name}] OK in {elapsed:.3f}s -> {json_path.name}, {png_path.name}")
            for warning in warnings:
                print(f"    render warning: {warning}")
        except Exception as exc:
            failed = True
            # A failed task must never masquerade as an old successful result.
            for p in (temp_json, temp_png, json_path, png_path):
                p.unlink(missing_ok=True)
            print(f"[{task_path.name}] FAILED: {exc}", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=INPUT_DIR)
    parser.add_argument('--output-dir', type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    raise SystemExit(main(args.input_dir, args.output_dir))
