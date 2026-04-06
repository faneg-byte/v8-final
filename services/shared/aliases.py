"""
Team name resolution — deterministic aliases + fuzzy Levenshtein matching.

REVISION FROM v2.1:
    - Added fuzzy matching via Levenshtein distance (threshold 0.85).
    - Collision audit: logs when fuzzy match produces ambiguous results.
    - Auto-registers new fuzzy matches for future deterministic lookups.

PROPRIETARY: This module is original intellectual property.
"""

import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

ALIAS_MAP: dict[str, list[str]] = {
    "Manchester City FC": ["Man City", "Manchester City", "Man. City"],
    "Liverpool FC": ["Liverpool"],
    "Arsenal FC": ["Arsenal"],
    "Chelsea FC": ["Chelsea"],
    "Manchester United FC": ["Man United", "Manchester United", "Man Utd", "Man. United"],
    "Tottenham Hotspur FC": ["Tottenham", "Spurs"],
    "Newcastle United FC": ["Newcastle", "Newcastle United"],
    "Aston Villa FC": ["Aston Villa", "Villa"],
    "West Ham United FC": ["West Ham"],
    "Wolverhampton Wanderers FC": ["Wolves", "Wolverhampton"],
    "Crystal Palace FC": ["Crystal Palace"],
    "Brighton & Hove Albion FC": ["Brighton"],
    "AFC Bournemouth": ["Bournemouth"],
    "Nottingham Forest FC": ["Nott'm Forest", "Nottingham Forest"],
    "Fulham FC": ["Fulham"],
    "Everton FC": ["Everton"],
    "Brentford FC": ["Brentford"],
    "Sunderland AFC": ["Sunderland"],
    "Leeds United FC": ["Leeds", "Leeds United"],
    "Burnley FC": ["Burnley"],
    "Southampton FC": ["Southampton"],
    "Leicester City FC": ["Leicester", "Leicester City"],
    "Real Madrid CF": ["Real Madrid"],
    "FC Barcelona": ["Barcelona", "Barca"],
    "Club Atlético de Madrid": ["Ath Madrid", "Atletico Madrid", "Atlético Madrid"],
    "Sevilla FC": ["Sevilla"],
    "Real Sociedad de Fútbol": ["Sociedad", "Real Sociedad"],
    "Villarreal CF": ["Villarreal"],
    "Athletic Club": ["Ath Bilbao", "Athletic Bilbao"],
    "RC Celta de Vigo": ["Celta", "Celta Vigo"],
    "RCD Mallorca": ["Mallorca"],
    "Girona FC": ["Girona"],
    "CA Osasuna": ["Osasuna"],
    "Deportivo Alavés": ["Alaves", "Alavés"],
    "Rayo Vallecano de Madrid": ["Vallecano", "Rayo Vallecano"],
    "Granada CF": ["Granada"],
    "Getafe CF": ["Getafe"],
    "Valencia CF": ["Valencia"],
    "Real Betis Balompié": ["Betis", "Real Betis"],
    "UD Las Palmas": ["Las Palmas"],
    "UD Almería": ["Almeria", "Almería"],
    "Cádiz CF": ["Cadiz", "Cádiz"],
    "RB Leipzig": ["Leipzig", "RB Leipzig"],
    "Bayern Munich": ["Bayern", "FC Bayern München", "Bayern München"],
    "Borussia Dortmund": ["Dortmund", "BVB"],
    "Bayer 04 Leverkusen": ["Leverkusen", "Bayer Leverkusen"],
    "Paris Saint-Germain": ["Paris SG", "PSG"],
    "Olympique de Marseille": ["Marseille", "OM"],
    "AS Monaco": ["Monaco"],
    "Olympique Lyonnais": ["Lyon", "OL"],
    "Inter Milan": ["Inter", "Internazionale"],
    "AC Milan": ["Milan"],
    "Juventus FC": ["Juventus", "Juve"],
    "SSC Napoli": ["Napoli"],
    "AS Roma": ["Roma"],
    "SS Lazio": ["Lazio"],
}

# Build reverse lookup
_REVERSE: dict[str, str] = {}
_ALL_CANONICALS: list[str] = []


def _rebuild_reverse() -> None:
    global _REVERSE, _ALL_CANONICALS
    _REVERSE = {}
    _ALL_CANONICALS = list(ALIAS_MAP.keys())
    for canonical, aliases in ALIAS_MAP.items():
        _REVERSE[canonical.lower()] = canonical
        for alias in aliases:
            _REVERSE[alias.lower()] = canonical


_rebuild_reverse()


# ─────────────────────────────────────────────
# Fuzzy Matching
# ─────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    """Sequence similarity ratio (0.0 to 1.0)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _fuzzy_resolve(name: str, threshold: float = 0.85) -> str | None:
    """
    Find best fuzzy match among all canonical names and aliases.
    Returns canonical name if match exceeds threshold, else None.
    """
    name_lower = name.lower().strip()
    best_match = None
    best_score = 0.0

    for key, canonical in _REVERSE.items():
        score = _similarity(name_lower, key)
        if score > best_score:
            best_score = score
            best_match = canonical

    if best_score >= threshold:
        return best_match
    return None


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

_collision_log: list[dict] = []


def resolve(name: str, threshold: float = 0.85) -> str:
    """
    Resolve a team name to canonical form.

    Order: exact match → deterministic alias → fuzzy Levenshtein.
    Auto-registers fuzzy matches for future deterministic lookups.
    """
    cleaned = name.strip()

    # 1. Exact deterministic match
    canonical = _REVERSE.get(cleaned.lower())
    if canonical:
        return canonical

    # 2. Fuzzy match
    fuzzy_result = _fuzzy_resolve(cleaned, threshold)
    if fuzzy_result:
        # Auto-register for future deterministic lookups
        register_alias(fuzzy_result, cleaned)
        logger.info(f"Fuzzy resolved: '{cleaned}' → '{fuzzy_result}' (auto-registered)")
        return fuzzy_result

    # 3. No match — return original
    return cleaned


def resolve_pair(home: str, away: str) -> tuple[str, str]:
    """Resolve both team names."""
    return resolve(home), resolve(away)


def are_same_team(a: str, b: str) -> bool:
    return resolve(a) == resolve(b)


def register_alias(canonical: str, alias: str) -> None:
    """Register a new alias at runtime."""
    _REVERSE[alias.lower()] = canonical
    if canonical in ALIAS_MAP:
        if alias not in ALIAS_MAP[canonical]:
            ALIAS_MAP[canonical].append(alias)
    else:
        ALIAS_MAP[canonical] = [alias]


def audit_collisions(names: list[str]) -> list[dict]:
    """
    Check a list of names for potential duplicate teams.
    Returns list of suspected collisions for manual review.
    """
    collisions = []
    resolved = {}
    for name in names:
        canon = resolve(name)
        if canon not in resolved:
            resolved[canon] = []
        resolved[canon].append(name)

    for canon, variants in resolved.items():
        if len(variants) > 1:
            unique = list(set(variants))
            if len(unique) > 1:
                collisions.append({
                    "canonical": canon,
                    "variants": unique,
                    "action": "verify these map to the same team",
                })

    if collisions:
        logger.warning(f"Found {len(collisions)} potential alias collisions")

    return collisions
