"""
Weaponized Matrix — Correlation-Adjusted Parlay Construction Engine (v3.0)

REVISION FROM v2.1:
    - Per-leg correlation haircut: 0.97^(n_legs - 1)
    - Same-day same-league penalty: 0.94^(n_same_league_pairs)
    - Rationale: matches share referee pools, weather, rotation patterns,
      and tactical chain reactions within the same league/matchday.

PROPRIETARY: This algorithm is original intellectual property.
"""

import logging
import uuid
from dataclasses import dataclass
from itertools import combinations
from collections import Counter
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ParlayLeg:
    alpha_id: int
    match_id: str
    home_team: str
    away_team: str
    market_type: str
    selection: str
    spe_implied_prob: float
    matchup_class: str
    kelly_modifier: float
    match_date: str
    league: str


@dataclass
class Parlay:
    parlay_id: str
    legs: list[ParlayLeg]
    raw_cumulative: float        # Before correlation adjustment
    adjusted_cumulative: float   # After correlation haircut
    correlation_penalty: float   # Total penalty applied
    payout_multiplier: float
    risk_grade: str
    n_same_league_pairs: int


@dataclass
class WeaponizedMatrix:
    parlays: list[Parlay]
    total_legs_available: int
    legs_used: int
    timestamp: str


# Probability floors (applied to ADJUSTED cumulative)
PROB_FLOORS = {2: 0.58, 3: 0.42, 4: 0.27, 5: 0.17, 6: 0.10}

# Correlation parameters
LEG_HAIRCUT = 0.97         # Per additional leg beyond the first
LEAGUE_DAY_PENALTY = 0.94  # Per same-league same-day pair


def _parlay_id() -> str:
    return f"WM-{uuid.uuid4().hex[:8].upper()}"


def _count_same_league_day_pairs(legs: list[ParlayLeg]) -> int:
    """Count pairs of legs from same league on same day."""
    groups: dict[str, int] = Counter()
    for leg in legs:
        day = leg.match_date[:10] if leg.match_date else ""
        key = f"{leg.league}|{day}"
        groups[key] += 1

    # Number of pairs within each group: C(n, 2) = n*(n-1)/2
    total_pairs = 0
    for count in groups.values():
        if count >= 2:
            total_pairs += count * (count - 1) // 2
    return total_pairs


def compute_adjusted_cumulative(
    legs: list[ParlayLeg],
) -> tuple[float, float, float, int]:
    """
    Compute correlation-adjusted cumulative probability.

    Returns: (raw_cumulative, adjusted_cumulative, penalty, n_same_league_pairs)
    """
    # Raw product of individual probabilities
    raw = 1.0
    for leg in legs:
        raw *= (leg.spe_implied_prob / 100.0)

    # Per-leg correlation haircut
    n = len(legs)
    leg_penalty = LEG_HAIRCUT ** max(n - 1, 0)

    # Same-league same-day penalty
    slp = _count_same_league_day_pairs(legs)
    league_penalty = LEAGUE_DAY_PENALTY ** slp

    total_penalty = leg_penalty * league_penalty
    adjusted = raw * total_penalty

    return (
        round(raw * 100, 2),
        round(adjusted * 100, 2),
        round(total_penalty, 4),
        slp,
    )


def compute_payout(legs: list[ParlayLeg]) -> float:
    mult = 1.0
    for leg in legs:
        fair = 100.0 / max(leg.spe_implied_prob, 1.0)
        mult *= fair
    return round(mult, 2)


def grade(adj_prob: float, n_legs: int) -> str:
    if adj_prob >= 55 and n_legs <= 3:
        return "A"
    elif adj_prob >= 38:
        return "B"
    elif adj_prob >= 22:
        return "C"
    return "D"


def filter_legs(
    signals: list[dict],
    min_spe: float = 76.0,
) -> list[ParlayLeg]:
    """Filter and sort eligible legs."""
    blocked_matchups = {
        "Micro-Shock vs Micro-Shock",
        "Decline vs Micro-Shock",
        "Micro-Shock vs Decline",
    }

    legs = []
    for s in signals:
        if s.get("spe_implied_prob", 0) < min_spe:
            continue
        if s.get("matchup_class", "") in blocked_matchups:
            continue
        if s.get("kelly_modifier", 1.0) <= 0:
            continue

        legs.append(ParlayLeg(
            alpha_id=s.get("alpha_id", 0),
            match_id=s["match_id"],
            home_team=s.get("home_team", ""),
            away_team=s.get("away_team", ""),
            market_type=s["market_type"],
            selection=s.get("predicted_outcome", ""),
            spe_implied_prob=s["spe_implied_prob"],
            matchup_class=s.get("matchup_class", ""),
            kelly_modifier=s.get("kelly_modifier", 1.0),
            match_date=str(s.get("match_date", "")),
            league=s.get("league", ""),
        ))

    legs.sort(key=lambda l: l.spe_implied_prob, reverse=True)
    return legs


def build_parlays_for_size(
    legs: list[ParlayLeg],
    size: int,
    floor: float,
    max_parlays: int = 3,
) -> list[Parlay]:
    """Build parlays of given size with correlation adjustment."""
    candidates = legs[:min(len(legs), 20)]
    valid = []

    for combo in combinations(range(len(candidates)), size):
        selected = [candidates[i] for i in combo]

        # No duplicate matches
        match_ids = [l.match_id for l in selected]
        if len(set(match_ids)) < len(match_ids):
            continue

        raw, adjusted, penalty, slp = compute_adjusted_cumulative(selected)

        # Apply floor to ADJUSTED cumulative
        if adjusted < floor * 100:
            continue

        valid.append(Parlay(
            parlay_id=_parlay_id(),
            legs=sorted(selected, key=lambda l: l.spe_implied_prob, reverse=True),
            raw_cumulative=raw,
            adjusted_cumulative=adjusted,
            correlation_penalty=penalty,
            payout_multiplier=compute_payout(selected),
            risk_grade=grade(adjusted, size),
            n_same_league_pairs=slp,
        ))

    valid.sort(key=lambda p: (-p.adjusted_cumulative, p.payout_multiplier))
    return valid[:max_parlays]


def construct_weaponized_matrix(
    signals: list[dict],
    min_spe: float = 76.0,
    max_parlays_per_size: int = 3,
) -> WeaponizedMatrix:
    """Full matrix construction with correlation adjustments."""
    legs = filter_legs(signals, min_spe=min_spe)

    if not legs:
        return WeaponizedMatrix(parlays=[], total_legs_available=0,
                                legs_used=0, timestamp="")

    all_parlays: list[Parlay] = []
    used: set[int] = set()

    for size in range(2, 7):
        floor = PROB_FLOORS.get(size, 0.10)
        if len(legs) < size:
            continue
        parlays = build_parlays_for_size(legs, size, floor, max_parlays_per_size)
        all_parlays.extend(parlays)
        for p in parlays:
            for leg in p.legs:
                used.add(leg.alpha_id)

    logger.info(
        f"Weaponized Matrix: {len(all_parlays)} parlays, "
        f"{len(used)}/{len(legs)} legs used, correlation-adjusted"
    )

    return WeaponizedMatrix(
        parlays=all_parlays,
        total_legs_available=len(legs),
        legs_used=len(used),
        timestamp=datetime.utcnow().isoformat(),
    )
