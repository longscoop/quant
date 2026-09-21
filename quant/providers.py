from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta
from math import isfinite
import os
import time

import requests

from .types import BenchmarkBar, FinancialRecord, IndustryRecord, PriceBar, PriceLimitRecord, RawRecord, Security, SecurityStatusRecord, TradingSuspensionRecord, ValuationBar


class DataProvider(ABC):
    @abstractmethod
    def fetch_securities(self) -> list[Security]: ...

    @abstractmethod
    def fetch_prices(self) -> list[PriceBar]: ...

    @abstractmethod
    def fetch_financials(self) -> list[FinancialRecord]: ...

    @abstractmethod
    def fetch_industries(self) -> list[IndustryRecord]: ...

    def fetch_valuations(self) -> list[ValuationBar]:
        return []


class FixtureProvider(DataProvider):
    """Small deterministic data set used by tests and `quant demo`."""
    def fetch_securities(self) -> list[Security]:
        return [
            Security("000001.SZ", "Alpha", date(2010, 1, 1), market_id="CN", currency="CNY"),
            Security("000002.SZ", "Beta", date(2011, 1, 1), market_id="CN", currency="CNY"),
            Security("000003.SZ", "Gamma ST", date(2012, 1, 1), is_st=True, market_id="CN", currency="CNY"),
        ]

    def fetch_prices(self) -> list[PriceBar]:
        dates = [date(2024, 2, 15), date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15), date(2024, 6, 15)]
        values = {"000001.SZ": [10, 11, 12, 13, 14], "000002.SZ": [10, 10.5, 10.8, 10.7, 11], "000003.SZ": [8, 8.2, 8.4, 8.1, 8.0]}
        return [PriceBar(code, day, close, volume=1000 + i * 50) for code, series in values.items() for i, (day, close) in enumerate(zip(dates, series))]

    def fetch_financials(self) -> list[FinancialRecord]:
        base = dict(report_period=date(2023, 12, 31), revenue=1000.0, roe=0.14, gross_margin=0.35, operating_cashflow=140.0, debt_ratio=0.4, data_version="pit_v1.0")
        return [
            FinancialRecord("000001.SZ", ann_date=date(2024, 4, 10), net_profit=100.0, **base),
            FinancialRecord("000001.SZ", ann_date=date(2024, 4, 25), net_profit=120.0, **base),
            FinancialRecord("000002.SZ", ann_date=date(2024, 4, 12), net_profit=80.0, **{**base, "revenue": 900.0, "roe": 0.11}),
            FinancialRecord("000003.SZ", ann_date=date(2024, 4, 20), net_profit=60.0, **base),
        ]

    def fetch_industries(self) -> list[IndustryRecord]:
        return [IndustryRecord("000001.SZ", "银行", date(2020, 1, 1)), IndustryRecord("000002.SZ", "银行", date(2020, 1, 1)), IndustryRecord("000003.SZ", "医药", date(2020, 1, 1))]

    def fetch_valuations(self) -> list[ValuationBar]:
        return [ValuationBar(code, day, 12.0 if code == "000001.SZ" else 9.0, 1.6, 1.2, .02, .5) for code in ("000001.SZ", "000002.SZ") for day in [date(2024, 2, 15), date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15), date(2024, 6, 15)]]

    def fetch_benchmark(self, ts_code: str = "000300.SH") -> list[BenchmarkBar]:
        return [BenchmarkBar(ts_code, day, close, close * .999) for day, close in zip([date(2024, 2, 15), date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15), date(2024, 6, 15)], [4000, 4050, 4100, 4200, 4300])]


