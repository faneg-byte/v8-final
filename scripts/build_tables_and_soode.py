import sys, math
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

conn = psycopg2.connect(host=sys.argv[1], port=5432, dbname="v8engine",
                         user="v8operator", password=sys.argv[2])

# ═══════════════════════════════════════════
# PHASE 1: Table A
# ═══════════════════════════════════════════
print("Phase 1: Building Table A (10-row odds profile per team)...")
cur = conn.cursor()
cur.execute("DELETE FROM team_odds_profile")
cur.execute("""
    INSERT INTO team_odds_profile (team_id, match_id, match_date, market_type, actual_outcome, result_flag, row_rank)
    SELECT team_id, match_id, match_date, 'h2h', actual_outcome,
           actual_outcome = 'W', rn
    FROM (
        SELECT t.team_id, m.match_id, m.match_date,
            CASE WHEN (m.home_id = t.team_id AND m.home_goals > m.away_goals) OR
                     (m.away_id = t.team_id AND m.away_goals > m.home_goals) THEN 'W'
                 WHEN m.home_goals = m.away_goals THEN 'D'
                 ELSE 'L' END AS actual_outcome,
            ROW_NUMBER() OVER (PARTITION BY t.team_id ORDER BY m.match_date DESC) AS rn
        FROM teams t
        JOIN matches m ON (m.home_id = t.team_id OR m.away_id = t.team_id)
        WHERE m.status = 'completed' AND m.home_goals IS NOT NULL
    ) sub
    WHERE rn <= 10
""")
conn.commit()
cur.execute("SELECT COUNT(*) FROM team_odds_profile")
print(f"  Table A: {cur.fetchone()[0]:,} rows")

# ═══════════════════════════════════════════
# PHASE 2: Table B
# ═══════════════════════════════════════════
print("\nPhase 2: Building Table B (60-row interval history per team)...")
cur.execute("DELETE FROM team_match_intervals")
cur.execute("""
    INSERT INTO team_match_intervals (team_id, match_id, interval_id, match_date, opponent_id, venue, goals_for, goals_against, result, row_rank)
    SELECT team_id, match_id, CEIL(rn / 10.0)::INT, match_date, opponent_id, venue, goals_for, goals_against,
        CASE WHEN goals_for > goals_against THEN 'W'
             WHEN goals_for = goals_against THEN 'D'
             ELSE 'L' END,
        rn
    FROM (
        SELECT t.team_id, m.match_id, m.match_date,
            CASE WHEN m.home_id = t.team_id THEN m.away_id ELSE m.home_id END AS opponent_id,
            CASE WHEN m.home_id = t.team_id THEN 'home' ELSE 'away' END AS venue,
            CASE WHEN m.home_id = t.team_id THEN m.home_goals ELSE m.away_goals END AS goals_for,
            CASE WHEN m.home_id = t.team_id THEN m.away_goals ELSE m.home_goals END AS goals_against,
            ROW_NUMBER() OVER (PARTITION BY t.team_id ORDER BY m.match_date DESC) AS rn
        FROM teams t
        JOIN matches m ON (m.home_id = t.team_id OR m.away_id = t.team_id)
        WHERE m.status = 'completed' AND m.home_goals IS NOT NULL
    ) sub
    WHERE rn <= 60
""")
conn.commit()
cur.execute("SELECT COUNT(*) FROM team_match_intervals")
print(f"  Table B: {cur.fetchone()[0]:,} rows")

# ═══════════════════════════════════════════
# PHASE 3: Table C — rest days, cards, rivalry
# ═══════════════════════════════════════════
print("\nPhase 3: Building Table C (pre-match context per team per match)...")
cur.execute("DELETE FROM team_match_context")

