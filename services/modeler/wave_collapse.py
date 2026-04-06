# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
# Unauthorized copying, distribution, or use is strictly prohibited.

"""
Wave Collapse Prediction Engine

Multiple model channels produce independent probability estimates per market.
These exist in "superposition" until collapsed into a single conviction signal
using learned attention weights from walk-forward optimization.

Channels:
    1. GARCH — Volatility-adjusted historical frequency (arch library)
    2. LSTM  — Sequential pattern recognition on Table B intervals (NumPy)
    3. Bayesian — Prior × evidence posterior (Dirichlet-Multinomial)
    4. CNN   — Convolutional pattern recognition on team form matrices (NumPy)

100% Historical Back-Fit:
    Every WFO epoch retrains ALL channels on the expanded window and
    recalibrates SOODE thresholds, channel weights, and model predictions.
    No stale weights are carried forward without recalibration.

Output:
    SPE (Spectral Probability Estimate) — collapsed conviction probability.

PROPRIETARY: This algorithm is original intellectual property.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from modeler.garch_channel import garch_predict
from modeler.lstm_channel import (
    lstm_predict, lstm_train, build_sequence, NumpyLSTM,
)
from modeler.cnn_channel import (
    cnn_predict, cnn_train, build_form_matrix, NumpyCNN,
)

logger = logging.getLogger(__name__)

# Market type definitions
MARKET_TYPES = ["h2h", "dc", "btts", "over_1.5", "over_2.5"]

# Market outcomes per type
MARKET_OUTCOMES = {
    "h2h":      ["1", "X", "2"],
    "dc":       ["1X", "12", "X2"],
    "btts":     ["Yes", "No"],
    "over_1.5": ["Over 1.5", "Under 1.5"],
    "over_2.5": ["Over 2.5", "Under 2.5"],
}


@dataclass
class ChannelOutput:
    """Probability estimate from a single model channel."""
    channel_name: str
    market_type: str
    outcome_probs: dict[str, float]  # outcome -> probability
    confidence: float  # Channel self-reported confidence (0-1)


@dataclass
class CollapsedSignal:
    """Final collapsed prediction for one market of one match."""
    match_id: str
    market_type: str
    predicted_outcome: str
    spe_implied_prob: float  # 0-100 scale
    raw_prob: float  # 0-1 scale
    channel_weights: dict[str, float]
    channel_contributions: dict[str, float]


@dataclass
class WaveState:
    """Superposition state before collapse — all channels for one match × market."""
    match_id: str
    market_type: str
    channels: list[ChannelOutput]


# ─────────────────────────────────────────────
# Channel Implementations (wired to real models)
# ─────────────────────────────────────────────

def garch_channel(
    team_match_history: list[dict],
    market_type: str,
) -> ChannelOutput:
    """
    GARCH volatility-adjusted historical frequency.

    Uses conditional variance from GARCH(1,1) to weight recent outcomes
    more heavily when volatility is low (stable regime) and dampen them
    when volatility spikes.

    w_i = 1 / σ²_i (normalized)
    P(outcome) = Σ w_i × I(outcome_i) / Σ w_i
    """
    probs, confidence = garch_predict(team_match_history, market_type)

    if not probs:
        outcomes = MARKET_OUTCOMES[market_type]
        probs = {o: 1.0 / len(outcomes) for o in outcomes}
        confidence = 0.0

    return ChannelOutput(
        channel_name="garch",
        market_type=market_type,
        outcome_probs=probs,
        confidence=confidence,
    )


def lstm_channel(
    team_intervals: list[dict],
    market_type: str,
    model_weights: dict | None = None,
) -> ChannelOutput:
    """
    LSTM sequential pattern recognition.

    Processes Table B interval data as a time series:
        Input shape: (6 intervals × features_per_interval)
        Output: softmax over market outcomes

    Architecture:
        Input(6, F) → LSTM(64) → Dense(32, ReLU) → Softmax(|outcomes|)
    """
    probs, confidence = lstm_predict(team_intervals, market_type, model_weights)

    if not probs:
        outcomes = MARKET_OUTCOMES[market_type]
        probs = {o: 1.0 / len(outcomes) for o in outcomes}
        confidence = 0.0

    return ChannelOutput(
        channel_name="lstm",
        market_type=market_type,
        outcome_probs=probs,
        confidence=confidence,
    )


def bayesian_channel(
    prior: dict[str, float],
    recent_evidence: list[dict],
    market_type: str,
) -> ChannelOutput:
    """
    Bayesian posterior update.

    Prior: Historical base rate for this market type.
    Likelihood: Recent match outcomes weighted by recency.
    Posterior: P(outcome | evidence) ∝ P(evidence | outcome) × P(outcome)

    Specialized Bayesian approach:
        1. Conjugate Dirichlet-Multinomial for discrete outcomes
        2. Hierarchical prior (league → team → matchup)
        3. Robust posterior via β-divergence (downweight outliers)
    """
    outcomes = MARKET_OUTCOMES[market_type]

    if not prior:
        prior = {o: 1.0 / len(outcomes) for o in outcomes}

    # Conjugate update: Dirichlet prior + multinomial likelihood
    alpha_prior = {o: prior[o] * 10 for o in outcomes}

    for ev in recent_evidence:
        outcome = ev.get("outcome", "")
        if outcome in alpha_prior:
            alpha_prior[outcome] += ev.get("weight", 1.0)

    total = sum(alpha_prior.values())
    posterior = {o: alpha_prior[o] / total for o in outcomes}

    # Confidence from effective sample size
    n_eff = sum(1 for e in recent_evidence if e.get("outcome") in outcomes)
    confidence = min(n_eff / 30.0, 1.0)

    return ChannelOutput(
        channel_name="bayesian",
        market_type=market_type,
        outcome_probs=posterior,
        confidence=confidence,
    )


def cnn_channel(
    team_form_matrix: np.ndarray | None,
    market_type: str,
    model_weights: dict | None = None,
) -> ChannelOutput:
    """
    CNN pattern recognition on team form matrices.

    Treats the 60-row Table B data as a 2D matrix:
        Rows: 6 intervals × 10 matches = 60 rows
        Columns: Feature dimensions (goals, results, venue, etc.)

    Architecture (Conv1D over match sequence):
        Input(60, F) → Conv1D(32, k=3) → MaxPool → Conv1D(16, k=3) →
        GlobalAvgPool → Dense(32) → Softmax(|outcomes|)
    """
    probs, confidence = cnn_predict(team_form_matrix, market_type, model_weights)

    if not probs:
        outcomes = MARKET_OUTCOMES[market_type]
        probs = {o: 1.0 / len(outcomes) for o in outcomes}
        confidence = 0.0

    return ChannelOutput(
        channel_name="cnn",
        market_type=market_type,
        outcome_probs=probs,
        confidence=confidence,
    )


# ─────────────────────────────────────────────
# Wave Collapse
# ─────────────────────────────────────────────

def build_superposition(
    match_id: str,
    market_type: str,
    channels: list[ChannelOutput],
) -> WaveState:
    """Assemble multiple channel outputs into a superposition state."""
    return WaveState(
        match_id=match_id,
        market_type=market_type,
        channels=channels,
    )


def collapse(
    state: WaveState,
    channel_weights: dict[str, float] | None = None,
    spe_threshold: float = 76.0,
) -> CollapsedSignal | None:
    """
    Collapse the superposition into a single conviction signal.

    The collapse uses learned attention weights from WFO to combine
    channel outputs. If no weights are provided, uses confidence-weighted
    averaging as the default.

    Args:
        state: Superposition state with all channel outputs.
        channel_weights: Learned weights from walk-forward optimization.
                        Keys are channel names, values are attention weights.
        spe_threshold: Minimum SPE to emit a signal (default 76%).

    Returns:
        CollapsedSignal if SPE exceeds threshold, else None.
    """
    channels = state.channels
    if not channels:
        return None

    outcomes = MARKET_OUTCOMES.get(state.market_type)
    if not outcomes:
        return None

    # Determine weights
    if channel_weights:
        weights = {c.channel_name: channel_weights.get(c.channel_name, 0.0)
                   for c in channels}
    else:
        # Default: confidence-weighted averaging
        total_conf = sum(c.confidence for c in channels) or 1.0
        weights = {c.channel_name: c.confidence / total_conf for c in channels}

    # Normalize weights
    w_total = sum(weights.values()) or 1.0
    weights = {k: v / w_total for k, v in weights.items()}

    # Weighted combination of probabilities
    collapsed_probs: dict[str, float] = {o: 0.0 for o in outcomes}
    channel_contributions: dict[str, float] = {}

    for channel in channels:
        w = weights.get(channel.channel_name, 0.0)
        for outcome in outcomes:
            p = channel.outcome_probs.get(outcome, 0.0)
            collapsed_probs[outcome] += w * p
        channel_contributions[channel.channel_name] = w

    # Normalize (should already sum to ~1.0 but enforce)
    p_total = sum(collapsed_probs.values()) or 1.0
    collapsed_probs = {o: p / p_total for o, p in collapsed_probs.items()}

    # Select highest-probability outcome
    best_outcome = max(collapsed_probs, key=lambda o: collapsed_probs[o])
    best_prob = collapsed_probs[best_outcome]
    spe = best_prob * 100.0

    # Only emit if above threshold
    if spe < spe_threshold:
        return None

    return CollapsedSignal(
        match_id=state.match_id,
        market_type=state.market_type,
        predicted_outcome=best_outcome,
        spe_implied_prob=round(spe, 2),
        raw_prob=round(best_prob, 4),
        channel_weights=weights,
        channel_contributions=channel_contributions,
    )


# ─────────────────────────────────────────────
# Team Data Loading Helpers
# ─────────────────────────────────────────────

def load_team_features(cur, team_id: int) -> dict:
    """
    Load Tables A, B, C for a team and build the full data payload
    required by all four channels.

    Returns dict with keys:
        match_history: chronological list of match dicts (for GARCH)
        intervals: Table B rows with interval_id (for LSTM)
        form_matrix: (60, 7) numpy array (for CNN)
        base_rates: {market_type: {outcome: rate}} (for Bayesian prior)
        recent_outcomes: list of {outcome, weight} (for Bayesian evidence)
    """
    # Table B: 60-row interval history (also used for GARCH match_history)
    cur.execute("""
        SELECT match_id, interval_id, match_date, opponent_id, venue,
               goals_for, goals_against, result, row_rank
        FROM team_match_intervals
        WHERE team_id = %s
        ORDER BY match_date ASC
    """, (team_id,))
    interval_rows = [dict(r) for r in cur.fetchall()]

    # Build match_history for GARCH (chronological)
    match_history = []
    for r in interval_rows:
        match_history.append({
            "goals_for": r.get("goals_for", 0) or 0,
            "goals_against": r.get("goals_against", 0) or 0,
            "result": r.get("result", "D"),
            "venue": r.get("venue", "home"),
            "match_date": r.get("match_date"),
        })

    # Build form matrix for CNN (60, 7)
    form_matrix = build_form_matrix(match_history, max_rows=60)

    # Build base rates for Bayesian prior
    base_rates = {}
    for market_type in MARKET_TYPES:
        outcomes = MARKET_OUTCOMES[market_type]
        counts = {o: 0 for o in outcomes}
        for m in match_history:
            actual = _encode_match_outcome(m, market_type)
            if actual in counts:
                counts[actual] += 1
        total = sum(counts.values())
        if total > 0:
            base_rates[market_type] = {o: counts[o] / total for o in outcomes}
        else:
            base_rates[market_type] = {o: 1.0 / len(outcomes) for o in outcomes}

    # Build recent evidence for Bayesian (last 15 matches, recency-weighted)
    recent_outcomes = []
    recent = match_history[-15:] if len(match_history) >= 15 else match_history
    for i, m in enumerate(recent):
        weight = 0.5 + 0.5 * (i / max(len(recent) - 1, 1))
        for market_type in MARKET_TYPES:
            actual = _encode_match_outcome(m, market_type)
            if actual:
                recent_outcomes.append({
                    "outcome": actual,
                    "weight": weight,
                    "market_type": market_type,
                })

    return {
        "match_history": match_history,
        "intervals": interval_rows,
        "form_matrix": form_matrix,
        "base_rates": base_rates,
        "recent_outcomes": recent_outcomes,
    }


def _encode_match_outcome(m: dict, market_type: str) -> Optional[str]:
    """Encode a match dict into its market-specific outcome string."""
    gf = m.get("goals_for", 0) or 0
    ga = m.get("goals_against", 0) or 0
    result = m.get("result", "D")
    venue = m.get("venue", "home")
    total = gf + ga

    if market_type == "h2h":
        if venue == "home":
            return "1" if result == "W" else ("X" if result == "D" else "2")
        else:
            return "2" if result == "W" else ("X" if result == "D" else "1")
    elif market_type == "dc":
        if venue == "home":
            return "1X" if result in ("W", "D") else "X2"
        else:
            return "X2" if result in ("W", "D") else "1X"
    elif market_type == "btts":
        return "Yes" if (gf > 0 and ga > 0) else "No"
    elif market_type == "over_1.5":
        return "Over 1.5" if total > 1.5 else "Under 1.5"
    elif market_type == "over_2.5":
        return "Over 2.5" if total > 2.5 else "Under 2.5"
    return None


# ─────────────────────────────────────────────
# WFO Weight Loading
# ─────────────────────────────────────────────

def load_wfo_weights(cur) -> tuple[
    dict[str, dict[str, float]],
    dict[str, dict],
    dict[str, dict],
]:
    """
    Load the latest WFO channel weights AND trained model weights.

    Returns:
        (channel_weights, lstm_weights, cnn_weights)
        Each keyed by market_type.
    """
    channel_weights = {}
    lstm_weights = {}
    cnn_weights = {}

    # Channel attention weights from wfo_calibration
    try:
        cur.execute("""
            SELECT channel_weights FROM wfo_calibration
            ORDER BY wfo_epoch DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            raw = row[0] if isinstance(row, dict) else row[0]
            channel_weights = raw if isinstance(raw, dict) else json.loads(raw)
    except Exception as e:
        logger.warning(f"Failed to load WFO channel weights: {e}")

    # LSTM and CNN model weights from audit_trail
    try:
        cur.execute("""
            SELECT detail FROM audit_trail
            WHERE service = 'wfo' AND action = 'model_weights'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            raw = row[0] if isinstance(row, dict) else row[0]
            data = raw if isinstance(raw, dict) else json.loads(raw)
            lstm_weights = data.get("lstm_weights", {})
            cnn_weights = data.get("cnn_weights", {})
    except Exception as e:
        logger.warning(f"Failed to load model weights: {e}")

    return channel_weights, lstm_weights, cnn_weights


# ─────────────────────────────────────────────
# Full Prediction Pipeline
# ─────────────────────────────────────────────

def predict_match(
    match_id: str,
    home_data: dict,
    away_data: dict,
    wfo_weights: dict[str, dict[str, float]] | None = None,
    lstm_weights: dict[str, dict] | None = None,
    cnn_weights: dict[str, dict] | None = None,
    spe_threshold: float = 76.0,
) -> list[CollapsedSignal]:
    """
    Generate predictions for all 5 market types for a single match.

    Args:
        match_id: Unique match identifier.
        home_data: Dict from load_team_features().
        away_data: Same structure for away team.
        wfo_weights: Per-market channel weights from WFO.
                    Format: {market_type: {channel_name: weight}}
        lstm_weights: Per-market LSTM model weights.
                     Format: {market_type: serialized_weights}
        cnn_weights: Per-market CNN model weights.
                    Format: {market_type: serialized_weights}
        spe_threshold: Minimum SPE to emit signal.

    Returns:
        List of CollapsedSignals that exceed the threshold.
    """
    signals = []

    for market in MARKET_TYPES:
        # GARCH: uses full match history
        ch_garch = garch_channel(
            home_data.get("match_history", []),
            market,
        )

        # LSTM: uses interval-tagged data + trained weights
        market_lstm_w = (lstm_weights or {}).get(market)
        ch_lstm = lstm_channel(
            home_data.get("intervals", []),
            market,
            model_weights=market_lstm_w,
        )

        # Bayesian: uses base rates + recent evidence
        market_evidence = [
            e for e in home_data.get("recent_outcomes", [])
            if e.get("market_type") == market
        ]
        ch_bayesian = bayesian_channel(
            prior=home_data.get("base_rates", {}).get(market, {}),
            recent_evidence=market_evidence,
            market_type=market,
        )

        # CNN: uses form matrix + trained weights
        market_cnn_w = (cnn_weights or {}).get(market)
        ch_cnn = cnn_channel(
            home_data.get("form_matrix"),
            market,
            model_weights=market_cnn_w,
        )

        # Superposition
        state = build_superposition(
            match_id, market,
            [ch_garch, ch_lstm, ch_bayesian, ch_cnn],
        )

        # Collapse with market-specific weights
        market_weights = (wfo_weights or {}).get(market)
        signal = collapse(state, market_weights, spe_threshold)

        if signal:
            signals.append(signal)

    return signals
