"""Training pipeline for Neural Geometric Nudger.

Follows ML best practices:
- Strict chronological/seed-based train, validation, test splits (70/15/15)
- Feature standardization fit strictly on the training partition only
- Adam optimizer with momentum and weight decay
- Evaluation across train/val/test splits with MAE, MSE, and R2 metrics
- Automatic export to compressed .npz weights for offline deployment
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from ai.data_gen import generate_synthetic_dataset
from ai.model import GeometricNudgerModel



class AdamOptimizer:
    """Standard Adam optimizer for numpy array parameters."""

    def __init__(
        self,
        params: Dict[str, np.ndarray],
        lr: float = 0.003,
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
        weight_decay: float = 1e-4,
    ) -> None:
        self.params = params
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.weight_decay = weight_decay
        self.t = 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, grads: Dict[str, np.ndarray]) -> None:
        self.t += 1
        for k in self.params:
            g = grads[k] + self.weight_decay * self.params[k]
            self.m[k] = self.beta1 * self.m[k] + (1 - self.beta1) * g
            self.v[k] = self.beta2 * self.v[k] + (1 - self.beta2) * (g ** 2)

            m_hat = self.m[k] / (1 - self.beta1 ** self.t)
            v_hat = self.v[k] / (1 - self.beta2 ** self.t)

            self.params[k] -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    mse = float(np.mean((y_true - y_pred) ** 2))
    mae = float(np.mean(np.abs(y_true - y_pred)))
    var_y = float(np.var(y_true))
    r2 = 1.0 - (mse / max(1e-8, var_y))
    return {"mse": mse, "mae": mae, "r2": r2}


def train_nudger(
    X: np.ndarray,
    Y: np.ndarray,
    epochs: int = 120,
    batch_size: int = 64,
    learning_rate: float = 0.003,
    output_weights: str = "ai/weights/geometric_nudger.npz",
    seed: int = 42,
) -> Tuple[GeometricNudgerModel, Dict[str, Any]]:
    """Train the geometric nudger model on X, Y."""
    N = X.shape[0]
    rng = np.random.RandomState(seed)
    indices = rng.permutation(N)

    # 1. Strict train / validation / test splits (70% / 15% / 15%)
    n_train = int(0.70 * N)
    n_val = int(0.15 * N)

    idx_train = indices[:n_train]
    idx_val = indices[n_train : n_train + n_val]
    idx_test = indices[n_train + n_val :]

    X_train, Y_train = X[idx_train], Y[idx_train]
    X_val, Y_val = X[idx_val], Y[idx_val]
    X_test, Y_test = X[idx_test], Y[idx_test]

    # 2. Strict featurization ordering: fit mean and std on TRAINING SET ONLY
    mean = np.mean(X_train, axis=0)
    std = np.std(X_train, axis=0)
    std = np.where(std < 1e-6, 1.0, std)

    model = GeometricNudgerModel(
        input_dim=X.shape[1],
        hidden_dim=64,
        output_dim=Y.shape[1],
        max_nudge_pct=0.04,
        seed=seed,
    )
    model.set_normalization(mean, std)

    params = {
        "W1": model.W1,
        "b1": model.b1,
        "W2": model.W2,
        "b2": model.b2,
        "W3": model.W3,
        "b3": model.b3,
    }
    optimizer = AdamOptimizer(params, lr=learning_rate, weight_decay=1e-4)

    best_val_loss = float("inf")
    best_weights = None

    for epoch in range(1, epochs + 1):
        perm = rng.permutation(n_train)
        X_train_shuff = X_train[perm]
        Y_train_shuff = Y_train[perm]

        for i in range(0, n_train, batch_size):
            xb = X_train_shuff[i : i + batch_size]
            yb = Y_train_shuff[i : i + batch_size]
            bs = xb.shape[0]
            if bs == 0:
                continue

            # Forward
            pred, cache = model.forward(xb)
            x_norm, z1, a1, z2, a2, z3 = cache

            # Backward
            # dL/dpred
            dpred = (pred - yb) / bs

            # tanh backward
            # pred = M * tanh(z3)
            # dpred/dz3 = M * (1 - tanh(z3)^2)
            tanh_z3 = pred / model.max_nudge_pct
            dz3 = dpred * model.max_nudge_pct * (1.0 - tanh_z3 ** 2)

            dW3 = np.dot(a2.T, dz3)
            db3 = np.sum(dz3, axis=0)

            da2 = np.dot(dz3, model.W3.T)
            dz2 = da2 * (z2 > 0)

            dW2 = np.dot(a1.T, dz2)
            db2 = np.sum(dz2, axis=0)

            da1 = np.dot(dz2, model.W2.T)
            dz1 = da1 * (z1 > 0)

            dW1 = np.dot(x_norm.T, dz1)
            db1 = np.sum(dz1, axis=0)

            grads = {
                "W1": dW1,
                "b1": db1,
                "W2": dW2,
                "b2": db2,
                "W3": dW3,
                "b3": db3,
            }
            optimizer.step(grads)

        # Validation check every 10 epochs
        if epoch % 10 == 0 or epoch == epochs:
            val_preds = model.predict(X_val)
            val_metrics = compute_metrics(Y_val, val_preds)
            if val_metrics["mse"] < best_val_loss:
                best_val_loss = val_metrics["mse"]
                best_weights = {k: v.copy() for k, v in params.items()}

    # Restore best weights
    if best_weights:
        model.W1 = best_weights["W1"]
        model.b1 = best_weights["b1"]
        model.W2 = best_weights["W2"]
        model.b2 = best_weights["b2"]
        model.W3 = best_weights["W3"]
        model.b3 = best_weights["b3"]

    # Final evaluation on holdout test set
    test_preds = model.predict(X_test)
    test_metrics = compute_metrics(Y_test, test_preds)

    # Save trained model
    model.save(output_weights)

    return model, {
        "n_train": n_train,
        "n_val": n_val,
        "n_test": len(idx_test),
        "test_metrics": test_metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Neural Layout Nudger")
    parser.add_argument("--samples", type=int, default=600, help="Number of layout scenarios to fuzz")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--out", type=str, default="ai/weights/geometric_nudger.npz")
    args = parser.parse_args()

    print(f"[1/3] Generating synthetic layout data ({args.samples} scenarios)...")
    X, Y = generate_synthetic_dataset(num_samples=args.samples)
    print(f"      Extracted {X.shape[0]} element samples with {X.shape[1]} features each.")

    print(f"[2/3] Training MLP Residual Nudger for {args.epochs} epochs...")
    model, report = train_nudger(X, Y, epochs=args.epochs, output_weights=args.out)

    print(f"[3/3] Training Complete!")
    print(f"      Train Samples: {report['n_train']}, Val: {report['n_val']}, Test: {report['n_test']}")
    print(f"      Test MSE: {report['test_metrics']['mse']:.6f}")
    print(f"      Test MAE: {report['test_metrics']['mae']:.6f}")
    print(f"      Test R2:  {report['test_metrics']['r2']:.4f}")
    print(f"      Saved model to: {args.out} ({os.path.getsize(args.out) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
