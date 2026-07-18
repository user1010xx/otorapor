"""Toniva Public API istemcisi — performans raporu."""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from typing import Any

import httpx

from config import FIELD_ALIASES

logger = logging.getLogger(__name__)

# Türkçe karakter sadeleştirme (eşleşme için)
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

        if rows:
            sample = rows[0]
            if isinstance(sample, dict):
                logger.info("API satır anahtarları (örnek): %s", list(sample.keys()))
                logger.info("API satır örneği (ilk kayıt): %s", _preview_row(sample))
            else:
                logger.info("API satır tipi: %s örnek: %s", type(sample).__name__, sample)

        # columns + array satır formatı
        columns = _extract_columns(payload)
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

        if normalized:
            empty_names = sum(1 for r in normalized if not r.get("Dahili Adı"))
            if empty_names:
                logger.warning(
                    "Dahili Adı boş satır: %s/%s — ham anahtarlar: %s",
                    empty_names,
                    len(normalized),
                    list(rows[0].keys()) if rows and isinstance(rows[0], dict) else columns,
                )

        logger.info("Performans raporu: %s satır", len(normalized))
        return normalized


def _preview_row(row: dict[str, Any], limit: int = 30) -> dict[str, Any]:
    items = list(row.items())[:limit]
    return {k: v for k, v in items}


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
                    name = col.get("name") or col.get("key") or col.get("label") or col.get("field")
                    if name:
                        names.append(str(name))
                if names:
                    return names
    meta = payload.get("meta")
    if isinstance(meta, dict):
        return _extract_columns(meta)
    return []


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

    if "meta" in payload:
        for key, value in payload.items():
            if key != "meta" and isinstance(value, list):
                return value

    logger.warning("Beklenmeyen API yanıt yapısı: %s", type(payload).__name__)
    return []


def _norm_key(key: str) -> str:
    text = str(key).strip().translate(_TR_MAP).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _pick(row: dict[str, Any], aliases: list[str]) -> Any:
    """Tam / case-insensitive / Türkçe-normalize anahtar eşlemesi."""
    exact = {str(k): v for k, v in row.items()}
    lower_map = {str(k).lower(): v for k, v in row.items()}
    norm_map = {_norm_key(k): v for k, v in row.items()}

    for alias in aliases:
        if alias in exact and exact[alias] is not None and exact[alias] != "":
            return exact[alias]
        low = alias.lower()
        if low in lower_map and lower_map[low] is not None and lower_map[low] != "":
            return lower_map[low]
        nk = _norm_key(alias)
        if nk in norm_map and norm_map[nk] is not None and norm_map[nk] != "":
            return norm_map[nk]
    return None


def _format_duration(value: Any) -> str:
    """Saniye (int/float) veya hazır süre metnini HH:MM:SS yap."""
    if value is None or value == "":
        return "00:00:00"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        total = int(round(float(value)))
        if total < 0:
            total = 0
        # milisaniye gibi duran çok büyük değerler (ör. 1 saat = 3_600_000 ms)
        if total >= 100_000:
            total = total // 1000
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    text = str(value).strip()
    if not text:
        return "00:00:00"

    parts = text.split(":")
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        h, m, s = (int(p) for p in parts)
        return f"{h:02d}:{m:02d}:{s:02d}"
    if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
        m, s = (int(p) for p in parts)
        return f"00:{m:02d}:{s:02d}"

    if text.isdigit():
        return _format_duration(int(text))

    # "1h 17m 24s" gibi kaba parse yok — olduğu gibi bırak
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


def _deep_flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Tüm iç içe dict'leri düzleştir (stats.outbound.count vb.)."""
    flat: dict[str, Any] = {}
    for key, value in row.items():
        full = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            flat.update(_deep_flatten(value, full))
            # kısa anahtarlar da erişilebilir olsun (son yazan kazanır)
            for sk, sv in value.items():
                if not isinstance(sv, dict):
                    flat.setdefault(str(sk), sv)
        else:
            flat[full] = value
            flat[str(key)] = value
    return flat


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _looks_like_person_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 2 or len(text) > 80:
        return False
    if text.isdigit():
        return False
    if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", text):
        return False
    return True


def _heuristic_name(flat: dict[str, Any]) -> Any:
    """İsim benzeri anahtarları tara."""
    # önce bilinen alias'lar
    name = _pick(flat, FIELD_ALIASES["Dahili Adı"])
    if not _is_empty(name) and _looks_like_person_name(str(name)):
        return name
    if not _is_empty(name) and not isinstance(name, (int, float, bool, dict, list)):
        return name

    # ad + soyad birleştir
    first = _pick(
        flat,
        [
            "firstName",
            "first_name",
            "firstname",
            "ad",
            "Ad",
            "givenName",
            "given_name",
        ],
    )
    last = _pick(
        flat,
        [
            "lastName",
            "last_name",
            "lastname",
            "soyad",
            "Soyad",
            "familyName",
            "family_name",
        ],
    )
    if not _is_empty(first) or not _is_empty(last):
        combined = f"{first or ''} {last or ''}".strip()
        if combined:
            return combined

    # anahtar içeriğine göre
    prefer = (
        "dahili_adi",
        "agent_name",
        "agentname",
        "display_name",
        "displayname",
        "full_name",
        "fullname",
        "member_name",
        "membername",
        "user_name",
        "username",
        "personel",
        "temsilci",
        "operator_name",
        "operatorname",
        "caller_name",
        "cid_name",
        "peer_name",
        "label",
    )
    candidates: list[tuple[int, str, Any]] = []
    for key, value in flat.items():
        if _is_empty(value) or not _looks_like_person_name(value):
            continue
        nk = _norm_key(key)
        # sayısal / süre alanlarını ele
        if any(
            tok in nk
            for tok in (
                "count",
                "sayi",
                "duration",
                "sure",
                "time",
                "ring",
                "extension",
                "dahili",
                "phone",
                "number",
                "id",
                "date",
                "score",
                "rate",
                "avg",
                "average",
            )
        ):
            # "dahili_adi" istisnası
            if nk not in ("dahili_adi", "dahili_ad", "agent_name", "display_name", "full_name"):
                if "name" not in nk and "adi" not in nk and "isim" not in nk:
                    continue

        score = 0
        if nk in prefer:
            score += 50
        if "name" in nk or nk.endswith("_adi") or nk.endswith("adi") or "isim" in nk:
            score += 20
        if any(p in nk for p in ("agent", "user", "member", "operator", "personel", "temsilci")):
            score += 10
        if "outbound" in nk or "inbound" in nk:
            score -= 20
        candidates.append((score, nk, value))

    if not candidates:
        return name  # belki boş string

    candidates.sort(key=lambda x: (-x[0], x[1]))
    best = candidates[0]
    if best[0] > 0:
        return best[2]
    return name


