# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
# Unauthorized copying, distribution, or use is strictly prohibited.

"""
Walk-Forward Optimization (WFO) Pipeline — 100% Historical Back-Fit

Training strategy:
    1. Initial window: Years 1-3 (or configurable) for first training epoch
    2. 100% back-fit: Every epoch retrains ALL channels from scratch on the
       full expanded training window — no stale weights carried forward
    3. Full recalibration: SOODE thresholds, channel attention weights,
       LSTM weights, CNN weights, and Bayesian priors are ALL recalibrated
       each epoch
    4. Walk-forward: Train on N years, test on next M months, step S months
    5. Continue to present: Final epoch weights represent best calibration

Storage:
    - Channel attention weights → wfo_calibration table
    - LSTM/CNN model weights → audit_trail (service='wfo', action='model_weights')
    - SOODE thresholds → wfo_calibration.soode_thresholds JSONB

PROPRIETARY: This algorithm is original intellectual property.
"""

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

import numpy as np

from modeler.garch_channel import garch_predict
from modeler.lstm_channel import (
    lstm_predict, lstm_train, build_sequence, NumpyLSTM,
    _encode_interval_features, _encode_target,
)
from modeler.cnn_channel import (
    cnn_predict, cnn_train, build_form_matrix, NumpyCNN,
)
from modeler.wave_collapse import (
    bayesian_channel, _encode_match_outcome,
    MARKET_TYPES, MARKET_OUTCOMES,
)

logger = logging.getLogger(__name__)


@dataclass
class WFOEpoch:
    """Result of one walk-forward epoch."""
    epoch_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    channel_weights: dict     # {market_type: {channel: weight}}
    log_loss: float
    accuracy: float
    soode_thresholds: dict
    lstm_weights: dict        # {market_type: serialized_weights}
    cnn_weights: dict         # {market_type: serialized_weights}
    n_train_matches: int
    n_test_matches: int


# ─────────────────────────────────────────────
# Channel Weight Optimization
# ─────────────────────────────────────────────

