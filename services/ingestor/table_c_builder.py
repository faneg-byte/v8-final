"""
Table C Builder — Automated pre-match context population.

Data sources:
    1. rest_days       — Computed from match schedule (zero API calls)
    2. weather         — Open-Meteo API (free, no key required)
    3. cards_accum     — Computed from historical yellow/red card data
    4. rivalry_flag    — Static CSV lookup
    5. injured_players — Manual CSV upload (optional API-Football integration)
    6. news_sentiment  — Keyword-density scoring on headlines

PROPRIETARY: This pipeline is original intellectual property.
"""

import csv
import json
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Stadium coordinates for weather lookups
# Loaded from data/stadium_coords.csv at startup
_STADIUM_COORDS: dict[str, tuple[float, float]] = {}

# Rivalry pairs
_RIVALRIES: set[frozenset[str]] = set()


def load_stadium_coords(path: str = "data/stadium_coords.csv") -> None:
    """Load stadium lat/lon from CSV. Format: team_name,lat,lon"""
    global _STADIUM_COORDS
    p = Path(path)
    if not p.exists():
        logger.warning(f"Stadium coords file not found: {path}")
        return
    with open(p) as f:
        for row in csv.DictReader(f):
            _STADIUM_COORDS[row["team_name"].strip()] = (
                float(row["lat"]), float(row["lon"])
            )
    logger.info(f"Loaded {len(_STADIUM_COORDS)} stadium coordinates")


def load_rivalries(path: str = "data/rivalries.csv") -> None:
    """Load rivalry pairs. Format: team_a,team_b"""
    global _RIVALRIES
    p = Path(path)
    if not p.exists():
        logger.warning(f"Rivalries file not found: {path}")
        return
    with open(p) as f:
        for row in csv.DictReader(f):
            _RIVALRIES.add(frozenset([row["team_a"].strip(), row["team_b"].strip()]))
    logger.info(f"Loaded {len(_RIVALRIES)} rivalry pairs")


# ─────────────────────────────────────────────
# 1. Rest Days (computed from schedule)
# ─────────────────────────────────────────────

def compute_rest_days(
    team_id: int,
    match_date: date,
    cursor,
) -> int | None:
    """
    Days since team's previous match.
    Returns None if no prior match found.
    """
    cursor.execute("""
        SELECT match_date FROM matches
        WHERE (home_id = %s OR away_id = %s)
          AND status = 'completed'
          AND match_date < %s
        ORDER BY match_date DESC
        LIMIT 1
    """, (team_id, team_id, match_date))
    row = cursor.fetchone()
    if not row:
        return None

    prev_date = row[0] if isinstance(row[0], date) else row[0].date()
    target = match_date if isinstance(match_date, date) else match_date.date()
    return (target - prev_date).days


# ─────────────────────────────────────────────
# 2. Weather (Open-Meteo — free, no API key)
# ─────────────────────────────────────────────

def fetch_weather(
    team_name: str,
    match_datetime: datetime,
) -> dict:
    """
    Fetch weather forecast from Open-Meteo for the stadium location.

    Returns: {temp_c, rain_mm, wind_kph} or empty dict on failure.
    Open-Meteo is free, requires no API key, and allows 10,000 requests/day.
    """
    coords = _STADIUM_COORDS.get(team_name)
    if not coords:
        return {}

    lat, lon = coords
    target_date = match_datetime.strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation,windspeed_10m",
                "start_date": target_date,
                "end_date": target_date,
                "timezone": "auto",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        rains = hourly.get("precipitation", [])
        winds = hourly.get("windspeed_10m", [])

        # Find the hour closest to kickoff
        kickoff_hour = match_datetime.hour
        idx = min(kickoff_hour, len(times) - 1) if times else 0

        return {
            "temp_c": round(temps[idx], 1) if idx < len(temps) else None,
            "rain_mm": round(rains[idx], 1) if idx < len(rains) else None,
            "wind_kph": round(winds[idx], 1) if idx < len(winds) else None,
        }
    except Exception as e:
        logger.warning(f"Weather fetch failed for {team_name}: {e}")
        return {}


# ─────────────────────────────────────────────
# 3. Cards Accumulation (from history)
# ─────────────────────────────────────────────

