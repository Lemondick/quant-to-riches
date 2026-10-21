"""Next-year market holiday generation (worker op ``calendar.generate_next_year``).

Computes exchange holidays per market from first principles so the worker can
produce a *next* year calendar without waiting for the official notice:

- CN: lunar anchors (Spring Festival / Dragon Boat / Mid-Autumn) + Qingming
  solar-term approximation + statutory blocks + a documented holiday-swap
  (调休) heuristic. confidence=medium: the State Council announces the exact
  arrangement each December, so any pre-announcement computation is an
  estimate.
- US: NYSE rules. JP: 国民祝日法 + 振替休日 + 国民の休日 + JPX year-end
  market holidays. HK: HKEX general-holiday rules. confidence=high.

``holidays`` contains only weekday (Mon-Fri) dates as ISO strings: ordinary
weekends are excluded by contract, and a market is closed on every weekend
anyway, so the weekend component of a holiday block carries no information.
"""

from __future__ import annotations

from datetime import date, timedelta

CALENDAR_CAPABILITIES = ("calendar.generate_next_year",)

MARKETS = ("CN", "US", "JP", "HK")

_MIN_YEAR, _MAX_YEAR = 2000, 2100

# ---------------------------------------------------------------------------
# Shared date helpers
# ---------------------------------------------------------------------------


def _nth_weekday(year, month, n, weekday):
    """n-th ``weekday`` (Mon=0) of a month, 1-based n."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year):
    """Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Lunar calendar (1900-2100 lookup table)
# ---------------------------------------------------------------------------
#
# _LUNAR_INFO[y - 1900]: low 4 bits = leap month (0 = none); bit 16 set =
# 30-day leap month; bits 15..4 = 1 for each 30-day regular month (month 1 is
# bit 15). Entries were cross-validated against two independent lunar
# implementations (lunardate 1900-2099, lunar-python for 2100); the two oracles
# disagree by +-1 day on a lunar month boundary in 8 far-future years
# (2057/2067/2070/2076/2079/2083/2086/2095) - a genuine new-moon-midnight
# ambiguity, covered by the CN medium confidence.

_LUNAR_INFO = (
    0x04BD8, 0x04AE0, 0x0A570, 0x054D5, 0x0D260, 0x0D950, 0x16554, 0x056A0, 0x09AD0, 0x055D2,
    0x04AE0, 0x0A5B6, 0x0A4D0, 0x0D250, 0x1D255, 0x0B540, 0x0D6A0, 0x0ADA2, 0x095B0, 0x14977,
    0x04970, 0x0A4B0, 0x0B4B5, 0x06A50, 0x06D40, 0x1AB54, 0x02B60, 0x09570, 0x052F2, 0x04970,
    0x06566, 0x0D4A0, 0x0EA50, 0x06E95, 0x05AD0, 0x02B60, 0x186E3, 0x092E0, 0x1C8D7, 0x0C950,
    0x0D4A0, 0x1D8A6, 0x0B550, 0x056A0, 0x1A5B4, 0x025D0, 0x092D0, 0x0D2B2, 0x0A950, 0x0B557,
    0x06CA0, 0x0B550, 0x15355, 0x04DA0, 0x0A5D0, 0x14573, 0x052B0, 0x0A9A8, 0x0E950, 0x06AA0,
    0x0AEA6, 0x0AB50, 0x04B60, 0x0AAE4, 0x0A570, 0x05260, 0x0F263, 0x0D950, 0x05B57, 0x056A0,
    0x096D0, 0x04DD5, 0x04AD0, 0x0A4D0, 0x0D4D4, 0x0D250, 0x0D558, 0x0B540, 0x0B5A0, 0x195A6,
    0x095B0, 0x049B0, 0x0A974, 0x0A4B0, 0x0B27A, 0x06A50, 0x06D40, 0x0AF46, 0x0AB60, 0x09570,
    0x04AF5, 0x04970, 0x064B0, 0x074A3, 0x0EA50, 0x06B58, 0x05AC0, 0x0AB60, 0x096D5, 0x092E0,
    0x0C960, 0x0D954, 0x0D4A0, 0x0DA50, 0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5,
    0x0A950, 0x0B4A0, 0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260, 0x0EA65, 0x0D530,
    0x05AA0, 0x076A3, 0x096D0, 0x04AFB, 0x04AD0, 0x0A4D0, 0x1D0B6, 0x0D250, 0x0D520, 0x0DD45,
    0x0B5A0, 0x056D0, 0x055B2, 0x049B0, 0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0,
    0x14B63, 0x09370, 0x049F8, 0x04970, 0x064B0, 0x168A6, 0x0EA50, 0x06AA0, 0x1A6C4, 0x0AAE0,
    0x092E0, 0x0D2E3, 0x0C960, 0x0D557, 0x0D4A0, 0x0DA50, 0x05D55, 0x056A0, 0x0A6D0, 0x055D4,
    0x052D0, 0x0A9B8, 0x0A950, 0x0B4A0, 0x0B6A6, 0x0AD50, 0x055A0, 0x0ABA4, 0x0A5B0, 0x052B0,
    0x0B273, 0x06930, 0x07337, 0x06AA0, 0x0AD50, 0x14B55, 0x04B60, 0x0A570, 0x054E4, 0x0D160,
    0x0E968, 0x0D520, 0x0DAA0, 0x16AA6, 0x056D0, 0x04AE0, 0x0A9D4, 0x0A2D0, 0x0D150, 0x0F252,
    0x0D520,
)

