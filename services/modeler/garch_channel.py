# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
# Unauthorized copying, distribution, or use is strictly prohibited.

"""
GARCH Channel — Volatility-Adjusted Historical Frequency

Uses GARCH(1,1) conditional variance to weight recent outcomes:
    - Low volatility regime  → recent results weighted heavily (stable signal)
    - High volatility regime → recent results dampened (noisy signal)

Weight formula:  w_i = 1 / σ²_i  (inverse variance weighting, normalized)
Probability:     P(outcome) = Σ w_i × I(outcome_i) / Σ w_i

Training: Fit GARCH(1,1) on historical outcome indicator series.
Inference: Compute volatility-weighted outcome frequencies.

PROPRIETARY: This algorithm is original intellectual property.
"""

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Market outcomes
MARKET_OUTCOMES = {
    "h2h":      ["1", "X", "2"],
    "dc":       ["1X", "12", "X2"],
    "btts":     ["Yes", "No"],
    "over_1.5": ["Over 1.5", "Under 1.5"],
    "over_2.5": ["Over 2.5", "Under 2.5"],
}


def _encode_outcomes_for_market(matches: list[dict], market_type: str) -> list[str]:
    """
    Encode match results into market-specific outcome labels.

    matches: list of dicts with keys: goals_for, goals_against, result ('W','D','L'), venue
    """
    encoded = []
    for m in matches:
        gf = m.get("goals_for", 0) or 0
        ga = m.get("goals_against", 0) or 0
        result = m.get("result", "D")
        venue = m.get("venue", "home")
        total = gf + ga

        if market_type == "h2h":
            if venue == "home":
                encoded.append("1" if result == "W" else ("X" if result == "D" else "2"))
            else:
                encoded.append("2" if result == "W" else ("X" if result == "D" else "1"))

        elif market_type == "dc":
            if venue == "home":
                if result == "W":
                    encoded.append("1X")
                elif result == "D":
                    encoded.append("1X")
                else:
                    encoded.append("X2")
            else:
                if result == "W":
                    encoded.append("X2")
                elif result == "D":
                    encoded.append("1X")
                else:
                    encoded.append("1X")

        elif market_type == "btts":
            encoded.append("Yes" if (gf > 0 and ga > 0) else "No")

        elif market_type == "over_1.5":
            encoded.append("Over 1.5" if total > 1.5 else "Under 1.5")

        elif market_type == "over_2.5":
            encoded.append("Over 2.5" if total > 2.5 else "Under 2.5")

    return encoded


def _build_indicator_series(outcomes: list[str], target_outcome: str) -> np.ndarray:
    """Build binary indicator series: 1 if outcome matches target, 0 otherwise."""
    return np.array([1.0 if o == target_outcome else 0.0 for o in outcomes])


def _fit_garch_variances(series: np.ndarray) -> np.ndarray:
    """
    Fit GARCH(1,1) and return conditional variance series.

    Uses the `arch` library if available, falls back to EWMA variance.
    Minimum 10 observations required.
    """
    n = len(series)
    if n < 10:
        return np.ones(n)

    try:
        from arch import arch_model

        # Rescale to percentage returns for numerical stability
        rescaled = (series - series.mean()) * 100.0

        model = arch_model(rescaled, vol="Garch", p=1, q=1, mean="Constant", rescale=False)
        result = model.fit(disp="off", show_warning=False)
        cond_var = result.conditional_volatility ** 2

        # Replace any zero/nan variances with the mean
        mean_var = np.nanmean(cond_var)
        if mean_var <= 0:
            return np.ones(n)
        cond_var = np.where((cond_var <= 0) | np.isnan(cond_var), mean_var, cond_var)

        return cond_var

    except Exception as e:
        logger.warning(f"GARCH fit failed, falling back to EWMA: {e}")
        return _ewma_variance(series)


def _ewma_variance(series: np.ndarray, span: int = 10) -> np.ndarray:
    """Exponentially weighted moving variance as GARCH fallback."""
    n = len(series)
    if n < 2:
        return np.ones(n)

    alpha = 2.0 / (span + 1)
    mean = series.mean()
    var = np.zeros(n)
    var[0] = (series[0] - mean) ** 2

    for i in range(1, n):
        var[i] = alpha * (series[i] - mean) ** 2 + (1 - alpha) * var[i - 1]

    var = np.maximum(var, 1e-6)
    return var


def garch_predict(
    matches: list[dict],
    market_type: str,
    min_matches: int = 15,
) -> tuple[dict[str, float], float]:
    """
    Produce GARCH volatility-adjusted outcome probabilities.

    Args:
        matches: Chronologically ordered match history (oldest first).
        market_type: One of h2h, dc, btts, over_1.5, over_2.5.
        min_matches: Minimum matches for meaningful prediction.

    Returns:
        (outcome_probs, confidence) where confidence is 0-1.
    """
    outcomes_list = MARKET_OUTCOMES.get(market_type)
    if not outcomes_list:
        return {}, 0.0

    n_outcomes = len(outcomes_list)
    uniform = {o: 1.0 / n_outcomes for o in outcomes_list}

    if len(matches) < min_matches:
        return uniform, 0.0

    # Encode match results into market outcomes
    encoded = _encode_outcomes_for_market(matches, market_type)
    if not encoded:
        return uniform, 0.0

    # For each possible outcome, fit GARCH on indicator series and compute
    # inverse-variance-weighted frequency
    weighted_probs = {}

    for target in outcomes_list:
        indicator = _build_indicator_series(encoded, target)
        variances = _fit_garch_variances(indicator)

        # Inverse variance weights
        inv_var = 1.0 / variances
        inv_var_sum = inv_var.sum()

        if inv_var_sum <= 0:
            weighted_probs[target] = indicator.mean()
        else:
            weighted_probs[target] = (inv_var * indicator).sum() / inv_var_sum

    # Normalize
    total = sum(weighted_probs.values())
    if total <= 0:
        return uniform, 0.0

    probs = {o: max(weighted_probs[o] / total, 0.01) for o in outcomes_list}
    p_total = sum(probs.values())
    probs = {o: p / p_total for o, p in probs.items()}

    # Confidence based on sample size and GARCH convergence
    n = len(matches)
    confidence = min(n / 60.0, 1.0)

    return probs, round(confidence, 4)
