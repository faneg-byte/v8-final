# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
# Unauthorized copying, distribution, or use is strictly prohibited.

"""
LSTM Channel — Sequential Pattern Recognition (Pure NumPy)

Processes Table B interval data as a time series of 6 intervals.
Each interval summarizes 10 matches into aggregate features.

Architecture (NumPy implementation):
    Input(6, F) → LSTM(64) → Dense(32, ReLU) → Softmax(|outcomes|)

Training: Online gradient descent with walk-forward windows.
Inference: Forward pass through learned weights.

No PyTorch/TensorFlow dependency — keeps Docker image small and
stays within $50/month GCP budget (no GPU required).

PROPRIETARY: This algorithm is original intellectual property.
"""

import logging
import math
import json
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

def _encode_interval_features(interval_matches: list[dict]) -> np.ndarray:
    """
    Encode a list of matches from one interval into aggregate features.

    Features per interval (7 total):
        0: win_rate
        1: draw_rate
        2: loss_rate
        3: avg_goals_for
        4: avg_goals_against
        5: avg_goal_diff
        6: home_ratio
    """
    if not interval_matches:
        return np.zeros(7)

    n = len(interval_matches)
    wins = sum(1 for m in interval_matches if m.get("result") == "W")
    draws = sum(1 for m in interval_matches if m.get("result") == "D")
    losses = sum(1 for m in interval_matches if m.get("result") == "L")
    gf = sum(m.get("goals_for", 0) or 0 for m in interval_matches)
    ga = sum(m.get("goals_against", 0) or 0 for m in interval_matches)
    home = sum(1 for m in interval_matches if m.get("venue") == "home")

    return np.array([
        wins / n,
        draws / n,
        losses / n,
        gf / n,
        ga / n,
        (gf - ga) / n,
        home / n,
    ], dtype=np.float64)


def build_sequence(intervals_data: list[dict], n_intervals: int = 6) -> np.ndarray:
    """
    Build a (n_intervals, n_features) sequence from Table B data.

    intervals_data: list of dicts with 'interval_id' key (1-6) and match data.
    Returns: (6, 7) numpy array.
    """
    grouped = {}
    for m in intervals_data:
        iid = m.get("interval_id", 1)
        grouped.setdefault(iid, []).append(m)

    sequence = np.zeros((n_intervals, 7))
    for i in range(1, n_intervals + 1):
        if i in grouped:
            sequence[i - 1] = _encode_interval_features(grouped[i])

    return sequence


def _encode_target(matches: list[dict], market_type: str) -> Optional[np.ndarray]:
    """
    Encode the most recent match outcome as a one-hot target vector.
    """
    outcomes = MARKET_OUTCOMES.get(market_type)
    if not outcomes or not matches:
        return None

    last = matches[-1]
    gf = last.get("goals_for", 0) or 0
    ga = last.get("goals_against", 0) or 0
    result = last.get("result", "D")
    venue = last.get("venue", "home")
    total = gf + ga

    if market_type == "h2h":
        if venue == "home":
            actual = "1" if result == "W" else ("X" if result == "D" else "2")
        else:
            actual = "2" if result == "W" else ("X" if result == "D" else "1")
    elif market_type == "dc":
        if venue == "home":
            actual = "1X" if result in ("W", "D") else "X2"
        else:
            actual = "X2" if result in ("W", "D") else "1X"
    elif market_type == "btts":
        actual = "Yes" if (gf > 0 and ga > 0) else "No"
    elif market_type == "over_1.5":
        actual = "Over 1.5" if total > 1.5 else "Under 1.5"
    elif market_type == "over_2.5":
        actual = "Over 2.5" if total > 2.5 else "Under 2.5"
    else:
        return None

    target = np.zeros(len(outcomes))
    if actual in outcomes:
        target[outcomes.index(actual)] = 1.0
    return target


# ─────────────────────────────────────────────
# NumPy LSTM Implementation
# ─────────────────────────────────────────────

def _sigmoid(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, -500, 500)
    return 1.0 / (1.0 + np.exp(-x))


def _tanh(x: np.ndarray) -> np.ndarray:
    return np.tanh(np.clip(x, -500, 500))


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x))
    return e / (e.sum() + 1e-10)


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0, x)


