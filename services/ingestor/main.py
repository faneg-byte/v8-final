"""
Ingestor Service — CSV ingestion with idempotent upserts.

Reads match data from CSV files (local or GCS), validates schema,
computes deterministic match IDs, and upserts into Postgres.
"""

import csv
import hashlib
import io
import logging
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.db import execute_batch, get_cursor, audit, close_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ─────────────────────────────────────────────
# Schema & Validation
# ─────────────────────────────────────────────

REQUIRED_COLUMNS = {"date", "home_team", "away_team", "home_goals", "away_goals"}

COLUMN_ALIASES = {
    "Date": "date",
    "HomeTeam": "home_team",
    "Home": "home_team",
    "AwayTeam": "away_team",
    "Away": "away_team",
    "FTHG": "home_goals",
    "HomeGoals": "home_goals",
    "HG": "home_goals",
    "FTAG": "away_goals",
    "AwayGoals": "away_goals",
    "AG": "away_goals",
    "Div": "league",
    "League": "league",
    "Season": "season",
}


def normalize_columns(header: list[str]) -> dict[str, str]:
    """Map raw CSV headers to canonical column names."""
    mapping = {}
    for col in header:
        stripped = col.strip()
        if stripped.lower() in {c.lower() for c in REQUIRED_COLUMNS}:
            mapping[stripped] = stripped.lower().replace(" ", "_")
        elif stripped in COLUMN_ALIASES:
            mapping[stripped] = COLUMN_ALIASES[stripped]
    return mapping


def generate_match_id(home_team: str, away_team: str, date_str: str) -> str:
    """Deterministic MD5-based match ID. Guarantees idempotent upserts."""
    raw = f"{home_team.strip().lower()}|{away_team.strip().lower()}|{date_str.strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def parse_date(raw: str) -> str | None:
    """Try common date formats. Returns ISO date string or None."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────
# Ingestion Pipeline
# ─────────────────────────────────────────────


def ensure_team(team_name: str, league: str | None = None) -> int:
    """Get or create a team. Returns team_id."""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT team_id FROM teams WHERE name = %s", (team_name,))
        row = cur.fetchone()
        if row:
            return row["team_id"]

        cur.execute(
            "INSERT INTO teams (name, league) VALUES (%s, %s) RETURNING team_id",
            (team_name, league),
        )
        return cur.fetchone()["team_id"]


def ingest_csv(file_content: str, source_name: str, league_override: str | None = None) -> dict:
    """
    Parse CSV content and upsert matches.

    Returns stats dict with counts of inserted, skipped, and error rows.
    """
    reader = csv.DictReader(io.StringIO(file_content))
    if not reader.fieldnames:
        return {"error": "Empty CSV or missing header"}

    col_map = normalize_columns(reader.fieldnames)
    missing = REQUIRED_COLUMNS - set(col_map.values())
    if missing:
        return {"error": f"Missing required columns: {missing}"}

    # Build reverse lookup: canonical -> raw header name
    reverse_map = {v: k for k, v in col_map.items()}

    # Cache team IDs to avoid repeated lookups
    team_cache: dict[str, int] = {}
    rows_to_upsert = []
    errors = []

    for i, raw_row in enumerate(reader, start=2):  # Line 2 = first data row
        try:
            row = {col_map[k]: v for k, v in raw_row.items() if k in col_map}

            home = row["home_team"].strip()
            away = row["away_team"].strip()
            date_str = parse_date(row["date"])
            if not date_str:
                errors.append({"line": i, "reason": f"Unparseable date: {row['date']}"})
                continue

            home_goals = int(row["home_goals"])
            away_goals = int(row["away_goals"])
            league = league_override or row.get("league", "Unknown")
            season = row.get("season", "")

            # Ensure teams exist
            if home not in team_cache:
                team_cache[home] = ensure_team(home, league)
            if away not in team_cache:
                team_cache[away] = ensure_team(away, league)

            match_id = generate_match_id(home, away, date_str)

            rows_to_upsert.append((
                match_id,
                team_cache[home],
                team_cache[away],
                date_str,
                league,
                season,
                home_goals,
                away_goals,
                "completed",
                source_name,
            ))
        except (ValueError, KeyError) as e:
            errors.append({"line": i, "reason": str(e)})

    # Batch upsert
    upserted = 0
    if rows_to_upsert:
        upserted = execute_batch(
            """
            INSERT INTO matches (match_id, home_id, away_id, match_date, league, season,
                                 home_goals, away_goals, status, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id) DO UPDATE SET
                home_goals = EXCLUDED.home_goals,
                away_goals = EXCLUDED.away_goals,
                status     = EXCLUDED.status,
                source     = EXCLUDED.source
            """,
            rows_to_upsert,
        )

    stats = {
        "source": source_name,
        "total_rows": len(rows_to_upsert) + len(errors),
        "upserted": upserted,
        "errors": len(errors),
        "error_details": errors[:20],  # Cap error details to prevent log bloat
    }

    audit("ingestor", "csv_ingested", stats)
    logger.info(f"Ingested {upserted} matches from {source_name} ({len(errors)} errors)")
    return stats


# ─────────────────────────────────────────────
# HTTP Endpoints
# ─────────────────────────────────────────────


@app.route("/run", methods=["POST"])
def run_ingestion():
    """
    Triggered by Cloud Scheduler or manual call.
    Accepts JSON body with:
      - file_path: local path to CSV file
      - csv_content: raw CSV string (alternative to file_path)
      - league: optional league name override
      - source: source identifier
    """
    data = request.get_json(silent=True) or {}

    if "csv_content" in data:
        content = data["csv_content"]
        source = data.get("source", "api_upload")
    elif "file_path" in data:
        path = Path(data["file_path"])
        if not path.exists():
            return jsonify({"error": f"File not found: {path}"}), 404
        content = path.read_text(encoding="utf-8-sig")
        source = data.get("source", path.name)
    else:
        return jsonify({"error": "Provide 'csv_content' or 'file_path'"}), 400

    result = ingest_csv(content, source, data.get("league"))
    status = 200 if "error" not in result else 400
    return jsonify(result), status


@app.route("/health", methods=["GET"])
def health():
    """Health check for Cloud Run."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
