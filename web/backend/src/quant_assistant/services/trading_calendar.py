"""Year-specific official exchange calendars; unknown years are visibly unavailable."""

import json
import os
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from quant_assistant.providers.market_calendar import MarketCalendars


class TradingCalendar:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None

    @staticmethod
    def validate(payload):
        year = payload.get("year")
        source = payload.get("source", "")
        if type(year) is not int or not 2000 <= year <= 2100:
            raise ValueError("日历年份须在2000至2100之间")
        url = urlparse(source)
        if url.scheme != "https" or not any(
            url.hostname == domain or (url.hostname or "").endswith("." + domain)
            for domain in ("sse.com.cn", "szse.cn", "bse.cn")
        ):
            raise ValueError("请填写交易所官方公告的HTTPS链接")
        holidays = payload.get("holidays")
        if not isinstance(holidays, list) or not 1 <= len(holidays) <= 100:
            raise ValueError("请提供1至100个休市日期")
        for day in holidays:
            if not isinstance(day, str) or date.fromisoformat(day).year != year:
                raise ValueError("休市日期必须属于所选年份")
        return {"year": year, "source": source, "holidays": sorted(set(holidays))}

    def load(self, year):
        path = self.directory / f"{year}.json" if self.directory else None
        if path and path.is_file():
            try:
                payload = self.validate(json.loads(path.read_text(encoding="utf-8")))
                return payload if payload["year"] == year else None
            except (ValueError, OSError, TypeError, AttributeError):
                return None
        builtin = MarketCalendars.builtin().load(year)
        if builtin is not None:
            return {
                "year": year,
                "source": builtin.source,
                "holidays": [day.isoformat() for day in sorted(builtin.holidays)],
            }
        return None

    def trading_day(self, value):
        payload = self.load(value.year)
        if payload is None:
            return None
        return value.weekday() < 5 and value.isoformat() not in payload["holidays"]

    def save(self, payload):
        payload = self.validate(payload)
        self.directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.directory, delete=False
        ) as output:
            json.dump(payload, output, ensure_ascii=False)
            temporary = Path(output.name)
        try:
            os.replace(temporary, self.directory / f"{payload['year']}.json")
        finally:
            temporary.unlink(missing_ok=True)
        return payload
