"""
Telegram alert notifications for value bet signals.
"""

import logging
import httpx

logger = logging.getLogger(__name__)


def send_signal_alert(
    bot_token: str,
    chat_id: str,
    match_id: str,
    home_team: str,
    away_team: str,
    match_date: str,
    league: str,
    selection: str,
    market_odds: float,
    fair_odds: float,
    edge: float,
    stake: float,
    bankroll: float,
) -> bool:
    """Send a formatted signal alert via Telegram."""
    if not bot_token or not chat_id:
        logger.warning("Telegram not configured — skipping alert")
        return False

    emoji = {"home": "🏠", "draw": "🤝", "away": "✈️"}.get(selection, "⚽")

    message = (
        f"{emoji} *VALUE SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*{home_team}* vs *{away_team}*\n"
        f"📅 {match_date} | {league}\n\n"
        f"Selection: *{selection.upper()}*\n"
        f"Market Odds: `{market_odds:.2f}`\n"
        f"Fair Odds: `{fair_odds:.2f}`\n"
        f"Edge: `{edge:.1%}`\n\n"
        f"💰 Stake: *${stake:.2f}*\n"
        f"📊 Bankroll: ${bankroll:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{match_id[:12]}`"
    )

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"Telegram alert sent for {match_id[:12]}")
        return True
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")
        return False


def send_circuit_breaker_alert(bot_token: str, chat_id: str, reason: str) -> bool:
    """Alert when the circuit breaker trips."""
    if not bot_token or not chat_id:
        return False

    message = (
        f"🚨 *CIRCUIT BREAKER ACTIVATED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{reason}\n\n"
        f"All signal generation is paused.\n"
        f"Manual review required."
    )

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Circuit breaker alert failed: {e}")
        return False