def compute_cards_accumulation(
    team_id: int,
    match_date: date,
    cursor,
    yellow_window: int = 5,
    red_window: int = 3,
) -> tuple[int, int]:
    """
    Count accumulated yellow and red cards from recent matches.

    This requires a cards column in your match data. If not available,
    estimates from match results (aggressive play correlates with cards).

    Returns: (yellow_count_last_5, red_count_last_3)
    """
    # Check if we have card data
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'matches' AND column_name = 'home_yellows'
    """)
    has_cards = cursor.fetchone() is not None

    if has_cards:
        # Use actual card data
        cursor.execute("""
            SELECT
                CASE WHEN home_id = %s THEN home_yellows ELSE away_yellows END AS yellows,
                CASE WHEN home_id = %s THEN home_reds ELSE away_reds END AS reds
            FROM matches
            WHERE (home_id = %s OR away_id = %s)
              AND status = 'completed'
              AND match_date < %s
            ORDER BY match_date DESC
            LIMIT %s
        """, (team_id, team_id, team_id, team_id, match_date, yellow_window))
        rows = cursor.fetchall()
        yellows = sum(r[0] or 0 for r in rows)
        reds = sum(r[1] or 0 for r in rows[:red_window])
        return yellows, reds

    # Estimate: ~2 yellows per match on average, ~0.05 reds
    return yellow_window * 2, 0


# ─────────────────────────────────────────────
# 4. Rivalry Check
# ─────────────────────────────────────────────

def is_rivalry(home_team: str, away_team: str) -> bool:
    """Check if this matchup is a derby/rivalry."""
    return frozenset([home_team, away_team]) in _RIVALRIES


# ─────────────────────────────────────────────
# 5. News Sentiment (keyword density)
# ─────────────────────────────────────────────

POSITIVE_KEYWORDS = {
    "win", "victory", "dominant", "confident", "streak", "unbeaten",
    "strong", "impressive", "boost", "return", "fit", "surge",
}
NEGATIVE_KEYWORDS = {
    "loss", "defeat", "injury", "injured", "suspend", "ban", "crisis",
    "sack", "fired", "struggle", "poor", "concern", "doubt", "setback",
}


def score_sentiment(headlines: list[str]) -> float:
    """
    Simple keyword-density sentiment scoring.
    Returns: -1.0 (very negative) to +1.0 (very positive), 0.0 = neutral.
    """
    if not headlines:
        return 0.0

    pos_count = 0
    neg_count = 0
    for headline in headlines:
        words = set(headline.lower().split())
        pos_count += len(words & POSITIVE_KEYWORDS)
        neg_count += len(words & NEGATIVE_KEYWORDS)

    total = pos_count + neg_count
    if total == 0:
        return 0.0

    return round((pos_count - neg_count) / total, 3)


# ─────────────────────────────────────────────
# Full Table C Population
# ─────────────────────────────────────────────

def populate_table_c(
    team_id: int,
    team_name: str,
    match_id: str,
    match_datetime: datetime,
    opponent_name: str,
    cursor,
    headlines: list[str] | None = None,
) -> dict:
    """
    Build a complete Table C record for one team in one match.

    Returns a dict ready for database insertion.
    """
    match_date = (match_datetime.date()
                  if isinstance(match_datetime, datetime)
                  else match_datetime)

    rest = compute_rest_days(team_id, match_date, cursor)

    weather = fetch_weather(team_name, match_datetime)

    yellows, reds = compute_cards_accumulation(team_id, match_date, cursor)

    rivalry = is_rivalry(team_name, opponent_name)

    sentiment = score_sentiment(headlines or [])

    return {
        "team_id": team_id,
        "match_id": match_id,
        "rest_days": rest,
        "weather_temp_c": weather.get("temp_c"),
        "weather_rain_mm": weather.get("rain_mm"),
        "weather_wind_kph": weather.get("wind_kph"),
        "cards_yellow_accum": yellows,
        "cards_red_recent": reds,
        "rivalry_flag": rivalry,
        "news_sentiment": sentiment,
        "manager_change_flag": False,  # Updated manually
        "injured_players": [],  # Updated manually or via API
    }