def _log_loss(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute log loss. y_true: one-hot, y_pred: probabilities."""
    y_pred = np.clip(y_pred, 1e-10, 1.0 - 1e-10)
    return -np.sum(y_true * np.log(y_pred)) / max(y_true.shape[0], 1)


def optimize_channel_weights(
    channel_predictions: dict[str, list[dict[str, float]]],
    actual_outcomes: list[str],
    market_type: str,
    n_iterations: int = 200,
) -> tuple[dict[str, float], float]:
    """
    Optimize channel attention weights to minimize log-loss on test set.
    Uses coordinate descent on the simplex.
    """
    outcomes = MARKET_OUTCOMES.get(market_type)
    if not outcomes:
        return {}, float("inf")

    channels = list(channel_predictions.keys())
    n_channels = len(channels)
    n_samples = len(actual_outcomes)

    if n_channels == 0 or n_samples == 0:
        return {c: 1.0 / max(n_channels, 1) for c in channels}, float("inf")

    # Convert to arrays
    channel_probs = {}
    for c in channels:
        preds = channel_predictions[c]
        arr = np.zeros((n_samples, len(outcomes)))
        for i, pred in enumerate(preds):
            for j, o in enumerate(outcomes):
                arr[i, j] = pred.get(o, 1.0 / len(outcomes))
        channel_probs[c] = arr

    # One-hot encode actuals
    y_true = np.zeros((n_samples, len(outcomes)))
    for i, actual in enumerate(actual_outcomes):
        if actual in outcomes:
            y_true[i, outcomes.index(actual)] = 1.0
        else:
            y_true[i] = 1.0 / len(outcomes)

    # Initialize weights uniformly
    weights = np.ones(n_channels) / n_channels
    best_weights = weights.copy()
    best_loss = float("inf")

    # Coordinate descent
    for iteration in range(n_iterations):
        for c_idx in range(n_channels):
            best_w = weights[c_idx]
            best_iter_loss = float("inf")

            for trial_w in np.linspace(0.01, 0.99, 20):
                test_weights = weights.copy()
                test_weights[c_idx] = trial_w
                w_sum = test_weights.sum()
                if w_sum <= 0:
                    continue
                test_weights /= w_sum

                blended = np.zeros((n_samples, len(outcomes)))
                for ci, ch in enumerate(channels):
                    blended += test_weights[ci] * channel_probs[ch]
                row_sums = np.maximum(blended.sum(axis=1, keepdims=True), 1e-10)
                blended /= row_sums

                loss = _log_loss(y_true, blended)
                if loss < best_iter_loss:
                    best_iter_loss = loss
                    best_w = trial_w

            weights[c_idx] = best_w

        w_sum = weights.sum()
        if w_sum > 0:
            weights /= w_sum

        blended = np.zeros((n_samples, len(outcomes)))
        for ci, ch in enumerate(channels):
            blended += weights[ci] * channel_probs[ch]
        row_sums = np.maximum(blended.sum(axis=1, keepdims=True), 1e-10)
        blended /= row_sums
        loss = _log_loss(y_true, blended)

        if loss < best_loss:
            best_loss = loss
            best_weights = weights.copy()

    result = {channels[i]: round(float(best_weights[i]), 4) for i in range(n_channels)}
    return result, best_loss


# ─────────────────────────────────────────────
# SOODE Threshold Computation
# ─────────────────────────────────────────────

def compute_soode_thresholds(
    predictions: list[dict],
    actuals: list[str],
    market_type: str,
) -> dict:
    """
    Compute SOODE calibration thresholds from WFO test results.
    """
    outcomes = MARKET_OUTCOMES.get(market_type, [])
    if not predictions or not actuals:
        return {"divergence_threshold": 0.35, "confidence_floor": 0.3}

    divergences = []
    for pred, actual in zip(predictions, actuals):
        if actual in pred:
            prob_actual = pred[actual]
            div = abs(1.0 - prob_actual)
            divergences.append(div)

    if not divergences:
        return {"divergence_threshold": 0.35, "confidence_floor": 0.3}

    div_array = np.array(divergences)
    return {
        "divergence_threshold": round(float(np.percentile(div_array, 75)), 4),
        "confidence_floor": round(float(np.percentile(div_array, 25)), 4),
        "mean_divergence": round(float(div_array.mean()), 4),
        "median_divergence": round(float(np.median(div_array)), 4),
    }


# ─────────────────────────────────────────────
# Team History Builder
# ─────────────────────────────────────────────

def _build_team_histories(matches: list[dict]) -> dict[int, list[dict]]:
    """Build per-team chronological match history from raw match data."""
    histories = {}

    for m in matches:
        home_id = m["home_id"]
        away_id = m["away_id"]
        hg = m.get("home_goals", 0) or 0
        ag = m.get("away_goals", 0) or 0

        home_result = "W" if hg > ag else ("D" if hg == ag else "L")
        away_result = "W" if ag > hg else ("D" if hg == ag else "L")

        home_entry = {
            "match_id": m["match_id"],
            "match_date": m["match_date"],
            "goals_for": hg,
            "goals_against": ag,
            "result": home_result,
            "venue": "home",
            "opponent_id": away_id,
        }
        away_entry = {
            "match_id": m["match_id"],
            "match_date": m["match_date"],
            "goals_for": ag,
            "goals_against": hg,
            "result": away_result,
            "venue": "away",
            "opponent_id": home_id,
        }

        histories.setdefault(home_id, []).append(home_entry)
        histories.setdefault(away_id, []).append(away_entry)

    return histories


def _history_to_intervals(history: list[dict], n_intervals: int = 6,
                          per_interval: int = 10) -> list[dict]:
    """Convert flat match history into interval-tagged records for LSTM."""
    recent = history[-(n_intervals * per_interval):]
    result = []
    for i, m in enumerate(recent):
        interval_id = (i // per_interval) + 1
        interval_id = min(interval_id, n_intervals)
        entry = dict(m)
        entry["interval_id"] = interval_id
        result.append(entry)
    return result


def _encode_onehot(m: dict, market_type: str, outcomes: list[str]) -> Optional[np.ndarray]:
    """Encode a match dict as a one-hot target vector."""
    actual = _encode_match_outcome(m, market_type)
    if actual is None or actual not in outcomes:
        return None
    target = np.zeros(len(outcomes))
    target[outcomes.index(actual)] = 1.0
    return target


# ─────────────────────────────────────────────
# 100% Historical Back-Fit WFO Pipeline
# ─────────────────────────────────────────────

def run_wfo(conn, train_years: int = 3, test_months: int = 6,
            step_months: int = 3) -> list[WFOEpoch]:
    """
    Run the full walk-forward optimization pipeline with 100% back-fit.

    CRITICAL DESIGN: Every epoch retrains ALL channels from scratch on the
    full expanded training window. No incremental updates — this ensures
    complete recalibration of every unit at every step.

    Args:
        conn: psycopg2 database connection.
        train_years: Initial training window in years.
        test_months: Test window in months.
        step_months: Step size in months.

    Returns:
        List of WFOEpoch results.
    """
    from psycopg2 import extras as pg_extras

    cur = conn.cursor(cursor_factory=pg_extras.RealDictCursor)

    # Get data range
    cur.execute("SELECT MIN(match_date), MAX(match_date) FROM matches WHERE status = 'completed'")
    row = cur.fetchone()
    if not row or not row["min"]:
        logger.error("No completed matches found for WFO")
        return []

    data_start = row["min"]
    data_end = row["max"]
    logger.info(f"WFO data range: {data_start} to {data_end}")

    # Calculate WFO windows
    train_start = data_start
    train_end = data_start + timedelta(days=train_years * 365)

    if train_end >= data_end:
        logger.warning("Not enough data for multi-epoch WFO — single epoch on all data")
        train_end = data_end - timedelta(days=test_months * 30)

    epochs = []
    epoch_id = 0

    while train_end + timedelta(days=test_months * 30) <= data_end + timedelta(days=1):
        epoch_id += 1
        test_start = train_end
        test_end = test_start + timedelta(days=test_months * 30)

        logger.info(
            f"═══ WFO Epoch {epoch_id} ═══\n"
            f"  TRAIN: [{train_start.date()} → {train_end.date()}] "
            f"(100% back-fit from origin)\n"
            f"  TEST:  [{test_start.date()} → {test_end.date()}]"
        )

        # ── Load ALL training data from origin (100% back-fit) ──
        cur.execute("""
            SELECT m.match_id, m.home_id, m.away_id, m.match_date, m.league,
                   m.home_goals, m.away_goals
            FROM matches m
            WHERE m.status = 'completed'
              AND m.match_date >= %s AND m.match_date < %s
              AND m.home_goals IS NOT NULL
            ORDER BY m.match_date
        """, (train_start, train_end))
        train_matches = [dict(r) for r in cur.fetchall()]

        # ── Load test data ──
        cur.execute("""
            SELECT m.match_id, m.home_id, m.away_id, m.match_date, m.league,
                   m.home_goals, m.away_goals
            FROM matches m
            WHERE m.status = 'completed'
              AND m.match_date >= %s AND m.match_date < %s
              AND m.home_goals IS NOT NULL
            ORDER BY m.match_date
        """, (test_start, test_end))
        test_matches = [dict(r) for r in cur.fetchall()]

        if len(train_matches) < 50 or len(test_matches) < 10:
            logger.warning(
                f"Epoch {epoch_id}: insufficient data "
                f"(train={len(train_matches)}, test={len(test_matches)}), skipping"
            )
            train_end += timedelta(days=step_months * 30)
            continue

        # ── Build team histories from FULL training window ──
        team_histories = _build_team_histories(train_matches)

        # ── 100% BACK-FIT: Train ALL channels from scratch per market ──
        epoch_lstm_weights = {}
        epoch_cnn_weights = {}
        epoch_channel_weights = {}
        epoch_soode_thresholds = {}
        total_log_loss = 0.0
        total_accuracy = 0.0
        n_markets = 0

        for market_type in MARKET_TYPES:
            outcomes = MARKET_OUTCOMES[market_type]
            n_out = len(outcomes)

            # ── Prepare LSTM + CNN training data (from scratch) ──
            lstm_sequences = []
            lstm_targets = []
            cnn_matrices = []
            cnn_targets = []

            for team_id, history in team_histories.items():
                if len(history) < 20:
                    continue

                # LSTM: interval-based sequence
                intervals = _history_to_intervals(history)
                seq = build_sequence(intervals, n_intervals=6)
                target = _encode_onehot(history[-1], market_type, outcomes)

                if target is not None and np.abs(seq).sum() > 0.01:
                    lstm_sequences.append(seq)
                    lstm_targets.append(target)

                    # CNN: form matrix
                    form_mat = build_form_matrix(history, max_rows=60)
                    cnn_matrices.append(form_mat)
                    cnn_targets.append(target)

            # ── Train LSTM from scratch (100% back-fit) ──
            lstm_w = {}
            if len(lstm_sequences) >= 10:
                model = NumpyLSTM(input_dim=7, hidden_dim=64, dense_dim=32, output_dim=n_out)
                avg_loss = model.train_epoch(lstm_sequences, lstm_targets, lr=0.001, n_epochs=3)
                lstm_w = model.to_dict()
                logger.info(
                    f"  LSTM [{market_type}]: FRESH train on {len(lstm_sequences)} samples, "
                    f"loss={avg_loss:.4f}"
                )
            epoch_lstm_weights[market_type] = lstm_w

            # ── Train CNN from scratch (100% back-fit) ──
            cnn_w = {}
            if len(cnn_matrices) >= 10:
                model = NumpyCNN(input_features=7, output_dim=n_out)
                avg_loss = model.train_epoch(cnn_matrices, cnn_targets, lr=0.001, n_epochs=3)
                cnn_w = model.to_dict()
                logger.info(
                    f"  CNN  [{market_type}]: FRESH train on {len(cnn_matrices)} samples, "
                    f"loss={avg_loss:.4f}"
                )
            epoch_cnn_weights[market_type] = cnn_w

            # ── Evaluate ALL channels on test set ──
            channel_preds = {"garch": [], "lstm": [], "bayesian": [], "cnn": []}
            test_actuals = []

            # Build test team histories (include training data for context)
            all_matches = train_matches + test_matches
            full_histories = _build_team_histories(all_matches)

            for tm in test_matches:
                home_id = tm["home_id"]
                home_hist = full_histories.get(home_id, [])
                if len(home_hist) < 10:
                    continue

                # Actual outcome (from home perspective)
                match_as_home = {
                    "goals_for": tm["home_goals"],
                    "goals_against": tm["away_goals"],
                    "result": ("W" if tm["home_goals"] > tm["away_goals"]
                               else ("D" if tm["home_goals"] == tm["away_goals"] else "L")),
                    "venue": "home",
                }
                actual = _encode_match_outcome(match_as_home, market_type)
                if actual is None or actual not in outcomes:
                    continue

                test_actuals.append(actual)

                # GARCH (uses full history — recalibrated)
                garch_probs, _ = garch_predict(home_hist, market_type)
                channel_preds["garch"].append(
                    garch_probs if garch_probs else {o: 1.0 / n_out for o in outcomes}
                )

                # LSTM (uses freshly trained weights)
                intervals = _history_to_intervals(home_hist)
                lstm_probs, _ = lstm_predict(intervals, market_type, lstm_w if lstm_w else None)
                channel_preds["lstm"].append(
                    lstm_probs if lstm_probs else {o: 1.0 / n_out for o in outcomes}
                )

                # Bayesian (recalibrated prior from full history)
                base_rates = {}
                counts = {o: 0 for o in outcomes}
                for hm in home_hist:
                    enc = _encode_match_outcome(hm, market_type)
                    if enc in counts:
                        counts[enc] += 1
                total_c = sum(counts.values())
                if total_c > 0:
                    base_rates = {o: counts[o] / total_c for o in outcomes}
                else:
                    base_rates = {o: 1.0 / n_out for o in outcomes}

                recent_ev = []
                for i, hm in enumerate(home_hist[-15:]):
                    enc = _encode_match_outcome(hm, market_type)
                    if enc:
                        w = 0.5 + 0.5 * (i / max(14, 1))
                        recent_ev.append({"outcome": enc, "weight": w})

                bay_out = bayesian_channel(base_rates, recent_ev, market_type)
                channel_preds["bayesian"].append(bay_out.outcome_probs)

                # CNN (uses freshly trained weights)
                form_mat = build_form_matrix(home_hist, max_rows=60)
                cnn_probs, _ = cnn_predict(form_mat, market_type, cnn_w if cnn_w else None)
                channel_preds["cnn"].append(
                    cnn_probs if cnn_probs else {o: 1.0 / n_out for o in outcomes}
                )

            if len(test_actuals) < 5:
                logger.warning(f"  Epoch {epoch_id} [{market_type}]: insufficient test samples")
                epoch_channel_weights[market_type] = {
                    "garch": 0.25, "lstm": 0.25, "bayesian": 0.30, "cnn": 0.20
                }
                epoch_soode_thresholds[market_type] = {
                    "divergence_threshold": 0.35, "confidence_floor": 0.3
                }
                continue

            # ── Optimize channel attention weights (recalibrated) ──
            opt_weights, opt_loss = optimize_channel_weights(
                channel_preds, test_actuals, market_type
            )
            epoch_channel_weights[market_type] = opt_weights

            # ── Compute accuracy ──
            correct = 0
            blended_preds = []
            for i in range(len(test_actuals)):
                blended = {o: 0.0 for o in outcomes}
                for ch, w in opt_weights.items():
                    for o in outcomes:
                        blended[o] += w * channel_preds[ch][i].get(o, 1.0 / n_out)
                pred_outcome = max(blended, key=lambda o: blended[o])
                if pred_outcome == test_actuals[i]:
                    correct += 1
                blended_preds.append(blended)

            accuracy = correct / len(test_actuals)
            total_log_loss += opt_loss
            total_accuracy += accuracy
            n_markets += 1

            # ── SOODE thresholds (recalibrated) ──
            soode_thresh = compute_soode_thresholds(blended_preds, test_actuals, market_type)
            epoch_soode_thresholds[market_type] = soode_thresh

            logger.info(
                f"  Epoch {epoch_id} [{market_type}]: "
                f"weights={opt_weights}, loss={opt_loss:.4f}, accuracy={accuracy:.3f}"
            )

        # ── Store epoch results ──
        avg_log_loss = total_log_loss / max(n_markets, 1)
        avg_accuracy = total_accuracy / max(n_markets, 1)

        epoch = WFOEpoch(
            epoch_id=epoch_id,
            train_start=str(train_start.date()),
            train_end=str(train_end.date()),
            test_start=str(test_start.date()),
            test_end=str(test_end.date()),
            channel_weights=epoch_channel_weights,
            log_loss=avg_log_loss,
            accuracy=avg_accuracy,
            soode_thresholds=epoch_soode_thresholds,
            lstm_weights=epoch_lstm_weights,
            cnn_weights=epoch_cnn_weights,
            n_train_matches=len(train_matches),
            n_test_matches=len(test_matches),
        )
        epochs.append(epoch)

        # ── Persist to database ──
        try:
            raw_cur = conn.cursor()
            raw_cur.execute("""
                INSERT INTO wfo_calibration
                    (wfo_epoch, train_start, train_end, test_start, test_end,
                     channel_weights, log_loss, accuracy, soode_thresholds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                epoch_id,
                train_start.date(), train_end.date(),
                test_start.date(), test_end.date(),
                json.dumps(epoch_channel_weights),
                avg_log_loss, avg_accuracy,
                json.dumps(epoch_soode_thresholds),
            ))
            conn.commit()
            raw_cur.close()
        except Exception as e:
            logger.warning(f"Failed to save WFO epoch to DB: {e}")
            conn.rollback()

        # ── Persist model weights ──
        try:
            raw_cur = conn.cursor()
            from psycopg2 import extras as pg_extras
            raw_cur.execute("""
                INSERT INTO audit_trail (service, action, detail)
                VALUES ('wfo', 'model_weights', %s)
            """, (pg_extras.Json({
                "epoch_id": epoch_id,
                "lstm_weights": epoch_lstm_weights,
                "cnn_weights": epoch_cnn_weights,
            }),))
            conn.commit()
            raw_cur.close()
        except Exception as e:
            logger.warning(f"Failed to save model weights: {e}")
            conn.rollback()

        logger.info(
            f"═══ Epoch {epoch_id} COMPLETE ═══\n"
            f"  avg_loss={avg_log_loss:.4f}, avg_accuracy={avg_accuracy:.3f}\n"
            f"  train_matches={len(train_matches)}, test_matches={len(test_matches)}"
        )

        # ── Advance window (training always starts from origin = 100% back-fit) ──
        train_end += timedelta(days=step_months * 30)

    logger.info(f"WFO complete: {len(epochs)} epochs processed")
    return epochs
