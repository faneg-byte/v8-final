# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
# Unauthorized copying, distribution, or use is strictly prohibited.

"""
Modeler Service — Orchestrates the full V8 prediction pipeline.

Pipeline:
    1. Refresh per-team Tables A, B, C
    2. Compute SOODE 4-grip mesh for all teams
    3. Load WFO-calibrated channel weights + trained model weights
    4. Load real team features (Tables A, B, C) for each match
    5. Run all 4 model channels (GARCH, LSTM, Bayesian, CNN)
    6. Wave collapse → Live Alpha signals
    7. Store model predictions for SOODE feedback loop
    8. Walk-forward optimization (periodic, 100% back-fit)

PROPRIETARY: This pipeline is original intellectual property.
"""

import logging
import sys
from datetime import date, datetime
from pathlib import Path

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.db import get_cursor, get_conn, execute_batch, audit, close_pool
from shared.config import CONFIG
from modeler.soode import compute_team_profile, SOODEProfile, Diagnosis
from modeler.wave_collapse import (
    predict_match, CollapsedSignal, MARKET_TYPES, MARKET_OUTCOMES,
    load_team_features, load_wfo_weights,
)
from shared.monitor import send_digest, detect_soode_anomalies, load_previous_distribution

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ─────────────────────────────────────────────
# Table Population
# ─────────────────────────────────────────────

def refresh_team_tables(team_id: int) -> dict:
    """Refresh Tables A, B, C for a single team."""
    stats = {"table_a": 0, "table_b": 0, "table_c": 0}

    with get_cursor() as cur:
        # Table A: Last 10 matches with odds context
        cur.execute("""
            INSERT INTO team_odds_profile (team_id, match_id, match_date, market_type,
                actual_outcome, result_flag, row_rank)
            SELECT
                %(tid)s, m.match_id, m.match_date, 'h2h',
                CASE WHEN (m.home_id = %(tid)s AND m.home_goals > m.away_goals) OR
                         (m.away_id = %(tid)s AND m.away_goals > m.home_goals) THEN 'W'
                     WHEN m.home_goals = m.away_goals THEN 'D'
                     ELSE 'L' END,
                CASE WHEN (m.home_id = %(tid)s AND m.home_goals > m.away_goals) OR
                         (m.away_id = %(tid)s AND m.away_goals > m.home_goals) THEN TRUE
                     ELSE FALSE END,
                ROW_NUMBER() OVER (ORDER BY m.match_date DESC)
            FROM matches m
            WHERE (m.home_id = %(tid)s OR m.away_id = %(tid)s)
              AND m.status = 'completed'
            ORDER BY m.match_date DESC
            LIMIT 10
            ON CONFLICT (team_id, match_id, market_type) DO UPDATE SET
                actual_outcome = EXCLUDED.actual_outcome,
                result_flag = EXCLUDED.result_flag,
                row_rank = EXCLUDED.row_rank
        """, {"tid": team_id})
        stats["table_a"] = cur.rowcount

        # Table B: 60 matches in 6 intervals of 10
        cur.execute("""
            WITH ranked AS (
                SELECT m.match_id, m.match_date,
                    CASE WHEN m.home_id = %(tid)s THEN m.away_id ELSE m.home_id END AS opponent_id,
                    CASE WHEN m.home_id = %(tid)s THEN 'home' ELSE 'away' END AS venue,
                    CASE WHEN m.home_id = %(tid)s THEN m.home_goals ELSE m.away_goals END AS goals_for,
                    CASE WHEN m.home_id = %(tid)s THEN m.away_goals ELSE m.home_goals END AS goals_against,
                    ROW_NUMBER() OVER (ORDER BY m.match_date DESC) AS rn
                FROM matches m
                WHERE (m.home_id = %(tid)s OR m.away_id = %(tid)s)
                  AND m.status = 'completed'
                  AND m.home_goals IS NOT NULL
            )
            INSERT INTO team_match_intervals
                (team_id, match_id, interval_id, match_date, opponent_id, venue,
                 goals_for, goals_against, result, row_rank)
            SELECT %(tid)s, match_id,
                CEIL(rn / 10.0)::INT AS interval_id,
                match_date, opponent_id, venue, goals_for, goals_against,
                CASE WHEN goals_for > goals_against THEN 'W'
                     WHEN goals_for = goals_against THEN 'D'
                     ELSE 'L' END,
                rn
            FROM ranked
            WHERE rn <= 60
            ON CONFLICT (team_id, match_id) DO UPDATE SET
                interval_id = EXCLUDED.interval_id,
                row_rank = EXCLUDED.row_rank
        """, {"tid": team_id})
        stats["table_b"] = cur.rowcount

    return stats