class NumpyLSTM:
    """
    Minimal LSTM(64) → Dense(32, ReLU) → Softmax(n_out) in pure NumPy.

    Supports:
        - Forward pass for inference
        - Online SGD training with gradient approximation
        - Weight serialization to/from JSON
    """

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64,
                 dense_dim: int = 32, output_dim: int = 3):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.dense_dim = dense_dim
        self.output_dim = output_dim

        # Xavier initialization
        scale_lstm = np.sqrt(2.0 / (input_dim + hidden_dim))
        scale_dense = np.sqrt(2.0 / (hidden_dim + dense_dim))
        scale_out = np.sqrt(2.0 / (dense_dim + output_dim))

        # LSTM gates: [input, forget, cell, output] concatenated
        self.W_ih = np.random.randn(4 * hidden_dim, input_dim) * scale_lstm
        self.W_hh = np.random.randn(4 * hidden_dim, hidden_dim) * scale_lstm
        self.b_h = np.zeros(4 * hidden_dim)
        # Forget gate bias initialized to 1.0 for better gradient flow
        self.b_h[hidden_dim:2*hidden_dim] = 1.0

        # Dense layer
        self.W_dense = np.random.randn(dense_dim, hidden_dim) * scale_dense
        self.b_dense = np.zeros(dense_dim)

        # Output layer
        self.W_out = np.random.randn(output_dim, dense_dim) * scale_out
        self.b_out = np.zeros(output_dim)

    def forward(self, sequence: np.ndarray) -> np.ndarray:
        """
        Forward pass through LSTM → Dense → Softmax.

        Args:
            sequence: (seq_len, input_dim) array

        Returns:
            (output_dim,) probability vector
        """
        seq_len = sequence.shape[0]
        h = np.zeros(self.hidden_dim)
        c = np.zeros(self.hidden_dim)

        for t in range(seq_len):
            x_t = sequence[t]
            gates = self.W_ih @ x_t + self.W_hh @ h + self.b_h

            hd = self.hidden_dim
            i_gate = _sigmoid(gates[:hd])
            f_gate = _sigmoid(gates[hd:2*hd])
            g_gate = _tanh(gates[2*hd:3*hd])
            o_gate = _sigmoid(gates[3*hd:])

            c = f_gate * c + i_gate * g_gate
            h = o_gate * _tanh(c)

        # Dense + ReLU
        dense_out = _relu(self.W_dense @ h + self.b_dense)

        # Output + Softmax
        logits = self.W_out @ dense_out + self.b_out
        return _softmax(logits)

    def train_step(self, sequence: np.ndarray, target: np.ndarray,
                   lr: float = 0.001, eps: float = 1e-4) -> float:
        """
        Train one step using numerical gradient approximation.
        Returns: cross-entropy loss
        """
        pred = self.forward(sequence)
        loss = -np.sum(target * np.log(pred + 1e-10))

        # Numerical gradient for output and dense layers
        for param_name in ["W_out", "b_out", "W_dense", "b_dense"]:
            param = getattr(self, param_name)
            grad = np.zeros_like(param)

            flat = param.ravel()
            n_params = len(flat)
            n_sample = min(n_params, max(n_params // 4, 10))
            indices = np.random.choice(n_params, n_sample, replace=False)

            for idx in indices:
                old_val = flat[idx]

                flat[idx] = old_val + eps
                loss_plus = -np.sum(target * np.log(self.forward(sequence) + 1e-10))

                flat[idx] = old_val - eps
                loss_minus = -np.sum(target * np.log(self.forward(sequence) + 1e-10))

                flat[idx] = old_val
                grad.ravel()[idx] = (loss_plus - loss_minus) / (2 * eps)

            setattr(self, param_name, param - lr * grad)

        return loss

    def train_epoch(self, sequences: list[np.ndarray], targets: list[np.ndarray],
                    lr: float = 0.001, n_epochs: int = 5) -> float:
        """Train over all samples for n_epochs. Returns final average loss."""
        avg_loss = 0.0
        for epoch in range(n_epochs):
            total_loss = 0.0
            indices = np.random.permutation(len(sequences))
            for idx in indices:
                loss = self.train_step(sequences[idx], targets[idx], lr=lr)
                total_loss += loss
            avg_loss = total_loss / max(len(sequences), 1)
        return avg_loss

    def to_dict(self) -> dict:
        """Serialize weights to dict for JSON storage."""
        return {
            "input_dim": self.input_dim,
            "hidden_dim": self.hidden_dim,
            "dense_dim": self.dense_dim,
            "output_dim": self.output_dim,
            "W_ih": self.W_ih.tolist(),
            "W_hh": self.W_hh.tolist(),
            "b_h": self.b_h.tolist(),
            "W_dense": self.W_dense.tolist(),
            "b_dense": self.b_dense.tolist(),
            "W_out": self.W_out.tolist(),
            "b_out": self.b_out.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NumpyLSTM":
        """Deserialize weights from dict."""
        model = cls(d["input_dim"], d["hidden_dim"], d["dense_dim"], d["output_dim"])
        model.W_ih = np.array(d["W_ih"])
        model.W_hh = np.array(d["W_hh"])
        model.b_h = np.array(d["b_h"])
        model.W_dense = np.array(d["W_dense"])
        model.b_dense = np.array(d["b_dense"])
        model.W_out = np.array(d["W_out"])
        model.b_out = np.array(d["b_out"])
        return model


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def lstm_predict(
    intervals_data: list[dict],
    market_type: str,
    model_weights: Optional[dict] = None,
) -> tuple[dict[str, float], float]:
    """
    Produce LSTM-based outcome probabilities.

    Args:
        intervals_data: Table B data with interval_id, goals_for, goals_against, result, venue.
        market_type: One of h2h, dc, btts, over_1.5, over_2.5.
        model_weights: Serialized NumpyLSTM weights (from WFO training).

    Returns:
        (outcome_probs, confidence) where confidence is 0-1.
    """
    outcomes = MARKET_OUTCOMES.get(market_type)
    if not outcomes:
        return {}, 0.0

    n_out = len(outcomes)
    uniform = {o: 1.0 / n_out for o in outcomes}

    if not intervals_data or len(intervals_data) < 10:
        return uniform, 0.0

    sequence = build_sequence(intervals_data, n_intervals=6)

    if np.abs(sequence).sum() < 0.01:
        return uniform, 0.0

    if model_weights:
        try:
            model = NumpyLSTM.from_dict(model_weights)
        except Exception:
            return uniform, 0.0
    else:
        return uniform, 0.0

    try:
        probs_array = model.forward(sequence)

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
        logger.warning(f"LSTM forward pass failed: {e}")
        return uniform, 0.0


def lstm_train(
    training_samples: list[tuple[list[dict], list[dict]]],
    market_type: str,
    existing_weights: Optional[dict] = None,
    n_epochs: int = 5,
    lr: float = 0.001,
) -> dict:
    """
    Train LSTM on historical data and return serialized weights.

    Args:
        training_samples: List of (intervals_data, recent_matches) tuples.
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

    sequences = []
    targets = []

    for intervals_data, recent_matches in training_samples:
        seq = build_sequence(intervals_data, n_intervals=6)
        if np.abs(seq).sum() < 0.01:
            continue

        target = _encode_target(recent_matches, market_type)
        if target is None or len(target) != n_out:
            continue

        sequences.append(seq)
        targets.append(target)

    if len(sequences) < 5:
        logger.warning(f"LSTM: insufficient training samples ({len(sequences)})")
        return existing_weights or {}

    if existing_weights:
        try:
            model = NumpyLSTM.from_dict(existing_weights)
        except Exception:
            model = NumpyLSTM(input_dim=7, hidden_dim=64, dense_dim=32, output_dim=n_out)
    else:
        model = NumpyLSTM(input_dim=7, hidden_dim=64, dense_dim=32, output_dim=n_out)

    avg_loss = model.train_epoch(sequences, targets, lr=lr, n_epochs=n_epochs)
    logger.info(f"LSTM trained on {len(sequences)} samples, avg_loss={avg_loss:.4f}")

    return model.to_dict()
