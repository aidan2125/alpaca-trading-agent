import os
import logging

try:
    import requests
except Exception:
    requests = None

LOG = logging.getLogger(__name__)


def send_telegram_message(message: str) -> bool:
    """Send a Telegram message using bot token + chat id from env.

    Env vars:
      TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    Returns True if sent (or printed), False only on error.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id or requests is None:
        # Fallback to printing so behavior remains visible when not configured
        LOG.info("Telegram not configured or requests missing; printing message")
        print("[Telegram]", message)
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            LOG.error("Telegram API error: %s", data)
            return False
        return True
    except Exception as e:
        LOG.exception("Failed to send Telegram message: %s", e)
        return False