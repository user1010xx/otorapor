"""Performans raporu Excel üretimi."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from config import EXCEL_HEADERS


def build_performance_workbook(rows: list[dict[str, Any]]) -> BytesIO:
    """
    Görseldeki sütun düzeniyle .xlsx üretir.
    Dönüş: BytesIO (seek=0), Telegram'a dosya olarak gönderilebilir.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Performans"

    header_font = Font(bold=True)
    for col_idx, header in enumerate(EXCEL_HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_idx, row in enumerate(rows, start=2):
        for col_idx, header in enumerate(EXCEL_HEADERS, start=1):
            value = row.get(header, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            if header in ("Dahili", "Dış Arama Sayısı") and isinstance(value, (int, float)):
                cell.alignment = Alignment(horizontal="right")
            elif header in ("Dış Arama Süresi", "Dış Arama Çaldırma Süresi"):
                cell.alignment = Alignment(horizontal="right")

    # Sütun genişlikleri (okunabilir)
    widths = {
        "A": 16,  # Dahili Adı
        "B": 10,  # Dahili
        "C": 18,  # Dış Arama Sayısı
        "D": 18,  # Dış Arama Süresi
        "E": 26,  # Dış Arama Çaldırma Süresi
    }
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width

    # Boş satır olsa bile başlık kalsın
    for col_idx in range(1, len(EXCEL_HEADERS) + 1):
        letter = get_column_letter(col_idx)
        if letter not in widths:
            ws.column_dimensions[letter].width = 14

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def report_filename(when: datetime) -> str:
    return f"performans_raporu_{when.strftime('%Y-%m-%d_%H%M')}.xlsx"