# ─────────────────────────────────────────────
# SOODE Computation
# ─────────────────────────────────────────────

def compute_soode_for_team(team_id: int, team_name: str) -> SOODEProfile:
    """
    Load model predictions and compute SOODE profile.

    Uses actual model-predicted probabilities from model_predictions table.
    Falls back to base-rate bootstrap when insufficient model predictions exist.
    """
    with get_cursor(dict_cursor=True) as cur:
        # Primary source: model predictions with actual probabilities
        cur.execute("""
            SELECT predicted_prob, actual_outcome, was_correct,
                   TRUE AS has_model_prob,
                   CASE WHEN was_correct THEN 'W' ELSE 'L' END AS result
            FROM model_predictions
            WHERE team_id = %s
            ORDER BY created_at ASC
        """, (team_id,))
        model_rows = cur.fetchall()

        # Fallback: Table B results for bootstrap
        cur.execute("""
            SELECT result, NULL AS predicted_prob,
                   FALSE AS has_model_prob
            FROM team_match_intervals
            WHERE team_id = %s
            ORDER BY match_date ASC
        """, (team_id,))
        interval_rows = cur.fetchall()

    # Build match history
    match_history = []

    for r in interval_rows:
        match_history.append({
            "model_prob": None,
            "result": r["result"] or "D",
            "has_model_prob": False,
        })

    for r in model_rows:
        match_history.append({
            "model_prob": float(r["predicted_prob"]) if r["predicted_prob"] else None,
            "result": r["result"] or "D",
            "has_model_prob": bool(r["has_model_prob"]),
        })

    # Load previous grips for cascade continuity
    prev_grips = None
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT meso_grip, macro_grip, dna_grip
            FROM soode_keys
            WHERE team_id = %s
            ORDER BY computed_at DESC LIMIT 1
        """, (team_id,))
        prev = cur.fetchone()
        if prev:
            prev_grips = {
                "meso": float(prev["meso_grip"]),
                "macro": float(prev["macro_grip"]),
                "dna": float(prev["dna_grip"]),
            }

    return compute_team_profile(team_id, team_name, match_history, prev_grips)


def save_soode_keys(profiles: list[SOODEProfile]) -> int:
    """Write SOODE profiles to database."""
    rows = [
        (p.team_id, p.micro_grip, p.meso_grip, p.macro_grip, p.dna_grip,
         p.diagnosis.value, CONFIG.model_version)
        for p in profiles
    ]
    return execute_batch("""
        INSERT INTO soode_keys (team_id, micro_grip, meso_grip, macro_grip, dna_grip,
                               system_diagnosis, model_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (team_id, model_version) DO UPDATE SET
            micro_grip = EXCLUDED.micro_grip, meso_grip = EXCLUDED.meso_grip,
            macro_grip = EXCLUDED.macro_grip, dna_grip = EXCLUDED.dna_grip,
            system_diagnosis = EXCLUDED.system_diagnosis, computed_at = NOW()
    """, rows)


# ─────────────────────────────────────────────
# Live Alpha Emission
# ─────────────────────────────────────────────

def save_live_alpha(signals: list[CollapsedSignal], match_meta: dict) -> int:
    """Write wave-collapsed signals as Live Alpha."""
    rows = [
        (s.match_id, match_meta.get("match_date"), match_meta.get("home_team"),
         match_meta.get("away_team"), s.market_type, s.predicted_outcome,
         s.spe_implied_prob, CONFIG.model_version,
         str(s.channel_weights))
        for s in signals
    ]
    return execute_batch("""
        INSERT INTO live_alpha (match_id, match_date, home_team, away_team,
                               market_type, predicted_outcome, spe_implied_prob,
                               model_version, channel_weights)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, rows)


