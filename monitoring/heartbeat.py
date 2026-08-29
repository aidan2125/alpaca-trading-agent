"""
monitoring/heartbeat.py
Logs a heartbeat every 5 minutes. Sends Telegram summary every 30 minutes.
Run alongside the bot in a tmux window or as a background thread.
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN    = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID  = os.getenv('TELEGRAM_CHAT_ID', '')
INTERVAL = 300   # 5 minutes between heartbeat log entries
TG_EVERY = 6     # Send Telegram every N heartbeats (= 30 minutes)

Path('logs').mkdir(exist_ok=True)
logger = logging.getLogger(__name__)


def send_telegram(text: str) -> None:
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TOKEN}/sendMessage',
            data={'chat_id': CHAT_ID, 'text': text},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f'Heartbeat Telegram send failed: {e}')


def log_heartbeat(count: int) -> None:
    now = datetime.now().isoformat(timespec='seconds')
    try:
        with open('logs/heartbeat.log', 'a') as f:
            f.write(f'{now} | HEARTBEAT | count={count}\n')
    except OSError as e:
        logger.error(f'Heartbeat log write failed: {e}')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    count = 0
    start = time.time()

    while True:
        count += 1
        log_heartbeat(count)

        if count % TG_EVERY == 0:
            running_min = int((time.time() - start) / 60)
            send_telegram(f'💓 Bot heartbeat — running {running_min} mins | count={count}')

        time.sleep(INTERVAL)