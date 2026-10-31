# Geometric nudger model

The scoring engine combines geometric layout rules with a small neural network
that suggests position offsets. The model is trained from scratch using the
synthetic layout generator in `data_gen.py`; it is not an adapter or fine-tune of
an external foundation model. Its code and weights are available in this repo.

- Architecture: 24 input features → 64 ReLU units → 64 ReLU units → 2 outputs.
- Learned parameters: 5,890, stored with normalization statistics.
- Output: normalized x/y offsets bounded by 4% of the target canvas dimension.
- Runtime: NumPy only, already pinned in `requirements.lock`.
- Weights: `ai/weights/geometric_nudger.npz`, 24,695 bytes.
- SHA-256: `e88860961b36f2b4776ca5ef94680bd2b4c304f64386c5fd2344af877672596e`.

`engine/validator.py` invokes `ai.infer.apply_geometric_nudges` after the initial
layout. Full-canvas backgrounds are excluded, grouped button/badge/ribbon
members share predicted offsets, and a local audit rejects an increased penalty.
Final containment still runs after refinement. If weights are absent or fail to
load, the geometric layout remains available. No model service, runtime download,
GPU, or generated/replacement image content is required.

To train a replacement with the supplied code, after installing the locked
dependencies, run from the repository root:

```sh
python -m ai.train --samples 600 --epochs 100 --out /tmp/geometric_nudger.npz
```

The code uses seeded synthetic data, training-only normalization, and separate
training, validation and test partitions. The committed weights have no retained
training-run metadata; this command documents the training procedure rather than
claiming to reproduce the committed file byte for byte. The repository's layout
sweep is the available end-to-end evidence; it does not isolate the model's
contribution or establish an advantage on Adobe's hidden evaluation.

The model is not a vision model: it adjusts existing element positions and never
reads or generates image pixels. Dense micro banners can still require small
text. Optional Ollama/Tesseract diagnostics in `scripts/ai_evaluator.py` are
separate tools and do not participate in stage-one reflow.