class TushareProvider(DataProvider):
    """Tushare adapter restricted to an explicitly supplied security universe."""
    def __init__(self, token: str | None = None, start_date: date | None = None, end_date: date | None = None, *, client=None, universe: list[str] | None = None, progress=None, request_timeout: int = 20, request_interval: float = 0.35, windows=None):
        self.token = token or os.getenv("TUSHARE_TOKEN")
        if not self.token:
            raise ValueError("TUSHARE_TOKEN is required for TushareProvider")
        if client is not None:
            self.pro = client
        else:
            try:
                import tushare as ts
            except ImportError as exc:
                raise RuntimeError("Install project dependencies to use TushareProvider") from exc
            # Keep a bounded network timeout.  The library default is 30s and
            # a long-range per-security query can otherwise be retried for
            # several minutes while the UI appears frozen at one counter.
            self.pro = ts.pro_api(self.token, timeout=request_timeout)
        self.start_date = start_date or (date.today() - timedelta(days=35))
        self.end_date = end_date or date.today()
        self.universe = universe
        self.progress = progress
        self.request_timeout = request_timeout
        if request_interval < 0:
            raise ValueError("request_interval must be non-negative")
        self.request_interval = request_interval
        self._last_request_at: float | None = None
        self._raw_financial_records: dict[str, list[RawRecord]] = {}
        self._raw_market_records: dict[str, list[RawRecord]] = {}
        self._price_limit_records: dict[str, list[PriceLimitRecord]] = {}
        self._suspension_records: dict[str, list[TradingSuspensionRecord]] = {}
        self._raw_base_records: list[RawRecord] = []
        self._reference_records: list[RawRecord] | None = None
        self.errors: list[dict[str, str]] = []
        self.windows = windows or {}

    def _range(self, dataset: str) -> tuple[date, date]:
        window = self.windows.get(dataset)
        return (window.start, window.end) if window else (self.start_date, self.end_date)

    def _record_error(self, dataset: str, ts_code: str, exc: Exception) -> None:
        self.errors.append({"dataset": dataset, "ts_code": ts_code, "error": str(exc)})

    def _progress(self, phase: str, current: int, total: int, detail: str | None = None) -> None:
        if self.progress is not None:
            if detail is None:
                self.progress(phase, current, total)
                return
            try:
                self.progress(phase, current, total, detail)
            except TypeError:
                # Keep compatibility with callers written for the original
                # three-argument callback while allowing richer UI progress.
                self.progress(phase, current, total)

    def _request(self, method: str, **params):
        """Call Tushare with bounded retries for transient transport failures."""
        # Index membership/bootstrap calls are small and safe to retry three
        # times.  Long-range data calls are deliberately limited to one retry;
        # replaying a multi-year response three times is what made a sync look
        # permanently stuck for hours.
        attempts = 3 if method in {"index_weight", "stock_basic"} else 2
        for attempt in range(attempts):
            try:
                if self._last_request_at is not None:
                    remaining = self.request_interval - (time.monotonic() - self._last_request_at)
                    if remaining > 0:
                        time.sleep(remaining)
                self._last_request_at = time.monotonic()
                return getattr(self.pro, method)(**params)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt == attempts - 1:
                    raise
                time.sleep(2**attempt)

    def index_weight(self, **params):
        return self._request("index_weight", **params)

    def fetch_securities(self) -> list[Security]:
        """Load all relevant listing states so historical members remain resolvable."""
        fields = "ts_code,name,list_date,list_status"
        records: dict[str, Security] = {}
        for status in ("L", "D", "P"):
            frame = self._request("stock_basic", exchange="", list_status=status, fields=fields)
            for row in frame.itertuples():
                listed = getattr(row, "list_date", None)
                if not listed:
                    continue
                records[row.ts_code] = Security(row.ts_code, row.name, date.fromisoformat(listed), is_st=self._is_st_name(row.name), market_id="CN", currency="CNY")
                self._raw_base_records.append(RawRecord("stock_basic", row.ts_code, self._source_payload(row)))
        return [record for code, record in sorted(records.items()) if not self.universe or code in self.universe]

    @staticmethod
    def _is_st_name(name: str | None) -> bool:
        normalized = str(name or "").strip().upper()
        return normalized.startswith(("ST", "*ST", "S*ST", "SST"))

    @staticmethod
    def _parse_source_date(value) -> date | None:
        if not value:
            return None
        value = str(value)
        try:
            return date.fromisoformat(value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:]}")
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _source_payload(row) -> dict:
        values = row._asdict() if hasattr(row, "_asdict") else vars(row)
        return {key: value for key, value in values.items() if key != "Index"}

    def _raw_financial_frame(self, dataset: str, code: str, frame) -> list[RawRecord]:
        records = []
        for row in frame.itertuples():
            payload = self._source_payload(row)
            records.append(RawRecord(
                dataset=dataset,
                ts_code=getattr(row, "ts_code", code),
                payload=payload,
                report_period=self._parse_source_date(getattr(row, "end_date", None)),
                ann_date=self._parse_source_date(getattr(row, "ann_date", None)),
                first_ann_date=self._parse_source_date(getattr(row, "f_ann_date", None)),
                version=str(getattr(row, "update_flag", "")) or None,
            ))
        return records

    def raw_financial_records_for(self, code: str) -> list[RawRecord]:
        return list(self._raw_financial_records.get(code, ()))

    def raw_market_records_for(self, code: str) -> list[RawRecord]:
        return list(self._raw_market_records.get(code, ()))

    def price_limit_records_for(self, code: str) -> list[PriceLimitRecord]:
        return list(self._price_limit_records.get(code, ()))

    def suspension_records_for(self, code: str) -> list[TradingSuspensionRecord]:
        return list(self._suspension_records.get(code, ()))

    def all_price_limit_records(self) -> list[PriceLimitRecord]:
        return [row for rows in self._price_limit_records.values() for row in rows]

    def all_suspension_records(self) -> list[TradingSuspensionRecord]:
        return [row for rows in self._suspension_records.values() for row in rows]

    def raw_base_records(self) -> list[RawRecord]:
        return list(self._raw_base_records)

    def fetch_reference_records(self) -> list[RawRecord]:
        """Collect slowly-changing company and calendar data for the configured pool."""
        if self._reference_records is not None:
            return list(self._reference_records)
        records: list[RawRecord] = []
        start, end = self.start_date.strftime("%Y%m%d"), self.end_date.strftime("%Y%m%d")
        if hasattr(self.pro, "stock_company"):
            for exchange in ("SSE", "SZSE"):
                for row in self._request("stock_company", exchange=exchange).itertuples():
                    if not self.universe or getattr(row, "ts_code", None) in self.universe:
                        records.append(RawRecord("stock_company", getattr(row, "ts_code", None), self._source_payload(row)))
        if hasattr(self.pro, "namechange"):
            for code in self._codes():
                for row in self._request("namechange", ts_code=code, start_date=start, end_date=end).itertuples():
                    records.append(RawRecord("namechange", code, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "start_date", None))))
        if hasattr(self.pro, "trade_cal"):
            for exchange in ("SSE", "SZSE"):
                for row in self._request("trade_cal", exchange=exchange, start_date=start, end_date=end).itertuples():
                    records.append(RawRecord("trade_cal", exchange, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "cal_date", None))))
        self._reference_records = list(records)
        return records

    def fetch_security_statuses(self) -> list[SecurityStatusRecord]:
        records = []
        for raw in self.fetch_reference_records():
            if raw.dataset != "namechange" or not raw.ts_code:
                continue
            start = self._parse_source_date(raw.payload.get("start_date")) or raw.report_period
            if start is None:
                continue
            end = self._parse_source_date(raw.payload.get("end_date"))
            name = str(raw.payload.get("name") or "")
            records.append(
                SecurityStatusRecord(
                    raw.ts_code,
                    start,
                    end,
                    is_st=self._is_st_name(name),
                    status_name=name or None,
                )
            )
        return records

    def fetch_trade_calendar(self, day: date, exchange: str = "SSE") -> list[RawRecord]:
        """Fetch one authoritative calendar date before the scheduler decides to run."""
        if not hasattr(self.pro, "trade_cal"):
            return []
        value = day.strftime("%Y%m%d")
        return [
            RawRecord("trade_cal", exchange, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "cal_date", None)))
            for row in self._request("trade_cal", exchange=exchange, start_date=value, end_date=value).itertuples()
        ]

    def _codes(self) -> list[str]:
        return self.universe or [security.ts_code for security in self.fetch_securities()]

    def iter_price_batches(self, skip_codes=None):
        range_start, range_end = self._range("market")
        start, end = range_start.strftime("%Y%m%d"), range_end.strftime("%Y%m%d")
        # Tushare validates the security/date query shape differently by account
        # level; requesting per security avoids the missing-ts_code failure and
        # works for non-VIP accounts.
        daily_rows, factor_rows = [], []
        codes = self._codes()
        skip_codes = set(skip_codes or ())
        for index, code in enumerate(codes, 1):
            if code in skip_codes:
                self._progress("行情与复权", index, len(codes), f"{code}：已入库，跳过")
                continue
            self._progress("行情与复权", index - 1, len(codes), f"{code}：正在请求 daily")
            try:
                daily_frame = self._request("daily", ts_code=code, start_date=start, end_date=end)
                factor_frame = None
                daily_rows = list(daily_frame.itertuples())
                self._progress("行情与复权", index - 1, len(codes), f"{code}：daily 完成，正在请求 adj_factor")
                factor_frame = self._request("adj_factor", ts_code=code, start_date=start, end_date=end)
                factor_rows = list(factor_frame.itertuples())
            except Exception as exc:
                self._record_error("market", code, exc)
                self._progress("行情与复权", index, len(codes), f"{code}：失败（{exc}）")
                continue
            factor_map = {(r.ts_code, r.trade_date): float(r.adj_factor) for r in factor_rows}
            limit_rows, suspension_rows = [], []
            if hasattr(self.pro, "stk_limit"):
                try:
                    limit_rows = list(self._request("stk_limit", ts_code=code, start_date=start, end_date=end).itertuples())
                except Exception as exc:
                    self._record_error("price_limits", code, exc)
            if hasattr(self.pro, "suspend_d"):
                try:
                    suspension_rows = list(self._request("suspend_d", ts_code=code, start_date=start, end_date=end).itertuples())
                except Exception as exc:
                    self._record_error("trading_suspensions", code, exc)

            def optional_float(value):
                try:
                    return float(value) if value is not None and isfinite(float(value)) else None
                except (TypeError, ValueError):
                    return None

            self._price_limit_records[code] = [
                PriceLimitRecord(
                    code,
                    self._parse_source_date(getattr(row, "trade_date", None)),
                    optional_float(getattr(row, "up_limit", None)),
                    optional_float(getattr(row, "down_limit", None)),
                )
                for row in limit_rows
                if self._parse_source_date(getattr(row, "trade_date", None)) is not None
            ]
            self._suspension_records[code] = [
                TradingSuspensionRecord(
                    code,
                    self._parse_source_date(getattr(row, "suspend_date", None)),
                    self._parse_source_date(getattr(row, "resume_date", None)),
                    getattr(row, "suspend_reason", None) or getattr(row, "reason", None),
                )
                for row in suspension_rows
                if self._parse_source_date(getattr(row, "suspend_date", None)) is not None
            ]
            limit_map = {row.trade_date: row for row in self._price_limit_records[code]}
            self._raw_market_records[code] = [
                RawRecord("daily", code, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "trade_date", None)))
                for row in daily_rows
            ] + [
                RawRecord("adj_factor", code, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "trade_date", None)))
                for row in factor_rows
            ] + [
                RawRecord("stk_limit", code, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "trade_date", None)))
                for row in limit_rows
            ] + [
                RawRecord("suspend_d", code, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "suspend_date", None)))
                for row in suspension_rows
            ]
            batch = []
            for r in daily_rows:
                trade_day = date.fromisoformat(f"{r.trade_date[:4]}-{r.trade_date[4:6]}-{r.trade_date[6:]}")
                open_price = float(r.open)
                limit = limit_map.get(trade_day)
                batch.append(
                    PriceBar(
                        r.ts_code,
                        trade_day,
                        float(r.close),
                        factor_map.get((r.ts_code, r.trade_date), 1.0),
                        limit_up=bool(limit and limit.up_limit is not None and open_price >= float(limit.up_limit) - 1e-9),
                        limit_down=bool(limit and limit.down_limit is not None and open_price <= float(limit.down_limit) + 1e-9),
                        volume=float(getattr(r, "vol", 0.0)),
                        open=open_price,
                        high=float(r.high),
                        low=float(r.low),
                    )
                )
            self._progress("行情与复权", index, len(codes), f"{code}：完成（{len(batch)} 条）")
            yield code, batch

    def iter_prices(self):
        for _, batch in self.iter_price_batches():
            yield batch

    def fetch_prices(self) -> list[PriceBar]:
        return [row for batch in self.iter_prices() for row in batch]

    def iter_financial_batches(self, skip_codes=None):
        def parse(value):
            try:
                number = float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
            return number if number is not None and isfinite(number) else None
        codes = self._codes()
        range_start, range_end = self._range("financial")
        start, end = range_start.strftime("%Y%m%d"), range_end.strftime("%Y%m%d")
        skip_codes = set(skip_codes or ())
        for index, code in enumerate(codes, 1):
            if code in skip_codes:
                self._progress("财务报表", index, len(codes), f"{code}：已入库，跳过")
                continue
            self._progress("财务报表", index - 1, len(codes), f"{code}：正在请求财务报表")
            try:
                frame = self._request("fina_indicator", ts_code=code, start_date=start, end_date=end)
                frames = {"fina_indicator": frame}
                for dataset, method in (("income", "income"), ("balancesheet", "balancesheet"), ("cashflow", "cashflow")):
                    if hasattr(self.pro, method):
                        frames[dataset] = self._request(method, ts_code=code, start_date=start, end_date=end)
                self._raw_financial_records[code] = [record for dataset, source in frames.items() for record in self._raw_financial_frame(dataset, code, source)]
                income_rows = frames["income"].itertuples() if "income" in frames else []
                cashflow_rows = frames["cashflow"].itertuples() if "cashflow" in frames else []
                income = {(getattr(row, "end_date", None), getattr(row, "ann_date", None)): row for row in income_rows}
                cashflows = {(getattr(row, "end_date", None), getattr(row, "ann_date", None)): row for row in cashflow_rows}
                result = []
                for r in frame.itertuples():
                    if not getattr(r, "ann_date", None) or not getattr(r, "end_date", None): continue
                    report, announced = getattr(r, "end_date"), getattr(r, "ann_date")
                    income_row, cashflow_row = income.get((report, announced)), cashflows.get((report, announced))
                    percent = lambda value: None if value is None else value / 100
                    ann_date = date.fromisoformat(f"{announced[:4]}-{announced[4:6]}-{announced[6:]}")
                    first_ann = self._parse_source_date(getattr(r, "f_ann_date", None)) or ann_date
                    result.append(
                        FinancialRecord(
                            r.ts_code,
                            date.fromisoformat(f"{report[:4]}-{report[4:6]}-{report[6:]}"),
                            ann_date,
                            parse(getattr(income_row, "total_revenue", getattr(r, "total_revenue", None))),
                            parse(getattr(income_row, "n_income_attr_p", getattr(r, "n_income", None))),
                            percent(parse(getattr(r, "roe", None))),
                            percent(parse(getattr(r, "grossprofit_margin", None))),
                            parse(getattr(cashflow_row, "n_cashflow_act", None)),
                            percent(parse(getattr(r, "debt_to_assets", None))),
                            roic=percent(parse(getattr(r, "roic", None))),
                            current_ratio=parse(getattr(r, "current_ratio", None)),
                            free_cashflow=parse(getattr(cashflow_row, "free_cashflow", None)),
                            deduct_net_profit=parse(getattr(r, "profit_dedt", None)),
                            data_version="pit_v1.0",
                            first_ann_date=first_ann,
                            available_at=ann_date,
                            source_version=str(getattr(r, "update_flag", "")) or None,
                        )
                    )
            except Exception as exc:
                self._record_error("financial", code, exc)
                self._progress("财务报表", index, len(codes), f"{code}：失败（{exc}）")
                continue
            self._progress("财务报表", index, len(codes), f"{code}：完成（{len(result)} 条）")
            yield code, result

    def iter_financials(self):
        for _, batch in self.iter_financial_batches():
            yield batch

    def fetch_financials(self) -> list[FinancialRecord]:
        return [row for batch in self.iter_financials() for row in batch]

    def iter_valuation_batches(self, skip_codes=None):
        range_start, range_end = self._range("market")
        start, end = range_start.strftime("%Y%m%d"), range_end.strftime("%Y%m%d")
        codes = self._codes()
        skip_codes = set(skip_codes or ())
        for index, code in enumerate(codes, 1):
            if code in skip_codes:
                self._progress("每日估值", index, len(codes), f"{code}：已入库，跳过")
                continue
            self._progress("每日估值", index - 1, len(codes), f"{code}：正在请求每日估值")
            result = []
            try:
                valuation_frame = self._request("daily_basic", ts_code=code, start_date=start, end_date=end)
                valuation_rows = list(valuation_frame.itertuples())
                self._raw_market_records.setdefault(code, []).extend(
                    RawRecord("daily_basic", code, self._source_payload(row), report_period=self._parse_source_date(getattr(row, "trade_date", None)))
                    for row in valuation_rows
                )
                for row in valuation_rows:
                    trade_date = getattr(row, "trade_date")
                    def value(name):
                        raw = getattr(row, name, None)
                        try: return float(raw) if raw is not None and isfinite(float(raw)) else None
                        except (TypeError, ValueError): return None
                    result.append(ValuationBar(code, date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"), value("pe_ttm"), value("pb"), value("ps_ttm"), value("dv_ttm"), value("turnover_rate")))
            except Exception as exc:
                self._record_error("valuation", code, exc)
                self._progress("每日估值", index, len(codes), f"{code}：失败（{exc}）")
                continue
            self._progress("每日估值", index, len(codes), f"{code}：完成（{len(result)} 条）")
            yield code, result

    def iter_valuations(self):
        for _, batch in self.iter_valuation_batches():
            yield batch

    def fetch_valuations(self) -> list[ValuationBar]:
        return [row for batch in self.iter_valuations() for row in batch]

    def fetch_industries(self) -> list[IndustryRecord]:
        frame = self._request("stock_basic", exchange="", list_status="L", fields="ts_code,industry")
        allowed = set(self._codes())
        return [IndustryRecord(r.ts_code, getattr(r, "industry", "UNKNOWN") or "UNKNOWN", self.end_date) for r in frame.itertuples() if r.ts_code in allowed]

    def fetch_benchmark(self, ts_code: str = "000300.SH") -> list[BenchmarkBar]:
        frame = self._request("index_daily", ts_code=ts_code, start_date=self.start_date.strftime("%Y%m%d"), end_date=self.end_date.strftime("%Y%m%d"))
        return [BenchmarkBar(row.ts_code, date.fromisoformat(f"{row.trade_date[:4]}-{row.trade_date[4:6]}-{row.trade_date[6:]}"), float(row.close), float(row.open) if getattr(row, "open", None) is not None else None) for row in frame.itertuples()]
