"""Birim testleri — ağ / Telegram olmadan."""

from __future__ import annotations

from datetime import date, datetime, time
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from config import EXCEL_HEADERS, SCHEDULE_TIMES
from excel_builder import build_performance_workbook, report_filename
from toniva_client import (
    _extract_rows,
    _format_duration,
    normalize_row,
)


class TestSchedule:
    def test_five_daily_slots(self):
        assert SCHEDULE_TIMES == [
            (12, 27),
            (13, 51),
            (16, 27),
            (17, 27),
            (18, 50),
        ]

    def test_times_are_valid_clock(self):
        for h, m in SCHEDULE_TIMES:
            t = time(hour=h, minute=m, tzinfo=ZoneInfo("Europe/Istanbul"))
            assert t.hour == h and t.minute == m


class TestDuration:
    def test_seconds(self):
        assert _format_duration(1908) == "00:31:48"
        assert _format_duration(4176) == "01:09:36"
        assert _format_duration(0) == "00:00:00"

    def test_hhmmss_text(self):
        assert _format_duration("1:09:36") == "01:09:36"
        assert _format_duration("00:39:36") == "00:39:36"
        assert _format_duration("39:36") == "00:39:36"

    def test_empty(self):
        assert _format_duration(None) == "00:00:00"
        assert _format_duration("") == "00:00:00"


class TestExtractRows:
    def test_list_payload(self):
        assert _extract_rows([{"a": 1}]) == [{"a": 1}]

    def test_rows_key(self):
        assert _extract_rows({"rows": [1, 2], "meta": {}}) == [1, 2]

    def test_nested_data_rows(self):
        assert _extract_rows({"data": {"rows": [{"x": 1}]}}) == [{"x": 1}]

    def test_unknown(self):
        assert _extract_rows({"meta": {"ok": True}, "foo": "bar"}) == []


class TestNormalize:
    def test_english_keys(self):
        row = normalize_row(
            {
                "agentName": "adem",
                "extension": 583,
                "outboundCallCount": 365,
                "outboundCallDuration": 1908,
                "outboundRingDuration": 4176,
            }
        )
        assert row["Dahili Adı"] == "adem"
        assert row["Dahili"] == 583
        assert row["Dış Arama Sayısı"] == 365
        assert row["Dış Arama Süresi"] == "00:31:48"
        assert row["Dış Arama Çaldırma Süresi"] == "01:09:36"

    def test_turkish_api_headers(self):
        """UI/export ile aynı Türkçe başlıklar gelirse map edilsin."""
        row = normalize_row(
            {
                "Dahili Adı": "selen",
                "Dahili": 585,
                "Dış Arama Sayısı": 392,
                "Dış Arama Süresi": "00:39:36",
                "Dış Arama Çaldırma Süresi": "01:13:48",
            }
        )
        assert row["Dahili Adı"] == "selen"
        assert row["Dahili"] == 585
        assert row["Dış Arama Sayısı"] == 392
        assert row["Dış Arama Süresi"] == "00:39:36"
        assert row["Dış Arama Çaldırma Süresi"] == "01:13:48"

    def test_nested_agent(self):
        row = normalize_row(
            {
                "agent": {"name": "dilara", "extension": 635},
                "outbound_call_count": 412,
                "outbound_call_duration": 2592,
                "outbound_ring_duration": 5508,
            }
        )
        assert row["Dahili Adı"] == "dilara"
        assert row["Dahili"] == 635
        assert row["Dış Arama Sayısı"] == 412
        assert row["Dış Arama Süresi"] == "00:43:12"
        assert row["Dış Arama Çaldırma Süresi"] == "01:31:48"

    def test_missing_fields_defaults(self):
        row = normalize_row({})
        assert row["Dahili Adı"] == ""
        assert row["Dahili"] == 0
        assert row["Dış Arama Sayısı"] == 0
        assert row["Dış Arama Süresi"] == "00:00:00"


class TestExcel:
    def test_headers_and_values(self):
        rows = [
            {
                "Dahili Adı": "adem",
                "Dahili": 583,
                "Dış Arama Sayısı": 365,
                "Dış Arama Süresi": "00:31:48",
                "Dış Arama Çaldırma Süresi": "01:09:36",
            }
        ]
        buf = build_performance_workbook(rows)
        assert isinstance(buf, BytesIO)
        assert buf.getvalue()[:2] == b"PK"

        wb = load_workbook(buf)
        ws = wb.active
        headers = [ws.cell(1, c).value for c in range(1, 6)]
        assert headers == EXCEL_HEADERS
        assert ws.cell(2, 1).value == "adem"
        assert ws.cell(2, 2).value == 583
        assert ws.cell(2, 3).value == 365
        assert ws.cell(2, 4).value == "00:31:48"
        assert ws.cell(2, 5).value == "01:09:36"

    def test_empty_rows_still_has_header(self):
        buf = build_performance_workbook([])
        wb = load_workbook(buf)
        ws = wb.active
        assert [ws.cell(1, c).value for c in range(1, 6)] == EXCEL_HEADERS
        assert ws.cell(2, 1).value is None

    def test_filename(self):
        when = datetime(2026, 7, 18, 12, 27)
        assert report_filename(when) == "performans_raporu_2026-07-18_1227.xlsx"


class TestBotGuards:
    def test_allowed_group(self):
        from bot_app import is_allowed_group
        from config import Settings

        settings = Settings(
            telegram_bot_token="x",
            telegram_chat_id=-100123,
            toniva_api_key="tva_x",
            toniva_base_url="https://crm.toniva.net/api/public/v1",
        )
        assert is_allowed_group(-100123, settings) is True
        assert is_allowed_group(-100999, settings) is False
        assert is_allowed_group(None, settings) is False


class TestSettings:
    def test_missing_env(self, monkeypatch):
        from config import Settings

        for key in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "TONIVA_API_KEY",
            "TONIVA_BASE_URL",
        ):
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID"):
            Settings.from_env()

    def test_from_env_ok(self, monkeypatch):
        from config import Settings

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10055")
        monkeypatch.setenv("TONIVA_API_KEY", "tva_test")
        monkeypatch.delenv("TONIVA_BASE_URL", raising=False)

        s = Settings.from_env()
        assert s.telegram_chat_id == -10055
        assert s.toniva_base_url.endswith("/api/public/v1")