_LUNAR_EPOCH = date(1900, 1, 31)  # lunar 1900-01-01


def _lunar_year_days(year):
    info = _LUNAR_INFO[year - 1900]
    days = sum(30 if info & (0x10000 >> month) else 29 for month in range(1, 13))
    if info & 0xF:
        days += 30 if info & 0x10000 else 29
    return days


def _lunar_month_days(year, month):
    return 30 if _LUNAR_INFO[year - 1900] & (0x10000 >> month) else 29


def lunar_to_solar(lunar_year, lunar_month, lunar_day):
    """Gregorian date of a lunar date (regular month only)."""
    if not 1900 <= lunar_year <= 2100:
        raise ValueError("lunar year out of range")
    offset = sum(_lunar_year_days(year) for year in range(1900, lunar_year))
    leap = _LUNAR_INFO[lunar_year - 1900] & 0xF
    for month in range(1, lunar_month):
        offset += _lunar_month_days(lunar_year, month)
        if month == leap:
            offset += 30 if _LUNAR_INFO[lunar_year - 1900] & 0x10000 else 29
    return _LUNAR_EPOCH + timedelta(days=offset + lunar_day - 1)


def _qingming(year):
    """Qingming solar-term approximation (D+C-L formula, 21st century).

    Good to +/-1 day; the exact date is an astronomical value. Documented in
    the CN notes because the 3-day block is built around it.
    """
    short = year % 100
    return date(year, 4, int(short * 0.2422 + 5.15) - (short - 1) // 4)


# ---------------------------------------------------------------------------
# CN
# ---------------------------------------------------------------------------


def _three_day_block(day):
    """3-day festival block snapped to cover an adjacent weekend.

    Official practice centers the block on the festival but shifts it so a
    Friday/Monday festival merges with the weekend instead of stranding
    isolated workdays.
    """
    weekday = day.weekday()
    if weekday == 4:  # Friday -> Fri..Sun
        return day, day + timedelta(days=2)
    if weekday == 0:  # Monday -> Sat..Mon
        return day - timedelta(days=2), day
    return day - timedelta(days=1), day + timedelta(days=1)


def _cn_blocks(year):
    """Statutory holiday blocks as (start, end, name); callers drop weekends."""
    blocks = [(date(year, 1, 1), date(year, 1, 3), "元旦")]
    spring = lunar_to_solar(year, 1, 1)
    start, end = spring - timedelta(days=1), spring + timedelta(days=5)  # 除夕..初六
    # 调休启发式: a 7-day block ending on Sunday is bridged to Monday, and one
    # starting on Monday pulls in the preceding Sunday (2026: 02-15..02-23).
    # The State Council may extend/trim differently in a given year.
    if end.weekday() == 6:
        end += timedelta(days=1)
    if start.weekday() == 0:
        start -= timedelta(days=1)
    blocks.append((start, end, "春节"))
    qingming = _qingming(year)
    qingming_start, qingming_end = _three_day_block(qingming)
    blocks.append((qingming_start, qingming_end, "清明节"))
    blocks.append((date(year, 5, 1), date(year, 5, 5), "劳动节"))
    dragon_start, dragon_end = _three_day_block(lunar_to_solar(year, 5, 5))
    blocks.append((dragon_start, dragon_end, "端午节"))
    mid_start, mid_end = _three_day_block(lunar_to_solar(year, 8, 15))
    blocks.append((mid_start, mid_end, "中秋节"))
    blocks.append((date(year, 10, 1), date(year, 10, 7), "国庆节"))
    return blocks


def _cn_holidays(year):
    notes = [
        "农历锚点（春节/端午/中秋/除夕）由 1900-2100 农历表推算，确定性高。",
        "清明按 D+C-L 近似公式计算，存在 +/-1 天误差。",
        "法定连休天数（春节 7 天、国庆 7 天、劳动节 5 天、元旦/清明/端午/中秋 3 天）"
        "为近年惯例；国务院每年 12 月公布次年具体调休安排，届时可能增减。",
        "调休启发式：7 天长假逢周日结尾顺延至周一、逢周一开头前扩至周日"
        "（复现 2026 年春节 02-15..02-23）；实际安排可能不同。",
        "输出仅含工作日：调休上班的周末交易所本就休市，不在列表中。",
    ]
    holidays = set()
    for start, end, _name in _cn_blocks(year):
        day = start
        while day <= end:
            if day.weekday() < 5 and day.year == year:
                holidays.add(day)
            day += timedelta(days=1)
    return holidays, notes


# ---------------------------------------------------------------------------
# US (NYSE)
# ---------------------------------------------------------------------------


def _us_holidays(year):
    def fixed(month, day):
        current = date(year, month, day)
        if current.weekday() == 5:
            return current - timedelta(days=1)
        if current.weekday() == 6:
            return current + timedelta(days=1)
        return current

    holidays = {
        fixed(1, 1),
        _nth_weekday(year, 1, 3, 0),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 3, 0),  # Presidents Day
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        fixed(6, 19),  # Juneteenth
        fixed(7, 4),  # Independence Day
        _nth_weekday(year, 9, 1, 0),  # Labor Day
        _nth_weekday(year, 11, 4, 3),  # Thanksgiving
        fixed(12, 25),  # Christmas
    }
    # New Year observed in the prior December (e.g. 2022-01-01 Sat -> 2021-12-31).
    following = date(year + 1, 1, 1)
    if following.weekday() == 5:
        holidays.add(date(year, 12, 31))
    notes = [
        "NYSE 规则：固定节假日逢周六顺延至周五、逢周日顺延至周一；"
        "次年元旦逢周六时，前一年 12 月 31 日（周五）休市，计入本年。",
    ]
    return {day for day in holidays if day.weekday() < 5 and day.year == year}, notes


