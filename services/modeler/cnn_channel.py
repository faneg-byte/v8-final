# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
# Unauthorized copying, distribution, or use is strictly prohibited.

"""
CNN Channel — Convolutional Pattern Recognition (Pure NumPy)

Treats Table B data as a 1D sequence of 60 matches with feature columns.
Conv1D filters detect repeating form patterns, seasonal rhythms, and
structural signatures that linear models miss.

Architecture (NumPy Conv1D):
    Input(60, F) → Conv1D(32, k=3) → ReLU → MaxPool(2) →
    Conv1D(16, k=3) → ReLU → GlobalAvgPool → Dense(32, ReLU) → Softmax(|outcomes|)

No PyTorch/TensorFlow dependency.

PROPRIETARY: This algorithm is original intellectual property.
"""

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MARKET_OUTCOMES = {
    "h2h":      ["1", "X", "2"],
    "dc":       ["1X", "12", "X2"],
    "btts":     ["Yes", "No"],
    "over_1.5": ["Over 1.5", "Under 1.5"],
    "over_2.5": ["Over 2.5", "Under 2.5"],
}


# ─────────────────────────────────────────────
# Feature Engineering
# ─────────────────────────────────────────────

def _encode_match_features(m: dict) -> np.ndarray:
    """
    Encode a single match into a feature vector (7 features).

    Features:
        0: goals_for (normalized)
        1: goals_against (normalized)
        2: goal_diff (normalized)
        3: result_win (1/0)
        4: result_draw (1/0)
        5: result_loss (1/0)
        6: venue_home (1/0)
    """
    gf = (m.get("goals_for", 0) or 0) / 5.0
    ga = (m.get("goals_against", 0) or 0) / 5.0
    gd = (gf - ga)
    result = m.get("result", "D")
    venue = m.get("venue", "home")

    return np.array([
        gf, ga, gd,
        1.0 if result == "W" else 0.0,
        1.0 if result == "D" else 0.0,
        1.0 if result == "L" else 0.0,
        1.0 if venue == "home" else 0.0,
    ], dtype=np.float64)


def build_form_matrix(matches: list[dict], max_rows: int = 60) -> np.ndarray:
    """
    Build a (max_rows, 7) form matrix from match history.

    Pads with zeros if fewer than max_rows matches.
    Uses most recent max_rows matches.
    """
    n_features = 7
    matrix = np.zeros((max_rows, n_features))

    recent = matches[-max_rows:] if len(matches) > max_rows else matches
    for i, m in enumerate(recent):
        offset = max_rows - len(recent)
        matrix[offset + i] = _encode_match_features(m)

    return matrix


# ─────────────────────────────────────────────
# NumPy CNN Implementation
# ─────────────────────────────────────────────

def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0, x)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x))
    return e / (e.sum() + 1e-10)


def _conv1d(x: np.ndarray, filters: np.ndarray, bias: np.ndarray) -> np.ndarray:
    """
    1D convolution.

    x: (seq_len, in_channels)
    filters: (n_filters, kernel_size, in_channels)
    bias: (n_filters,)

    Returns: (seq_len - kernel_size + 1, n_filters)
    """
    seq_len, in_ch = x.shape
    n_filters, k, _ = filters.shape
    out_len = seq_len - k + 1

    if out_len <= 0:
        return np.zeros((1, n_filters))

    out = np.zeros((out_len, n_filters))
    for i in range(out_len):
        patch = x[i:i+k]
        for f in range(n_filters):
            out[i, f] = np.sum(patch * filters[f]) + bias[f]

    return out


def _maxpool1d(x: np.ndarray, pool_size: int = 2) -> np.ndarray:
    """1D max pooling. x: (seq_len, channels)"""
    seq_len, ch = x.shape
    out_len = seq_len // pool_size
    if out_len <= 0:
        return x[:1]

    out = np.zeros((out_len, ch))
    for i in range(out_len):
        out[i] = np.max(x[i*pool_size:(i+1)*pool_size], axis=0)

    return out


