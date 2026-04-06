"""
Scanner Service — Refined Alpha, Weaponized Matrix, and Signal Emission.

Pipeline:
    1. Load Live Alpha signals + SOODE keys for both teams in each match.
    2. Apply matchup matrix → Refined Alpha with accentuation/contradiction.
    3. Build Weaponized Matrix (optimized parlays).
    4. Emit Telegram alerts for actionable signals.

PROPRIETARY: This pipeline is original intellectual property.
"""

import logging
import sys
from pathlib import Path

from flask import Flask, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.db import get_cursor, execute_batch, audit, close_pool
from shared.config import CONFIG
from modeler.soode import Diagnosis, assess_matchup, SOODEProfile
from scanner.weaponized import construct_weaponized_matrix
from scanner.kelly import compute_stake
from scanner.alerts import send_signal_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


def load_live_alpha() -> list[dict]:
    """Load pending Live Alpha signals."""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT la.id AS alpha_id, la.match_id, la.match_date,
                   la.home_team, la.away_team,
                   la.market_type, la.predicted_outcome, la.spe_implied_prob,
                   m.home_id, m.away_id
            FROM live_alpha la
            JOIN matches m ON la.match_id = m.match_id
            WHERE la.match_date >= CURRENT_DATE
              AND la.model_version = %s
            ORDER BY la.spe_implied_prob DESC
        """, (CONFIG.model_version,))
        return cur.fetchall()


def load_soode_profile(team_id: int) -> SOODEProfile | None:
    """Load the most recent SOODE profile for a team."""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT team_id, micro_grip, meso_grip, macro_grip, dna_grip,
                   system_diagnosis
            FROM soode_keys
            WHERE team_id = %s
            ORDER BY computed_at DESC LIMIT 1
        """, (team_id,))
        row = cur.fetchone()

    if not row:
        return None

    # Map diagnosis string back to enum
    diag_map = {d.value: d for d in Diagnosis}
    diagnosis = diag_map.get(row["system_diagnosis"], Diagnosis.STABLE)

    return SOODEProfile(
        team_id=row["team_id"],
        team_name="",
        micro_grip=float(row["micro_grip"]),
        meso_grip=float(row["meso_grip"]),
        macro_grip=float(row["macro_grip"]),
        dna_grip=float(row["dna_grip"]),
        diagnosis=diagnosis,
        confidence=0.5,
    )


def save_refined_alpha(refined: list[dict]) -> int:
    """Write Refined Alpha records."""
    rows = [
        (r["alpha_id"], r["match_id"], r["home_diagnosis"], r["away_diagnosis"],
         r["matchup_class"], r["kelly_modifier"], r["accentuation"],
         r["refined_spe"], r["recommended_action"])
        for r in refined
    ]
    return execute_batch("""
        INSERT INTO refined_alpha (alpha_id, match_id, home_diagnosis, away_diagnosis,
            matchup_class, kelly_modifier, accentuation_flag, refined_spe, recommended_action)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, rows)


def save_weaponized_matrix(matrix) -> int:
    """Write Weaponized Matrix parlays to database."""
    rows = []
    for parlay in matrix.parlays:
        for i, leg in enumerate(parlay.legs):
            rows.append((
                parlay.parlay_id, i + 1, leg.alpha_id, leg.match_id,
                leg.market_type, leg.selection, leg.spe_implied_prob,
                parlay.cumulative_prob,
            ))

    if not rows:
        return 0

    return execute_batch("""
        INSERT INTO weaponized_matrix (parlay_id, leg_number, alpha_id, match_id,
            market_type, selection, spe_implied_prob, cumulative_prob)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, rows)


@app.route("/run", methods=["POST"])
def run_scan():
    """Full scan: Refined Alpha → Weaponized Matrix → Alerts."""
    try:
        alphas = load_live_alpha()
        logger.info(f"Processing {len(alphas)} Live Alpha signals")

        refined_records = []
        enriched_signals = []

        for alpha in alphas:
            home_soode = load_soode_profile(alpha["home_id"])
            away_soode = load_soode_profile(alpha["away_id"])

            if not home_soode or not away_soode:
                # Default to Stable if SOODE data missing
                home_soode = home_soode or SOODEProfile(
                    alpha["home_id"], alpha["home_team"], 0.15, 0.15, 0.15, 0.15,
                    Diagnosis.STABLE, 0.5)
                away_soode = away_soode or SOODEProfile(
                    alpha["away_id"], alpha["away_team"], 0.15, 0.15, 0.15, 0.15,
                    Diagnosis.STABLE, 0.5)

            assessment = assess_matchup(home_soode, away_soode)

            # Accentuation logic:
            #   "accentuate" → boost SPE by 2%
            #   "contradict" → reduce SPE by 5%
            #   "neutral" → no change
            spe = float(alpha["spe_implied_prob"])
            if assessment.accentuation == "accentuate":
                refined_spe = min(spe + 2.0, 99.0)
            elif assessment.accentuation == "contradict":
                refined_spe = max(spe - 5.0, 50.0)
            else:
                refined_spe = spe

            refined_records.append({
                "alpha_id": alpha["alpha_id"],
                "match_id": alpha["match_id"],
                "home_diagnosis": home_soode.diagnosis.value,
                "away_diagnosis": away_soode.diagnosis.value,
                "matchup_class": assessment.matchup_class,
                "kelly_modifier": assessment.kelly_modifier,
                "accentuation": assessment.accentuation,
                "refined_spe": refined_spe,
                "recommended_action": assessment.recommended_action,
            })

            # Enrich for weaponized matrix
            enriched_signals.append({
                **alpha,
                "spe_implied_prob": refined_spe,
                "matchup_class": assessment.matchup_class,
                "kelly_modifier": assessment.kelly_modifier,
            })

        # Save Refined Alpha
        refined_saved = 0
        if refined_records:
            refined_saved = save_refined_alpha(refined_records)
        logger.info(f"Saved {refined_saved} Refined Alpha records")

        # Build Weaponized Matrix
        matrix = construct_weaponized_matrix(
            enriched_signals,
            min_spe=CONFIG.parlay.min_spe,
            max_parlays_per_size=CONFIG.parlay.max_parlays_per_size,
        )
        wm_saved = save_weaponized_matrix(matrix)
        logger.info(f"Weaponized Matrix: {len(matrix.parlays)} parlays, {wm_saved} legs saved")

        # Send alerts for top signals
        alerts_sent = 0
        for r in sorted(refined_records, key=lambda x: x["refined_spe"], reverse=True)[:10]:
            alpha = next((a for a in alphas if a["alpha_id"] == r["alpha_id"]), None)
            if alpha and r["kelly_modifier"] > 0:
                sent = send_signal_alert(
                    bot_token=CONFIG.telegram_bot_token,
                    chat_id=CONFIG.telegram_chat_id,
                    match_id=r["match_id"],
                    home_team=alpha["home_team"],
                    away_team=alpha["away_team"],
                    match_date=str(alpha["match_date"]),
                    league="",
                    selection=alpha["predicted_outcome"],
                    market_odds=0,
                    fair_odds=0,
                    edge=r["refined_spe"],
                    stake=0,
                    bankroll=0,
                )
                if sent:
                    alerts_sent += 1

        result = {
            "alphas_processed": len(alphas),
            "refined_saved": refined_saved,
            "parlays_built": len(matrix.parlays),
            "weaponized_legs": wm_saved,
            "alerts_sent": alerts_sent,
        }

        audit("scanner", "scan_complete", result)
        return jsonify(result), 200

    except Exception as e:
        logger.exception("Scan failed")
        audit("scanner", "scan_failed", {"error": str(e)})
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
