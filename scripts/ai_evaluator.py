#!/usr/bin/env python3
"""
Multi-Model AI Ingestion & Layout Evaluation Benchmark for Chameleon.
Compares:
  1. Moondream VLM (1.7 GB, Vision) - Ultra-fast (300ms) pixel aesthetic & element audit
  2. NVIDIA Nemotron 3.5 Bolt (6.0 GB, Vision) - Deep pixel text reading & legibility audit
  3. OpenJev 1.7B (1.1 GB GGUF, Decision Reasoning) - System-One layout topology candidate ranking
  4. Tesseract OCR (0 GB VRAM, C++ Native) - Exact pixel word recall & OCR verification
  5. NumPy Center-of-Mass & Contrast (Deterministic) - 2D visual physics & WCAG balance
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def evaluate_moondream(image_path: Path) -> dict:
    """Evaluates design layout using Moondream VLM via Ollama (1.7 GB)."""
    t0 = time.perf_counter()
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        "Evaluate this banner image layout and typography: "
        "What text is visible? Is the text clearly legible and formatted well against the background? "
        "Describe the visual layout and balance in 1-2 concise sentences."
    )

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({
                "model": "moondream",
                "prompt": prompt,
                "images": [img_b64],
                "stream": False
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.perf_counter() - t0
            raw = (data.get("response") or data.get("thinking") or "").strip()
            return {
                "available": True,
                "model": "Moondream (1.7 GB)",
                "modality": "Vision (Image -> Text)",
                "latency_s": round(elapsed, 3),
                "assessment": raw.replace("\n", " ")[:220]
            }
    except Exception as e:
        return {
            "available": False,
            "model": "Moondream (1.7 GB)",
            "latency_s": round(time.perf_counter() - t0, 3),
            "error": str(e)
        }


def evaluate_nemotron(image_path: Path) -> dict:
    """Evaluates pixel text legibility using NVIDIA Nemotron 3.5 Bolt (6.0 GB)."""
    t0 = time.perf_counter()
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = "List all text words visible in this image and verify if they are legible."

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({
                "model": "hf.co/unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF:UD-Q4_K_XL",
                "prompt": prompt,
                "images": [img_b64],
                "stream": False
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.perf_counter() - t0
            raw = (data.get("response") or data.get("thinking") or "").strip()
            # Clean reasoning if present
            if "clearly readable" in raw.lower() or "visible text" in raw.lower():
                lines = [l.strip() for l in raw.split("\n") if l.strip() and not l.startswith("Here's a thinking")]
                clean = " ".join(lines)
            else:
                clean = raw.replace("\n", " ")
            return {
                "available": True,
                "model": "Nemotron 3.5 Bolt (6.0 GB)",
                "modality": "Vision (Deep Pixel Audit)",
                "latency_s": round(elapsed, 3),
                "assessment": clean[:220]
            }
    except Exception as e:
        return {
            "available": False,
            "model": "Nemotron 3.5 Bolt (6.0 GB)",
            "latency_s": round(time.perf_counter() - t0, 3),
            "error": str(e)
        }


def evaluate_openjev_reasoning(width: int, height: int, element_count: int) -> dict:
    """Evaluates layout topology decision using OpenJev 1.7B reasoning model."""
    t0 = time.perf_counter()
    aspect = round(width / max(1, height), 2)
    prompt = (
        f"Canvas size: {width}x{height} (aspect ratio {aspect}:1). Total elements: {element_count}.\n"
        f"Candidate A: Vertical stack layout.\n"
        f"Candidate B: Split horizontal 2-column layout.\n"
        f"Which candidate provides better visual balance and prevents text collision? "
        f"Answer in 1 concise sentence with your decision."
    )

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({
                "model": "hf.co/mradermacher/OpenJev-1.7B-GGUF:Q4_K_M",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 100}
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.perf_counter() - t0
            # OpenJev outputs reasoning trace in 'thinking'
            raw = (data.get("response") or data.get("thinking") or "").strip()
            return {
                "available": True,
                "model": "OpenJev 1.7B (1.1 GB)",
                "modality": "Reasoning / Decision Scorer",
                "latency_s": round(elapsed, 3),
                "decision": raw.replace("\n", " ")[:200]
            }
    except Exception as e:
        return {
            "available": False,
            "model": "OpenJev 1.7B (1.1 GB)",
            "latency_s": round(time.perf_counter() - t0, 3),
            "error": str(e)
        }


def evaluate_tesseract_ocr(image_path: Path, expected_texts: list[str]) -> dict:
    """Evaluates text preservation and legibility by running native Tesseract OCR on PNG pixels."""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "6"],
            capture_output=True,
            text=True,
            check=False
        )
        elapsed = time.perf_counter() - t0
        extracted = proc.stdout.lower()

        found_count = 0
        for exp in expected_texts:
            words = [w.lower() for w in exp.split() if len(w) > 3]
            if words and any(w in extracted for w in words):
                found_count += 1

        recall = round((found_count / max(1, len(expected_texts))) * 100, 1)
        return {
            "available": True,
            "model": "Tesseract OCR (C++ Native)",
            "modality": "Pixel OCR Text Extraction",
            "latency_s": round(elapsed, 3),
            "text_recall_pct": recall,
            "sample_snippet": proc.stdout.strip().replace("\n", " ")[:90] + "..."
        }
    except FileNotFoundError:
        return {"available": False, "error": "tesseract binary not found"}


def evaluate_visual_balance(image_path: Path) -> dict:
    """Evaluates 2D visual center-of-mass and contrast using Pillow + NumPy."""
    t0 = time.perf_counter()
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    gray = np.dot(arr[..., :3], [0.2989, 0.5870, 0.1140])
    std_contrast = float(np.std(gray))

    bg = np.median(arr[:10, :10], axis=(0, 1))
    diff = np.linalg.norm(arr - bg, axis=-1)
    total_mass = float(np.sum(diff))

    if total_mass > 0:
        y_coords, x_coords = np.indices(diff.shape)
        cx = float(np.sum(x_coords * diff) / total_mass / img.width)
        cy = float(np.sum(y_coords * diff) / total_mass / img.height)
        dist = math.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)
        balance_pct = max(0, min(100, int((1.0 - dist / 0.5) * 100)))
    else:
        cx, cy, balance_pct = 0.5, 0.5, 100

    elapsed = time.perf_counter() - t0
    return {
        "available": True,
        "model": "NumPy Physics Scorer",
        "modality": "Pixel Center-of-Mass & Contrast",
        "latency_s": round(elapsed, 3),
        "visual_center": (round(cx, 2), round(cy, 2)),
        "balance_score_pct": balance_pct,
        "contrast_std": round(std_contrast, 1),
        "wcag_status": "AA (High)" if std_contrast >= 25 else "Low"
    }


def run_full_benchmark(image_path: Path, width: int, height: int, expected_texts: list[str]) -> dict:
    """Runs all 5 evaluation systems on an image and returns a unified comparison dictionary."""
    results = {}
    results["numpy_physics"] = evaluate_visual_balance(image_path)
    results["tesseract_ocr"] = evaluate_tesseract_ocr(image_path, expected_texts)
    results["openjev_decision"] = evaluate_openjev_reasoning(width, height, len(expected_texts))
    results["moondream_vlm"] = evaluate_moondream(image_path)
    results["nemotron_vlm"] = evaluate_nemotron(image_path)
    return results


def main():
    parser = argparse.ArgumentParser(description="Chameleon Multi-Model AI Ingestion & Evaluation Suite")
    parser.add_argument("--image", type=Path, help="Specific output PNG to test")
    args = parser.parse_args()

    targets = []
    if args.image:
        targets.append(("Custom Image", args.image, 1200, 630, []))
    else:
        tasks = [
            ("Level 1 (Warmup)", "output/chameleon_sample_level1_warmup_output.png", "input/chameleon_sample_level1_warmup.json"),
            ("Level 2 (Core)", "output/chameleon_sample_level2_core_output.png", "input/chameleon_sample_level2_core.json"),
            ("Level 3 (Advanced)", "output/chameleon_sample_level3_advanced_output.png", "input/chameleon_sample_level3_advanced.json")
        ]
        for name, img_rel, json_rel in tasks:
            img_p = ROOT / img_rel
            json_p = ROOT / json_rel
            expected = []
            w, h = 1080, 1080
            if json_p.exists():
                data = json.loads(json_p.read_text())
                target_canvas = data.get("target_canvas", {})
                w = target_canvas.get("width", 1080)
                h = target_canvas.get("height", 1080)
                expected = [e["content"] for e in data["source"]["elements"] if e["type"] == "text"]
            targets.append((name, img_p, w, h, expected))

    print("\n" + "=" * 80)
    print(" CHAMELEON MULTI-MODEL AI INGESTION & COMPARISON SCORECARD")
    print("=" * 80)

    for label, img_path, w, h, expected in targets:
        if not img_path.exists():
            print(f"Skipping {label}: {img_path} does not exist.")
            continue

        print(f"\n▶ EVALUATING: {label} ({img_path.name} · {w}x{h} px)")
        print("-" * 80)

        data = run_full_benchmark(img_path, w, h, expected)

        # 1. NumPy Physics
        p = data["numpy_physics"]
        print(f" [1] NumPy Center-of-Mass  │ Latency: {p['latency_s']}s │ Balance: {p['balance_score_pct']}% │ Contrast: {p['wcag_status']}")

        # 2. Tesseract OCR
        t = data["tesseract_ocr"]
        if t.get("available"):
            print(f" [2] Tesseract Native OCR  │ Latency: {t['latency_s']}s │ Recall: {t['text_recall_pct']}%  │ Sample: {t['sample_snippet']}")

        # 3. OpenJev 1.7B
        o = data["openjev_decision"]
        if o.get("available"):
            print(f" [3] OpenJev 1.7B Decision │ Latency: {o['latency_s']}s │ Modality: Decision Reasoning")
            print(f"     Verdict: {o['decision']}")

        # 4. Moondream VLM
        m = data["moondream_vlm"]
        if m.get("available"):
            print(f" [4] Moondream VLM (1.7GB) │ Latency: {m['latency_s']}s │ Modality: Fast Pixel Audit")
            print(f"     Verdict: {m['assessment']}")

        # 5. Nemotron 3.5 Bolt
        n = data["nemotron_vlm"]
        if n.get("available"):
            print(f" [5] Nemotron 3.5 (6.0GB)  │ Latency: {n['latency_s']}s │ Modality: Deep Pixel Inspection")
            print(f"     Verdict: {n['assessment']}")

        print("-" * 80)

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
