"""Toniva Public API istemcisi — performans raporu."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from config import FIELD_ALIASES

logger = logging.getLogger(__name__)


class TonivaError(Exception):
    """Toniva API hata sarmalayıcısı."""

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
        Dönüş: normalize edilmiş satır listesi (Excel sütun anahtarlarıyla).
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

        if rows and isinstance(rows[0], dict):
            logger.info("API satır anahtarları (örnek): %s", list(rows[0].keys()))

        normalized = [normalize_row(row) for row in rows if isinstance(row, dict)]
        logger.info("Performans raporu: %s satır", len(normalized))
        return normalized


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _extract_rows(payload: Any) -> list[Any]:
    """Farklı sarmalayıcı formatlarını destekle."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("rows", "data", "items", "results", "records", "report"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in ("rows", "data", "items", "results"):
                if isinstance(value.get(nested), list):
                    return value[nested]

    # Bazı API'ler { meta, ...fields as list under unknown key }
    if "meta" in payload:
        for key, value in payload.items():
            if key != "meta" and isinstance(value, list):
                return value

    logger.warning("Beklenmeyen API yanıt yapısı: %s", type(payload).__name__)
    return []


def _pick(row: dict[str, Any], aliases: list[str]) -> Any:
    lower_map = {str(k).lower(): v for k, v in row.items()}
    for alias in aliases:
        if alias in row and row[alias] is not None:
            return row[alias]
        low = alias.lower()
        if low in lower_map and lower_map[low] is not None:
            return lower_map[low]
    return None


def _format_duration(value: Any) -> str:
    """Saniye (int/float) veya hazır süre metnini HH:MM:SS yap."""
    if value is None or value == "":
        return "00:00:00"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        total = int(round(float(value)))
        if total < 0:
            total = 0
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    text = str(value).strip()
    if not text:
        return "00:00:00"

    # Zaten HH:MM:SS veya H:MM:SS
    parts = text.split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        h, m, s = (int(p) for p in parts)
        return f"{h:02d}:{m:02d}:{s:02d}"
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        m, s = (int(p) for p in parts)
        return f"00:{m:02d}:{s:02d}"

    # "123s" veya saf sayı string
    if text.isdigit():
        return _format_duration(int(text))

    return text


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


def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """İç içe agent/user objelerini üst seviyeye yay."""
    flat = dict(row)
    for nest_key in ("agent", "user", "employee", "operator", "dahili"):
        nested = row.get(nest_key)
        if isinstance(nested, dict):
            for k, v in nested.items():
                flat.setdefault(k, v)
                flat.setdefault(f"{nest_key}_{k}", v)
    return flat


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """API satırını Excel sütunlarına map et."""
    flat = _flatten_row(row)
    name = _pick(flat, FIELD_ALIASES["Dahili Adı"])
    ext = _pick(flat, FIELD_ALIASES["Dahili"])
    count = _pick(flat, FIELD_ALIASES["Dış Arama Sayısı"])
    duration = _pick(flat, FIELD_ALIASES["Dış Arama Süresi"])
    ring = _pick(flat, FIELD_ALIASES["Dış Arama Çaldırma Süresi"])

    return {
        "Dahili Adı": "" if name is None else str(name).strip(),
        "Dahili": _as_int(ext),
        "Dış Arama Sayısı": _as_int(count),
        "Dış Arama Süresi": _format_duration(duration),
        "Dış Arama Çaldırma Süresi": _format_duration(ring),
        "_raw": row,
    }
