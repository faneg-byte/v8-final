"""
Table A + B Builder — Per-team data population.

Table A: 10 most recent completed matches with odds context.
Table B: 60 matches in 6 intervals of 10, capturing multi-scale form.

PROPRIETARY: This module is original intellectual property.
"""

import logging
from shared.db import get_cursor

logger = logging.getLogger(__name__)


def refresh_table_a(team_id: int) -> int:
    """
    Refresh Table A: last 10 matches with odds and outcome per market.
    Returns rows upserted.
    """
    with get_cursor() as cur:
        cur.execute("""
            WITH recent AS (
                SELECT m.match_id, m.match_date,
                    CASE WHEN (m.home_id = %(t)s AND m.home_goals > m.away_goals) OR
                             (m.away_id = %(t)s AND m.away_goals > m.home_goals) THEN 'W'
                         WHEN m.home_goals = m.away_goals THEN 'D'
                         ELSE 'L' END AS actual_outcome,
                    ROW_NUMBER() OVER (ORDER BY m.match_date DESC) AS rn
                FROM matches m
                WHERE (m.home_id = %(t)s OR m.away_id = %(t)s)
                  AND m.status = 'completed'
                  AND m.home_goals IS NOT NULL
            )
            INSERT INTO team_odds_profile
                (team_id, match_id, match_date, market_type, actual_outcome, result_flag, row_rank)
            SELECT %(t)s, match_id, match_date, 'h2h', actual_outcome,
                   actual_outcome = 'W', rn
            FROM recent WHERE rn <= 10
            ON CONFLICT (team_id, match_id, market_type) DO UPDATE SET
                actual_outcome = EXCLUDED.actual_outcome,
                result_flag = EXCLUDED.result_flag,
                row_rank = EXCLUDED.row_rank
        """, {"t": team_id})
        return cur.rowcount


def refresh_table_b(team_id: int) -> int:
    """
    Refresh Table B: 60 matches in 6 intervals of 10.
    Interval 1 = most recent 10, Interval 6 = oldest 10 in the 60-match window.
    Returns rows upserted.
    """
    with get_cursor() as cur:
        cur.execute("""
            WITH ranked AS (
                SELECT m.match_id, m.match_date,
                    CASE WHEN m.home_id = %(t)s THEN m.away_id ELSE m.home_id END AS opp_id,
                    CASE WHEN m.home_id = %(t)s THEN 'home' ELSE 'away' END AS venue,
                    CASE WHEN m.home_id = %(t)s THEN m.home_goals ELSE m.away_goals END AS gf,
                    CASE WHEN m.home_id = %(t)s THEN m.away_goals ELSE m.home_goals END AS ga,
                    ROW_NUMBER() OVER (ORDER BY m.match_date DESC) AS rn
                FROM matches m
                WHERE (m.home_id = %(t)s OR m.away_id = %(t)s)
                  AND m.status = 'completed'
                  AND m.home_goals IS NOT NULL
            )
            INSERT INTO team_match_intervals
                (team_id, match_id, interval_id, match_date, opponent_id, venue,
                 goals_for, goals_against, result, row_rank)
            SELECT %(t)s, match_id,
                CEIL(rn / 10.0)::INT,
                match_date, opp_id, venue, gf, ga,
                CASE WHEN gf > ga THEN 'W' WHEN gf = ga THEN 'D' ELSE 'L' END,
                rn
            FROM ranked WHERE rn <= 60
            ON CONFLICT (team_id, match_id) DO UPDATE SET
                interval_id = EXCLUDED.interval_id,
                goals_for = EXCLUDED.goals_for,
                goals_against = EXCLUDED.goals_against,
                result = EXCLUDED.result,
                row_rank = EXCLUDED.row_rank
        """, {"t": team_id})
        return cur.rowcount


def refresh_all_tables(team_id: int) -> dict:
    """Refresh both Table A and B for a single team."""
    a = refresh_table_a(team_id)
    b = refresh_table_b(team_id)
    return {"table_a_rows": a, "table_b_rows": b}
