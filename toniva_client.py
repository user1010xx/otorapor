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

# Alias listesinden tehlikeli generic alanlar (avg=1 / avg=2 tuzağı)
_DANGEROUS_DURATION_KEYS = frozenset(
    {
        "call_duration",
        "callduration",
        "ring_time",
        "ringtime",
        "ring_duration",
        "ringduration",
        "talk_time",
        "talktime",
        "talk_duration",
        "talkduration",
        "duration",
        "time",
        "avg_duration",
        "avgduration",
        "average_duration",
        "averageduration",
        "avg_ring",
        "avgring",
        "avg_talk",
        "avgtalk",
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
        """GET /reports/performance → normalize satırlar."""
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

        if rows:
            sample = rows[0]
            if isinstance(sample, dict):
                logger.info("API satır anahtarları: %s", list(sample.keys()))
                logger.info("API satır örneği: %s", _preview_row(sample))
            else:
                logger.info("API satır tipi=%s örnek=%s columns=%s", type(sample).__name__, sample, columns)

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
            bad = [
                r
                for r in normalized
                if _duration_implausible(
                    r.get("Dış Arama Sayısı"),
                    _duration_seconds(r.get("Dış Arama Süresi")),
                )
                or _duration_implausible(
                    r.get("Dış Arama Sayısı"),
                    _duration_seconds(r.get("Dış Arama Çaldırma Süresi")),
                )
            ]
            if bad:
                raw0 = rows[0] if rows else {}
                logger.warning(
                    "Süre alanları şüpheli (%s/%s satır). Ham örnek: %s",
                    len(bad),
                    len(normalized),
                    raw0 if isinstance(raw0, dict) else raw0,
                )

        logger.info("Performans raporu: %s satır", len(normalized))
        return normalized


def _preview_row(row: dict[str, Any], limit: int = 40) -> dict[str, Any]:
    return {k: v for k, v in list(row.items())[:limit]}


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
                names: list[str] = []
                for col in value:
                    name = (
                        col.get("label")
                        or col.get("name")
                        or col.get("key")
                        or col.get("field")
                        or col.get("header")
                    )
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

    for key in ("rows", "data", "items", "results", "records", "report"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            # { "583": {...}, "608": {...} } → liste
            if value and all(isinstance(v, dict) for v in value.values()):
                out = []
                for k, v in value.items():
                    item = dict(v)
                    item.setdefault("extension", k)
                    item.setdefault("dahili", k)
                    out.append(item)
                return out
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


def _pick(row: dict[str, Any], aliases: list[str], *, skip: set[str] | None = None) -> Any:
    skip_n = {_norm_key(s) for s in (skip or set())}
    exact = {str(k): v for k, v in row.items()}
    lower_map = {str(k).lower(): v for k, v in row.items()}
    norm_map = {_norm_key(k): v for k, v in row.items()}

    for alias in aliases:
        nk = _norm_key(alias)
        if nk in skip_n:
            continue
        if alias in exact and not _is_empty(exact[alias]):
            return exact[alias]
        low = alias.lower()
        if low in lower_map and not _is_empty(lower_map[low]):
            return lower_map[low]
        if nk in norm_map and not _is_empty(norm_map[nk]):
            return norm_map[nk]
    return None


def _format_duration(value: Any) -> str:
    """Saniye / HH:MM:SS / [h,m,s] → HH:MM:SS."""
    if value is None or value == "":
        return "00:00:00"

    # [h, m, s] veya (h, m, s)
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            h, m, s = int(value[0]), int(value[1]), int(value[2])
            return f"{h:02d}:{m:02d}:{s:02d}"
        except (TypeError, ValueError):
            pass

    # {hours, minutes, seconds}
    if isinstance(value, dict):
        if any(k in value for k in ("hours", "hour", "h", "minutes", "seconds")):
            try:
                h = int(value.get("hours") or value.get("hour") or value.get("h") or 0)
                m = int(value.get("minutes") or value.get("minute") or value.get("m") or 0)
                s = int(value.get("seconds") or value.get("second") or value.get("s") or 0)
                return f"{h:02d}:{m:02d}:{s:02d}"
            except (TypeError, ValueError):
                pass
        for k in ("formatted", "display", "text", "label", "hhmmss"):
            if k in value and value[k] not in (None, ""):
                return _format_duration(value[k])
        for k in ("seconds", "totalSeconds", "total_seconds", "sec", "value", "total"):
            if k in value and value[k] not in (None, ""):
                return _format_duration(value[k])

    sec = _duration_seconds(value)
    if sec is None:
        return str(value).strip() or "00:00:00"

    hours, rem = divmod(sec, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _duration_seconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None

    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            h, m, s = int(value[0]), int(value[1]), int(value[2])
            return h * 3600 + m * 60 + s
        except (TypeError, ValueError):
            return None

    if isinstance(value, dict):
        for k in ("seconds", "totalSeconds", "total_seconds", "sec", "value", "total"):
            if k in value and value[k] not in (None, ""):
                return _duration_seconds(value[k])
        for k in ("formatted", "display", "text", "label"):
            if k in value and value[k] not in (None, ""):
                return _duration_seconds(value[k])
        return None

    if isinstance(value, (int, float)):
        total = float(value)
        if total < 0:
            return 0
        # milisaniye (ör. 1 saat = 3_600_000)
        if total >= 100_000:
            return int(round(total / 1000.0))
        return int(round(total))

    text = str(value).strip()
    if not text:
        return None

    # "01:17:24" / "1:17:24" / "01:17:24.5"
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        s = int(float(m.group(3)))
        return h * 3600 + mi * 60 + s

    m = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text)
    if m:
        mi = int(m.group(1))
        s = int(float(m.group(2)))
        return mi * 60 + s

    # "0 days 01:17:24" / "1 day, 0:05:00"
    m = re.search(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        s = int(float(m.group(3)))
        return h * 3600 + mi * 60 + s

    if re.fullmatch(r"\d+(\.\d+)?", text):
        return _duration_seconds(float(text))

    return None


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


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _expand_metric_lists(row: dict[str, Any]) -> dict[str, Any]:
    """
    metrics: [{code/key/name, value}, ...] yapısını düz alanlara çevir.
    """
    out = dict(row)
    for key, value in list(row.items()):
        if not isinstance(value, list) or not value:
            continue
        if not all(isinstance(x, dict) for x in value):
            continue

        # metric listesi mi?
        sample = value[0]
        key_field = next(
            (k for k in ("code", "key", "name", "field", "metric", "id", "label", "slug") if k in sample),
            None,
        )
        val_field = next(
            (k for k in ("value", "val", "amount", "total", "count", "seconds", "duration") if k in sample),
            None,
        )
        if not key_field or not val_field:
            continue

        for item in value:
            mk = item.get(key_field)
            mv = item.get(val_field)
            if mk is None or mv is None:
                continue
            out[str(mk)] = mv
            # parent.metric_key erişimi
            out[f"{key}.{mk}"] = mv
    return out


def _deep_flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    row = _expand_metric_lists(row)
    flat: dict[str, Any] = {}
    for key, value in row.items():
        full = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            # süre objesi olduğu gibi de kalsın
            flat[full] = value
            flat[str(key)] = value
            flat.update(_deep_flatten(value, full))
            for sk, sv in value.items():
                if not isinstance(sv, (dict, list)):
                    flat.setdefault(str(sk), sv)
        elif isinstance(value, list):
            flat[full] = value
            flat[str(key)] = value
            # metric list tekrar
            expanded = _expand_metric_lists({key: value})
            for ek, ev in expanded.items():
                if ek != key:
                    flat.setdefault(ek, ev)
        else:
            flat[full] = value
            flat[str(key)] = value
    return flat


def _looks_like_person_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 2 or len(text) > 80:
        return False
    if text.isdigit():
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        return False
    return True


def _heuristic_name(flat: dict[str, Any]) -> Any:
    name = _pick(flat, FIELD_ALIASES["Dahili Adı"])
    if not _is_empty(name) and _looks_like_person_name(str(name)):
        return name
    if not _is_empty(name) and not isinstance(name, (int, float, bool, dict, list)):
        return name

    first = _pick(flat, ["firstName", "first_name", "firstname", "ad", "Ad", "givenName", "given_name"])
    last = _pick(flat, ["lastName", "last_name", "lastname", "soyad", "Soyad", "familyName", "family_name"])
    if not _is_empty(first) or not _is_empty(last):
        combined = f"{first or ''} {last or ''}".strip()
        if combined:
            return combined

    prefer = {
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
        "label",
    }
    candidates: list[tuple[int, str, Any]] = []
    for key, value in flat.items():
        if _is_empty(value) or not _looks_like_person_name(value):
            continue
        nk = _norm_key(key)
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
                "phone",
                "number",
                "date",
                "score",
                "rate",
                "avg",
            )
        ):
            if nk not in prefer and "name" not in nk and "adi" not in nk and "isim" not in nk:
                continue
        score = 0
        if nk in prefer:
            score += 50
        if "name" in nk or "adi" in nk or "isim" in nk:
            score += 20
        if any(p in nk for p in ("agent", "user", "member", "operator", "personel", "temsilci")):
            score += 10
        candidates.append((score, nk, value))

    if not candidates:
        return name
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2] if candidates[0][0] > 0 else name


def _duration_implausible(count: Any, duration_sec: int | None) -> bool:
    """Yüksek arama sayısına rağmen 1-2 sn toplam süre → yanlış alan."""
    if duration_sec is None:
        return False
    try:
        c = int(count or 0)
    except (TypeError, ValueError):
        c = 0
    d = int(duration_sec)
    if c >= 10 and d < max(c, 10):
        # ort. < 1 sn — performans raporunda toplam süre için gerçekçi değil
        return True
    if c >= 3 and d <= 5:
        return True
    return False


def _is_ring_key(nk: str) -> bool:
    return any(t in nk for t in ("ring", "caldir", "ringing", "çaldir"))


def _is_talk_key(nk: str) -> bool:
    if _is_ring_key(nk):
        return False
    return any(
        t in nk
        for t in (
            "talk",
            "billsec",
            "bill_sec",
            "conversation",
            "speak",
            "konusma",
            "gorusme",
            "call_duration",
            "callduration",
            "call_time",
            "calltime",
            "out_duration",
            "outduration",
            "outbound_duration",
            "outboundduration",
            "outbound_call_duration",
            "dis_arama_suresi",
            "disaramasuresi",
            "duration",
            "sure",
        )
    )


def _is_outboundish(nk: str) -> bool:
    return any(
        t in nk
        for t in (
            "out",
            "outbound",
            "outgoing",
            "dis_arama",
            "disarama",
            "external",
            "harici",
            "dis",
        )
    )


def _score_duration_key(nk: str, kind: str, sec: int, value: Any) -> int:
    if nk in _DANGEROUS_DURATION_KEYS:
        # Sadece başka aday yoksa devreye girsin
        score = -30
    else:
        score = 0

    if kind == "talk":
        if _is_ring_key(nk):
            score -= 80
        elif _is_talk_key(nk):
            score += 40
    else:
        if _is_ring_key(nk):
            score += 50
        elif _is_talk_key(nk) and "ring" not in nk:
            score -= 50

    if _is_outboundish(nk):
        score += 35
    if any(t in nk for t in ("total", "sum", "toplam", "cumul")):
        score += 15
    if any(t in nk for t in ("avg", "average", "mean", "max", "min", "inbound", "incoming", "missed")):
        score -= 60
    if "count" in nk or "sayi" in nk or nk.endswith("_id") or nk == "id":
        score -= 50

    # HH:MM:SS string güçlü sinyal
    if isinstance(value, str) and re.search(r"\d+:\d{2}:\d{2}", value):
        score += 25

    if sec <= 5:
        score -= 25
    elif sec >= 60:
        score += 15
    elif sec >= 20:
        score += 5

    # UI ile birebir normalize isimler
    if kind == "talk" and nk in {"dis_arama_suresi", "outbound_call_duration", "outbound_duration", "outbound_talk_time"}:
        score += 40
    if kind == "ring" and nk in {
        "dis_arama_caldirma_suresi",
        "outbound_ring_duration",
        "outbound_ring_time",
        "outbound_ringing_duration",
    }:
        score += 40

    return score


def _iter_duration_candidates(flat: dict[str, Any]) -> list[tuple[str, str, Any, int]]:
    """(norm_key, original_key, value, seconds) listesi."""
    out: list[tuple[str, str, Any, int]] = []
    seen: set[str] = set()
    for key, value in flat.items():
        if isinstance(value, (dict, list)) and not isinstance(value, (str, bytes)):
            # dict süre objesi _duration_seconds ile açılabilir
            if not isinstance(value, dict):
                continue
        sec = _duration_seconds(value)
        if sec is None:
            continue
        nk = _norm_key(key)
        # tamamen alakasız anahtarları ele
        if not any(
            t in nk
            for t in (
                "duration",
                "sure",
                "time",
                "talk",
                "ring",
                "bill",
                "sec",
                "saniye",
                "dur",
                "elapsed",
                "length",
                "konusma",
                "gorusme",
                "caldir",
            )
        ):
            # yine de HH:MM:SS ise al
            if not (isinstance(value, str) and re.search(r"\d+:\d{2}", value)):
                continue
        # count alanları
        if re.search(r"(count|sayi|calls)$", nk) and "duration" not in nk and "time" not in nk:
            continue
        sig = f"{nk}:{sec}"
        if sig in seen:
            continue
        seen.add(sig)
        out.append((nk, str(key), value, sec))
    return out


def _pick_duration(flat: dict[str, Any], kind: str, call_count: Any) -> Any:
    """
    Toplam konuşma / çaldırma süresini seç.
    call_duration=1 / ring_time=2 gibi avg tuzaklarını eler.
    """
    aliases = (
        FIELD_ALIASES["Dış Arama Süresi"]
        if kind == "talk"
        else FIELD_ALIASES["Dış Arama Çaldırma Süresi"]
    )

    # 1) Güvenli alias'lar (dangerous hariç)
    direct = _pick(flat, aliases, skip=_DANGEROUS_DURATION_KEYS)
    direct_sec = _duration_seconds(direct)

    candidates = _iter_duration_candidates(flat)
    scored: list[tuple[int, int, str, Any]] = []
    for nk, _orig, value, sec in candidates:
        score = _score_duration_key(nk, kind, sec, value)
        # çağrı sayısına göre ceza
        if _duration_implausible(call_count, sec):
            score -= 100
        scored.append((score, sec, nk, value))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))

    best = scored[0] if scored else None

    # 2) Alias sonucu makul ise ve daha iyi aday yoksa kullan
    if direct is not None and direct_sec is not None and not _duration_implausible(call_count, direct_sec):
        if not best or best[0] < 30 or best[1] <= direct_sec:
            # best çok daha yüksek skorlu ve daha büyükse onu al
            if best and best[0] >= 50 and best[1] > direct_sec * 2:
                return best[3]
            return direct

    # 3) Skorlu en iyi aday
    if best and best[0] >= 20 and not _duration_implausible(call_count, best[1]):
        return best[3]

    # 4) Tehlikeli generic alias (son çare) — yalnızca makulse
    fallback = _pick(flat, aliases)
    fb_sec = _duration_seconds(fallback)
    if fallback is not None and fb_sec is not None and not _duration_implausible(call_count, fb_sec):
        return fallback

    # 5) Makul en büyük outbound adayı (talk) / ring adayı
    pool = [
        s
        for s in scored
        if not _duration_implausible(call_count, s[1]) and s[0] >= 0
    ]
    if kind == "talk":
        pool = [s for s in pool if not _is_ring_key(s[2])] or pool
    else:
        ring_pool = [s for s in pool if _is_ring_key(s[2])]
        pool = ring_pool or pool

    if pool:
        pool.sort(key=lambda x: (-x[0], -x[1]))
        return pool[0][3]

    # 6) Hiçbiri makul değilse: en yüksek skor (log uyarısı normalize tarafında)
    if best:
        return best[3]
    return direct if direct is not None else fallback


