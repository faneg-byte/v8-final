"""
SOODE — Stochastic Outcome-Oriented Divergence Engine (v3.0)

REVISION FROM v2.1:
    - Divergence now computed from MODEL-PREDICTED PROBABILITIES vs actual outcomes.
    - Result flags used ONLY during cold-start bootstrap (first 100 matches per team).
    - After bootstrap, SOODE measures how wrong the MODEL is, not just whether the team won.
    - This captures calibration quality: a 90% predicted win that doesn't happen is worse
      divergence than a 55% predicted win that doesn't happen.

PROPRIETARY: This algorithm is original intellectual property.
"""

import math
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Diagnosis(Enum):
    STABLE = "🟢 Stable"
    SURGING = "🚀 Surging Form (Underpriced by Bookies)"
    MICRO_SHOCK = "⚡ Micro-Shock (Temporary Variance)"
    DECLINE = "🔴 Fundamental Decline (Engine Deprecated)"


@dataclass
class SOODEProfile:
    team_id: int
    team_name: str
    micro_grip: float
    meso_grip: float
    macro_grip: float
    dna_grip: float
    diagnosis: Diagnosis
    confidence: float
    bootstrap_mode: bool  # True if using proxy probabilities


@dataclass
class MatchupAssessment:
    home_diagnosis: Diagnosis
    away_diagnosis: Diagnosis
    matchup_class: str
    kelly_modifier: float
    recommended_action: str
    accentuation: str


# ─────────────────────────────────────────────
# Divergence (model-probability based)
# ─────────────────────────────────────────────

# Historical base rates for cold-start bootstrap
BASE_RATES = {"W": 0.46, "D": 0.27, "L": 0.27}


def compute_divergence(predictions: list[dict]) -> float:
    """
    Mean absolute divergence between model prediction and reality.

    Each prediction dict:
        model_prob: float — model's predicted probability for the outcome that actually occurred.
                           High model_prob + outcome happened = low divergence (model was right).
                           High model_prob + outcome didn't happen = high divergence (model was wrong).
        actual: int       — 1 if this was the outcome that occurred, 0 otherwise.

    A perfectly calibrated model has divergence ≈ 0.
    A coin-flip model on binary outcomes has divergence ≈ 0.5.
    """
    if not predictions:
        return 0.0
    total = sum(abs(p["model_prob"] - p["actual"]) for p in predictions)
    return total / len(predictions)


def rolling_divergence(predictions: list[dict], window: int) -> float:
    """Rolling window divergence over most recent N predictions."""
    recent = predictions[-window:] if len(predictions) >= window else predictions
    return compute_divergence(recent)


def build_prediction_record(
    model_prob: float | None,
    actual_outcome: str,
    result: str,
    is_bootstrap: bool = False,
) -> dict:
    """
    Build a standardized prediction record.

    If model_prob is None (cold-start), use historical base rates as proxy.
    model_prob is the probability the model assigned to the outcome that actually happened.
    """
    if model_prob is not None and not is_bootstrap:
        return {"model_prob": model_prob, "actual": 1}

    # Bootstrap: use historical base rates
    proxy_prob = BASE_RATES.get(result, 0.33)
    return {"model_prob": proxy_prob, "actual": 1}


# ─────────────────────────────────────────────
# Cascading Mesh
# ─────────────────────────────────────────────

def compute_grips(
    predictions: list[dict],
    prev_meso: float | None = None,
    prev_macro: float | None = None,
    prev_dna: float | None = None,
    meso_alpha: float = 0.7,
    macro_alpha: float = 0.6,
    dna_alpha: float = 0.3,
) -> tuple[float, float, float, float]:
    """
    4-grip cascade: micro → meso → macro → dna.

    Each successive grip has more inertia, smoothing out short-term noise
    while preserving structural signals.
    """
    micro = rolling_divergence(predictions, window=5)

    raw_meso = rolling_divergence(predictions, window=15)
    meso = (meso_alpha * raw_meso + (1 - meso_alpha) * prev_meso
            if prev_meso is not None else raw_meso)

    raw_macro = rolling_divergence(predictions, window=30)
    macro = (macro_alpha * raw_macro + (1 - macro_alpha) * prev_macro
             if prev_macro is not None else raw_macro)

    dna = (dna_alpha * macro + (1 - dna_alpha) * prev_dna
           if prev_dna is not None else macro)

    return round(micro, 4), round(meso, 4), round(macro, 4), round(dna, 4)


# ─────────────────────────────────────────────
# Diagnosis
# ─────────────────────────────────────────────

def diagnose(
    micro: float, meso: float, macro: float, dna: float,
    shock_ratio: float = 1.6,
    shock_floor: float = 0.25,
    surge_ratio: float = 0.4,
    surge_ceiling: float = 0.10,
    decline_threshold: float = 0.25,
) -> tuple[Diagnosis, float]:
    """Priority: Micro-Shock → Surging → Decline → Stable."""
    meso_s = max(meso, 0.001)
    dna_s = max(dna, 0.001)

    if micro > shock_ratio * meso_s and micro > shock_floor:
        strength = min((micro / meso_s - shock_ratio) / shock_ratio, 1.0)
        return Diagnosis.MICRO_SHOCK, round(strength, 3)

    if micro < surge_ratio * dna_s and micro < surge_ceiling:
        strength = min(1.0 - (micro / (surge_ratio * dna_s)), 1.0)
        return Diagnosis.SURGING, round(strength, 3)

    if dna > decline_threshold:
        strength = min((dna - decline_threshold) / decline_threshold, 1.0)
        return Diagnosis.DECLINE, round(strength, 3)

    stability = max(1.0 - (dna / decline_threshold), 0.1)
    return Diagnosis.STABLE, round(stability, 3)


