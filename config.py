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


# Excel sütun başlıkları (görseldeki format)
EXCEL_HEADERS = [
    "Dahili Adı",
    "Dahili",
    "Dış Arama Sayısı",
    "Dış Arama Süresi",
    "Dış Arama Çaldırma Süresi",
]

# API alan adları farklılık gösterebilir; sırayla denenir
FIELD_ALIASES: dict[str, list[str]] = {
    "Dahili Adı": [
        "Dahili Adı",
        "Dahili Adi",
        "dahili adı",
        "dahili adi",
        "dahiliAdi",
        "dahili_adi",
        "dahiliAd",
        "agentName",
        "agent_name",
        "agentLabel",
        "agent_label",
        "memberName",
        "member_name",
        "displayName",
        "display_name",
        "fullName",
        "full_name",
        "userName",
        "user_name",
        "username",
        "personelAdi",
        "personel_adi",
        "personel",
        "temsilci",
        "operatorName",
        "operator_name",
        "peerName",
        "peer_name",
        "label",
        "name",
        "agent",
    ],
    "Dahili": [
        "Dahili",
        "dahili",
        "extension",
        "ext",
        "exten",
        "agentExtension",
        "agent_extension",
        "extensionNumber",
        "extension_number",
        "internal",
        "internalNumber",
        "internal_number",
        "internalExt",
        "internal_ext",
        "sipUser",
        "sip_user",
    ],
    "Dış Arama Sayısı": [
        "Dış Arama Sayısı",
        "dış arama sayısı",
        "disAramaSayisi",
        "dis_arama_sayisi",
        "outboundCallCount",
        "outbound_call_count",
        "outboundCount",
        "outbound_count",
        "outboundCalls",
        "outbound_calls",
        "externalCallCount",
        "external_call_count",
        "outgoingCallCount",
        "outgoing_call_count",
        "outgoingCalls",
        "outgoing_calls",
        "outCalls",
        "out_calls",
        "callCount",
        "call_count",
    ],
    "Dış Arama Süresi": [
        # UI ile birebir + outbound toplam süre alanları
        # NOT: call_duration / talk_time gibi generic avg alanları bilerek YOK
        "Dış Arama Süresi",
        "dış arama süresi",
        "disAramaSuresi",
        "dis_arama_suresi",
        "outboundCallDuration",
        "outbound_call_duration",
        "outboundCallDurationSeconds",
        "outbound_call_duration_seconds",
        "outboundDuration",
        "outbound_duration",
        "outboundDurationSeconds",
        "outbound_duration_seconds",
        "outboundTalkTime",
        "outbound_talk_time",
        "outboundTalkDuration",
        "outbound_talk_duration",
        "outboundTalkSeconds",
        "outbound_talk_seconds",
        "outboundBillsec",
        "outbound_billsec",
        "outboundTotalTalkTime",
        "outbound_total_talk_time",
        "externalCallDuration",
        "external_call_duration",
        "outgoingDuration",
        "outgoing_duration",
        "outgoingCallDuration",
        "outgoing_call_duration",
        "outDuration",
        "out_duration",
        "outTalkTime",
        "out_talk_time",
        "outCallDuration",
        "out_call_duration",
        "totalOutboundDuration",
        "total_outbound_duration",
        "totalTalkTime",
        "total_talk_time",
        "talkTimeTotal",
        "talk_time_total",
        "sumTalkTime",
        "sum_talk_time",
        "sumBillsec",
        "sum_billsec",
        "totalBillsec",
        "total_billsec",
        "billsec",
    ],
    "Dış Arama Çaldırma Süresi": [
        # Generic ring_time / ringTime YOK (avg=2 tuzağı)
        "Dış Arama Çaldırma Süresi",
        "dış arama çaldırma süresi",
        "disAramaCaldirmaSuresi",
        "dis_arama_caldirma_suresi",
        "outboundRingDuration",
        "outbound_ring_duration",
        "outboundRingDurationSeconds",
        "outbound_ring_duration_seconds",
        "outboundRingTime",
        "outbound_ring_time",
        "outboundRingSeconds",
        "outbound_ring_seconds",
        "outboundRingingDuration",
        "outbound_ringing_duration",
        "outboundRingingTime",
        "outbound_ringing_time",
        "outboundTotalRingTime",
        "outbound_total_ring_time",
        "externalRingDuration",
        "external_ring_duration",
        "outgoingRingDuration",
        "outgoing_ring_duration",
        "outRingDuration",
        "out_ring_duration",
        "outRingTime",
        "out_ring_time",
        "totalOutboundRingDuration",
        "total_outbound_ring_duration",
        "totalRingTime",
        "total_ring_time",
        "sumRingTime",
        "sum_ring_time",
        "ringingDurationTotal",
        "ringing_duration_total",
    ],
}
