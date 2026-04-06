"""
SOODE-Gated Kelly — Staking engine modulated by SOODE matchup diagnosis.

The base Kelly fraction is multiplied by the matchup modifier from the
SOODE assessment. Micro-Shock × Micro-Shock matchups produce a 0× modifier,
effectively blocking the bet entirely.

PROPRIETARY: This algorithm is original intellectual property.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StakeResult:
    match_id: str
    market_type: str
    selection: str
    spe_implied_prob: float
    base_kelly_pct: float
    matchup_modifier: float
    matchup_class: str
    final_stake: float
    bankroll_at_signal: float
    blocked: bool
    reason: str


def compute_edge_from_spe(spe_implied_prob: float, market_odds: float) -> float:
    """Edge = (model_prob × market_odds) - 1."""
    model_prob = spe_implied_prob / 100.0
    return (model_prob * market_odds) - 1.0


def kelly_fraction(edge: float, market_odds: float) -> float:
    """Kelly: f* = edge / (odds - 1)."""
    if market_odds <= 1.0 or edge <= 0:
        return 0.0
    return edge / (market_odds - 1.0)


def compute_stake(
    match_id: str,
    market_type: str,
    selection: str,
    spe_implied_prob: float,
    market_odds: float,
    bankroll: float,
    matchup_modifier: float,
    matchup_class: str,
    daily_exposure_used: float = 0.0,
    kelly_frac: float = 0.25,
    min_edge_pct: float = 2.0,
    max_stake_pct: float = 0.03,
    max_daily_exposure_pct: float = 0.10,
    min_bankroll: float = 500.0,
    drawdown_limit: float = 0.20,
    peak_bankroll: float | None = None,
) -> StakeResult:
    """
    Full SOODE-gated staking pipeline.

    The matchup_modifier comes from the SOODE matchup assessment:
        1.5× for Surging vs Decline (max value)
        0.0× for Micro-Shock vs Micro-Shock (blocked)
    """
    edge = compute_edge_from_spe(spe_implied_prob, market_odds)
    edge_pct = edge * 100

    def blocked(reason: str) -> StakeResult:
        return StakeResult(
            match_id=match_id, market_type=market_type, selection=selection,
            spe_implied_prob=spe_implied_prob, base_kelly_pct=0,
            matchup_modifier=matchup_modifier, matchup_class=matchup_class,
            final_stake=0, bankroll_at_signal=bankroll, blocked=True, reason=reason,
        )

    # Gate 1: Matchup block
    if matchup_modifier <= 0:
        return blocked(f"SOODE BLOCK: {matchup_class} — modifier is 0×")

    # Gate 2: Bankroll minimum
    if bankroll < min_bankroll:
        return blocked(f"Bankroll ${bankroll:.2f} below minimum ${min_bankroll:.2f}")

    # Gate 3: Drawdown circuit breaker
    if peak_bankroll and peak_bankroll > 0:
        dd = (peak_bankroll - bankroll) / peak_bankroll
        if dd >= drawdown_limit:
            return blocked(f"CIRCUIT BREAKER: {dd:.1%} drawdown from peak")

    # Gate 4: Minimum edge
    if edge_pct < min_edge_pct:
        return blocked(f"Edge {edge_pct:.2f}% below {min_edge_pct}% threshold")

    # Compute base Kelly
    raw_kelly = kelly_fraction(edge, market_odds)
    base_pct = raw_kelly * kelly_frac

    # Apply SOODE matchup modifier
    modified_pct = base_pct * matchup_modifier

    # Compute stake
    stake = bankroll * modified_pct

    # Cap: single bet
    max_single = bankroll * max_stake_pct
    if stake > max_single:
        stake = max_single

    # Cap: daily exposure
    remaining = (bankroll * max_daily_exposure_pct) - daily_exposure_used
    if remaining <= 0:
        return blocked("Daily exposure limit reached")
    if stake > remaining:
        stake = remaining

    stake = round(stake, 2)

    return StakeResult(
        match_id=match_id, market_type=market_type, selection=selection,
        spe_implied_prob=spe_implied_prob,
        base_kelly_pct=round(base_pct * 100, 3),
        matchup_modifier=matchup_modifier, matchup_class=matchup_class,
        final_stake=stake, bankroll_at_signal=bankroll,
        blocked=False, reason=f"VALUE: {matchup_class} @ {matchup_modifier}× modifier",
    )
