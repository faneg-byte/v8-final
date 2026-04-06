"""
Health monitoring — Telegram digests after every job + anomaly detection.

Sends a structured digest after each pipeline run. Detects anomalies
by comparing SOODE distribution against the previous run.

PROPRIETARY: This module is original intellectual property.
"""

import json
import logging

import httpx

logger = logging.getLogger(__name__)


def send_digest(
    bot_token: str,
    chat_id: str,
    service: str,
    stats: dict,
    anomalies: list[str] | None = None,
) -> bool:
    """
    Send a structured health digest via Telegram.

    Args:
        service: Name of the job (ingestor, modeler, scanner).
        stats: Key metrics dict to display.
        anomalies: List of anomaly descriptions. If non-empty, digest is flagged.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram not configured — digest skipped")
        return False

    has_anomalies = bool(anomalies)
    icon = "🔴" if has_anomalies else "✅"

    lines = [
        f"{icon} *V8 {service.upper()} digest*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for key, val in stats.items():
        display_key = key.replace("_", " ").capitalize()
        lines.append(f"{display_key}: `{val}`")

    if has_anomalies:
        lines.append("")
        lines.append("⚠ *Anomalies detected:*")
        for a in anomalies:
            lines.append(f"  → {a}")

    message = "\n".join(lines)

    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Digest send failed: {e}")
        return False


def detect_soode_anomalies(
    current_distribution: dict[str, int],
    previous_distribution: dict[str, int] | None,
    shift_threshold: float = 0.15,
) -> list[str]:
    """
    Compare SOODE diagnosis distribution against the previous run.

    If any diagnosis state shifts by more than shift_threshold (15%),
    an anomaly is flagged.

    Args:
        current_distribution: {"🟢 Stable": 150, "🔴 Fundamental Decline": 40, ...}
        previous_distribution: Same format from prior run. None = first run.
        shift_threshold: Fractional shift that triggers an anomaly.

    Returns:
        List of anomaly descriptions. Empty list = healthy.
    """
    if not previous_distribution:
        return []

    anomalies = []
    current_total = sum(current_distribution.values()) or 1
    prev_total = sum(previous_distribution.values()) or 1

    all_keys = set(current_distribution.keys()) | set(previous_distribution.keys())

    for key in all_keys:
        curr_pct = current_distribution.get(key, 0) / current_total
        prev_pct = previous_distribution.get(key, 0) / prev_total
        shift = abs(curr_pct - prev_pct)

        if shift > shift_threshold:
            direction = "↑" if curr_pct > prev_pct else "↓"
            anomalies.append(
                f"{key}: {prev_pct:.0%} → {curr_pct:.0%} "
                f"({direction}{shift:.0%} shift)"
            )

    return anomalies


def load_previous_distribution(cursor) -> dict[str, int] | None:
    """Load SOODE distribution from the most recent audit trail entry."""
    cursor.execute("""
        SELECT detail->'diagnosis_distribution' AS dist
        FROM audit_trail
        WHERE service = 'modeler' AND action = 'pipeline_complete'
        ORDER BY created_at DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row and row[0]:
        return dict(row[0]) if isinstance(row[0], dict) else json.loads(row[0])
    return None
