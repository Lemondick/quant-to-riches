"""Public fund profile data for display-only detail pages."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import RLock

import requests
from bs4 import BeautifulSoup

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.base import ProviderDataError, ProviderUnavailable
from quant_assistant.providers.data_quality import (
    MemoryCache,
    metadata,
    trading_day,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundAnnouncement as FundAnnouncement,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundDetail as FundDetail,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundFee as FundFee,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundHolding as FundHolding,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundManager as FundManager,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundNavPoint as FundNavPoint,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundPeriodReturn as FundPeriodReturn,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundRuleField as FundRuleField,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundSourceHealth as FundSourceHealth,
)
from quant_assistant.providers.fund_detail_parsing import (
    FundTradingRules as FundTradingRules,
)
from quant_assistant.providers.fund_detail_parsing import (
    _fees as _fees,
)
from quant_assistant.providers.fund_detail_parsing import (
    _first_text as _first_text,
)
from quant_assistant.providers.fund_detail_parsing import (
    _holdings as _holdings,
)
from quant_assistant.providers.fund_detail_parsing import (
    _managers as _managers,
)
from quant_assistant.providers.fund_detail_parsing import (
    _millisecond_date as _millisecond_date,
)
from quant_assistant.providers.fund_detail_parsing import (
    _optional_date as _optional_date,
)
from quant_assistant.providers.fund_detail_parsing import (
    _optional_decimal as _optional_decimal,
)
from quant_assistant.providers.fund_detail_parsing import (
    _optional_text as _optional_text,
)
from quant_assistant.providers.fund_detail_parsing import (
    _percent_rate as _percent_rate,
)
from quant_assistant.providers.fund_detail_parsing import (
    _period_returns as _period_returns,
)
from quant_assistant.providers.fund_detail_parsing import (
    _public_rules as _public_rules,
)
from quant_assistant.providers.fund_detail_parsing import (
    _required_text as _required_text,
)
from quant_assistant.providers.fund_detail_parsing import (
    _return_since as _return_since,
)
from quant_assistant.providers.fund_detail_parsing import (
    _stale as _stale,
)
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.providers.market_calendar import MarketCalendars
from quant_assistant.providers.public_http import PublicHttp, log_validation

logger = logging.getLogger(__name__)


class PublicFundDetailProvider:
    """Combine bounded Eastmoney and Danjuan public endpoints with a short cache."""

    def __init__(
        self,
        *,
        http_get: Callable[..., object] = requests.get,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 300.0,
        calendar: MarketCalendars | None = None,
    ) -> None:
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("fund detail cache ttl must be between 0 and 3600 seconds")
        self._http = PublicHttp(http_get)
        self._calendar = calendar if calendar is not None else MarketCalendars.builtin()

        def get(url, **kwargs):
            return self._http.get(
                url,
                expected="html" if "jjfl_" in url else "text" if "qt.gtimg.cn" in url else "data",
                module="fund_detail",
                **kwargs,
            )

        self._http_get = get
        self._now = now or (lambda: datetime.now(SHANGHAI_TZ))
        self._monotonic = monotonic_clock
        self._ttl = float(ttl_seconds)
        self._memory = MemoryCache(capacity=8, clock=monotonic_clock)
        self._parts = MemoryCache(capacity=64, clock=monotonic_clock)
        self._nav_cache = MemoryCache(capacity=4, clock=monotonic_clock)
        self._part_clocks = OrderedDict()
        self._part_stale = set()
        self._nav_clocks = OrderedDict()
        self._cache: dict[str, tuple[float, FundDetail]] = {}
        self._lock = RLock()
        self._source_state: dict[str, dict[str, object]] = {
            source: {
                "failures": 0,
                "last_error": None,
                "last_success_at": None,
            }
            for source in ("danjuan", "sina", "eastmoney", "eastmoney_rules", "tencent")
        }

    def source_status(self) -> tuple[FundSourceHealth, ...]:
        """Return a redacted health snapshot suitable for UI/metrics."""

        return tuple(
            FundSourceHealth(
                name=name,
                available=int(state["failures"]) == 0 and state["last_success_at"] is not None,
                consecutive_failures=int(state["failures"]),
                last_error=(None if state["last_error"] is None else str(state["last_error"])),
                last_success_at=state["last_success_at"]
                if isinstance(state["last_success_at"], datetime)
                else None,
                status=(
                    "unavailable"
                    if int(state["failures"]) > 0
                    else "available"
                    if state["last_success_at"] is not None
                    else "unchecked"
                ),
            )
            for name, state in self._source_state.items()
        )

    def detail(self, symbol: str) -> FundDetail:
        code = normalize_fund_symbol(symbol)
        try:
            loaded, stale = self._memory.read(code, lambda: self._load(code), ttl=self._ttl)
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError, ProviderDataError)):
                raise
            raise ProviderUnavailable("基金详情公开数据暂不可用") from exc
        return _stale(loaded, self.source_status()) if stale else loaded

    def expire_cache(self):
        self._memory.expire()
        self._parts.expire()
        self._nav_cache.expire()

    def _part(self, key, loader, ttl=86400):
        def load():
            value = loader()
            if not value or isinstance(value, tuple) and not any(value):
                raise ProviderUnavailable("optional_section_unavailable")
            return value, self._now().isoformat()

        packed, stale = self._parts.read(key, load, ttl=ttl)
        with self._lock:
            if stale:
                self._part_stale.add(key)
            else:
                self._part_stale.discard(key)
            self._part_clocks[key] = packed[1]
            self._part_clocks.move_to_end(key)
            while len(self._part_clocks) > 64:
                evicted, _ = self._part_clocks.popitem(last=False)
                self._part_stale.discard(evicted)
        return packed[0]

    def _basic(self, code: str) -> Mapping[str, object]:
        try:
            data = self._json_data(f"https://danjuanfunds.com/djapi/fund/{code}", "danjuan")
            if data.get("fd_code") != code:
                raise ProviderDataError("fund profile identity mismatch")
            return dict(data, _source="danjuan", _fetched_at=self._now().isoformat())
        except (ProviderUnavailable, ProviderDataError, ValueError, TypeError) as exc:
            logger.warning("fund profile primary source unavailable: %s", type(exc).__name__)
            return dict(
                self._sina_basic(code), _fallback_used=True, _fallback_reason=type(exc).__name__
            )

    def _extended(self, code: str) -> Mapping[str, object]:
        try:
            return self._json_data(f"https://danjuanfunds.com/djapi/fund/detail/{code}", "danjuan")
        except (ProviderUnavailable, ProviderDataError, ValueError, TypeError) as exc:
            logger.warning("fund extended profile unavailable: %s", type(exc).__name__)
            return {}

    def _rules(self, code: str):
        public_fees: tuple[FundFee, ...] = ()
        trading_rules = None
        public_status: dict[str, str] = {}
        try:
            response = self._http_get(
                f"https://fundf10.eastmoney.com/jjfl_{code}.html",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=(2, 5),
            )
            response.raise_for_status()
            title = BeautifulSoup(response.content, "html.parser").title
            if title is None or not re.search(rf"(?<!\d){code}(?!\d)", title.get_text()):
                raise ProviderDataError("fund fee page identity mismatch")
            public_fees, trading_rules, public_status = _public_rules(response.content, code)
            self._mark_source_success("eastmoney_rules")
        except Exception as exc:
            self._mark_source_failure("eastmoney_rules", type(exc).__name__)
            logger.warning("fund public trading rules unavailable: %s", type(exc).__name__)
        return public_fees, trading_rules, public_status

    def _optional_announcements(self, code: str) -> tuple[FundAnnouncement, ...]:
        try:
            return self._announcements(code)
        except (ProviderUnavailable, ProviderDataError, ValueError, TypeError) as exc:
            logger.warning("fund announcements unavailable: %s", type(exc).__name__)
            return ()

    def _load(self, code: str) -> FundDetail:
        # Each source has its own bounded timeout. Independent public requests
        # run together; a slow optional endpoint cannot add a second serial wait.
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="fund-public") as pool:
            basic_task = pool.submit(
                copy_context().run, self._part, "basic:" + code, lambda: self._basic(code)
            )
            extended_task = pool.submit(
                copy_context().run, self._part, "extended:" + code, lambda: self._extended(code)
            )
            nav_task = pool.submit(copy_context().run, self.nav_history, code)
            rules_task = pool.submit(
                copy_context().run, self._part, "rules:" + code, lambda: self._rules(code)
            )
            announcements_task = pool.submit(
                copy_context().run,
                self._part,
                "announcements:" + code,
                lambda: self._optional_announcements(code),
                300,
            )
            basic = basic_task.result()
            try:
                detail = extended_task.result()
            except (ProviderUnavailable, ProviderDataError, ValueError):
                detail = {}
            try:
                history = nav_task.result()
            except (ProviderUnavailable, ProviderDataError, ValueError):
                history = ()
            try:
                public_fees, trading_rules, public_status = rules_task.result()
            except (ProviderUnavailable, ProviderDataError, ValueError):
                public_fees, trading_rules, public_status = (), None, {}
            try:
                announcements = announcements_task.result()
            except (ProviderUnavailable, ProviderDataError, ValueError):
                announcements = ()
        if len(history) > 20000:
            raise ProviderDataError("fund NAV history exceeds the 20000 observation limit")
        position = detail.get("fund_position")
        position = position if isinstance(position, Mapping) else {}
        report_date = _optional_date(position.get("end_date_str"))
        managers = _managers(detail.get("manager_list"), code)
        conflicts = []
        for key, english in (("申购状态", "purchase_status"), ("赎回状态", "redemption_status")):
            primary = public_status.get(key)
            for candidate_source, candidate in (
                ("danjuan_extended", detail),
                (str(basic.get("_source")), basic),
            ):
                candidate_value = _first_text(candidate, key, english)
                if primary and candidate_value and primary != candidate_value:
                    conflicts.append(
                        f"{key}:eastmoney_rules={primary};{candidate_source}={candidate_value}"
                    )
        secondary_fees = {
            (fee.fee_type, fee.condition): fee for fee in _fees(detail.get("fund_rates"))
        }
        for fee in public_fees:
            candidate = secondary_fees.get((fee.fee_type, fee.condition))
            if candidate and (candidate.rate, candidate.fixed_amount) != (
                fee.rate,
                fee.fixed_amount,
            ):
                conflicts.append(
                    f"fee:{fee.fee_type}:{fee.condition}:primary=eastmoney;secondary=danjuan"
                )
        observed_at = self._now()
        if not isinstance(observed_at, datetime):
            raise TypeError("fund detail clock must return datetime")
        observed_at = (
            observed_at.replace(tzinfo=SHANGHAI_TZ)
            if observed_at.tzinfo is None
            else observed_at.astimezone(SHANGHAI_TZ)
        )
        return FundDetail(
            symbol=code,
            name=_required_text(basic.get("fd_name"), "fund name"),
            full_name=_optional_text(basic.get("fd_full_name")),
            fund_type=_optional_text(basic.get("type_desc")),
            company=_optional_text(basic.get("keeper_name")),
            established_on=_optional_date(basic.get("found_date")),
            latest_scale=_optional_text(basic.get("totshare")),
            benchmark=_optional_text(basic.get("performance_bench_mark")),
            investment_target=_optional_text(basic.get("invest_target")),
            nav_history=history,
            period_returns=_period_returns(history),
            holdings=_holdings(position, report_date),
            managers=managers,
            fees=public_fees or _fees(detail.get("fund_rates")),
            announcements=announcements,
            holdings_report_date=report_date,
            source="+".join(
                sorted(
                    {str(basic.get("_source", "unknown"))}
                    | {p.source for p in history if p.source}
                    | ({"danjuan"} if detail else set())
                    | ({"eastmoney"} if public_fees else set())
                )
            ),
            conflicts=tuple(conflicts),
            field_metadata={
                field: dict(
                    source=basic.get("_source"),
                    report_date=None,
                    fetched_at=basic.get("_fetched_at"),
                    quality_status="unknown_time",
                )
                for field in (
                    "name",
                    "full_name",
                    "fund_type",
                    "company",
                    "established_on",
                    "latest_scale",
                    "benchmark",
                    "investment_target",
                )
            }
            | {
                "holdings": {
                    "source": "danjuan" if position else None,
                    "report_date": report_date.isoformat() if report_date else None,
                    "fetched_at": self._part_clocks.get("extended:" + code),
                    "is_stale": "extended:" + code in self._part_stale,
                },
                "managers": {
                    "source": "danjuan" if managers else None,
                    "report_date": None,
                    "fetched_at": self._part_clocks.get("extended:" + code),
                    "identity_status": "request_bound_no_echo",
                    "is_stale": "extended:" + code in self._part_stale,
                },
                "fees": {
                    "source": "eastmoney"
                    if public_fees
                    else "danjuan"
                    if detail.get("fund_rates")
                    else None,
                    "report_date": None,
                    "fetched_at": self._part_clocks.get(
                        ("rules:" if public_fees else "extended:") + code
                    ),
                    "is_stale": ("rules:" if public_fees else "extended:") + code
                    in self._part_stale,
                },
                "status": {
                    "source": "eastmoney" if public_status else basic.get("_source"),
                    "report_date": None,
                    "fetched_at": self._part_clocks.get("rules:" + code)
                    if public_status
                    else basic.get("_fetched_at"),
                },
            },
            nav_metadata={
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in metadata(
                    "official_nav",
                    history[-1].source if history else "unknown",
                    history[-1].nav_date.isoformat() if history else None,
                    datetime.fromisoformat(self._nav_clocks[code])
                    if code in self._nav_clocks
                    else observed_at,
                    calendar=self._calendar,
                    fallback_used=bool(history and history[-1].source == "tencent"),
                    fallback_reason="primary_nav_unavailable_or_invalid"
                    if history and history[-1].source == "tencent"
                    else None,
                ).items()
            },
            **metadata(
                "fund_profile",
                str(basic.get("_source", "unknown")),
                None,
                datetime.fromisoformat(str(basic.get("_fetched_at", observed_at.isoformat()))),
                calendar=self._calendar,
                fallback_used=bool(basic.get("_fallback_used")),
                fallback_reason=basic.get("_fallback_reason"),
            ),
            observed_at=observed_at,
            source_health=self.source_status(),
            trading_rules=trading_rules,
            status_source="eastmoney_rules" if public_status else None,
            purchase_status=public_status.get("申购状态")
            or _first_text(
                detail,
                "purchase_status",
                "申购状态",
                "purchaseStatus",
            )
            or _first_text(
                basic,
                "purchase_status",
                "申购状态",
                "purchaseStatus",
            ),
            redemption_status=public_status.get("赎回状态")
            or _first_text(
                detail,
                "redemption_status",
                "赎回状态",
                "redeem_status",
                "redemptionStatus",
            )
            or _first_text(
                basic,
                "redemption_status",
                "赎回状态",
                "redeem_status",
                "redemptionStatus",
            ),
        )

    def _json_data(self, url: str, source: str = "danjuan") -> Mapping[str, object]:
        try:
            response = self._http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, Mapping) else None
            if not isinstance(data, Mapping):
                raise ProviderDataError("fund detail response is missing data")
        except (ProviderDataError, ValueError, TypeError, KeyError) as exc:
            self._mark_source_failure(source, type(exc).__name__)
            raise
        except Exception as exc:
            self._mark_source_failure(source, type(exc).__name__)
            raise ProviderUnavailable("基金详情公开数据暂不可用") from exc
        self._mark_source_success(source)
        return data

    def _sina_basic(self, code: str) -> Mapping[str, object]:
        """Use Sina's public profile endpoint when Danjuan is unavailable."""

        url = f"https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/FundPageInfoService.tabjjgk?symbol={code}&format=json"
        try:
            response = self._http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") if isinstance(payload, Mapping) else None
            raw = result.get("data") if isinstance(result, Mapping) else None
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                raw = raw[0] if raw else None
            if not isinstance(raw, Mapping):
                raise ProviderDataError("Sina fund profile response is missing data")
            if raw.get("symbol") != code:
                raise ProviderDataError("Sina fund profile identity mismatch")
            aliases = {
                "fd_name": ("基金简称", "fund_name", "name", "short_name", "jjjc"),
                "fd_full_name": ("基金全称", "full_name", "jjqc"),
                "type_desc": ("基金类型", "type", "type_desc", "NewType2Name", "Type2Name"),
                "keeper_name": ("基金管理人", "company", "manager", "glr"),
                "found_date": ("成立日期", "found_date", "clrq"),
                "totshare": ("最新规模", "scale", "totshare"),
                "purchase_status": ("申购状态", "purchase_status", "purchaseStatus"),
                "redemption_status": ("赎回状态", "redemption_status", "redeem_status"),
                "performance_bench_mark": ("业绩比较基准", "benchmark", "bjjz"),
                "invest_target": ("投资目标", "invest_target", "tzmb"),
            }
            normalized: dict[str, object] = {}
            for target, keys in aliases.items():
                for key in keys:
                    if key in raw and raw[key] not in (None, ""):
                        normalized[target] = raw[key]
                        break
            if "fd_name" not in normalized:
                raise ProviderDataError("Sina fund profile is missing fund name")
        except (ProviderDataError, ValueError, TypeError, KeyError) as exc:
            self._mark_source_failure("sina", type(exc).__name__)
            raise
        except Exception as exc:
            self._mark_source_failure("sina", type(exc).__name__)
            raise ProviderUnavailable("Sina基金资料暂不可用") from exc
        self._mark_source_success("sina")
        return dict(normalized, _source="sina", _fetched_at=self._now().isoformat())

    def nav_history(self, symbol: str) -> tuple[FundNavPoint, ...]:
        """Fetch published unit NAVs without unrelated profile/fee requests."""
        code = normalize_fund_symbol(symbol)
        packed, _ = self._nav_cache.read(
            code,
            lambda: (self._nav_history(code), self._now().isoformat()),
            ttl=lambda packed: 43200 if len(packed[0]) >= 2 else 60,
            stale_on_error=False,
        )
        with self._lock:
            self._nav_clocks[code] = packed[1]
            self._nav_clocks.move_to_end(code)
            while len(self._nav_clocks) > 4:
                self._nav_clocks.popitem(last=False)
        return packed[0]

    def _nav_history(self, code: str) -> tuple[FundNavPoint, ...]:
        try:
            response = self._http_get(
                f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()
            raw = response.content.decode("utf-8-sig", errors="strict")
            identity = re.search(r'var\s+fS_code\s*=\s*["\'](\d{6})["\']', raw)
            if identity is None or identity.group(1) != code:
                raise ProviderDataError("fund NAV identity mismatch")
            match = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);", raw, re.S)
            if match is None:
                raise ProviderDataError("fund NAV history is missing")
            rows = json.loads(match.group(1))
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise ProviderDataError("fund NAV history has an invalid shape")
            accumulated: dict[date, Decimal | None] = {}
            accumulated_match = re.search(r"var\s+Data_ACWorthTrend\s*=\s*(\[.*?\]);", raw, re.S)
            if accumulated_match is not None:
                for entry in json.loads(accumulated_match.group(1)):
                    if not isinstance(entry, list) or len(entry) != 2:
                        raise ProviderDataError("accumulated fund NAV has an invalid shape")
                    day = _millisecond_date(entry[0])
                    value = _optional_decimal(entry[1])
                    if (
                        day is None
                        or day in accumulated
                        or (entry[1] is not None and (value is None or value <= 0))
                    ):
                        raise ProviderDataError("accumulated fund NAV has invalid dates or values")
                    accumulated[day] = value
            points: list[FundNavPoint] = []
            seen: set[date] = set()
            today = self._now().astimezone(SHANGHAI_TZ).date()
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ProviderDataError("invalid NAV row")
                nav_date = _millisecond_date(row.get("x"))
                unit_nav = _optional_decimal(row.get("y"))
                if (
                    nav_date is None
                    or unit_nav is None
                    or unit_nav <= 0
                    or nav_date in seen
                    or nav_date > today
                ):
                    raise ProviderDataError("fund NAV has invalid dates or values")
                seen.add(nav_date)
                points.append(
                    FundNavPoint(
                        nav_date,
                        unit_nav,
                        accumulated.get(nav_date),
                        _optional_decimal(row.get("equityReturn")),
                        "eastmoney",
                        str(row.get("unitMoney") or "") or None,
                    )
                )
            points.sort(key=lambda item: item.nav_date)
            if len(points) < 2:
                raise ProviderDataError("fund NAV history has fewer than two observations")
            from dataclasses import replace

            for i in range(1, len(points)):
                previous, current = points[i - 1], points[i]
                errors = []
                ratio = (current.unit_nav / previous.unit_nav - 1) * 100
                if current.corporate_action:
                    errors.append("corporate_action_terms_unverified")
                if abs(ratio) > 30:
                    errors.append("abnormal_nav_jump")
                if current.daily_return is None or abs(ratio - current.daily_return) > Decimal(
                    ".1"
                ):
                    errors.append("daily_return_inconsistent")
                missing = previous.nav_date + timedelta(days=1)
                while missing < current.nav_date:
                    scheduled = trading_day(missing, self._calendar)
                    if scheduled is True or scheduled is None and missing.weekday() < 5:
                        errors.append(
                            "missing_trading_date" if scheduled else "calendar_gap_unverified"
                        )
                        break
                    missing += timedelta(days=1)
                points[i] = replace(
                    current, return_verified=not errors, validation_errors=tuple(errors)
                )
            self._mark_source_success("eastmoney")
            log_validation(
                "fund_nav",
                "eastmoney",
                count=len(points),
                source_time=points[-1].nav_date.isoformat(),
            )
            return tuple(points)
        except (ProviderDataError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._mark_source_failure("eastmoney", type(exc).__name__)
            logger.warning("fund NAV primary source unavailable: %s", type(exc).__name__)
            return self._tencent_nav_history(code)
        except Exception as exc:
            self._mark_source_failure("eastmoney", type(exc).__name__)
            logger.warning("fund NAV primary source unavailable: %s", type(exc).__name__)
            return self._tencent_nav_history(code)

    def _tencent_nav_history(self, code: str) -> tuple[FundNavPoint, ...]:
        """Fallback latest NAV quote from Tencent's public quote endpoint."""

        try:
            response = self._http_get(
                f"https://qt.gtimg.cn/q=jj{code}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            raw_bytes = getattr(response, "content", b"")
            raw = (
                raw_bytes.decode("gb18030", errors="replace")
                if raw_bytes
                else str(getattr(response, "text", ""))
            )
            match = re.search(rf'v_jj{code}="(.*?)";', raw, re.S)
            if match is None:
                raise ProviderDataError("Tencent fund NAV response is missing data")
            fields = match.group(1).split("~")
            if len(fields) < 9:
                raise ProviderDataError("Tencent fund NAV response is incomplete")
            nav = _optional_decimal(fields[5])
            accumulated = _optional_decimal(fields[6])
            change = _optional_decimal(fields[7])
            nav_date = _optional_date(fields[8])
            if (
                fields[0] != code
                or nav is None
                or nav <= 0
                or accumulated is None
                or accumulated <= 0
                or nav_date is None
                or nav_date > self._now().date()
            ):
                raise ProviderDataError("Tencent fund NAV response is invalid")
            self._mark_source_success("tencent")
            return (FundNavPoint(nav_date, nav, accumulated, change, "tencent"),)
        except (ProviderDataError, ValueError, TypeError) as exc:
            self._mark_source_failure("tencent", type(exc).__name__)
            raise
        except Exception as exc:
            self._mark_source_failure("tencent", type(exc).__name__)
            raise ProviderUnavailable("Tencent基金净值暂不可用") from exc

    def _mark_source_success(self, source: str) -> None:
        with self._lock:
            state = self._source_state.get(source)
            if state is None:
                return
            state["failures"] = 0
            state["last_error"] = None
            state["last_success_at"] = self._now()

    def _mark_source_failure(self, source: str, error: str) -> None:
        with self._lock:
            state = self._source_state.get(source)
            if state is None:
                return
            state["failures"] = int(state["failures"]) + 1
            state["last_error"] = error

    def _announcements(self, code: str) -> tuple[FundAnnouncement, ...]:
        try:
            response = self._http_get(
                "https://api.fund.eastmoney.com/f10/JJGG",
                params={
                    "fundcode": code,
                    "pageIndex": 1,
                    "pageSize": 20,
                    "type": 3,
                    "_": round(time.time() * 1000),
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": f"https://fundf10.eastmoney.com/jjgg_{code}_3.html",
                },
                timeout=(2, 5),
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("Data") if isinstance(payload, Mapping) else None
        except (ValueError, TypeError, KeyError) as exc:
            self._mark_source_failure("eastmoney", type(exc).__name__)
            raise ProviderDataError("fund announcements response is invalid") from exc
        except Exception as exc:
            self._mark_source_failure("eastmoney", type(exc).__name__)
            raise ProviderUnavailable("基金公告暂不可用") from exc
        if not isinstance(rows, Sequence):
            return ()
        items: list[FundAnnouncement] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            identifier = _optional_text(row.get("ID"))
            published_at = _optional_date(row.get("PUBLISHDATE"))
            title = _optional_text(row.get("TITLE"))
            if identifier and published_at and title:
                items.append(
                    FundAnnouncement(
                        identifier,
                        title,
                        published_at,
                        f"https://fund.eastmoney.com/gonggao/{code},{identifier}.html",
                    )
                )
        items.sort(key=lambda item: item.published_at, reverse=True)
        return tuple(items[:20])


__all__ = [
    "FundAnnouncement",
    "FundDetail",
    "FundFee",
    "FundHolding",
    "FundManager",
    "FundNavPoint",
    "FundPeriodReturn",
    "FundSourceHealth",
    "PublicFundDetailProvider",
]