# ─────────────────────────────────────────────
# Model Prediction Storage (feeds SOODE)
# ─────────────────────────────────────────────

def save_model_predictions(
    signals: list[CollapsedSignal],
    match_meta: dict,
) -> int:
    """
    Store model predictions for SOODE divergence tracking.

    Each signal's SPE becomes the predicted_prob for the home team.
    actual_outcome and was_correct are populated after match completion.
    """
    rows = []
    for s in signals:
        rows.append((
            s.match_id,
            match_meta.get("home_id"),
            s.market_type,
            s.raw_prob,
            None,   # actual_outcome — filled after match
            None,   # was_correct — filled after match
            CONFIG.model_version,
        ))

    if not rows:
        return 0

    return execute_batch("""
        INSERT INTO model_predictions
            (match_id, team_id, market_type, predicted_prob,
             actual_outcome, was_correct, model_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_id, team_id, market_type, model_version) DO UPDATE SET
            predicted_prob = EXCLUDED.predicted_prob
    """, rows)


# ─────────────────────────────────────────────
# Main Pipeline
# ─────────────────────────────────────────────

@app.route("/run", methods=["POST"])
def run_pipeline():
    """Full modeler pipeline: Tables → SOODE → Models → Wave Collapse → Alpha."""
    try:
        # Step 1: Get all active teams
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT team_id, name FROM teams ORDER BY name")
            teams = cur.fetchall()

        logger.info(f"Processing {len(teams)} teams")

        # Step 2: Refresh per-team tables
        for t in teams:
            refresh_team_tables(t["team_id"])

        # Step 3: Compute SOODE for all teams
        soode_profiles = {}
        for t in teams:
            profile = compute_soode_for_team(t["team_id"], t["name"])
            soode_profiles[t["team_id"]] = profile

        save_soode_keys(list(soode_profiles.values()))
        logger.info(f"SOODE computed for {len(soode_profiles)} teams")

        # Log diagnosis distribution
        diag_counts = {}
        for p in soode_profiles.values():
            d = p.diagnosis.value
            diag_counts[d] = diag_counts.get(d, 0) + 1
        logger.info(f"SOODE distribution: {diag_counts}")

        # Step 4: Load WFO-calibrated weights
        with get_cursor(dict_cursor=True) as cur:
            wfo_weights, lstm_weights, cnn_weights = load_wfo_weights(cur)

        if wfo_weights:
            logger.info(f"Loaded WFO channel weights for {len(wfo_weights)} markets")
        else:
            logger.info("No WFO weights found — using default channel weights")
            wfo_weights = {m: CONFIG.wave.default_weights for m in MARKET_TYPES}

        # Step 5: Load upcoming matches and predict
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT m.match_id, m.match_date, m.league,
                       m.home_id, m.away_id,
                       h.name AS home_team, a.name AS away_team
                FROM matches m
                JOIN teams h ON m.home_id = h.team_id
                JOIN teams a ON m.away_id = a.team_id
                WHERE m.status = 'scheduled'
                  AND m.match_date >= CURRENT_DATE
                ORDER BY m.match_date
            """)
            upcoming = cur.fetchall()

        logger.info(f"Predicting {len(upcoming)} upcoming matches")

        total_signals = 0
        total_predictions = 0

        for match in upcoming:
            # Load real team features from Tables A, B, C
            with get_cursor(dict_cursor=True) as cur:
                home_data = load_team_features(cur, match["home_id"])
                away_data = load_team_features(cur, match["away_id"])

            # Run all 4 channels through wave collapse
            signals = predict_match(
                match["match_id"],
                home_data,
                away_data,
                wfo_weights=wfo_weights,
                lstm_weights=lstm_weights,
                cnn_weights=cnn_weights,
                spe_threshold=CONFIG.wave.spe_threshold,
            )

            if signals:
                meta = {
                    "match_date": match["match_date"],
                    "home_team": match["home_team"],
                    "away_team": match["away_team"],
                    "home_id": match["home_id"],
                }
                save_live_alpha(signals, meta)
                n_pred = save_model_predictions(signals, meta)
                total_signals += len(signals)
                total_predictions += n_pred

                # Log channel contributions for each signal
                for s in signals:
                    active_channels = [
                        f"{ch}={w:.2f}" for ch, w in s.channel_weights.items() if w > 0.01
                    ]
                    logger.info(
                        f"  {match['home_team']} vs {match['away_team']} | "
                        f"{s.market_type} → {s.predicted_outcome} "
                        f"(SPE={s.spe_implied_prob}%) [{', '.join(active_channels)}]"
                    )

        result = {
            "teams_processed": len(teams),
            "soode_profiles": len(soode_profiles),
            "upcoming_matches": len(upcoming),
            "live_alpha_signals": total_signals,
            "model_predictions_stored": total_predictions,
            "model_version": CONFIG.model_version,
            "diagnosis_distribution": diag_counts,
            "wfo_weights_loaded": bool(wfo_weights),
            "lstm_weights_loaded": bool(lstm_weights),
            "cnn_weights_loaded": bool(cnn_weights),
        }

        audit("modeler", "pipeline_complete", result)
        logger.info(f"Pipeline complete: {result}")

        # Monitoring: detect anomalies and send digest
        with get_cursor(dict_cursor=True) as cur:
            prev_dist = load_previous_distribution(cur)
        anomalies = detect_soode_anomalies(diag_counts, prev_dist)

        bootstrap_count = sum(1 for p in soode_profiles.values() if p.bootstrap_mode)
        send_digest(
            CONFIG.telegram_bot_token, CONFIG.telegram_chat_id,
            "modeler",
            {
                "teams": len(teams),
                "signals": total_signals,
                "predictions": total_predictions,
                "bootstrap_teams": bootstrap_count,
                "wfo_active": bool(wfo_weights),
                **{k.split(" ")[0]: v for k, v in diag_counts.items()},
            },
            anomalies=anomalies if anomalies else None,
        )

        return jsonify(result), 200

    except Exception as e:
        logger.exception("Pipeline failed")
        audit("modeler", "pipeline_failed", {"error": str(e)})
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# WFO Endpoint (100% Historical Back-Fit)
# ─────────────────────────────────────────────

@app.route("/wfo", methods=["POST"])
def run_wfo_endpoint():
    """
    Trigger walk-forward optimization with 100% historical back-fit.

    Every epoch retrains ALL channels from scratch on the full expanded
    training window and recalibrates all units (SOODE, channel weights,
    LSTM, CNN, Bayesian priors).

    Can be triggered manually or by the nightly schedule.
    """
    try:
        from modeler.wfo_pipeline import run_wfo

        with get_conn() as conn:
            epochs = run_wfo(
                conn,
                train_years=CONFIG.wfo.train_window_years,
                test_months=CONFIG.wfo.test_window_months,
                step_months=CONFIG.wfo.step_months,
            )

        result = {
            "epochs_completed": len(epochs),
            "final_accuracy": epochs[-1].accuracy if epochs else None,
            "final_log_loss": epochs[-1].log_loss if epochs else None,
            "final_channel_weights": epochs[-1].channel_weights if epochs else None,
            "backfit_mode": "100%_historical",
        }

        audit("modeler", "wfo_complete", result)
        logger.info(f"WFO complete: {result}")

        return jsonify(result), 200

    except Exception as e:
        logger.exception("WFO failed")
        audit("modeler", "wfo_failed", {"error": str(e)})
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
