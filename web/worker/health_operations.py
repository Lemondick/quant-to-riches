"""Fund health checks (``fund.health_check``): four体检 dimensions.

Contract (batch2 §2): payload ``{funds: [{code, name?, declared_style?}]}``
→ ``{as_of, reports: [{code, findings: [{kind, severity, detail, evidence}]}]}``,
``kind`` ∈ ``scale_change`` | ``manager_change`` | ``style_drift`` | ``fee_level``,
``severity`` ∈ ``info`` | ``warn`` | ``critical``.

Data reuse (documented): fund facts come from the existing public fund
detail provider (``PublicFundDetailProvider``: 最新规模/经理/任职起始日/
持仓/费率/基金类型/投资目标). Anything the provider cannot supply degrades
to ``severity=info, detail=数据不足`` without blocking the other checks.

Documented assumptions and extensions:
- ``scale_change``: needs a quarterly scale series. The provider only
  exposes the latest scale, so callers may pass an optional per-fund
  ``scale_history: [{label, scale}]`` (scale in 亿元); quarter-over-quarter
  change > 30% → warn. Without it → info/数据不足.
- ``manager_change``: a manager whose ``tenure_start`` is within the last
  180 days (and not within 180 days of the fund's own establishment, i.e.
  not a new fund) is treated as a recent change → critical. Callers may
  pass ``previous_managers: [names]`` for a direct set comparison, which
  takes precedence. No tenure data → info/数据不足.
- ``style_drift``: holdings carry no industry field in the provider, so
  each holding name is bucketed by a documented keyword table into
  科技成长/大消费/金融地产/医药健康/周期制造/其他 (heuristic, name-based).
  Declared 价值 vs ≥50% growth-bucket weight (or declared 成长 vs ≥60%
  value-bucket weight) in the top-10 → warn. Without ``declared_style``
  or holdings → info/数据不足.
- ``fee_level``: 管理费+托管费 parsed from the provider's fee table vs a
  documented per-type reference ceiling (货币 0.50% / 债券 0.50% /
  指数 0.70% / QDII 2.20% / 其他主动 1.80%) — a stand-in for the peer
  percentile, which no provider publishes. Above ceiling → warn.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

HEALTH_CAPABILITIES = ("fund.health_check",)

_RECENT_MANAGER_DAYS = 180
_SCALE_WARN_RATIO = 0.30

# (bucket, keywords) — first match wins; documented name-based heuristic.
_HOLDING_BUCKETS = (
    (
        "科技成长",
        (
            "芯片",
            "半导体",
            "集成电路",
            "软件",
            "计算机",
            "电子",
            "通信",
            "5G",
            "人工智能",
            "互联网",
            "光伏",
            "新能源",
            "电池",
            "锂电",
            "锂能",
            "储能",
            "电源",
            "机器人",
            "智能",
            "科技",
        ),
    ),
    (
        "大消费",
        (
            "茅台",
            "五粮液",
            "白酒",
            "食品",
            "饮料",
            "乳业",
            "家电",
            "家居",
            "零售",
            "商超",
            "餐饮",
            "旅游",
            "免税",
            "服装",
            "化妆品",
            "啤酒",
            "消费",
        ),
    ),
    ("金融地产", ("银行", "证券", "券商", "保险", "信托", "地产", "房地产", "物业", "金融")),
    ("医药健康", ("医药", "医疗", "生物", "疫苗", "器械", "中药", "CXO", "创新药", "健康")),
    (
        "周期制造",
        (
            "钢铁",
            "煤炭",
            "有色",
            "化工",
            "建材",
            "机械",
            "工程",
            "电力",
            "交运",
            "航运",
            "汽车",
            "重工",
            "制造",
        ),
    ),
)
_GROWTH_BUCKETS = {"科技成长", "医药健康"}
_VALUE_BUCKETS = {"大消费", "金融地产", "周期制造"}

# 管理费+托管费 reference ceilings per fund type (documented stand-in for
# the peer percentile, which no public provider publishes).
_FEE_CEILINGS = (
    (("货币",), Decimal("0.005")),
    (("债券",), Decimal("0.005")),
    (("指数", "ETF"), Decimal("0.007")),
    (("QDII",), Decimal("0.022")),
)
_DEFAULT_FEE_CEILING = Decimal("0.018")  # 主动权益类常见上限 1.5%+0.25%


def _field(view: Mapping, name: str, default=None):
    if isinstance(view, Mapping):
        return view.get(name, default)
    return getattr(view, name, default)


def _finding(kind, severity, detail, evidence=None):
    return {
        "kind": kind,
        "severity": severity,
        "detail": detail,
        "evidence": evidence or {},
    }


def _parse_scale(text: object) -> Decimal | None:
    if text is None:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(亿|万)?", str(text))
    if not match:
        return None
    value = Decimal(match.group(1))
    unit = match.group(2)
    return value * (
        Decimal("1") if unit == "亿" else Decimal("0.0001") if unit == "万" else Decimal("1")
    )


def _check_scale(view: Mapping) -> dict:
    history = _field(view, "scale_history") or ()
    if isinstance(history, Sequence) and len(history) >= 2:
        points = []
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            scale = entry.get("scale")
            scale = (
                _parse_scale(scale)
                if isinstance(scale, str)
                else (Decimal(str(scale)) if scale is not None else None)
            )
            if scale is not None:
                points.append((entry.get("label", ""), scale))
        if len(points) >= 2:
            (_, previous), (label, latest) = points[-2], points[-1]
            change = (latest - previous) / previous if previous > 0 else None
            evidence = {
                "previous": {"label": points[-2][0], "scale_yi": str(previous)},
                "latest": {"label": label, "scale_yi": str(latest)},
                "change_ratio": None if change is None else round(float(change), 4),
            }
            if change is not None and abs(change) > _SCALE_WARN_RATIO:
                direction = "缩水" if change < 0 else "激增"
                return _finding(
                    "scale_change",
                    "warn",
                    f"近一季度规模{direction}{abs(change):.1%}，超过±30%阈值",
                    evidence,
                )
            return _finding(
                "scale_change",
                "info",
                f"近一季度规模变化{float(change):.1%}，处于正常范围"
                if change is not None
                else "规模序列无法计算变化",
                evidence,
            )
    latest = _parse_scale(_field(view, "latest_scale"))
    return _finding(
        "scale_change",
        "info",
        "数据不足：无季度规模序列",
        {"latest_scale": str(_field(view, "latest_scale"))} if latest is not None else {},
    )


def _check_manager(view: Mapping, today: date) -> dict:
    managers = _field(view, "managers") or ()
    names = {str(_field(manager, "name") or "").strip() for manager in managers}
    names.discard("")
    previous = _field(view, "previous_managers")
    if isinstance(previous, Sequence) and not isinstance(previous, str):
        previous_names = {str(name).strip() for name in previous if str(name).strip()}
        joined = sorted(names - previous_names)
        left = sorted(previous_names - names)
        if joined or left:
            parts = []
            if joined:
                parts.append("新增：" + "、".join(joined))
            if left:
                parts.append("离任：" + "、".join(left))
            return _finding(
                "manager_change",
                "critical",
                "经理变更，" + "；".join(parts),
                {"current": sorted(names), "previous": sorted(previous_names)},
            )
        return _finding("manager_change", "info", "基金经理未变更", {"managers": sorted(names)})
    established = _field(view, "established_on")
    recent = []
    dated = 0
    for manager in managers:
        name = str(_field(manager, "name") or "").strip()
        start = _field(manager, "tenure_start")
        if not name or not isinstance(start, date):
            continue
        dated += 1
        newborn = (
            isinstance(established, date) and (start - established).days <= _RECENT_MANAGER_DAYS
        )
        if (today - start).days <= _RECENT_MANAGER_DAYS and not newborn:
            recent.append({"name": name, "tenure_start": start.isoformat()})
    if recent:
        return _finding(
            "manager_change",
            "critical",
            "检测到近 180 天内基金经理变动："
            + "、".join(f"{item['name']}（{item['tenure_start']} 上任）" for item in recent),
            {"recent": recent},
        )
    if dated == 0:
        return _finding("manager_change", "info", "数据不足：无基金经理任职起始日", {})
    return _finding(
        "manager_change",
        "info",
        f"基金经理稳定（现任 {len(names)} 人，近 180 天无变动）",
        {"managers": sorted(names)},
    )


def _bucket_holding(name: str) -> str:
    for bucket, keywords in _HOLDING_BUCKETS:
        if any(keyword in name for keyword in keywords):
            return bucket
    return "其他"


def _check_style(view: Mapping) -> dict:
    declared = _field(view, "declared_style")
    holdings = _field(view, "holdings") or ()
    if not declared or not isinstance(declared, str):
        return _finding("style_drift", "info", "数据不足：未提供 declared_style", {})
    rows = [
        (str(_field(holding, "name") or ""), Decimal(str(_field(holding, "weight") or 0)))
        for holding in holdings
    ]
    rows = [(name, weight) for name, weight in rows if name and weight > 0][:10]
    if not rows:
        return _finding("style_drift", "info", "数据不足：无有效持仓", {})
    buckets: dict[str, Decimal] = {}
    for name, weight in rows:
        bucket = _bucket_holding(name)
        buckets[bucket] = buckets.get(bucket, Decimal("0")) + weight
    total = sum(buckets.values())
    growth = sum(weight for bucket, weight in buckets.items() if bucket in _GROWTH_BUCKETS)
    value = sum(weight for bucket, weight in buckets.items() if bucket in _VALUE_BUCKETS)
    style = total and (growth + value)
    growth_ratio = float(growth / style) if style else 0.0
    value_ratio = float(value / style) if style else 0.0
    evidence = {
        "declared_style": declared,
        "buckets": {bucket: str(weight) for bucket, weight in sorted(buckets.items())},
        "growth_ratio": round(growth_ratio, 3),
        "value_ratio": round(value_ratio, 3),
    }
    if "价值" in declared and growth_ratio >= 0.5:
        return _finding(
            "style_drift",
            "warn",
            f"宣称价值风格，但成长类持仓占比达 {growth_ratio:.0%}，疑似风格漂移",
            evidence,
        )
    if "成长" in declared and value_ratio >= 0.6:
        return _finding(
            "style_drift",
            "warn",
            f"宣称成长风格，但价值类持仓占比达 {value_ratio:.0%}，疑似风格漂移",
            evidence,
        )
    return _finding("style_drift", "info", f"持仓风格与宣称（{declared}）基本一致", evidence)


def _check_fee(view: Mapping) -> dict:
    management = custody = None
    for fee in _field(view, "fees") or ():
        fee_type = str(_field(fee, "fee_type") or "")
        rate = _field(fee, "rate")
        try:
            rate = Decimal(str(rate)) if rate is not None else None
        except Exception:
            rate = None
        if rate is None:
            continue
        if "管理费" in fee_type and management is None:
            management = rate
        elif "托管费" in fee_type and custody is None:
            custody = rate
    if management is None or custody is None:
        return _finding("fee_level", "info", "数据不足：未获取到管理费/托管费", {})
    total = management + custody
    fund_type = str(_field(view, "fund_type") or "")
    ceiling = _DEFAULT_FEE_CEILING
    for keywords, limit in _FEE_CEILINGS:
        if any(keyword in fund_type for keyword in keywords):
            ceiling = limit
            break
    evidence = {
        "management_fee": str(management),
        "custody_fee": str(custody),
        "total": str(total),
        "ceiling": str(ceiling),
        "fund_type": fund_type,
    }
    if total > ceiling:
        return _finding(
            "fee_level",
            "warn",
            f"管理费+托管费合计 {total:.2%}，高于同类参考上限 {ceiling:.2%}",
            evidence,
        )
    return _finding(
        "fee_level", "info", f"管理费+托管费合计 {total:.2%}，处于同类正常水平", evidence
    )


def _detail_to_view(detail: object) -> dict:
    def seq(items, names):
        return [{name: _field(item, name) for name in names} for item in (items or ())]

    return {
        "latest_scale": _field(detail, "latest_scale"),
        "established_on": _field(detail, "established_on"),
        "fund_type": _field(detail, "fund_type"),
        "managers": seq(_field(detail, "managers"), ("name", "tenure_start")),
        "holdings": seq(_field(detail, "holdings"), ("name", "weight")),
        "fees": seq(_field(detail, "fees"), ("fee_type", "rate")),
    }


def _production_fetcher(code: str) -> dict | None:
    from public_operations import _detail_provider

    try:
        return _detail_to_view(_detail_provider().detail(code))
    except Exception:
        return None


def health_check(
    funds: Sequence[Mapping],
    *,
    fetcher=None,
    as_of: date | None = None,
) -> dict:
    """Core computation; ``fetcher(code)`` injectable for deterministic tests."""
    if not isinstance(funds, Sequence) or isinstance(funds, (str, bytes)) or not funds:
        raise ValueError("funds must be a non-empty list")
    today = as_of or date.today()
    fetch = fetcher or _production_fetcher
    reports = []
    for position, fund in enumerate(funds):
        if not isinstance(fund, Mapping):
            raise ValueError(f"fund #{position} must be a mapping")
        code = fund.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"fund #{position}: code must be a non-empty string")
        code = code.strip()
        name = fund.get("name")
        try:
            view = fetch(code) or {}
        except Exception:
            view = None
        if view is None:
            findings = [
                _finding(kind, "info", "数据不足：基金详情获取失败", {})
                for kind in ("scale_change", "manager_change", "style_drift", "fee_level")
            ]
        else:
            merged = {
                **view,
                "scale_history": fund.get("scale_history", view.get("scale_history")),
                "previous_managers": fund.get("previous_managers", view.get("previous_managers")),
                "declared_style": fund.get("declared_style", view.get("declared_style")),
            }
            if name is None:
                name = view.get("name")
            findings = [
                _check_scale(merged),
                _check_manager(merged, today),
                _check_style(merged),
                _check_fee(merged),
            ]
        report = {"code": code, "findings": findings}
        if name:
            report["name"] = name
        reports.append(report)
    return {"as_of": today.isoformat(), "reports": reports}


def execute_health(kind: str, payload: dict) -> dict:
    if kind != "fund.health_check":
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    return health_check(payload.get("funds", ()))