def compute_team_profile(
    team_id: int,
    team_name: str,
    match_history: list[dict],
    prev_grips: dict | None = None,
    bootstrap_threshold: int = 100,
) -> SOODEProfile:
    """
    Full SOODE computation for a single team.

    match_history: list of dicts with keys:
        model_prob: float | None — model's probability for actual outcome
        result: str — 'W', 'D', or 'L'
        has_model_prob: bool — True if model_prob is from actual model output
    """
    # Count how many have real model probabilities
    real_count = sum(1 for m in match_history if m.get("has_model_prob", False))
    is_bootstrap = real_count < bootstrap_threshold

    # Build prediction records
    predictions = []
    for m in match_history:
        rec = build_prediction_record(
            model_prob=m.get("model_prob"),
            actual_outcome=m.get("actual_outcome", ""),
            result=m.get("result", "D"),
            is_bootstrap=not m.get("has_model_prob", False),
        )
        predictions.append(rec)

    prev = prev_grips or {}
    micro, meso, macro, dna = compute_grips(
        predictions,
        prev_meso=prev.get("meso"),
        prev_macro=prev.get("macro"),
        prev_dna=prev.get("dna"),
    )

    diagnosis, confidence = diagnose(micro, meso, macro, dna)

    if is_bootstrap:
        confidence *= 0.6  # Reduce confidence during bootstrap

    return SOODEProfile(
        team_id=team_id,
        team_name=team_name,
        micro_grip=micro,
        meso_grip=meso,
        macro_grip=macro,
        dna_grip=dna,
        diagnosis=diagnosis,
        confidence=confidence,
        bootstrap_mode=is_bootstrap,
    )


# ─────────────────────────────────────────────
# Matchup Matrix
# ─────────────────────────────────────────────

MATCHUP_MATRIX: dict[tuple[Diagnosis, Diagnosis], tuple[float, str, str]] = {
    (Diagnosis.STABLE, Diagnosis.STABLE):           (1.0,  "Standard or elevated stake", "neutral"),
    (Diagnosis.STABLE, Diagnosis.SURGING):           (1.25, "Elevated, lean toward Surging", "accentuate"),
    (Diagnosis.STABLE, Diagnosis.DECLINE):           (1.25, "Elevated on Stable", "accentuate"),
    (Diagnosis.STABLE, Diagnosis.MICRO_SHOCK):       (0.5,  "Halve stake or avoid", "contradict"),
    (Diagnosis.SURGING, Diagnosis.STABLE):           (1.25, "Elevated, lean toward Surging", "accentuate"),
    (Diagnosis.SURGING, Diagnosis.SURGING):           (0.85, "Standard; consider derivative markets", "neutral"),
    (Diagnosis.SURGING, Diagnosis.DECLINE):           (1.5,  "Maximum value — exploit the bookmaker", "accentuate"),
    (Diagnosis.SURGING, Diagnosis.MICRO_SHOCK):       (0.5,  "Halve stake, only if massive edge", "contradict"),
    (Diagnosis.DECLINE, Diagnosis.STABLE):           (1.25, "Elevated on Stable", "accentuate"),
    (Diagnosis.DECLINE, Diagnosis.SURGING):           (1.5,  "Maximum value — exploit the bookmaker", "accentuate"),
    (Diagnosis.DECLINE, Diagnosis.DECLINE):           (0.6,  "Low stake; only extreme edge (>5%)", "neutral"),
    (Diagnosis.DECLINE, Diagnosis.MICRO_SHOCK):       (0.0,  "Blocked — avoid entirely", "contradict"),
    (Diagnosis.MICRO_SHOCK, Diagnosis.STABLE):       (0.5,  "Halve stake or avoid", "contradict"),
    (Diagnosis.MICRO_SHOCK, Diagnosis.SURGING):       (0.5,  "Halve stake, only if massive edge", "contradict"),
    (Diagnosis.MICRO_SHOCK, Diagnosis.DECLINE):       (0.0,  "Blocked — avoid entirely", "contradict"),
    (Diagnosis.MICRO_SHOCK, Diagnosis.MICRO_SHOCK): (0.0,  "Blocked — maximum volatility", "contradict"),
}

DIAG_SHORT = {
    Diagnosis.STABLE: "Stable",
    Diagnosis.SURGING: "Surging",
    Diagnosis.MICRO_SHOCK: "Micro-Shock",
    Diagnosis.DECLINE: "Decline",
}


def assess_matchup(home: SOODEProfile, away: SOODEProfile) -> MatchupAssessment:
    key = (home.diagnosis, away.diagnosis)
    modifier, action, accentuation = MATCHUP_MATRIX.get(key, (1.0, "Standard", "neutral"))

    matchup_class = f"{DIAG_SHORT[home.diagnosis]} vs {DIAG_SHORT[away.diagnosis]}"

    return MatchupAssessment(
        home_diagnosis=home.diagnosis,
        away_diagnosis=away.diagnosis,
        matchup_class=matchup_class,
        kelly_modifier=modifier,
        recommended_action=action,
        accentuation=accentuation,
    )
