"""Telegram bot — sadece grupta çalışır, zamanlı + /rapor performans Excel'i gönderir."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
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
from date_utils import DateParseError, join_command_args, parse_report_date
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
    report_date: date | None = None,
    full_day: bool = False,
    when: datetime | None = None,
    reply_to_message_id: int | None = None,
) -> None:
    """
    Performans Excel'i üretir ve gruba gönderir.

    - report_date=None → bugün (Toniva: o güne ait, o ana kadar birikimli veri)
    - report_date=geçmiş gün + full_day=True → o tarihin tüm günü
    """
    client: TonivaClient = context.application.bot_data["toniva"]
    tz: ZoneInfo = context.application.bot_data["tz"]

    now = when or datetime.now(tz)
    target = report_date or now.date()

    try:
        rows = await client.fetch_performance(start_date=target, end_date=target)
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
    filename = report_filename(now, report_date=target, full_day=full_day)

    date_label = target.strftime("%d.%m.%Y")
    if full_day and report_date is not None:
        scope_line = f"📅 {date_label} (tüm gün)"
        time_line = "🕒 00:00 – 23:59"
    else:
        scope_line = f"📅 {date_label}"
        time_line = f"🕒 {now.strftime('%H:%M')} itibarıyla"

    caption = (
        f"📊 Performans Raporu\n"
        f"{scope_line}\n"
        f"{time_line}\n"
        f"👥 {len(rows)} dahili"
    )

    await context.bot.send_document(
        chat_id=chat_id,
        document=InputFile(workbook, filename=filename),
        caption=caption,
        reply_to_message_id=reply_to_message_id,
    )
    logger.info(
        "Rapor gönderildi: chat_id=%s date=%s full_day=%s file=%s rows=%s",
        chat_id,
        target.isoformat(),
        full_day,
        filename,
        len(rows),
    )


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    tz: ZoneInfo = context.application.bot_data["tz"]
    logger.info("Zamanlanmış rapor tetiklendi")
    await send_performance_report(
        context,
        chat_id=settings.telegram_chat_id,
        report_date=None,
        full_day=False,
        when=datetime.now(tz),
    )


async def rapor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /rapor              → bugünün, şu ana kadarki verisi
    /rapor 18.07.2026   → o tarihin tüm günü

    Yalnızca tanımlı grupta çalışır; özel sohbet sessiz.
    """
    settings: Settings = context.application.bot_data["settings"]
    tz: ZoneInfo = context.application.bot_data["tz"]

    if not is_group_chat(update):
        return

    chat = update.effective_chat
    if chat is None or not is_allowed_group(chat.id, settings):
        logger.info("İzin verilmeyen sohbetten /rapor: %s", getattr(chat, "id", None))
        return

    message = update.effective_message
    arg_text = join_command_args(context.args)

    report_date: date | None = None
    full_day = False

    if arg_text:
        try:
            report_date = parse_report_date(arg_text)
        except DateParseError as exc:
            if message:
                await message.reply_text(f"❌ {exc}")
            return
        full_day = True
        wait_text = (
            f"⏳ {report_date.strftime('%d.%m.%Y')} tarihli "
            "tüm gün performans raporu hazırlanıyor..."
        )
    else:
        wait_text = "⏳ Güncel performans raporu hazırlanıyor..."

    if message:
        await message.reply_text(wait_text)

    await send_performance_report(
        context,
        chat_id=chat.id,
        report_date=report_date,
        full_day=full_day,
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

    app.add_handler(CommandHandler("rapor", rapor_command))

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
