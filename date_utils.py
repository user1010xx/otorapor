"""Rapor tarih argümanı parse yardımcıları."""

from __future__ import annotations

import re
from datetime import date, datetime


class DateParseError(ValueError):
    """Kullanıcı tarih formatı hatalı."""


_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 18.07.2026 / 18.07.26
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "%d.%m.%Y"),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), "%d.%m.%y"),
    # 18/07/2026 / 18-07-2026
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "%d/%m/%Y"),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "%d-%m-%Y"),
    # 2026-07-18 (ISO)
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "%Y-%m-%d"),
]


def parse_report_date(text: str) -> date:
    """
    Kullanıcı tarihini date'e çevir.
    Desteklenen: 18.07.2026, 18.07.26, 18/07/2026, 18-07-2026, 2026-07-18
    """
    raw = (text or "").strip()
    if not raw:
        raise DateParseError("Tarih boş olamaz.")

    # Birleşik argüman: "18.07.2026" veya yanlışlıkla birden fazla token
    token = raw.split()[0]

    for pattern, fmt in _DATE_PATTERNS:
        if not pattern.match(token):
            continue
        try:
            # %y / %Y için datetime.strptime; gün-ay-yıl sırası fmt'te
            if fmt == "%d.%m.%Y":
                d, m, y = token.split(".")
                return date(int(y), int(m), int(d))
            if fmt == "%d.%m.%y":
                d, m, y = token.split(".")
                year = int(y)
                year = 2000 + year if year < 100 else year
                return date(int(year), int(m), int(d))
            if fmt == "%d/%m/%Y":
                d, m, y = token.split("/")
                return date(int(y), int(m), int(d))
            if fmt == "%d-%m-%Y":
                d, m, y = token.split("-")
                return date(int(y), int(m), int(d))
            if fmt == "%Y-%m-%d":
                return date.fromisoformat(token)
        except ValueError as exc:
            raise DateParseError(
                f"Geçersiz tarih: {token}. Örnek: 18.07.2026"
            ) from exc

    raise DateParseError(
        f"Tarih anlaşılamadı: `{token}`\n"
        "Örnekler:\n"
        "• /rapor\n"
        "• /rapor 18.07.2026"
    )


def join_command_args(args: list[str] | None) -> str:
    """context.args birleşimi (boşluklu tarih yok; yine de güvenli)."""
    if not args:
        return ""
    return " ".join(a.strip() for a in args if a and a.strip()).strip()