def _global_avg_pool(x: np.ndarray) -> np.ndarray:
    """Global average pooling. x: (seq_len, channels) → (channels,)"""
    return np.mean(x, axis=0)


class NumpyCNN:
    """
    Conv1D(32,k=3) → ReLU → MaxPool(2) → Conv1D(16,k=3) → ReLU →
    GlobalAvgPool → Dense(32,ReLU) → Softmax(n_out)
    """

    def __init__(self, input_features: int = 7, output_dim: int = 3):
        self.input_features = input_features
        self.output_dim = output_dim

        # Conv1: 32 filters, kernel_size=3
        scale1 = np.sqrt(2.0 / (3 * input_features))
        self.conv1_w = np.random.randn(32, 3, input_features) * scale1
        self.conv1_b = np.zeros(32)

        # Conv2: 16 filters, kernel_size=3
        scale2 = np.sqrt(2.0 / (3 * 32))
        self.conv2_w = np.random.randn(16, 3, 32) * scale2
        self.conv2_b = np.zeros(16)

        # Dense: 16 → 32
        scale3 = np.sqrt(2.0 / (16 + 32))
        self.dense_w = np.random.randn(32, 16) * scale3
        self.dense_b = np.zeros(32)

        # Output: 32 → n_out
        scale4 = np.sqrt(2.0 / (32 + output_dim))
        self.out_w = np.random.randn(output_dim, 32) * scale4
        self.out_b = np.zeros(output_dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass.
        x: (seq_len, input_features) — typically (60, 7)
        Returns: (output_dim,) probability vector
        """
        h = _conv1d(x, self.conv1_w, self.conv1_b)
        h = _relu(h)
        h = _maxpool1d(h, pool_size=2)
        h = _conv1d(h, self.conv2_w, self.conv2_b)
        h = _relu(h)
        h = _global_avg_pool(h)
        h = _relu(self.dense_w @ h + self.dense_b)
        logits = self.out_w @ h + self.out_b
        return _softmax(logits)

    def train_step(self, x: np.ndarray, target: np.ndarray,
                   lr: float = 0.001, eps: float = 1e-4) -> float:
        """Train one step using numerical gradient on output layers."""
        pred = self.forward(x)
        loss = -np.sum(target * np.log(pred + 1e-10))

        for param_name in ["out_w", "out_b", "dense_w", "dense_b"]:
            param = getattr(self, param_name)
            flat = param.ravel()
            n_params = len(flat)
            n_sample = min(n_params, max(n_params // 4, 10))
            indices = np.random.choice(n_params, n_sample, replace=False)

            grad = np.zeros_like(flat)
            for idx in indices:
                old_val = flat[idx]

                flat[idx] = old_val + eps
                loss_p = -np.sum(target * np.log(self.forward(x) + 1e-10))

                flat[idx] = old_val - eps
                loss_m = -np.sum(target * np.log(self.forward(x) + 1e-10))

                flat[idx] = old_val
                grad[idx] = (loss_p - loss_m) / (2 * eps)

            param_reshaped = param - lr * grad.reshape(param.shape)
            setattr(self, param_name, param_reshaped)

        return loss

    def train_epoch(self, matrices: list[np.ndarray], targets: list[np.ndarray],
                    lr: float = 0.001, n_epochs: int = 5) -> float:
        """Train over all samples for n_epochs."""
        avg_loss = 0.0
        for epoch in range(n_epochs):
            total_loss = 0.0
            indices = np.random.permutation(len(matrices))
            for idx in indices:
                loss = self.train_step(matrices[idx], targets[idx], lr=lr)
                total_loss += loss
            avg_loss = total_loss / max(len(matrices), 1)
        return avg_loss

    def to_dict(self) -> dict:
        """Serialize weights."""
        return {
            "input_features": self.input_features,
            "output_dim": self.output_dim,
            "conv1_w": self.conv1_w.tolist(),
            "conv1_b": self.conv1_b.tolist(),
            "conv2_w": self.conv2_w.tolist(),
            "conv2_b": self.conv2_b.tolist(),
            "dense_w": self.dense_w.tolist(),
            "dense_b": self.dense_b.tolist(),
            "out_w": self.out_w.tolist(),
            "out_b": self.out_b.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NumpyCNN":
        """Deserialize weights."""
        model = cls(d["input_features"], d["output_dim"])
        model.conv1_w = np.array(d["conv1_w"])
        model.conv1_b = np.array(d["conv1_b"])
        model.conv2_w = np.array(d["conv2_w"])
        model.conv2_b = np.array(d["conv2_b"])
        model.dense_w = np.array(d["dense_w"])
        model.dense_b = np.array(d["dense_b"])
        model.out_w = np.array(d["out_w"])
        model.out_b = np.array(d["out_b"])
        return model


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def cnn_predict(
    form_matrix: Optional[np.ndarray],
    market_type: str,
    model_weights: Optional[dict] = None,
) -> tuple[dict[str, float], float]:
    """
    Produce CNN-based outcome probabilities.

    Args:
        form_matrix: (60, 7) numpy array from build_form_matrix().
        market_type: One of h2h, dc, btts, over_1.5, over_2.5.
        model_weights: Serialized NumpyCNN weights (from WFO training).

    Returns:
        (outcome_probs, confidence)
    """
    outcomes = MARKET_OUTCOMES.get(market_type)
    if not outcomes:
        return {}, 0.0

    n_out = len(outcomes)
    uniform = {o: 1.0 / n_out for o in outcomes}

    if form_matrix is None or form_matrix.shape[0] < 10:
        return uniform, 0.0

    if np.abs(form_matrix).sum() < 0.01:
        return uniform, 0.0

    if not model_weights:
        return uniform, 0.0

    try:
        model = NumpyCNN.from_dict(model_weights)
    except Exception:
        return uniform, 0.0

    try:
        probs_array = model.forward(form_matrix)

        probs = {}
        for i, outcome in enumerate(outcomes):
            probs[outcome] = max(float(probs_array[i]), 0.01)

        p_total = sum(probs.values())
        probs = {o: p / p_total for o, p in probs.items()}

        # Confidence from entropy
        entropy = -sum(p * math.log(p + 1e-10) for p in probs.values())
        max_entropy = math.log(n_out)
        confidence = max(1.0 - (entropy / max_entropy), 0.1)

        return probs, round(confidence, 4)

    except Exception as e:
        logger.warning(f"CNN forward pass failed: {e}")
        return uniform, 0.0


def cnn_train(
    training_matrices: list[np.ndarray],
    training_targets: list[np.ndarray],
    market_type: str,
    existing_weights: Optional[dict] = None,
    n_epochs: int = 5,
    lr: float = 0.001,
) -> dict:
    """
    Train CNN on historical form matrices.

    Args:
        training_matrices: List of (60, 7) form matrices.
        training_targets: List of one-hot target vectors.
        market_type: Target market type.
        existing_weights: Previous weights for incremental training.
        n_epochs: Training epochs.
        lr: Learning rate.

    Returns:
        Serialized model weights dict.
    """
    outcomes = MARKET_OUTCOMES.get(market_type)
    if not outcomes:
        return {}

    n_out = len(outcomes)

    valid_m = []
    valid_t = []
    for mat, tgt in zip(training_matrices, training_targets):
        if mat is not None and np.abs(mat).sum() > 0.01 and tgt is not None and len(tgt) == n_out:
            valid_m.append(mat)
            valid_t.append(tgt)

    if len(valid_m) < 5:
        logger.warning(f"CNN: insufficient training samples ({len(valid_m)})")
        return existing_weights or {}

    if existing_weights:
        try:
            model = NumpyCNN.from_dict(existing_weights)
        except Exception:
            model = NumpyCNN(input_features=7, output_dim=n_out)
    else:
        model = NumpyCNN(input_features=7, output_dim=n_out)

    avg_loss = model.train_epoch(valid_m, valid_t, lr=lr, n_epochs=n_epochs)
    logger.info(f"CNN trained on {len(valid_m)} samples, avg_loss={avg_loss:.4f}")

    return model.to_dict()