def _pick_count(flat: dict[str, Any]) -> Any:
    count = _pick(flat, FIELD_ALIASES["Dış Arama Sayısı"])
    if not _is_empty(count):
        return count
    best = None
    best_score = -10**9
    for key, value in flat.items():
        nk = _norm_key(key)
        if not any(t in nk for t in ("count", "sayi", "calls", "adet")):
            continue
        if any(t in nk for t in ("duration", "time", "ring", "sure", "sec")):
            continue
        score = 0
        if _is_outboundish(nk):
            score += 20
        if "inbound" in nk or "incoming" in nk:
            score -= 20
        try:
            n = int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            continue
        score += min(n, 1000) // 100
        if score > best_score:
            best_score = score
            best = value
    return best


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """API satırını Excel sütunlarına map et."""
    flat = _deep_flatten(row)

    name = _heuristic_name(flat)
    ext = _pick(flat, FIELD_ALIASES["Dahili"])
    count = _pick_count(flat)
    duration = _pick_duration(flat, "talk", count)
    ring = _pick_duration(flat, "ring", count)

    # Dakika birimi yedek: saniye olarak imkansız, dakika olarak makulse
    d_sec = _duration_seconds(duration)
    r_sec = _duration_seconds(ring)
    try:
        c_int = int(count or 0)
    except (TypeError, ValueError):
        c_int = 0

    if d_sec is not None and _duration_implausible(c_int, d_sec) and not _duration_implausible(c_int, d_sec * 60):
        # değer dakika olabilir
        duration = d_sec * 60
        logger.info("Konuşma süresi dakika birimi olarak yorumlandı: %s → %ss", d_sec, d_sec * 60)

    if r_sec is not None and _duration_implausible(c_int, r_sec) and not _duration_implausible(c_int, r_sec * 60):
        ring = r_sec * 60
        logger.info("Çaldırma süresi dakika birimi olarak yorumlandı: %s → %ss", r_sec, r_sec * 60)

    result = {
        "Dahili Adı": "" if _is_empty(name) else str(name).strip(),
        "Dahili": _as_int(ext),
        "Dış Arama Sayısı": _as_int(count),
        "Dış Arama Süresi": _format_duration(duration),
        "Dış Arama Çaldırma Süresi": _format_duration(ring),
        "_raw_keys": list(row.keys()),
        "_raw_sample": {str(k): row[k] for k in list(row.keys())[:25]},
    }
    return result