# ---------------------------------------------------------------------------
# JP (国民祝日法 + 振替休日 + 国民の休日 + JPX 年末年始)
# ---------------------------------------------------------------------------


def _jde_to_date(jde):
    """Julian Ephemeris Day -> Gregorian date (Meeus Ch. 7)."""
    z = int(jde + 0.5)
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day = b - d - int(30.6001 * e)
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    return date(year, month, day)


def _jp_equinox(year, spring):
    """Vernal/autumnal equinox date in JST (Meeus Table 27.C polynomial).

    Far more reliable than the 20.8431/23.2588 shortcut, which is off by a
    day for e.g. the 2026 autumnal equinox. TT->UTC uses the NASA Delta-T
    polynomial (extrapolated outside 2005-2050); a few seconds of Delta-T
    error only matters if the equinox falls within minutes of midnight JST.
    """
    y = year - 2000
    if spring:
        jde = (
            2451623.80984
            + 365.24237404 * y
            + 0.00005169 * y * y
            - 0.00000411 * y**3
            - 0.00000057 * y**4
        )
    else:
        jde = (
            2451810.21748
            + 365.24203414 * y
            - 0.00006129 * y * y
            + 0.00000325 * y**3
            + 0.00000020 * y**4
        )
    delta_t = 62.92 + 0.32217 * y + 0.005589 * y * y  # seconds, TT - UTC
    return _jde_to_date(jde - delta_t / 86400 + 9 / 24)


def _jp_holidays(year):
    base = {
        date(year, 1, 1),  # 元日
        _nth_weekday(year, 1, 2, 0),  # 成人の日
        date(year, 2, 11),  # 建国記念の日
        date(year, 2, 23),  # 天皇誕生日
        _jp_equinox(year, True),  # 春分の日
        date(year, 4, 29),  # 昭和の日
        date(year, 5, 3),  # 憲法記念日
        date(year, 5, 4),  # みどりの日
        date(year, 5, 5),  # こどもの日
        _nth_weekday(year, 7, 3, 0),  # 海の日
        date(year, 8, 11),  # 山の日
        _nth_weekday(year, 10, 2, 0),  # スポーツの日
        date(year, 11, 3),  # 文化の日
        date(year, 11, 23),  # 勤労感謝の日
    }
    respect = _nth_weekday(year, 9, 3, 0)  # 敬老の日
    autumn = _jp_equinox(year, False)  # 秋分の日
    base.add(respect)
    base.add(autumn)
    if (autumn - respect).days == 2:
        base.add(respect + timedelta(days=1))  # 国民の休日
    holidays = set(base)
    for day in sorted(base):
        if day.weekday() == 6:  # 振替休日：顺延至下一个非假日的工作日
            substitute = day + timedelta(days=1)
            while substitute in holidays:
                substitute += timedelta(days=1)
            holidays.add(substitute)
    # JPX 年末年始：交易所 1/2、1/3、12/31 休市（非法定祝日）。
    holidays.update([date(year, 1, 2), date(year, 1, 3), date(year, 12, 31)])
    notes = [
        "国民祝日法：祝日逢周日则顺延至下一个非祝日（振替休日）；"
        "敬老の日与秋分の日之间夹单个工作日时为国民の休日。",
        "春分/秋分按 Meeus 天文多项式计算 JST 日期（经与 JPX 公告核对）；"
        "分点时刻若恰在 JST 午夜前后数分钟内，日期可能差一天。"
        "含 JPX 年末年始交易所休市日（1/2、1/3、12/31），非法定祝日。",
    ]
    return {day for day in holidays if day.weekday() < 5 and day.year == year}, notes


