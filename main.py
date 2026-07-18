"""OTORAPOR — Toniva performans raporu Telegram botu (Railway giriş noktası)."""

from __future__ import annotations

import logging
import sys

from bot_app import build_application
from config import SCHEDULE_TIMES, Settings


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )
    # Gürültülü kütüphaneleri sakinleştir
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)


def main() -> None:
    setup_logging()
    logger = logging.getLogger("main")

    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    schedule_str = ", ".join(f"{h:02d}:{m:02d}" for h, m in SCHEDULE_TIMES)
    logger.info("OTORAPOR başlıyor")
    logger.info("Toniva base: %s", settings.toniva_base_url)
    logger.info("Hedef grup chat_id: %s", settings.telegram_chat_id)
    logger.info("Zaman dilimi: %s | Saatler: %s", settings.timezone_name, schedule_str)

    app = build_application(settings)
    # Long polling — Railway worker servisi için uygun
    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
