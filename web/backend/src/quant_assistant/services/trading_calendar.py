"""Year-specific official exchange calendars; unknown years are visibly unavailable."""

import json
import os
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

SOURCE_2026 = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
HOLIDAYS_2026 = (
    ("01-01", "01-03"),
    ("02-15", "02-23"),
    ("04-04", "04-06"),
    ("05-01", "05-05"),
    ("06-19", "06-21"),
    ("09-25", "09-27"),
    ("10-01", "10-07"),
)


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
        if year == 2026:
            return {
                "year": year,
                "source": SOURCE_2026,
                "holidays": [
                    date(year, month, day).isoformat()
                    for month in range(1, 13)
                    for day in range(1, 32)
                    if self._holiday_2026(month, day)
                ],
            }
        return None

    @staticmethod
    def _holiday_2026(month, day):
        text = f"{month:02}-{day:02}"
        return any(start <= text <= end for start, end in HOLIDAYS_2026)

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
