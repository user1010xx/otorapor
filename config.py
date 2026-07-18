"""Uygulama yapılandırması — değerler ortam değişkenlerinden okunur."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TIMEZONE = ZoneInfo("Europe/Istanbul")

# Günlük otomatik rapor saatleri (Europe/Istanbul)
SCHEDULE_TIMES: list[tuple[int, int]] = [
    (12, 27),
    (13, 51),
    (16, 27),
    (17, 27),
    (18, 50),
]


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Zorunlu ortam değişkeni eksik: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: int
    toniva_api_key: str
    toniva_base_url: str
    timezone_name: str = "Europe/Istanbul"

    @classmethod
    def from_env(cls) -> "Settings":
        chat_raw = _require("TELEGRAM_CHAT_ID")
        try:
            chat_id = int(chat_raw)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_CHAT_ID tam sayı olmalı (grup chat id)") from exc

        base = os.getenv("TONIVA_BASE_URL", "https://crm.toniva.net/api/public/v1").strip()
        base = base.rstrip("/")

        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=chat_id,
            toniva_api_key=_require("TONIVA_API_KEY"),
            toniva_base_url=base,
            timezone_name=os.getenv("TZ", "Europe/Istanbul") or "Europe/Istanbul",
        )


# Excel sütun başlıkları (UI / görsel format)
EXCEL_HEADERS = [
    "Dahili Adı",
    "Dahili",
    "Dış Arama Sayısı",
    "Dış Arama Süresi",
    "Dış Arama Çaldırma Süresi",
]

# Toniva PBX performans raporu gerçek alan adları
# (crm.toniva.net frontend: ExtensionName, OutboundCallDuration, ...)
# Süre alanları ONDALIK SAAT cinsinden gelir; UI: cl(x) = HH:MM:SS(x * 3600)
FIELD_ALIASES: dict[str, list[str]] = {
    "Dahili Adı": [
        "ExtensionName",
        "extensionName",
        "extension_name",
        "Dahili Adı",
        "Dahili Adi",
        "dahiliAdi",
        "dahili_adi",
        "agentName",
        "agent_name",
        "displayName",
        "name",
    ],
    "Dahili": [
        "ExtensionNumber",
        "extensionNumber",
        "extension_number",
        "Dahili",
        "dahili",
        "extension",
        "ext",
        "exten",
    ],
    "Dış Arama Sayısı": [
        "OutboundCallCount",
        "outboundCallCount",
        "outbound_call_count",
        "Dış Arama Sayısı",
        "outboundCalls",
        "outbound_calls",
        "outboundCount",
    ],
    "Dış Arama Süresi": [
        "OutboundCallDuration",
        "outboundCallDuration",
        "outbound_call_duration",
        "Dış Arama Süresi",
        "outboundDuration",
        "outbound_duration",
    ],
    "Dış Arama Çaldırma Süresi": [
        "OutboundRingDuration",
        "outboundRingDuration",
        "outbound_ring_duration",
        "Dış Arama Çaldırma Süresi",
        "outboundRingTime",
        "outbound_ring_time",
    ],
}

# Bu alanlar Toniva'da SAAT (float) birimindedir — saniye değil
HOUR_UNIT_DURATION_KEYS = frozenset(
    {
        "OutboundCallDuration",
        "outboundCallDuration",
        "outbound_call_duration",
        "OutboundRingDuration",
        "outboundRingDuration",
        "outbound_ring_duration",
        "TotalDuration",
        "totalDuration",
        "InboundCallDuration",
        "inboundCallDuration",
        "InboundRingDuration",
        "inboundRingDuration",
        "AverageDuration",
        "AverageOutboundDuration",
        "AverageInboundDuration",
        "AverageOutboundRingDuration",
        "SuccessAverageDuration",
        "SuccessAverageOutboundDuration",
        "SuccessAverageInboundDuration",
        "Dış Arama Süresi",
        "Dış Arama Çaldırma Süresi",
    }
)