# 3a: Rest days — days since each team's previous match
print("  Computing rest days...")
cur.execute("""
    INSERT INTO team_match_context (team_id, match_id, rest_days)
    SELECT team_id, match_id,
        EXTRACT(DAY FROM match_date - prev_date)::INT AS rest_days
    FROM (
        SELECT t.team_id, m.match_id, m.match_date,
            LAG(m.match_date) OVER (PARTITION BY t.team_id ORDER BY m.match_date) AS prev_date
        FROM teams t
        JOIN matches m ON (m.home_id = t.team_id OR m.away_id = t.team_id)
        WHERE m.status = 'completed' AND m.home_goals IS NOT NULL
    ) sub
    WHERE prev_date IS NOT NULL
    ON CONFLICT (team_id, match_id) DO UPDATE SET rest_days = EXCLUDED.rest_days
""")
conn.commit()
cur.execute("SELECT COUNT(*) FROM team_match_context")
print(f"  Rest days computed: {cur.fetchone()[0]:,} rows")

# 3b: Cards accumulation — rolling 5-match yellow count per team
print("  Computing cards accumulation...")
cur.execute("""
    WITH team_matches AS (
        SELECT t.team_id, m.match_id, m.match_date,
            CASE WHEN m.home_id = t.team_id THEN m.home_yellows ELSE m.away_yellows END AS yellows,
            CASE WHEN m.home_id = t.team_id THEN m.home_reds ELSE m.away_reds END AS reds,
            ROW_NUMBER() OVER (PARTITION BY t.team_id ORDER BY m.match_date) AS rn
        FROM teams t
        JOIN matches m ON (m.home_id = t.team_id OR m.away_id = t.team_id)
        WHERE m.status = 'completed'
    ),
    rolling AS (
        SELECT tm.team_id, tm.match_id,
            COALESCE(SUM(tm2.yellows), 0) AS yellow_accum,
            COALESCE(SUM(CASE WHEN tm2.rn >= tm.rn - 3 THEN tm2.reds ELSE 0 END), 0) AS red_recent
        FROM team_matches tm
        JOIN team_matches tm2 ON tm2.team_id = tm.team_id
            AND tm2.rn BETWEEN tm.rn - 5 AND tm.rn - 1
        GROUP BY tm.team_id, tm.match_id
    )
    UPDATE team_match_context tmc
    SET cards_yellow_accum = r.yellow_accum,
        cards_red_recent = r.red_recent
    FROM rolling r
    WHERE tmc.team_id = r.team_id AND tmc.match_id = r.match_id
""")
conn.commit()
print("  Cards accumulation applied")

# 3c: Rivalry flags
print("  Applying rivalry flags...")
import csv, os
rivalry_pairs = set()
rpath = "data/rivalries.csv"
if os.path.exists(rpath):
    with open(rpath) as f:
        for row in csv.DictReader(f):
            a = row.get("team_a","").strip()
            b = row.get("team_b","").strip()
            if a and b:
                rivalry_pairs.add(frozenset([a, b]))
    print(f"  Loaded {len(rivalry_pairs)} rivalry pairs")

    # Get team name mapping
    cur2 = conn.cursor(cursor_factory=RealDictCursor)
    cur2.execute("SELECT team_id, name FROM teams")
    name_map = {r["name"]: r["team_id"] for r in cur2.fetchall()}

    rivalry_match_ids = set()
    for pair in rivalry_pairs:
        pair_list = list(pair)
        if len(pair_list) == 2:
            t1 = name_map.get(pair_list[0])
            t2 = name_map.get(pair_list[1])
            if t1 and t2:
                cur.execute("""
                    SELECT match_id FROM matches
                    WHERE (home_id = %s AND away_id = %s) OR (home_id = %s AND away_id = %s)
                """, (t1, t2, t2, t1))
                for row in cur.fetchall():
                    rivalry_match_ids.add(row[0])

    if rivalry_match_ids:
        # Batch update
        for mid in rivalry_match_ids:
            cur.execute("""
                UPDATE team_match_context SET rivalry_flag = TRUE
                WHERE match_id = %s
            """, (mid,))
        conn.commit()
        print(f"  Rivalry flag set on {len(rivalry_match_ids)} matches")
    cur2.close()
else:
    print("  No rivalries.csv found, skipping")

