"""Read original fixed holdings and immutable marks; C# owns aggregation and persistence."""

import hashlib
import json
from contextlib import ExitStack
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from analysis_context import analytical_context, site_directory
from attribution_shared import EvidenceMissing, _amount, _day, _text
from ledger_attribution import calculate_periods

from quant_assistant.data.snapshots import _frame_sha256

ATTRIBUTION_CAPABILITIES = ("analysis.brinson",)


def _original(tables, start, end, *, ledger=False):
    baselines = [
        row
        for row in tables["portfolio_events"]
        if row["event_type"] == "baseline" and row["status"] == "confirmed"
    ]
    if len(baselines) != 1 or _day(baselines[0]["effective_date"]) > start:
        raise EvidenceMissing("需要在开始日期或之前唯一已确认的账户基线。")
    baseline = baselines[0]
    audits = [
        row
        for row in tables["audit_log"]
        if row["entity_type"] == "portfolio_account_import"
        and row["entity_id"] == baseline["event_id"]
    ]
    if len(audits) != 1:
        raise EvidenceMissing("账户基线缺少唯一完整原始持仓审计，不能用当前持仓替代。")
    original = audits[0]["after_json"]
    if isinstance(original, str):
        original = json.loads(original)
    positions = original.get("positions", []) if isinstance(original, dict) else []
    if not 1 <= len(positions) <= 101 or len({row["symbol"] for row in positions}) != len(
        positions
    ):
        raise EvidenceMissing("原始基线持仓不完整或标的重复。")
    cash = [row for row in positions if row["asset_type"] == "cash"]
    if (
        len(cash) != 1
        or _amount(cash[0]["quantity"]) != _amount(cash[0]["market_value"])
        or _amount(cash[0]["average_cost"]) != 1
    ):
        raise EvidenceMissing("原始基线需要完整且一致的单一现金持仓。")
    for row in positions:
        if (
            _day(row["as_of_date"]) != _day(baseline["effective_date"])
            or _amount(row["quantity"]) < 0
            or _amount(row["market_value"]) < 0
        ):
            raise EvidenceMissing("原始基线持仓日期或金额不一致。")
    for table, field, label in (
        ("transactions", "traded_at", "交易"),
        ("dividend_events", "paid_at", "分红"),
        ("corporate_action_events", "effective_at", "公司行动"),
        ("ledger_corrections", "effective_at", "冲正"),
    ):
        if not ledger and any(
            _day(baseline["effective_date"]) <= _day(row[field]) <= end for row in tables[table]
        ):
            raise EvidenceMissing(
                f"基线至结束日期存在{label}，需要动态持仓或多期归因；不能使用固定持仓模型。"
            )
    if any(
        row["event_id"] != baseline["event_id"]
        and row["status"] != "void"
        and (
            not ledger
            or row["status"] != "confirmed"
            or row["event_type"] not in {"deposit", "withdrawal"}
        )
        and _day(baseline["effective_date"]) <= _day(row["effective_date"]) <= end
        for row in tables["portfolio_events"]
    ):
        raise EvidenceMissing("基线后存在资金流、校准或待确认事件，需要动态持仓归因。")
    if any(
        row["entity_type"] == "position"
        and _day(baseline["effective_date"]) <= _day(row["changed_at"]) <= end
        for row in tables["audit_log"]
    ):
        raise EvidenceMissing("区间内有手工持仓覆盖，需要核对历史持仓后再归因。")
    return baseline, positions


