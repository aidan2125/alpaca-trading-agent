"""
monitoring/logger.py  —  Centralized rotating log configuration.

Usage in any module:
    import logging
    logger = logging.getLogger(__name__)
    logger.info('Something happened')

Call setup_logging() once at startup in bot/main.py.
"""

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging(log_dir: str = 'logs', level: str = 'INFO') -> logging.Logger:
    """
    Configure multi-file rotating logging.

    Files created:
      logs/bot.log      — INFO+  (5MB rotating, 3 backups)
      logs/error.log    — ERROR+ (2MB rotating, 5 backups)
      logs/trades.log   — trade-specific events
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # bot.log — INFO and above
    bot_handler = logging.handlers.RotatingFileHandler(
        f'{log_dir}/bot.log', maxBytes=5 * 1024 * 1024, backupCount=3
    )
    bot_handler.setLevel(logging.INFO)
    bot_handler.setFormatter(formatter)

    # error.log — ERROR and above only
    error_handler = logging.handlers.RotatingFileHandler(
        f'{log_dir}/error.log', maxBytes=2 * 1024 * 1024, backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Console — visible in tmux window
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(bot_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(console_handler)

    return root_logger


def get_trade_logger(log_dir: str = 'logs') -> logging.Logger:
    """Separate logger for trade events — writes to logs/trades.log."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    trade_logger = logging.getLogger('trades')
    if not trade_logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            f'{log_dir}/trades.log', maxBytes=10 * 1024 * 1024, backupCount=10
        )
        handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
        ))
        trade_logger.addHandler(handler)
        trade_logger.setLevel(logging.INFO)
        trade_logger.propagate = False  # Don't duplicate to root logger
    return trade_logger