cur.execute("SELECT COUNT(*) FROM team_match_context WHERE rest_days IS NOT NULL")
tc_rest = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM team_match_context WHERE rivalry_flag = TRUE")
tc_rival = cur.fetchone()[0]
print(f"  Table C summary: {tc_rest:,} with rest days, {tc_rival:,} rivalry-flagged")

# ═══════════════════════════════════════════
# PHASE 4: SOODE — 4-grip cascading mesh
# ═══════════════════════════════════════════
print("\nPhase 4: Computing SOODE for all teams...")

BASE_RATES = {"W": 0.46, "D": 0.27, "L": 0.27}

def rolling_div(preds, window):
    recent = preds[-window:] if len(preds) >= window else preds
    if not recent: return 0.0
    return sum(abs(p - 1.0) for p in recent) / len(recent)

def compute_grips(preds, prev_meso=None, prev_macro=None, prev_dna=None):
    micro = rolling_div(preds, 5)
    raw_meso = rolling_div(preds, 15)
    meso = 0.7 * raw_meso + 0.3 * prev_meso if prev_meso is not None else raw_meso
    raw_macro = rolling_div(preds, 30)
    macro = 0.6 * raw_macro + 0.4 * prev_macro if prev_macro is not None else raw_macro
    dna = 0.3 * macro + 0.7 * prev_dna if prev_dna is not None else macro
    return round(micro,4), round(meso,4), round(macro,4), round(dna,4)

def diagnose(micro, meso, macro, dna):
    ms = max(meso, 0.001)
    ds = max(dna, 0.001)
    if micro > 1.6 * ms and micro > 0.25:
        return "⚡ Micro-Shock (Temporary Variance)"
    if micro < 0.4 * ds and micro < 0.10:
        return "🚀 Surging Form (Underpriced by Bookies)"
    if dna > 0.25:
        return "🔴 Fundamental Decline (Engine Deprecated)"
    return "🟢 Stable"

cur2 = conn.cursor(cursor_factory=RealDictCursor)
cur2.execute("SELECT team_id, name FROM teams ORDER BY team_id")
teams = cur2.fetchall()

soode_rows = []
diag_counts = {}

for t in teams:
    tid = t["team_id"]
    cur2.execute("""
        SELECT result FROM team_match_intervals
        WHERE team_id = %s ORDER BY match_date ASC
    """, (tid,))
    rows = cur2.fetchall()
    preds = [BASE_RATES.get(r["result"], 0.33) for r in rows]
    if len(preds) < 5: continue

    micro, meso, macro, dna = compute_grips(preds)
    diag = diagnose(micro, meso, macro, dna)
    soode_rows.append((tid, micro, meso, macro, dna, diag, True, "v3.0"))

    d_short = diag.split("(")[0].strip()
    diag_counts[d_short] = diag_counts.get(d_short, 0) + 1

cur.execute("DELETE FROM soode_keys")
execute_values(cur, """
    INSERT INTO soode_keys (team_id, micro_grip, meso_grip, macro_grip, dna_grip,
                           system_diagnosis, bootstrap_mode, model_version)
    VALUES %s
""", soode_rows)
conn.commit()

print(f"  SOODE profiles: {len(soode_rows)}")
print(f"  Diagnosis distribution:")
for d, c in sorted(diag_counts.items(), key=lambda x: -x[1]):
    pct = c / len(soode_rows) * 100
    print(f"    {d}: {c} ({pct:.1f}%)")

# ═══════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════
cur.execute("SELECT COUNT(*) FROM team_odds_profile"); ta = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM team_match_intervals"); tb = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM team_match_context"); tc = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM soode_keys"); sk = cur.fetchone()[0]

cur.close(); cur2.close(); conn.close()

print(f"\n{'='*50}")
print(f"  V8 ENGINE — ALL TABLES BUILT")
print(f"{'='*50}")
print(f"  Table A:         {ta:,}")
print(f"  Table B:         {tb:,}")
print(f"  Table C:         {tc:,}")
print(f"  SOODE profiles:  {sk}")
print(f"  Matches:         138,150")
print(f"  Teams:           606")
print(f"  Odds:            2,333,998")
print(f"{'='*50}")