class Marks:
    def __init__(self, connection, directory, tables):
        self.connection = connection
        self.directory = directory
        self.metadata = {row["id"]: row for row in tables["data_snapshots"]}
        self.frames = {}
        self.files = []
        self.sources = []
        self.history_used = False
        self.fund_disclosures = []

    def classification(self, symbol, start):
        rows = self.connection.execute(
            """SELECT industry,as_of_date,'history' FROM instrument_history
               WHERE symbol=? AND asset_type='stock' AND as_of_date<=?
                 AND industry IS NOT NULL AND trim(industry)<>''
               UNION ALL SELECT industry,as_of_date,'account' FROM instruments
               WHERE symbol=? AND asset_type='stock' AND as_of_date<=?
                 AND industry IS NOT NULL AND trim(industry)<>''
               ORDER BY as_of_date DESC""",
            [symbol, start, symbol, start],
        ).fetchall()
        if not rows or not rows[0][0] or not str(rows[0][0]).strip():
            raise EvidenceMissing(f"{symbol}缺少{start}或之前的行业分类。")
        latest = [row for row in rows if row[1] == rows[0][1]]
        if len({row[0] for row in latest}) != 1:
            raise EvidenceMissing(f"{symbol}在同一日期存在冲突的行业分类。")
        self.history_used |= any(row[2] == "history" for row in latest)
        return str(rows[0][0]).strip(), str(rows[0][1])

    @staticmethod
    def _price_table(asset):
        if asset == "fund":
            return "fund_nav", "nav_date", "unit_nav"
        if asset == "stock":
            return "daily_prices", "trade_date", "close"
        raise EvidenceMissing(f"不支持的价格资产类型{asset}。")

    def _validate_price_source(self, symbol, day, asset, row):
        """Validate one located price row; return (source, table, field, amount)."""
        table, field, amount = self._price_table(asset)
        if row is None or row[1] not in self.metadata:
            raise EvidenceMissing(f"{symbol}缺少{day}的已验证收盘价格；不会使用上一日价格替代。")
        source = self.metadata[row[1]]
        issues = source.get("issues_json", [])
        if isinstance(issues, str):
            issues = json.loads(issues)
        safe_symbol = symbol.replace(".", "_").replace("-", "_")
        datasets = (
            {"fund_nav", f"fund_history_{safe_symbol}"}
            if asset == "fund"
            else {"daily_prices", f"stock_history_{safe_symbol}"}
        )
        if source["dataset"] not in datasets or source["status"] != "valid" or issues:
            raise EvidenceMissing(f"{symbol}在{day}的价格来源尚未通过校验。")
        return source, table, field, amount

    def _source_frame(self, source, symbol, table, field, amount):
        """Load, integrity-check and cache the immutable snapshot frame."""
        if source["id"] in self.frames:
            return self.frames[source["id"]]
        path = Path(source["path"])
        if (
            not path.is_absolute()
            or path.resolve() != path
            or not path.is_relative_to(self.directory)
            or path.is_symlink()
        ):
            raise EvidenceMissing(f"{symbol}的价格文件不在当前账号的真实目录内。")
        if not path.is_file() or path.stat().st_size > 50_000_000:
            raise EvidenceMissing(f"{symbol}的价格文件缺失或超过单次读取上限。")
        try:
            with path.open("rb") as stream:
                file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            frame = pd.read_parquet(path)
            if (
                len(frame) != source["row_count"]
                or len(frame) > 250_000
                or _frame_sha256(frame) != source["sha256"]
            ):
                raise EvidenceMissing(f"{symbol}的价格文件内容校验失败。")
            if not {"symbol", field, amount}.issubset(frame.columns):
                raise EvidenceMissing(f"{symbol}的价格文件缺少必需字段。")
        except (OSError, ValueError) as failure:
            if isinstance(failure, EvidenceMissing):
                raise
            raise EvidenceMissing(f"{symbol}的价格文件无法读取或校验。") from failure
        self.frames[source["id"]] = frame
        self.files.append({"path": str(path), "sha256": file_hash})
        self.sources.append(
            {
                key: source[key]
                for key in ("id", "dataset", "source", "as_of_date", "sha256", "row_count")
            }
        )
        return frame

    def price(self, symbol, day, *, asset="stock"):
        table, field, amount = self._price_table(asset)
        row = self.connection.execute(
            f"SELECT {amount},source_snapshot_id FROM {table} WHERE symbol=? AND {field}=?",
            [symbol, day],
        ).fetchone()
        source, table, field, amount = self._validate_price_source(symbol, day, asset, row)
        frame = self._source_frame(source, symbol, table, field, amount)
        points = frame.loc[
            frame["symbol"].astype(str).eq(symbol) & pd.to_datetime(frame[field]).dt.date.eq(day)
        ]
        price = _amount(row[0])
        if len(points) != 1 or price <= 0 or price != _amount(points.iloc[0][amount]):
            raise EvidenceMissing(f"{symbol}在{day}的价格与原始快照不一致。")
        return price, source["id"]

    def prefetch_prices(self, items):
        """Batch-warm price evidence for ``(symbol, day, asset)`` triples.

        One SQL round-trip per asset table warms the snapshot-frame cache, so
        later price() calls hit memory. Only caches are warmed: every
        validation still runs inside price(), which keeps raising
        EvidenceMissing for invalid points. Triples that fail validation are
        skipped here; price() will report them when actually requested.
        """
        grouped = {}
        for symbol, day, asset in dict.fromkeys(items):
            if asset not in {"stock", "fund"}:
                continue
            grouped.setdefault(self._price_table(asset), []).append((symbol, day, asset))
        for (table, field, _amount_col), triples in grouped.items():
            placeholders = ",".join(["(?, ?)"] * len(triples))
            try:
                rows = self.connection.execute(
                    f"SELECT symbol, {field}, source_snapshot_id FROM {table} "
                    f"WHERE (symbol, {field}) IN ({placeholders})",
                    [value for symbol, day, _asset in triples for value in (symbol, day)],
                ).fetchall()
            except Exception:
                continue
            asset = "fund" if table == "fund_nav" else "stock"
            for symbol, day, snapshot_id in rows:
                try:
                    source, _t, _f, _a = self._validate_price_source(
                        symbol, day, asset, (None, snapshot_id)
                    )
                    self._source_frame(source, symbol, table, field, _amount_col)
                except EvidenceMissing:
                    continue

    def fund_breakdown(self, holding, start):
        symbol = holding["symbol"]
        rows = self.connection.execute(
            "SELECT report_date,holding_symbol,asset_type,weight,source,observed_at "
            "FROM fund_holding_history WHERE symbol=? AND report_date<=? "
            "ORDER BY report_date DESC,holding_symbol",
            [symbol, start],
        ).fetchall()
        available = [
            row
            for row in rows
            if pd.Timestamp(row[5]).tzinfo is not None
            and pd.Timestamp(row[5]).tz_convert("Asia/Shanghai").date() <= start
        ]
        if not available:
            raise EvidenceMissing(
                f"{symbol}缺少期初已观察到的历史披露持仓；不会使用后来披露的季度持仓。"
            )
        selected = [row for row in available if row[0] == available[0][0]]
        if (
            not 1 <= len(selected) <= 100
            or len({row[1] for row in selected}) != len(selected)
            or (start - selected[0][0]).days > 180
        ):
            raise EvidenceMissing(f"{symbol}的披露持仓重复、过期或超出支持范围。")
        weights = {}
        for _report, target, asset, weight, source, _observed in selected:
            value = _amount(weight) / 100
            if value < 0 or value > 1 or asset not in {"stock", "bond"} or not source:
                raise EvidenceMissing(f"{symbol}的披露权重或来源无效。")
            sector = "债券" if asset == "bond" else self.classification(target, start)[0]
            weights[sector] = weights.get(sector, Decimal(0)) + value
        coverage = sum(weights.values(), Decimal(0))
        if coverage <= 0 or coverage > 1:
            raise EvidenceMissing(f"{symbol}的披露覆盖比例无效。")
        if coverage < 1:
            weights["基金未披露"] = 1 - coverage
        self.history_used = True
        self.fund_disclosures.append(
            {
                "symbol": symbol,
                "period_start": str(start),
                "report_date": str(selected[0][0]),
                "coverage": _text(coverage),
                "holdings": [
                    {
                        "symbol": row[1],
                        "asset_type": row[2],
                        "weight_percent": _text(_amount(row[3])),
                        "source": row[4],
                        "observed_at": str(row[5]),
                    }
                    for row in selected
                ],
            }
        )
        return [
            {
                **holding,
                "symbol": f"{symbol}:{index}",
                "sector_name": sector,
                "initial_value": _text(_amount(holding["initial_value"]) * weight),
            }
            for index, (sector, weight) in enumerate(sorted(weights.items()))
            if weight > 0
        ]

    def holding(self, symbol, asset, value, start, end, benchmark=False):
        value = _amount(value)
        if value <= 0 or value >= Decimal("1e20"):
            raise EvidenceMissing(f"{symbol}的期初金额或数量无效。")
        if asset == "cash":
            return {
                "symbol": symbol,
                "sector_name": "现金",
                "initial_value": _text(value),
                "period_return": "0",
            }
        if asset not in {"stock", "fund"}:
            raise EvidenceMissing(
                f"{symbol}需要基金穿透或其他资产模型，当前自动流程只支持股票与现金。"
            )
        sector, industry_date = (
            ("基金", None) if asset == "fund" else self.classification(symbol, start)
        )
        first, first_source = self.price(symbol, start, asset=asset)
        last, last_source = self.price(symbol, end, asset=asset)
        period_return = last / first - 1
        if period_return > Decimal("1e6"):
            raise EvidenceMissing(f"{symbol}的区间收益超出支持范围。")
        initial = value if benchmark else value * first
        if initial >= Decimal("1e20"):
            raise EvidenceMissing(f"{symbol}的期初金额超出支持范围。")
        return {
            "symbol": symbol,
            "sector_name": sector,
            "initial_value": _text(initial),
            "period_return": _text(period_return),
            "classification_date": industry_date,
            "start_price": _text(first),
            "end_price": _text(last),
            "start_source_id": first_source,
            "end_source_id": last_source,
        }