# ---------------------------------------------------------------------------
# HK (HKEX)
# ---------------------------------------------------------------------------


def _hk_holidays(year):
    spring = lunar_to_solar(year, 1, 1)
    holidays = {
        date(year, 1, 1),
        spring,
        spring + timedelta(days=1),
        spring + timedelta(days=2),  # 农历新年三天
        _qingming(year),  # 清明节
        _easter(year) - timedelta(days=2),  # Good Friday
        _easter(year) + timedelta(days=1),  # Easter Monday
        date(year, 5, 1),  # 劳动节
        lunar_to_solar(year, 4, 8),  # 佛诞
        lunar_to_solar(year, 5, 5),  # 端午节
        date(year, 7, 1),  # 香港特别行政区成立纪念日
        lunar_to_solar(year, 8, 16),  # 中秋节后一日
        date(year, 10, 1),  # 国庆节
        lunar_to_solar(year, 9, 9),  # 重阳节
        date(year, 12, 25),  # 圣诞节
    }
    # 圣诞节后首个周日之外的日子（条例中 weekday 含周六）：2021 年为周一
    # 12-27，2026 年 12-26 本就是周六，不另增。
    boxing = date(year, 12, 26)
    while boxing.weekday() == 6:
        boxing += timedelta(days=1)
    holidays.add(boxing)
    # 逢周日的公众假期顺延至下一个非假日（跳过已有的假期，如 2026 年清明）。
    for day in sorted(holidays):
        if day.weekday() == 6:
            substitute = day + timedelta(days=1)
            while substitute in holidays:
                substitute += timedelta(days=1)
            holidays.add(substitute)
    notes = [
        "农历新年为农历正月初一至初三；佛诞/端午/中秋翌日/重阳按农历推算。",
        "公众假期逢周日顺延至下一个非假期（跳过 Easter Monday 等已有假期）。",
        "圣诞节后首个'周日之外的日子'为假期（条例 weekday 含周六）。"
        "2026 年该日为周六，不另增工作日假期。",
    ]
    return {day for day in holidays if day.weekday() < 5 and day.year == year}, notes


# ---------------------------------------------------------------------------
# Operation entry point
# ---------------------------------------------------------------------------

_SOURCES = {
    "CN": "lunar_algorithm",
    "US": "nyse_rules",
    "JP": "japan_law",
    "HK": "hkex_rules",
}

_GENERATORS = {
    "CN": (_cn_holidays, "medium"),
    "US": (_us_holidays, "high"),
    "JP": (_jp_holidays, "high"),
    "HK": (_hk_holidays, "high"),
}


def execute_calendar(kind, payload):
    """Worker op ``calendar.generate_next_year``.

    payload: ``{"market": "CN"|"US"|"JP"|"HK", "year": int}``.
    Returns ``{"market", "year", "holidays", "source", "confidence", "notes"}``;
    ``holidays`` are ISO weekday dates only (no ordinary weekends).
    """
    if kind != "calendar.generate_next_year":
        raise ValueError("unsupported_operation")
    market = payload["market"]
    year = payload["year"]
    if market not in MARKETS:
        raise ValueError("unknown market")
    if type(year) is not int or not _MIN_YEAR <= year <= _MAX_YEAR:
        raise ValueError("invalid year")
    generate, confidence = _GENERATORS[market]
    holidays, notes = generate(year)
    return {
        "market": market,
        "year": year,
        "holidays": [day.isoformat() for day in sorted(holidays)],
        "source": _SOURCES[market],
        "confidence": confidence,
        "notes": notes,
    }
