"""Telegram bot — sadece grupta çalışır, zamanlı + /rapor performans Excel'i gönderir."""

from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from telegram import InputFile, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import SCHEDULE_TIMES, Settings
from excel_builder import build_performance_workbook, report_filename
from toniva_client import TonivaClient, TonivaError

logger = logging.getLogger(__name__)


def is_allowed_group(chat_id: int | None, settings: Settings) -> bool:
    return chat_id is not None and chat_id == settings.telegram_chat_id


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    return chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def ignore_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Özel mesajlara tamamen sessiz kal."""
    return


async def send_performance_report(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    when: datetime | None = None,
    reply_to_message_id: int | None = None,
) -> None:
    client: TonivaClient = context.application.bot_data["toniva"]
    tz: ZoneInfo = context.application.bot_data["tz"]

    now = when or datetime.now(tz)
    today = now.date()

    try:
        rows = await client.fetch_performance(start_date=today, end_date=today)
    except TonivaError as exc:
        logger.exception("Toniva rapor hatası: %s", exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Performans raporu alınamadı.\n{exc}",
            reply_to_message_id=reply_to_message_id,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Beklenmeyen hata: %s", exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Rapor üretilirken beklenmeyen bir hata oluştu.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    workbook = build_performance_workbook(rows)
    filename = report_filename(now)
    caption = (
        f"📊 Performans Raporu\n"
        f"📅 {now.strftime('%d.%m.%Y')}\n"
        f"🕒 {now.strftime('%H:%M')}\n"
        f"👥 {len(rows)} dahili"
    )

    await context.bot.send_document(
        chat_id=chat_id,
        document=InputFile(workbook, filename=filename),
        caption=caption,
        reply_to_message_id=reply_to_message_id,
    )
    logger.info("Rapor gönderildi: chat_id=%s file=%s rows=%s", chat_id, filename, len(rows))


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    tz: ZoneInfo = context.application.bot_data["tz"]
    logger.info("Zamanlanmış rapor tetiklendi")
    await send_performance_report(
        context,
        chat_id=settings.telegram_chat_id,
        when=datetime.now(tz),
    )


async def rapor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /rapor — yalnızca tanımlı grupta çalışır.
    Özel sohbet ve diğer gruplar: sessizce yok sayılır.
    """
    settings: Settings = context.application.bot_data["settings"]
    tz: ZoneInfo = context.application.bot_data["tz"]

    # Özel sohbet: hiç yanıt yok
    if not is_group_chat(update):
        return

    chat = update.effective_chat
    if chat is None or not is_allowed_group(chat.id, settings):
        # Yanlış grup: sessiz
        logger.info("İzin verilmeyen sohbetten /rapor: %s", getattr(chat, "id", None))
        return

    message = update.effective_message

    # Kısa geri bildirim
    if message:
        await message.reply_text("⏳ Güncel performans raporu hazırlanıyor...")

    await send_performance_report(
        context,
        chat_id=chat.id,
        when=datetime.now(tz),
        reply_to_message_id=None,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Handler hatası: %s", context.error)


def build_application(settings: Settings) -> Application:
    tz = ZoneInfo(settings.timezone_name)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["toniva"] = TonivaClient(settings.toniva_base_url, settings.toniva_api_key)
    app.bot_data["tz"] = tz

    # /rapor — grup komutu
    app.add_handler(CommandHandler("rapor", rapor_command))

    # Özel mesajları yut (yanıt yok)
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE, ignore_private),
        group=1,
    )

    app.add_error_handler(on_error)

    job_queue = app.job_queue
    if job_queue is None:
        raise RuntimeError(
            "JobQueue yok. 'python-telegram-bot[job-queue]' paketinin kurulu olduğundan emin olun."
        )

    for hour, minute in SCHEDULE_TIMES:
        job_queue.run_daily(
            scheduled_report,
            time=time(hour=hour, minute=minute, tzinfo=tz),
            name=f"performance_{hour:02d}{minute:02d}",
        )
        logger.info("Zamanlanmış iş eklendi: %02d:%02d (%s)", hour, minute, settings.timezone_name)

    return app