def execute_attribution(_kind, payload):
    start, end = _day(payload["start_date"]), _day(payload["end_date"])
    if start > end:
        raise ValueError("invalid_attribution_dates")
    try:
        directory = site_directory(payload["snapshot"])
        cache = directory / "quant_assistant.duckdb"
        cache_hash = None
        if cache.is_file():
            wal = Path(str(cache) + ".wal")
            if wal.exists() and wal.stat().st_size > 0:
                raise ValueError("uncheckpointed_analytical_cache")
            if cache.resolve() != cache or cache.is_symlink() or cache.stat().st_size > 512_000_000:
                raise ValueError("invalid_analytical_cache")
            with cache.open("rb") as stream:
                cache_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        # Entered inside the try so build_context input errors keep mapping to
        # the integrity blocker; the ExitStack is closed in the finally below.
        # (A plain `with` here would also catch main-logic ValueErrors that the
        # old code deliberately let propagate as invalid_request.)
        stack = ExitStack()
        database, _queries, directory = stack.enter_context(
            analytical_context(payload["snapshot"])
        )
    except (OSError, ValueError):
        return {"status": "blocked", "blockers": ["账号分析资料或快照文件不能通过完整性校验。"]}
    try:
        tables = payload["snapshot"]["tables"]
        try:
            ledger = payload.get("ledger", False)
            baseline, positions = _original(tables, start, end, ledger=ledger)
            benchmark = next(
                (
                    row
                    for row in tables["benchmark_configs"]
                    if row["benchmark_id"] == payload["benchmark_id"]
                ),
                None,
            )
            if benchmark is None:
                raise EvidenceMissing("当前账号未保存该自定义基准；不能推断指数历史成分。")
            components = benchmark["components_json"]
            if isinstance(components, str):
                components = json.loads(components)
            if (
                not 1 <= len(components) <= 20
                or any(_amount(value) <= 0 for value in components.values())
                or abs(sum((_amount(value) for value in components.values()), Decimal(0)) - 1)
                > Decimal("0.00000001")
            ):
                raise EvidenceMissing("自定义基准的成分权重不完整。")
            blockers, portfolio, benchmark_rows = [], [], []
            with database.connection() as connection:
                marks = Marks(connection, directory, tables)
                if ledger:
                    periods = calculate_periods(
                        tables, baseline, positions, components, start, end, marks
                    )
                for row in positions:
                    if ledger:
                        break
                    if _amount(row["quantity"]) == 0:
                        if _amount(row["market_value"]) != 0:
                            blockers.append(f"{row['symbol']}的原始数量与金额不一致。")
                        continue
                    try:
                        holding = marks.holding(
                            row["symbol"],
                            row["asset_type"],
                            row["market_value"] if row["asset_type"] == "cash" else row["quantity"],
                            start,
                            end,
                        )
                        portfolio.extend(
                            marks.fund_breakdown(holding, start)
                            if row["asset_type"] == "fund"
                            else [holding]
                        )
                    except EvidenceMissing as failure:
                        blockers.append(str(failure))
                for symbol, weight in components.items():
                    if ledger:
                        break
                    try:
                        benchmark_rows.append(
                            marks.holding(
                                symbol,
                                "cash" if symbol in {"CASH", "CASH.CNY"} else "stock",
                                weight,
                                start,
                                end,
                                benchmark=True,
                            )
                        )
                    except EvidenceMissing as failure:
                        blockers.append(str(failure))
            if not ledger and len(portfolio) > 121:
                blockers.append("归因行业拆分超过121项，请缩小组合范围。")
            if blockers or (not ledger and not portfolio):
                return {
                    "status": "blocked",
                    "blockers": list(dict.fromkeys(blockers)) or ["期初组合没有可用标的。"],
                }
            if marks.history_used:
                with cache.open("rb") as stream:
                    if cache_hash != hashlib.file_digest(stream, "sha256").hexdigest():
                        raise EvidenceMissing("计算期间历史行业缓存发生变化，请重新计算。")
                marks.files.append({"path": str(cache), "sha256": cache_hash})
            provenance = {
                "method": "ledger_close_periods_v1"
                if ledger
                else "static_holdings_price_return_v1",
                "baseline_id": baseline["event_id"],
                "baseline_date": baseline["effective_date"],
                "start_date": str(start),
                "end_date": str(end),
                "benchmark_id": benchmark["benchmark_id"],
                "benchmark_name": benchmark["name"],
                "benchmark_components": components,
                "source_snapshots": marks.sources,
                "fund_disclosures": marks.fund_disclosures,
                "portfolio": portfolio,
                "benchmark": benchmark_rows,
                "limitations": [
                    "固定持仓股票价格及基金净值收益归因，不包含账户分红、交易税费或动态再平衡。",
                    "基金按期初已观察到的披露行业权重分摊净值收益，未披露部分单列，不代表底层实际交易收益。",
                    "自定义基准按所选固定成分计算，不代表官方指数历史行业成分。",
                ],
            }
            if ledger:
                provenance["limitations"] = [
                    "按账本事件日分期，使用确切日期的收盘估值；外部现金流按期末发生中和，不等同于有日内估值的精确时间加权收益。",
                    "成交相对当日收盘的差额及费用计入原持仓贡献；期初无持仓标的的成交差额和分红归入现金资金贡献。",
                    "支持股票、基金、现金、成交、现金分红、已确认存取款、公司行动及按生效日恢复原数量与现金的冲正；手工校准须核对新基线。",
                    "基金净值收益及事件贡献按期初已经观察到的披露行业权重分摊；未披露部分单列，不代表底层股票真实交易收益。",
                    "每份最多366个事件日期区间；自定义基准各期按固定成分权重计算。",
                ]
                categories = {
                    "transactions": ("transaction", "traded_at", "id"),
                    "dividend_events": ("dividend", "paid_at", "id"),
                    "portfolio_events": ("external_cash_flow", "effective_date", "event_id"),
                    "corporate_action_events": ("corporate_action", "effective_at", "id"),
                    "ledger_corrections": ("ledger_correction", "effective_at", "id"),
                }
                frozen, identities = {}, {("portfolio_account_import", baseline["event_id"])}
                for name, (category, field, identity) in categories.items():
                    frozen[name] = [
                        row
                        for row in tables[name]
                        if _day(baseline["effective_date"]) <= _day(row[field]) <= end
                    ]
                    identities.update((category, str(row[identity])) for row in frozen[name])
                # Index by id once: the old `target not in frozen[name]` list scan
                # was O(n^2) over up to 20k events.
                targets_by_id = {
                    name: {str(row["id"]): row for row in tables[name]}
                    for name in ("transactions", "dividend_events")
                }
                frozen_ids = {
                    name: {str(row["id"]) for row in frozen[name]}
                    for name in targets_by_id
                }
                for correction in frozen["ledger_corrections"]:
                    name = (
                        "transactions"
                        if correction["target_type"] == "transaction"
                        else "dividend_events"
                    )
                    wanted = str(correction["target_id"])
                    target = targets_by_id[name].get(wanted)
                    if target is not None and wanted not in frozen_ids[name]:
                        frozen[name].append(target)
                        frozen_ids[name].add(wanted)
                        identities.add((correction["target_type"], wanted))
                frozen["audit_log"] = [
                    row
                    for row in tables["audit_log"]
                    if (row["entity_type"], str(row["entity_id"])) in identities
                ]
                if len(json.dumps(frozen, ensure_ascii=False).encode()) > 4_000_000:
                    raise EvidenceMissing("账本来源超过单次4MB保存上限，请缩小区间。")
                provenance["ledger_events"] = frozen
            return {
                "status": "ready",
                "blockers": [],
                "portfolio": portfolio,
                "benchmark": benchmark_rows,
                "provenance": provenance,
                "private_files": marks.files,
                **({"periods": periods} if ledger else {}),
            }
        except (EvidenceMissing, KeyError, TypeError, InvalidOperation) as failure:
            return {
                "status": "blocked",
                "blockers": [
                    str(failure)
                    if isinstance(failure, EvidenceMissing)
                    else "原始持仓或基准证据结构不完整。"
                ],
            }
    finally:
        stack.close()