def _duration_seconds(value: Any) -> int | None:
    """Karşılaştırma için saniyeye çevir; parse edilemezse None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        total = int(round(float(value)))
        if total >= 100_000:
            total = total // 1000
        return max(total, 0)
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        h, m, s = (int(p) for p in parts)
        return h * 3600 + m * 60 + s
    if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
        m, s = (int(p) for p in parts)
        return m * 60 + s
    if text.isdigit():
        return int(text)
    return None


def _heuristic_duration(flat: dict[str, Any], kind: str) -> Any:
    """
    kind: 'talk' | 'ring'
    Yanlışlıkla avg=1/2 gibi küçük metrikleri almak yerine
    toplam süre alanlarını tercih eder.
    """
    aliases = (
        FIELD_ALIASES["Dış Arama Süresi"]
        if kind == "talk"
        else FIELD_ALIASES["Dış Arama Çaldırma Süresi"]
    )
    direct = _pick(flat, aliases)
    direct_sec = _duration_seconds(direct)

    # Anahtar taraması
    talk_tokens = ("talk", "billsec", "conversation", "speak", "konusma", "gorusme", "call_duration", "callduration")
    ring_tokens = ("ring", "caldir", "ringing", "ringtime", "ring_time", "ringduration")
    out_tokens = ("out", "outbound", "outgoing", "dis_arama", "disarama", "external", "harici")
    bad_tokens = ("avg", "average", "mean", "max", "min", "inbound", "incoming", "missed", "queue")

    scored: list[tuple[int, int, str, Any]] = []
    for key, value in flat.items():
        sec = _duration_seconds(value)
        if sec is None:
            continue
        # HH:MM:SS string veya makul toplam süre
        nk = _norm_key(key)
        if not any(
            tok in nk
            for tok in (
                "duration",
                "sure",
                "time",
                "talk",
                "ring",
                "bill",
                "sec",
                "saniye",
            )
        ):
            # süre benzeri değer: "01:17:24"
            if not (isinstance(value, str) and ":" in value):
                continue

        score = 0
        if kind == "talk":
            if any(t in nk for t in talk_tokens):
                score += 30
            if any(t in nk for t in ring_tokens) and "talk" not in nk:
                score -= 40
        else:
            if any(t in nk for t in ring_tokens):
                score += 30
            if any(t in nk for t in talk_tokens) and "ring" not in nk:
                score -= 40

        if any(t in nk for t in out_tokens):
            score += 25
        if "total" in nk or "sum" in nk or "toplam" in nk:
            score += 15
        if any(t in nk for t in bad_tokens):
            score -= 50
        if "count" in nk or "sayi" in nk or nk.endswith("_id"):
            score -= 40

        # Toplam süreler genelde büyük olur; 1-2 sn şüpheli
        if sec <= 5:
            score -= 20
        elif sec >= 60:
            score += 10

        scored.append((score, sec, nk, value))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))

    if scored and scored[0][0] >= 20:
        return scored[0][3]

    # Alias sonucu makul ise kullan
    if direct is not None and direct_sec is not None:
        # Küçük değer + daha iyi aday varsa adayı al
        if direct_sec <= 5 and scored and scored[0][1] > direct_sec and scored[0][0] >= 0:
            return scored[0][3]
        return direct

    if scored and scored[0][0] >= 0:
        return scored[0][3]

    return direct


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """API satırını Excel sütunlarına map et."""
    flat = _deep_flatten(row)

    name = _heuristic_name(flat)
    ext = _pick(flat, FIELD_ALIASES["Dahili"])
    count = _pick(flat, FIELD_ALIASES["Dış Arama Sayısı"])
    duration = _heuristic_duration(flat, "talk")
    ring = _heuristic_duration(flat, "ring")

    # count bulunamadıysa outbound*count taraması
    if _is_empty(count):
        for key, value in flat.items():
            nk = _norm_key(key)
            if "count" in nk or "sayi" in nk or nk.endswith("calls"):
                if any(t in nk for t in ("out", "outbound", "outgoing", "dis", "external")):
                    if "duration" not in nk and "ring" not in nk:
                        count = value
                        break

    return {
        "Dahili Adı": "" if _is_empty(name) else str(name).strip(),
        "Dahili": _as_int(ext),
        "Dış Arama Sayısı": _as_int(count),
        "Dış Arama Süresi": _format_duration(duration),
        "Dış Arama Çaldırma Süresi": _format_duration(ring),
        "_raw_keys": list(row.keys()),
    }
