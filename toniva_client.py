"""Toniva Public API istemcisi — performans raporu."""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from datetime import date
from typing import Any

import httpx

from config import FIELD_ALIASES, HOUR_UNIT_DURATION_KEYS

logger = logging.getLogger(__name__)

_TR_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)


class TonivaError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TonivaClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "OTORAPOR-TelegramBot/1.0",
        }

    async def fetch_performance(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """
        GET /reports/performance?startDate=&endDate=

        Toniva alanları (UI ile aynı):
          ExtensionName, ExtensionNumber, OutboundCallCount,
          OutboundCallDuration (saat), OutboundRingDuration (saat)
        """
        url = f"{self.base_url}/reports/performance"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }

        logger.info("Toniva performans raporu isteniyor: %s", params)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers=self._headers(), params=params)
            except httpx.HTTPError as exc:
                raise TonivaError(f"Toniva bağlantı hatası: {exc}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "?")
            raise TonivaError(
                f"Rate limit aşıldı (CRM-2094). Retry-After: {retry_after}s",
                status_code=429,
                body=_safe_json(response),
            )

        if response.status_code >= 400:
            body = _safe_json(response)
            code = ""
            if isinstance(body, dict):
                code = body.get("code") or body.get("message") or ""
            raise TonivaError(
                f"Toniva API hatası HTTP {response.status_code}: {code or response.text[:300]}",
                status_code=response.status_code,
                body=body,
            )

        payload = _safe_json(response)
        rows = _extract_rows(payload)
        columns = _extract_columns(payload)

        if rows and isinstance(rows[0], dict):
            sample = rows[0]
            logger.info("API satır anahtarları: %s", list(sample.keys()))
            # Teşhis: süre ham değerleri
            for k in (
                "OutboundCallDuration",
                "OutboundRingDuration",
                "ExtensionName",
                "OutboundCallCount",
            ):
                if k in sample:
                    logger.info("Ham %s = %r (%s)", k, sample[k], type(sample[k]).__name__)

        normalized: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                normalized.append(normalize_row(row))
            elif isinstance(row, (list, tuple)) and columns:
                as_dict = {
                    str(columns[i]): row[i]
                    for i in range(min(len(columns), len(row)))
                }
                normalized.append(normalize_row(as_dict))

        logger.info("Performans raporu: %s satır", len(normalized))
        return normalized


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _extract_columns(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    for key in ("columns", "headers", "fields", "cols"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            if all(isinstance(x, str) for x in value):
                return value
            if all(isinstance(x, dict) for x in value):
                names = []
                for col in value:
                    name = col.get("label") or col.get("name") or col.get("key") or col.get("field")
                    if name:
                        names.append(str(name))
                if names:
                    return names
    meta = payload.get("meta")
    if isinstance(meta, dict):
        return _extract_columns(meta)
    return []


def _extract_rows(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    # UI iç API: { Status, Data: [...] } — public benzer sarmalayıcı olabilir
    for key in ("Data", "data", "rows", "items", "results", "records", "report"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
            out = []
            for k, v in value.items():
                item = dict(v)
                item.setdefault("ExtensionNumber", k)
                out.append(item)
            return out

    if "meta" in payload:
        for key, value in payload.items():
            if key != "meta" and isinstance(value, list):
                return value

    logger.warning("Beklenmeyen API yanıt yapısı: keys=%s", list(payload.keys())[:20])
    return []


def _norm_key(key: str) -> str:
    text = str(key).strip().translate(_TR_MAP).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _pick(row: dict[str, Any], aliases: list[str]) -> Any:
    exact = {str(k): v for k, v in row.items()}
    lower_map = {str(k).lower(): v for k, v in row.items()}
    norm_map = {_norm_key(k): v for k, v in row.items()}

    for alias in aliases:
        if alias in exact and not _is_empty(exact[alias]):
            return exact[alias], alias
        low = alias.lower()
        if low in lower_map and not _is_empty(lower_map[low]):
            # orijinal anahtarı bul
            for k, v in row.items():
                if str(k).lower() == low and not _is_empty(v):
                    return v, str(k)
        nk = _norm_key(alias)
        if nk in norm_map and not _is_empty(norm_map[nk]):
            for k, v in row.items():
                if _norm_key(k) == nk and not _is_empty(v):
                    return v, str(k)
    return None, None


def _as_int(value: Any) -> int | str:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = str(value).strip().replace(",", "")
    try:
        return int(float(text))
    except ValueError:
        return text


def _seconds_to_hhmmss(total_seconds: int) -> str:
    total = max(0, int(total_seconds))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _parse_hhmmss(text: str) -> int | None:
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text.strip())
    if not m:
        m2 = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text.strip())
        if not m2:
            return None
        return int(m2.group(1)) * 60 + int(float(m2.group(2)))
    h, mi = int(m.group(1)), int(m.group(2))
    s = int(float(m.group(3)))
    return h * 3600 + mi * 60 + s


def format_toniva_duration(value: Any, *, source_key: str | None = None) -> str:
    """
    Toniva performans süre alanlarını HH:MM:SS yap.

    UI kaynağı (crm frontend):
      function cl(e) { return ti(Math.floor(e * 3600)) }
      → süre alanları ONDALIK SAAT (ör. 1.29 → 01:17:24)

    - "01:17:24" string → olduğu gibi normalize
    - sayı ve bilinen saat-birimli alan → hours * 3600
    - sayı > 48 → büyük ihtimalle zaten saniye
    """
    if value is None or value == "":
        return "00:00:00"

    if isinstance(value, bool):
        return "00:00:00"

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "00:00:00"
        parsed = _parse_hhmmss(text)
        if parsed is not None:
            return _seconds_to_hhmmss(parsed)
        try:
            num = float(text.replace(",", "."))
        except ValueError:
            return text
    elif isinstance(value, (int, float)):
        num = float(value)
    else:
        return str(value)

    if not math.isfinite(num) or num == 0:
        return "00:00:00"

    key_is_hour_unit = False
    if source_key:
        if source_key in HOUR_UNIT_DURATION_KEYS:
            key_is_hour_unit = True
        elif _norm_key(source_key) in {_norm_key(k) for k in HOUR_UNIT_DURATION_KEYS}:
            key_is_hour_unit = True
        # OutboundCallDuration / OutboundRingDuration / *Duration (Average hariç total alanlar)
        nk = _norm_key(source_key)
        if nk in {
            "outboundcallduration",
            "outboundringduration",
            "totalduration",
            "inboundcallduration",
            "inboundringduration",
        }:
            key_is_hour_unit = True
        if "duration" in nk and "average" not in nk and "avg" not in nk and "ratio" not in nk:
            # PBX performans süre kolonları genelde saat
            if any(x in nk for x in ("outbound", "inbound", "total", "ring", "call")):
                key_is_hour_unit = True

    # Saat birimi: UI cl(e)=floor(e*3600)
    # Günlük raporda 48 saatten büyük "saat" değeri gerçekçi değil → saniye kabul et
    if key_is_hour_unit and abs(num) <= 48:
        seconds = int(math.floor(abs(num) * 3600))
    elif abs(num) > 48:
        # Ham saniye (veya ms)
        if abs(num) >= 100_000:
            seconds = int(math.floor(abs(num) / 1000))
        else:
            seconds = int(math.floor(abs(num)))
    else:
        # Anahtar bilinmiyor, küçük sayı: Toniva performansında saat olasılığı yüksek
        # 1.29 saat vs 1.29 saniye — UI ile uyum için saat varsay
        seconds = int(math.floor(abs(num) * 3600))

    return _seconds_to_hhmmss(seconds)


# Geriye dönük testler için alias
def _format_duration(value: Any) -> str:
    """Eski test uyumu: sayı saniye varsayar; string HH:MM:SS korur."""
    if value is None or value == "":
        return "00:00:00"
    if isinstance(value, str):
        parsed = _parse_hhmmss(value.strip())
        if parsed is not None:
            return _seconds_to_hhmmss(parsed)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        total = int(round(float(value)))
        if total < 0:
            total = 0
        if total >= 100_000:
            total = total // 1000
        return _seconds_to_hhmmss(total)
    return format_toniva_duration(value)


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Toniva performans satırı → Excel sütunları.

    Doğrulanan alanlar (frontend bundle):
      ExtensionName, ExtensionNumber, OutboundCallCount,
      OutboundCallDuration (saat), OutboundRingDuration (saat)
    """
    name, _ = _pick(row, FIELD_ALIASES["Dahili Adı"])
    ext, _ = _pick(row, FIELD_ALIASES["Dahili"])
    count, _ = _pick(row, FIELD_ALIASES["Dış Arama Sayısı"])
    talk, talk_key = _pick(row, FIELD_ALIASES["Dış Arama Süresi"])
    ring, ring_key = _pick(row, FIELD_ALIASES["Dış Arama Çaldırma Süresi"])

    # Case-insensitive doğrudan erişim yedek (API PascalCase)
    if _is_empty(name):
        for k in ("ExtensionName", "extensionName"):
            if k in row and not _is_empty(row[k]):
                name = row[k]
                break
    if _is_empty(ext):
        for k in ("ExtensionNumber", "extensionNumber"):
            if k in row and not _is_empty(row[k]):
                ext = row[k]
                break
    if _is_empty(count):
        for k in ("OutboundCallCount", "outboundCallCount"):
            if k in row and not _is_empty(row[k]):
                count = row[k]
                break
    if _is_empty(talk):
        for k in ("OutboundCallDuration", "outboundCallDuration"):
            if k in row and not _is_empty(row[k]):
                talk, talk_key = row[k], k
                break
    if _is_empty(ring):
        for k in ("OutboundRingDuration", "outboundRingDuration"):
            if k in row and not _is_empty(row[k]):
                ring, ring_key = row[k], k
                break

    return {
        "Dahili Adı": "" if _is_empty(name) else str(name).strip(),
        "Dahili": _as_int(ext),
        "Dış Arama Sayısı": _as_int(count),
        "Dış Arama Süresi": format_toniva_duration(
            talk, source_key=talk_key or "OutboundCallDuration"
        ),
        "Dış Arama Çaldırma Süresi": format_toniva_duration(
            ring, source_key=ring_key or "OutboundRingDuration"
        ),
        "_raw_keys": list(row.keys()),
        "_raw_sample": {str(k): row[k] for k in list(row.keys())[:30]},
    }
