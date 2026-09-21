from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from math import isfinite
import re
from uuid import uuid4
from .providers import DataProvider
from .types import AuditRun, BenchmarkBar, FinancialRecord, IndustryRecord, PriceBar, PriceLimitRecord, RawRecord, Security, SecurityStatusRecord, TradabilityFact, TradingSuspensionRecord, ValuationBar


_REDACTED = "[已隐藏]"
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:tushare_)?(?:token|password|secret|dsn|database_url)(?:$|_)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(tushare_token|token|password|secret|dsn|database_dsn|database_url)"
    r"(\s*(?:(?:=|:)\s*)|[\t ]+)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
    re.IGNORECASE,
)
_DATABASE_URL_RE = re.compile(
    r"(?:postgresql(?:\+psycopg)?|postgres)://[^\s\"'<>()[\]{},;]+",
    re.IGNORECASE,
)


def sanitize_sensitive_text(value: str | None) -> str | None:
    """Hide database URLs and common secret-bearing text values."""
    if value is None:
        return None
    text = value
    text = _DATABASE_URL_RE.sub(_REDACTED, text)
    return _SENSITIVE_VALUE_RE.sub(r"\1\2" + _REDACTED, text)


def _canonical_json_storage_key(key) -> str:
    """Match JSON object-key coercion before collision handling."""
    if isinstance(key, str):
        return key
    if key is None:
        return "null"
    if key is True:
        return "true"
    if key is False:
        return "false"
    if isinstance(key, (int, float)):
        return json.dumps(key, default=str)
    return str(key)


def _sanitize_storage_key(key) -> str:
    key = _canonical_json_storage_key(key)
    if _SENSITIVE_KEY_RE.search(key):
        return _REDACTED
    return sanitize_sensitive_text(key)


def sanitize_for_storage(value):
    """Recursively remove secrets while preserving ordinary values and containers."""
    if isinstance(value, str):
        return sanitize_sensitive_text(value)
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            canonical_key = _canonical_json_storage_key(key)
            clean_key = _sanitize_storage_key(canonical_key)
            if isinstance(clean_key, str):
                collision = 2
                base_key = clean_key
                while clean_key in sanitized:
                    clean_key = f"{base_key}#{collision}"
                    collision += 1
            sanitized[clean_key] = (
                _REDACTED
                if _SENSITIVE_KEY_RE.search(canonical_key)
                else sanitize_for_storage(item)
            )
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_storage(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_storage(item) for item in value)
    return value


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS securities (ts_code text PRIMARY KEY, name text NOT NULL, list_date date NOT NULL, is_st boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS security_statuses (ts_code text NOT NULL, effective_from date NOT NULL, effective_to date, is_st boolean NOT NULL DEFAULT false, status_name text, PRIMARY KEY (ts_code, effective_from));
CREATE TABLE IF NOT EXISTS price_bars (ts_code text NOT NULL, trade_date date NOT NULL, close double precision NOT NULL, adj_factor double precision NOT NULL, suspended boolean NOT NULL, limit_up boolean NOT NULL, limit_down boolean NOT NULL, volume double precision NOT NULL, open double precision, high double precision, low double precision, PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS financials (ts_code text NOT NULL, report_period date NOT NULL, ann_date date NOT NULL, revenue double precision NOT NULL, net_profit double precision NOT NULL, roe double precision NOT NULL, gross_margin double precision NOT NULL, operating_cashflow double precision NOT NULL, debt_ratio double precision NOT NULL, first_ann_date date, available_at date, source_version text, PRIMARY KEY (ts_code, report_period, ann_date));
CREATE TABLE IF NOT EXISTS industries (ts_code text NOT NULL, industry text NOT NULL, effective_date date NOT NULL, effective_to date, PRIMARY KEY (ts_code, effective_date));
CREATE TABLE IF NOT EXISTS ingestion_audit (dataset text NOT NULL, source text NOT NULL, status text NOT NULL, row_count integer NOT NULL, created_at timestamptz NOT NULL, error text);
CREATE TABLE IF NOT EXISTS index_members (index_code text NOT NULL, ts_code text NOT NULL, effective_date date NOT NULL, PRIMARY KEY (index_code, ts_code, effective_date));
CREATE TABLE IF NOT EXISTS benchmark_bars (ts_code text NOT NULL, trade_date date NOT NULL, close double precision NOT NULL, open double precision, PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS research_runs (run_id uuid PRIMARY KEY, run_type text NOT NULL, status text NOT NULL, parameters jsonb NOT NULL DEFAULT '{}'::jsonb, payload jsonb NOT NULL DEFAULT '{}'::jsonb, error text, created_at timestamptz NOT NULL, completed_at timestamptz);
CREATE TABLE IF NOT EXISTS factor_snapshots (snapshot_id uuid PRIMARY KEY, as_of_date date NOT NULL, factor_version text NOT NULL, pit_version text NOT NULL, universe_version text NOT NULL, status text NOT NULL, coverage double precision, audit jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT NOW(), completed_at timestamptz, error text, UNIQUE (as_of_date, factor_version, pit_version, universe_version));
CREATE TABLE IF NOT EXISTS factor_snapshot_items (snapshot_id uuid NOT NULL REFERENCES factor_snapshots(snapshot_id) ON DELETE CASCADE, ts_code text NOT NULL, factors jsonb NOT NULL, availability jsonb NOT NULL, audit jsonb NOT NULL DEFAULT '{}'::jsonb, PRIMARY KEY (snapshot_id, ts_code));
CREATE TABLE IF NOT EXISTS valuation_bars (ts_code text NOT NULL, trade_date date NOT NULL, pe_ttm double precision, pb double precision, ps_ttm double precision, dividend_yield double precision, turnover_rate double precision, data_version text NOT NULL DEFAULT 'pit_v1.0', PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS sync_checkpoints (sync_key text NOT NULL, dataset text NOT NULL, ts_code text NOT NULL, completed_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (sync_key, dataset, ts_code));
CREATE TABLE IF NOT EXISTS source_records (dataset text NOT NULL, ts_code text, record_key text NOT NULL, report_period date, ann_date date, first_ann_date date, source_version text, payload jsonb NOT NULL, retrieved_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (dataset, record_key));
CREATE TABLE IF NOT EXISTS sync_state (universe text NOT NULL, dataset text NOT NULL, watermark date, status text NOT NULL, run_id uuid, updated_at timestamptz NOT NULL DEFAULT NOW(), error text, PRIMARY KEY (universe, dataset));
CREATE TABLE IF NOT EXISTS sync_units (run_id uuid NOT NULL, dataset text NOT NULL, ts_code text NOT NULL, status text NOT NULL, range_start date, range_end date, row_count integer NOT NULL DEFAULT 0, error text, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (run_id, dataset, ts_code));
CREATE TABLE IF NOT EXISTS data_quality_checks (check_id uuid PRIMARY KEY, universe text NOT NULL, report jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS price_limits (ts_code text NOT NULL, trade_date date NOT NULL, up_limit double precision, down_limit double precision, PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS trading_suspensions (ts_code text NOT NULL, suspend_date date NOT NULL, resume_date date, reason text, PRIMARY KEY (ts_code, suspend_date));
CREATE TABLE IF NOT EXISTS research_portfolios (portfolio_id text PRIMARY KEY, name text NOT NULL, initial_capital double precision NOT NULL DEFAULT 1000000, benchmark_code text NOT NULL DEFAULT '000300.SH', transaction_cost_bps double precision NOT NULL DEFAULT 5, status text NOT NULL DEFAULT 'ACTIVE', created_at timestamptz NOT NULL DEFAULT NOW(), updated_at timestamptz NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS research_portfolio_positions (portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id) ON DELETE CASCADE, ts_code text NOT NULL REFERENCES securities(ts_code) ON DELETE CASCADE, weight double precision NOT NULL, note text, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (portfolio_id, ts_code));
CREATE TABLE IF NOT EXISTS portfolio_target_revisions (revision_id uuid PRIMARY KEY, portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), revision_no integer NOT NULL, signal_date date NOT NULL, status text NOT NULL, note text, created_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE (portfolio_id, revision_no));
CREATE TABLE IF NOT EXISTS portfolio_target_items (revision_id uuid NOT NULL REFERENCES portfolio_target_revisions(revision_id) ON DELETE CASCADE, ts_code text NOT NULL REFERENCES securities(ts_code), target_weight double precision NOT NULL CHECK (target_weight >= 0 AND target_weight <= 1), PRIMARY KEY (revision_id, ts_code));
CREATE TABLE IF NOT EXISTS portfolio_rebalance_orders (order_id uuid PRIMARY KEY, portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), revision_id uuid NOT NULL REFERENCES portfolio_target_revisions(revision_id), ts_code text NOT NULL REFERENCES securities(ts_code), side text NOT NULL, target_weight double precision NOT NULL CHECK (target_weight >= 0 AND target_weight <= 1), target_quantity double precision, remaining_quantity double precision, planned_trade_date date, status text NOT NULL, reason text, created_at timestamptz NOT NULL DEFAULT NOW(), updated_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE (revision_id, ts_code, side));
CREATE TABLE IF NOT EXISTS portfolio_trades (trade_id uuid PRIMARY KEY, order_id uuid NOT NULL REFERENCES portfolio_rebalance_orders(order_id), portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), revision_id uuid NOT NULL REFERENCES portfolio_target_revisions(revision_id), ts_code text NOT NULL REFERENCES securities(ts_code), trade_date date NOT NULL, side text NOT NULL, quantity double precision NOT NULL CHECK (quantity > 0), price double precision NOT NULL CHECK (price > 0), gross_amount double precision NOT NULL, fee_bps double precision NOT NULL, fee_amount double precision NOT NULL, created_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE (order_id, trade_date));
CREATE TABLE IF NOT EXISTS portfolio_cash_flows (cash_flow_id uuid PRIMARY KEY, portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), flow_date date NOT NULL, flow_type text NOT NULL, amount double precision NOT NULL, reference_type text NOT NULL, reference_id text NOT NULL, note text, created_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE (portfolio_id, reference_type, reference_id));
CREATE TABLE IF NOT EXISTS portfolio_daily_nav (portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), valuation_date date NOT NULL, cash double precision NOT NULL, market_value double precision, total_value double precision, nav double precision, benchmark_nav double precision, status text NOT NULL, coverage double precision, reason text, calculation_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (portfolio_id, valuation_date));
CREATE TABLE IF NOT EXISTS market_calendar_sessions (calendar_id text NOT NULL, trade_date date NOT NULL, is_open boolean NOT NULL, source text NOT NULL, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (calendar_id, trade_date));
CREATE INDEX IF NOT EXISTS price_bars_code_date_idx ON price_bars (ts_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS security_statuses_code_effective_idx ON security_statuses (ts_code, effective_from DESC);
CREATE INDEX IF NOT EXISTS industries_code_effective_idx ON industries (ts_code, effective_date DESC);
CREATE INDEX IF NOT EXISTS source_records_dataset_code_idx ON source_records (dataset, ts_code);
"""


def _benchmark_rows_with_open(provider, ts_code: str) -> list[BenchmarkBar]:
    rows = list(provider.fetch_benchmark(ts_code))
    invalid_rows = [row for row in rows if row.ts_code != ts_code or row.open is None]
    if invalid_rows:
        raise ValueError(f"{ts_code} 基准数据包含代码不匹配或缺失开盘价，拒绝写入")
    return rows


class InMemoryStore:
    def __init__(self):
        self.securities: dict[str, Security] = {}
        self.security_statuses: dict[tuple[str, object], SecurityStatusRecord] = {}
        self.prices: dict[tuple[str, object], PriceBar] = {}
        self.financials: dict[tuple[str, object, object], FinancialRecord] = {}
        self.industries: dict[tuple[str, object], IndustryRecord] = {}
        self.index_members: dict[tuple[str, str, object], tuple[str, str, object]] = {}
        self.benchmarks: dict[tuple[str, object], BenchmarkBar] = {}
        self.valuations: dict[tuple[str, object], ValuationBar] = {}
        self.price_limits: dict[tuple[str, object], PriceLimitRecord] = {}
        self.trading_suspensions: dict[tuple[str, object], TradingSuspensionRecord] = {}
        self.audit: list[AuditRun] = []
        self.portfolio_positions: dict[tuple[str, str], dict] = {}
        self.factor_snapshots: dict[tuple[object, str, str, str, str | None, str | None, str | None, str | None], dict] = {}
        self.portfolios: dict[str, dict] = {}
        self.portfolio_target_revisions: dict[str, dict] = {}
        self.portfolio_target_items: dict[str, list[dict]] = {}
        self.portfolio_orders: dict[str, dict] = {}
        self.portfolio_trades: dict[str, dict] = {}
        self.portfolio_cash_flows: dict[str, dict] = {}
        self.portfolio_nav: dict[tuple[str, object], dict] = {}
        self.market_calendar: dict[tuple[str, object], dict] = {}

    def sync(self, provider: DataProvider) -> None:
        datasets = (("securities", provider.fetch_securities(), self.securities, lambda r: r.ts_code), ("prices", provider.fetch_prices(), self.prices, lambda r: (r.ts_code, r.trade_date)), ("financials", provider.fetch_financials(), self.financials, lambda r: (r.ts_code, r.report_period, r.ann_date)), ("industries", provider.fetch_industries(), self.industries, lambda r: (r.ts_code, r.effective_date)))
        for name, rows, target, key in datasets:
            for row in rows:
                target[key(row)] = row
            self.audit.append(AuditRun(name, provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "fetch_security_statuses"):
            rows = provider.fetch_security_statuses()
            for row in rows:
                self.security_statuses[(row.ts_code, row.effective_from)] = row
            self.audit.append(AuditRun("security_statuses", provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "fetch_valuations"):
            rows = provider.fetch_valuations()
            for row in rows:
                self.valuations[(row.ts_code, row.trade_date)] = row
            self.audit.append(AuditRun("valuations", provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "all_price_limit_records"):
            rows = provider.all_price_limit_records()
            for row in rows:
                self.price_limits[(row.ts_code, row.trade_date)] = row
            self.audit.append(AuditRun("price_limits", provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "all_suspension_records"):
            rows = provider.all_suspension_records()
            for row in rows:
                self.trading_suspensions[(row.ts_code, row.suspend_date)] = row
            self.audit.append(AuditRun("trading_suspensions", provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "fetch_benchmark"):
            rows = provider.fetch_benchmark()
            for row in rows:
                self.benchmarks[(row.ts_code, row.trade_date)] = row
            self.audit.append(AuditRun("benchmark", provider.__class__.__name__, "success", len(rows)))

    def sync_benchmark(self, provider, ts_code: str = "000300.SH") -> int:
        rows = _benchmark_rows_with_open(provider, ts_code)
        for row in rows:
            self.benchmarks[(row.ts_code, row.trade_date)] = row
        self.audit.append(AuditRun("benchmark", provider.__class__.__name__, "success", len(rows)))
        return len(rows)

    def counts(self) -> dict[str, int]:
        return {"securities": len(self.securities), "security_statuses": len(self.security_statuses), "prices": len(self.prices), "financials": len(self.financials), "industries": len(self.industries), "valuation_count": len(self.valuations), "benchmark_count": len(self.benchmarks)}

    def data_quality(self, universe: str = "hs300") -> dict:
        """Return actionable coverage gaps for a snapshot without mutating it."""
        latest = max((bar.trade_date for bar in self.prices.values()), default=None)
        codes = sorted(self.securities)
        priced = {code for code, day in self.prices if day == latest} if latest else set()
        financial_codes = {code for code, _, _ in self.financials}
        missing_prices = sorted(set(codes) - priced) if latest else codes
        missing_financials = sorted(set(codes) - financial_codes)
        return {
            "universe": universe,
            "security_count": len(codes),
            "latest_trade_date": latest,
            "latest_price_coverage": len(priced),
            "missing_latest_price_codes": missing_prices,
            "missing_financial_codes": missing_financials,
            "valuation_count": len(self.valuations),
            "is_complete": bool(codes and latest and not missing_prices and not missing_financials and self.valuations),
        }

    def prices_for(self, code: str):
        return sorted((p for (c, _), p in self.prices.items() if c == code), key=lambda p: p.trade_date)

    def financials_for(self, code: str):
        return [f for (c, _, _), f in self.financials.items() if c == code]

    def industry_for(self, code: str):
        return [i for (c, _), i in self.industries.items() if c == code]

    def security_status_for(self, code: str, as_of_date):
        records = [
            record
            for (record_code, _), record in self.security_statuses.items()
            if record_code == code
            and record.effective_from <= as_of_date
            and (record.effective_to is None or as_of_date <= record.effective_to)
        ]
        if records:
            return max(records, key=lambda record: record.effective_from)
        security = self.securities.get(code)
        if security is None:
            return None
        return SecurityStatusRecord(
            code,
            security.list_date,
            None,
            is_st=security.is_st,
            status_name="ST" if security.is_st else "NORMAL",
        )

    def price_limit_for(self, code: str, day):
        return self.price_limits.get((code, day))

    def suspension_for(self, code: str, day):
        records = [
            record
            for (record_code, _), record in self.trading_suspensions.items()
            if record_code == code
            and record.suspend_date <= day
            and (record.resume_date is None or day < record.resume_date)
        ]
        return max(records, key=lambda record: record.suspend_date) if records else None

    def tradability_fact(self, code: str, day, *, min_listing_days: int = 180) -> TradabilityFact:
        security = self.securities.get(code)
        if security is None:
            return TradabilityFact(code, day, 0, False, False, False, False, False, False, False, False, "unknown_security", "unknown_security")
        listing_days = (day - security.list_date).days
        status = self.security_status_for(code, day)
        is_st = bool(status.is_st if status is not None else security.is_st)
        bar = self.prices.get((code, day))
        has_price = bar is not None
        has_open = bool(bar is not None and bar.open is not None)
        suspension = self.suspension_for(code, day)
        suspended = bool((bar.suspended if bar is not None else False) or suspension is not None)
        limit = self.price_limit_for(code, day)
        execution_price = float(bar.open) if bar is not None and bar.open is not None else None
        limit_up = bool(bar.limit_up if bar is not None else False)
        limit_down = bool(bar.limit_down if bar is not None else False)
        if execution_price is not None and limit is not None:
            if limit.up_limit is not None:
                limit_up = limit_up or execution_price >= float(limit.up_limit) - 1e-9
            if limit.down_limit is not None:
                limit_down = limit_down or execution_price <= float(limit.down_limit) + 1e-9

        common_reason = None
        if is_st:
            common_reason = "st"
        elif listing_days < min_listing_days:
            common_reason = "new_listing"
        elif not has_price:
            common_reason = "missing_price"
        elif not has_open:
            common_reason = "missing_open"
        elif suspended:
            common_reason = "suspended"

        buy_reason = common_reason or ("limit_up" if limit_up else None)
        sell_reason = common_reason or ("limit_down" if limit_down else None)
        return TradabilityFact(
            code,
            day,
            listing_days,
            is_st,
            has_price,
            has_open,
            suspended,
            limit_up,
            limit_down,
            buy_reason is None,
            sell_reason is None,
            buy_reason,
            sell_reason,
        )

    def sync_index_members(self, index_code: str, members: list[tuple[str, object]]) -> None:
        for ts_code, effective_date in members:
            self.index_members[(index_code, ts_code, effective_date)] = (index_code, ts_code, effective_date)

    def members_for(self, index_code: str, as_of_date):
        dates = [day for index, _, day in self.index_members if index == index_code and day <= as_of_date]
        if not dates:
            return []
        snapshot_date = max(dates)
        return [code for index, code, day in self.index_members if index == index_code and day == snapshot_date]

    def index_member_codes(self, index_code: str) -> list[str]:
        return sorted({code for index, code, _ in self.index_members if index == index_code})

    def benchmark_for(self, code: str = "000300.SH"):
        return sorted((bar for (c, _), bar in self.benchmarks.items() if c == code), key=lambda bar: bar.trade_date)

    def upsert_market_calendar_session(self, calendar_id: str, trade_date, is_open: bool, source: str) -> None:
        self.market_calendar[(calendar_id, trade_date)] = {
            "calendar_id": calendar_id,
            "trade_date": trade_date,
            "is_open": bool(is_open),
            "source": source,
            "updated_at": datetime.now(UTC),
        }

    def market_calendar_sessions(self, calendar_id: str, start=None, end=None) -> list[dict]:
        rows = [
            dict(row)
            for (row_calendar, day), row in self.market_calendar.items()
            if row_calendar == calendar_id and (start is None or day >= start) and (end is None or day <= end)
        ]
        return sorted(rows, key=lambda row: row["trade_date"])

    def valuations_for(self, code: str):
        return sorted((bar for (c, _), bar in self.valuations.items() if c == code), key=lambda bar: bar.trade_date)

    def create_portfolio(self, name: str, initial_capital: float = 1_000_000.0, transaction_cost_bps: float = 5.0, benchmark_code: str = "000300.SH", *, portfolio_id: str | None = None, market_id: str | None = None, currency: str | None = None) -> str:
        initial_capital = float(initial_capital)
        transaction_cost_bps = float(transaction_cost_bps)
        if not str(name).strip():
            raise ValueError("组合名称不能为空")
        if not isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("初始模拟资金必须大于 0")
        if not isfinite(transaction_cost_bps) or transaction_cost_bps < 0:
            raise ValueError("交易费率不能为负数")
        actual_id = portfolio_id or str(uuid4())
        if actual_id in self.portfolios:
            raise ValueError("组合标识已存在")
        now = datetime.now(UTC)
        self.portfolios[actual_id] = {
            "portfolio_id": actual_id,
            "name": str(name).strip(),
            "initial_capital": initial_capital,
            "benchmark_code": benchmark_code,
            "transaction_cost_bps": transaction_cost_bps,
            "market_id": market_id,
            "currency": currency,
            "status": "ACTIVE",
            "created_at": now,
            "updated_at": now,
        }
        cash_flow_id = str(uuid4())
        inception_date = self.latest_trade_date() or date.today()
        self.portfolio_cash_flows[cash_flow_id] = {
            "cash_flow_id": cash_flow_id,
            "portfolio_id": actual_id,
            "flow_date": inception_date,
            "flow_type": "INITIAL_CAPITAL",
            "amount": initial_capital,
            "reference_type": "PORTFOLIO",
            "reference_id": actual_id,
            "note": "组合初始模拟资金",
            "created_at": now,
        }
        return actual_id

    def _ensure_memory_portfolio(self, portfolio_id: str = "default") -> dict:
        if portfolio_id not in self.portfolios:
            self.create_portfolio("我的研究组合", portfolio_id=portfolio_id)
        return self.portfolios[portfolio_id]

    def get_portfolio(self, portfolio_id: str) -> dict:
        self._ensure_memory_portfolio(portfolio_id)
        return dict(self.portfolios[portfolio_id])

    def list_portfolios(self, include_archived: bool = False) -> list[dict]:
        rows = [dict(row) for row in self.portfolios.values() if include_archived or row["status"] == "ACTIVE"]
        return sorted(rows, key=lambda row: (row["created_at"], row["portfolio_id"]))

    def archive_portfolio(self, portfolio_id: str) -> None:
        portfolio = self._ensure_memory_portfolio(portfolio_id)
        portfolio["status"] = "ARCHIVED"
        portfolio["updated_at"] = datetime.now(UTC)

    def copy_portfolio(self, portfolio_id: str, name: str | None = None) -> str:
        source = self.get_portfolio(portfolio_id)
        copied_id = self.create_portfolio(name or f"{source['name']} 副本", source["initial_capital"], source["transaction_cost_bps"], source["benchmark_code"], market_id=source.get("market_id"), currency=source.get("currency"))
        revisions = self.list_portfolio_target_revisions(portfolio_id)
        targets = self.get_portfolio_target_items(revisions[-1]["revision_id"]) if revisions else self.get_portfolio_positions(portfolio_id)
        for row in targets:
            self.upsert_portfolio_position(copied_id, row["ts_code"], float(row.get("target_weight", row.get("weight", 0.0))), "复制的目标权重草案")
        return copied_id

    def list_portfolio_cash_flows(self, portfolio_id: str) -> list[dict]:
        return sorted((dict(row) for row in self.portfolio_cash_flows.values() if row["portfolio_id"] == portfolio_id), key=lambda row: (row["flow_date"], row["created_at"]))

    def add_portfolio_cash_flow(self, portfolio_id: str, flow_date, amount: float, note: str | None = None) -> str:
        self._ensure_memory_portfolio(portfolio_id)
        amount = float(amount)
        if not isfinite(amount) or abs(amount) <= 1e-12:
            raise ValueError("资金调整金额必须是非零有限值")
        cash_flows = self.list_portfolio_cash_flows(portfolio_id)
        inception_date = next(row["flow_date"] for row in cash_flows if row["flow_type"] == "INITIAL_CAPITAL")
        if flow_date < inception_date:
            raise ValueError("资金流水不能早于组合成立日")
        if sum(float(row["amount"]) for row in cash_flows) + amount <= 0:
            raise ValueError("资金流出不能使累计净投入小于等于 0")
        cash_flow_id = str(uuid4())
        self.portfolio_cash_flows[cash_flow_id] = {
            "cash_flow_id": cash_flow_id,
            "portfolio_id": portfolio_id,
            "flow_date": flow_date,
            "flow_type": "CAPITAL_IN" if amount > 0 else "CAPITAL_OUT",
            "amount": amount,
            "reference_type": "MANUAL_CASH_FLOW",
            "reference_id": cash_flow_id,
            "note": note,
            "created_at": datetime.now(UTC),
        }
        return cash_flow_id

    def save_portfolio_target_revision(self, portfolio_id: str, signal_date, targets: Mapping[str, float], note: str | None = None) -> str:
        from .portfolio import validate_target_weights

        portfolio = self._ensure_memory_portfolio(portfolio_id)
        if portfolio["status"] != "ACTIVE":
            raise ValueError("已归档组合不能保存新权重")
        weights = validate_target_weights(targets)
        unknown = sorted(set(weights) - set(self.securities))
        if unknown:
            raise ValueError("组合成员必须属于当前证券池")
        prior_revisions = self.list_portfolio_target_revisions(portfolio_id)
        previous_targets = self.get_portfolio_target_items(prior_revisions[-1]["revision_id"]) if prior_revisions else []
        previous_map = {row["ts_code"]: float(row["target_weight"]) for row in previous_targets}
        for order in self.portfolio_orders.values():
            if order["portfolio_id"] == portfolio_id and order["status"] in {"PENDING", "PARTIAL"}:
                order["status"] = "SUPERSEDED"
                order["reason"] = "已由更新的目标权重版本取代"
                order["updated_at"] = datetime.now(UTC)
        revision_id = str(uuid4())
        revision_no = len(prior_revisions) + 1
        now = datetime.now(UTC)
        self.portfolio_target_revisions[revision_id] = {
            "revision_id": revision_id,
            "portfolio_id": portfolio_id,
            "revision_no": revision_no,
            "signal_date": signal_date,
            "status": "PENDING",
            "note": note,
            "created_at": now,
        }
        self.portfolio_target_items[revision_id] = [
            {"revision_id": revision_id, "ts_code": code, "target_weight": weight}
            for code, weight in sorted(weights.items())
        ]
        for code in sorted(set(previous_map) | set(weights)):
            old_weight = previous_map.get(code, 0.0)
            target_weight = weights.get(code, 0.0)
            if abs(target_weight - old_weight) <= 1e-12:
                continue
            order_id = str(uuid4())
            self.portfolio_orders[order_id] = {
                "order_id": order_id,
                "portfolio_id": portfolio_id,
                "revision_id": revision_id,
                "ts_code": code,
                "side": "BUY" if target_weight > old_weight else "SELL",
                "target_weight": target_weight,
                "target_quantity": None,
                "remaining_quantity": None,
                "planned_trade_date": None,
                "status": "PENDING",
                "reason": None,
                "created_at": now,
                "updated_at": now,
            }
        portfolio["updated_at"] = now
        return revision_id

    def list_portfolio_target_revisions(self, portfolio_id: str) -> list[dict]:
        return sorted((dict(row) for row in self.portfolio_target_revisions.values() if row["portfolio_id"] == portfolio_id), key=lambda row: row["revision_no"])

    def get_portfolio_target_items(self, revision_id: str) -> list[dict]:
        return [dict(row) for row in self.portfolio_target_items.get(revision_id, ())]

    def list_portfolio_orders(self, portfolio_id: str, revision_id: str | None = None) -> list[dict]:
        rows = [
            dict(row) for row in self.portfolio_orders.values()
            if row["portfolio_id"] == portfolio_id and (revision_id is None or row["revision_id"] == revision_id)
        ]
        return sorted(rows, key=lambda row: (row["created_at"], row["ts_code"]))

    def update_portfolio_order(self, order_id: str, **changes) -> None:
        if order_id not in self.portfolio_orders:
            raise KeyError(f"Unknown portfolio order: {order_id}")
        allowed = {"side", "target_quantity", "remaining_quantity", "planned_trade_date", "status", "reason"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise ValueError(f"Unsupported portfolio order fields: {sorted(unexpected)}")
        self.portfolio_orders[order_id].update(changes)
        self.portfolio_orders[order_id]["updated_at"] = datetime.now(UTC)

    def update_portfolio_revision_status(self, revision_id: str, status: str) -> None:
        if revision_id not in self.portfolio_target_revisions:
            raise KeyError(f"Unknown portfolio revision: {revision_id}")
        self.portfolio_target_revisions[revision_id]["status"] = status

    def record_portfolio_trade(self, trade: Mapping) -> str:
        existing = next((row for row in self.portfolio_trades.values() if row["order_id"] == trade["order_id"] and row["trade_date"] == trade["trade_date"]), None)
        if existing:
            return existing["trade_id"]
        trade_id = str(trade.get("trade_id") or uuid4())
        self.portfolio_trades[trade_id] = {**dict(trade), "trade_id": trade_id, "created_at": trade.get("created_at") or datetime.now(UTC)}
        return trade_id

    def list_portfolio_trades(self, portfolio_id: str) -> list[dict]:
        return sorted((dict(row) for row in self.portfolio_trades.values() if row["portfolio_id"] == portfolio_id), key=lambda row: (row["trade_date"], row["created_at"]))

    def upsert_portfolio_nav(self, row: Mapping) -> None:
        key = (str(row["portfolio_id"]), row["valuation_date"])
        self.portfolio_nav[key] = dict(row)

    def list_portfolio_nav(self, portfolio_id: str) -> list[dict]:
        return sorted((dict(row) for (pid, _), row in self.portfolio_nav.items() if pid == portfolio_id), key=lambda row: row["valuation_date"])

    def latest_trade_date(self):
        return max((bar.trade_date for bar in self.prices.values()), default=None)

    def upsert_portfolio_position(self, portfolio_id: str, ts_code: str, weight: float, note: str | None = None) -> None:
        if not isfinite(float(weight)) or float(weight) < 0 or float(weight) > 1:
            raise ValueError("组合权重必须在 0 到 100% 之间")
        if ts_code not in self.securities:
            raise ValueError("组合成员必须属于当前证券池")
        self._ensure_memory_portfolio(portfolio_id)
        self.portfolio_positions[(portfolio_id, ts_code)] = {"portfolio_id": portfolio_id, "ts_code": ts_code, "weight": float(weight), "note": note}

    def delete_portfolio_position(self, portfolio_id: str, ts_code: str) -> None:
        self.portfolio_positions.pop((portfolio_id, ts_code), None)

    def get_portfolio_positions(self, portfolio_id: str = "default") -> list[dict]:
        return sorted((dict(row) for (pid, _), row in self.portfolio_positions.items() if pid == portfolio_id), key=lambda row: row["ts_code"])

    def get_factor_snapshot(self, as_of_date, factor_version: str, pit_version: str, universe_version: str, context=None) -> dict | None:
        prefix = (as_of_date, factor_version, pit_version, universe_version)
        if context is not None:
            key = (*prefix, context.market_id, context.currency, context.calendar_id, context.universe_id)
            snapshot = self.factor_snapshots.get(key)
            return dict(snapshot) if snapshot else None
        matches = [snapshot for key, snapshot in self.factor_snapshots.items() if key[:4] == prefix]
        return dict(matches[-1]) if matches else None

    def record_factor_snapshot(self, snapshot: dict, items: list[dict]) -> str:
        key = (
            snapshot["as_of_date"],
            snapshot["factor_version"],
            snapshot["pit_version"],
            snapshot["universe_version"],
            snapshot.get("market_id"),
            snapshot.get("currency"),
            snapshot.get("calendar_id"),
            snapshot.get("universe_id"),
        )
        existing = self.factor_snapshots.get(key)
        if existing:
            return existing["snapshot_id"]
        snapshot_id = str(uuid4())
        self.factor_snapshots[key] = {**snapshot, "snapshot_id": snapshot_id, "items": [dict(item) for item in items]}
        return snapshot_id

    def latest_factor_snapshot_before(self, as_of_date, status: str = "completed") -> dict | None:
        candidates = [
            snapshot for snapshot in self.factor_snapshots.values()
            if snapshot.get("as_of_date") <= as_of_date and str(snapshot.get("status")).lower() == status.lower()
        ]
        if not candidates:
            return None
        snapshot = max(candidates, key=lambda row: (row["as_of_date"], str(row.get("factor_version") or "")))
        return {**snapshot, "items": [dict(item) for item in snapshot.get("items") or ()]}


class PostgresStore:
    """PostgreSQL repository. The UI reads from this repository only."""
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("DATABASE_URL is required; configure PostgreSQL before opening the research workspace")
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
        self._initialized = False

    def _connect(self):
        try: import psycopg
        except ImportError as exc: raise RuntimeError("Install psycopg to use PostgreSQL storage") from exc
        return psycopg.connect(self.dsn)

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn:
            conn.execute(POSTGRES_SCHEMA)
            for column in ("revenue", "net_profit", "roe", "gross_margin", "operating_cashflow", "debt_ratio"):
                conn.execute(f"ALTER TABLE financials ALTER COLUMN {column} DROP NOT NULL")
            for column in (
                "roic double precision",
                "current_ratio double precision",
                "free_cashflow double precision",
                "deduct_net_profit double precision",
                "data_version text NOT NULL DEFAULT 'legacy-v0'",
                "first_ann_date date",
                "available_at date",
                "source_version text",
            ):
                conn.execute(f"ALTER TABLE financials ADD COLUMN IF NOT EXISTS {column}")
            for column in ("pe", "pb", "ps", "dividend_yield"):
                conn.execute(f"ALTER TABLE financials DROP COLUMN IF EXISTS {column}")
            for column in ("open double precision", "high double precision", "low double precision"):
                conn.execute(f"ALTER TABLE price_bars ADD COLUMN IF NOT EXISTS {column}")
            for column in ("metadata jsonb NOT NULL DEFAULT '{}'::jsonb",):
                conn.execute(f"ALTER TABLE securities ADD COLUMN IF NOT EXISTS {column}")
            for column in ("market_id text", "currency text"):
                conn.execute(f"ALTER TABLE securities ADD COLUMN IF NOT EXISTS {column}")
            conn.execute("UPDATE securities SET market_id=CASE WHEN ts_code LIKE '%.HK' THEN 'HK' WHEN ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ' THEN 'CN' ELSE market_id END WHERE market_id IS NULL")
            conn.execute("UPDATE securities SET currency=CASE WHEN market_id='HK' THEN 'HKD' WHEN market_id='CN' THEN 'CNY' ELSE currency END WHERE currency IS NULL")
            conn.execute("ALTER TABLE industries ADD COLUMN IF NOT EXISTS effective_to date")
            conn.execute("ALTER TABLE index_members ADD COLUMN IF NOT EXISTS weight double precision")
            conn.execute("ALTER TABLE benchmark_bars ADD COLUMN IF NOT EXISTS open double precision")
            for column in (
                "initial_capital double precision NOT NULL DEFAULT 1000000",
                "benchmark_code text NOT NULL DEFAULT '000300.SH'",
                "transaction_cost_bps double precision NOT NULL DEFAULT 5",
                "status text NOT NULL DEFAULT 'ACTIVE'",
                "created_at timestamptz NOT NULL DEFAULT NOW()",
                "market_id text",
                "currency text",
            ):
                conn.execute(f"ALTER TABLE research_portfolios ADD COLUMN IF NOT EXISTS {column}")
            conn.execute("UPDATE research_portfolios SET market_id=CASE WHEN benchmark_code LIKE '%.HK' THEN 'HK' WHEN benchmark_code LIKE '%.SH' OR benchmark_code LIKE '%.SZ' THEN 'CN' ELSE market_id END WHERE market_id IS NULL")
            conn.execute("UPDATE research_portfolios SET currency=CASE WHEN market_id='HK' THEN 'HKD' WHEN market_id='CN' THEN 'CNY' ELSE currency END WHERE currency IS NULL")
            for column in ("market_id text", "currency text", "calendar_id text", "universe_id text"):
                conn.execute(f"ALTER TABLE factor_snapshots ADD COLUMN IF NOT EXISTS {column}")
            conn.execute("UPDATE factor_snapshots SET market_id='CN',currency='CNY',calendar_id='CN_A_SHARE',universe_id=COALESCE(universe_id,'000300.SH') WHERE market_id IS NULL")
            conn.execute("ALTER TABLE factor_snapshots DROP CONSTRAINT IF EXISTS factor_snapshots_as_of_date_factor_version_pit_version_univ_key")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS factor_snapshots_market_context_v2_uidx ON factor_snapshots (as_of_date,factor_version,pit_version,universe_version,COALESCE(market_id,''),COALESCE(currency,''),COALESCE(calendar_id,''),COALESCE(universe_id,''))")
        self._initialized = True

    @staticmethod
    def _json_safe(value):
        if isinstance(value, float):
            return value if isfinite(value) else None
        if isinstance(value, dict):
            return {key: PostgresStore._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [PostgresStore._json_safe(item) for item in value]
        if hasattr(value, "item") and not isinstance(value, (str, bytes)):
            try:
                return PostgresStore._json_safe(value.item())
            except (TypeError, ValueError):
                pass
        return value

    @staticmethod
    def serialize_raw_payload(payload: dict) -> str:
        """Produce strict JSON acceptable to PostgreSQL jsonb from Tushare frames."""
        return json.dumps(PostgresStore._json_safe(payload), default=str, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _raw_record_key(record: RawRecord) -> str:
        identity = "|".join(str(value or "") for value in (
            record.ts_code, record.report_period, record.ann_date,
            record.first_ann_date, record.version,
            PostgresStore.serialize_raw_payload(record.payload),
        ))
        return sha256(identity.encode("utf-8")).hexdigest()

    def _persist_raw_records(self, conn, records: list[RawRecord]) -> None:
        sql = """
            INSERT INTO source_records (dataset,ts_code,record_key,report_period,ann_date,first_ann_date,source_version,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (dataset,record_key) DO UPDATE
              SET payload=EXCLUDED.payload, source_version=EXCLUDED.source_version, retrieved_at=NOW()
        """
        for record in records:
            conn.execute(sql, (
                record.dataset, record.ts_code, self._raw_record_key(record), record.report_period,
                record.ann_date, record.first_ann_date, record.version,
                self.serialize_raw_payload(record.payload),
            ))

    def persist_raw_records(self, records: list[RawRecord]) -> None:
        self.initialize()
        with self._connect() as conn:
            self._persist_raw_records(conn, records)
            conn.commit()

    def set_sync_state(self, universe: str, dataset: str, watermark, status: str, run_id: str | None = None, error: str | None = None) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_state (universe,dataset,watermark,status,run_id,error) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (universe,dataset) DO UPDATE SET watermark=EXCLUDED.watermark,status=EXCLUDED.status,run_id=EXCLUDED.run_id,error=EXCLUDED.error,updated_at=NOW()",
                (universe, dataset, watermark, status, run_id, sanitize_sensitive_text(error)),
            )

    def sync_states(self, universe: str = "hs300") -> list[dict]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT dataset,watermark,status,run_id::text,updated_at,error FROM sync_state WHERE universe=%s ORDER BY dataset", (universe,)).fetchall()
        return [{"dataset": row[0], "watermark": row[1], "status": row[2], "run_id": row[3], "updated_at": row[4], "error": row[5]} for row in rows]

    def is_trade_day(self, day, exchange: str = "SSE") -> bool:
        """Read the persisted Tushare calendar; weekday is only an initial bootstrap fallback."""
        self.initialize()
        with self._connect() as conn:
            row = conn.execute("SELECT payload->>'is_open' FROM source_records WHERE dataset='trade_cal' AND ts_code=%s AND report_period=%s ORDER BY retrieved_at DESC LIMIT 1", (exchange, day)).fetchone()
        return str(row[0]).lower() in {"1", "true"} if row else day.weekday() < 5

    @contextmanager
    def sync_lock(self, universe: str = "hs300"):
        """Acquire a database-wide lock so scheduled and manual syncs cannot overlap."""
        self.initialize()
        with self._connect() as conn:
            acquired = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (f"quant-sync:{universe}",)).fetchone()[0]
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"quant-sync:{universe}",))

    def _completed_sync_codes(self, conn, sync_key: str | None, dataset: str) -> set[str]:
        if not sync_key:
            return set()
        return {row[0] for row in conn.execute("SELECT ts_code FROM sync_checkpoints WHERE sync_key=%s AND dataset=%s", (sync_key, dataset)).fetchall()}

    def _record_sync_checkpoint(self, conn, sync_key: str | None, dataset: str, ts_code: str) -> None:
        if sync_key:
            conn.execute("INSERT INTO sync_checkpoints (sync_key,dataset,ts_code) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (sync_key, dataset, ts_code))

    def sync(self, provider: DataProvider, sync_key: str | None = None, skip_codes: dict[str, set[str]] | None = None) -> None:
        self.initialize()
        skip_codes = skip_codes or {}
        with self._connect() as conn:
            rows = provider.fetch_securities()
            for r in rows: conn.execute("INSERT INTO securities (ts_code,name,list_date,is_st,market_id,currency) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, list_date=EXCLUDED.list_date, is_st=EXCLUDED.is_st, market_id=COALESCE(EXCLUDED.market_id,securities.market_id), currency=COALESCE(EXCLUDED.currency,securities.currency)", (r.ts_code, r.name, r.list_date, r.is_st, r.market_id, r.currency))
            if hasattr(provider, "raw_base_records"):
                self._persist_raw_records(conn, provider.raw_base_records())
            if hasattr(provider, "fetch_reference_records"):
                reference_records = provider.fetch_reference_records()
                self._persist_raw_records(conn, reference_records)
                calendar_records = [record for record in reference_records if record.dataset == "trade_cal"]
                if calendar_records:
                    conn.commit()
                    from .markets.cn import persist_cn_calendar_intersection
                    persist_cn_calendar_intersection(self, calendar_records)
            if hasattr(provider, "fetch_security_statuses"):
                for r in provider.fetch_security_statuses():
                    conn.execute(
                        "INSERT INTO security_statuses (ts_code,effective_from,effective_to,is_st,status_name) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (ts_code,effective_from) DO UPDATE SET effective_to=EXCLUDED.effective_to,is_st=EXCLUDED.is_st,status_name=EXCLUDED.status_name",
                        (r.ts_code, r.effective_from, r.effective_to, r.is_st, r.status_name),
                    )
            price_skip = set(skip_codes.get("prices", set())) | self._completed_sync_codes(conn, sync_key, "prices")
            financial_skip = set(skip_codes.get("financials", set())) | self._completed_sync_codes(conn, sync_key, "financials")
            valuation_skip = set(skip_codes.get("valuations", set())) | self._completed_sync_codes(conn, sync_key, "valuations")
            for dataset, codes in (("prices", price_skip), ("financials", financial_skip), ("valuations", valuation_skip)):
                for code in skip_codes.get(dataset, set()):
                    self._record_sync_checkpoint(conn, sync_key, dataset, code)
            conn.commit()
            price_sql = "INSERT INTO price_bars (ts_code,trade_date,close,adj_factor,suspended,limit_up,limit_down,volume,open,high,low) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ts_code,trade_date) DO UPDATE SET close=EXCLUDED.close, adj_factor=EXCLUDED.adj_factor, suspended=EXCLUDED.suspended, limit_up=EXCLUDED.limit_up, limit_down=EXCLUDED.limit_down, volume=EXCLUDED.volume, open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low"
            if hasattr(provider, "iter_price_batches"):
                for code, batch in provider.iter_price_batches(skip_codes=price_skip):
                    for r in batch:
                        conn.execute(price_sql, (r.ts_code, r.trade_date, r.close, r.adj_factor, r.suspended, r.limit_up, r.limit_down, r.volume, r.open, r.high, r.low))
                    if hasattr(provider, "raw_market_records_for"):
                        self._persist_raw_records(conn, provider.raw_market_records_for(code))
                    if hasattr(provider, "price_limit_records_for"):
                        for record in provider.price_limit_records_for(code):
                            conn.execute(
                                "INSERT INTO price_limits (ts_code,trade_date,up_limit,down_limit) VALUES (%s,%s,%s,%s) ON CONFLICT (ts_code,trade_date) DO UPDATE SET up_limit=EXCLUDED.up_limit,down_limit=EXCLUDED.down_limit",
                                (record.ts_code, record.trade_date, record.up_limit, record.down_limit),
                            )
                    if hasattr(provider, "suspension_records_for"):
                        for record in provider.suspension_records_for(code):
                            conn.execute(
                                "INSERT INTO trading_suspensions (ts_code,suspend_date,resume_date,reason) VALUES (%s,%s,%s,%s) ON CONFLICT (ts_code,suspend_date) DO UPDATE SET resume_date=EXCLUDED.resume_date,reason=EXCLUDED.reason",
                                (record.ts_code, record.suspend_date, record.resume_date, record.reason),
                            )
                    self._record_sync_checkpoint(conn, sync_key, "prices", code)
                    conn.commit()
            else:
                for r in provider.fetch_prices():
                    conn.execute(price_sql, (r.ts_code, r.trade_date, r.close, r.adj_factor, r.suspended, r.limit_up, r.limit_down, r.volume, r.open, r.high, r.low))
                conn.commit()
            financial_sql = "INSERT INTO financials (ts_code,report_period,ann_date,revenue,net_profit,roe,gross_margin,operating_cashflow,debt_ratio,roic,current_ratio,free_cashflow,deduct_net_profit,data_version,first_ann_date,available_at,source_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ts_code,report_period,ann_date) DO UPDATE SET revenue=EXCLUDED.revenue, net_profit=EXCLUDED.net_profit, roe=EXCLUDED.roe, gross_margin=EXCLUDED.gross_margin, operating_cashflow=EXCLUDED.operating_cashflow, debt_ratio=EXCLUDED.debt_ratio, roic=EXCLUDED.roic, current_ratio=EXCLUDED.current_ratio, free_cashflow=EXCLUDED.free_cashflow, deduct_net_profit=EXCLUDED.deduct_net_profit, data_version=EXCLUDED.data_version, first_ann_date=EXCLUDED.first_ann_date, available_at=EXCLUDED.available_at, source_version=EXCLUDED.source_version"
            if hasattr(provider, "iter_financial_batches"):
                for code, batch in provider.iter_financial_batches(skip_codes=financial_skip):
                    for r in batch:
                        conn.execute(financial_sql, (r.ts_code, r.report_period, r.ann_date, r.revenue, r.net_profit, r.roe, r.gross_margin, r.operating_cashflow, r.debt_ratio, r.roic, r.current_ratio, r.free_cashflow, r.deduct_net_profit, r.data_version, r.first_ann_date, r.available_at, r.source_version))
                    if hasattr(provider, "raw_financial_records_for"):
                        self._persist_raw_records(conn, provider.raw_financial_records_for(code))
                    self._record_sync_checkpoint(conn, sync_key, "financials", code)
                    conn.commit()
            else:
                for r in provider.fetch_financials():
                    conn.execute(financial_sql, (r.ts_code, r.report_period, r.ann_date, r.revenue, r.net_profit, r.roe, r.gross_margin, r.operating_cashflow, r.debt_ratio, r.roic, r.current_ratio, r.free_cashflow, r.deduct_net_profit, r.data_version, r.first_ann_date, r.available_at, r.source_version))
                conn.commit()
            for r in provider.fetch_industries(): conn.execute("INSERT INTO industries (ts_code,industry,effective_date,effective_to) VALUES (%s,%s,%s,%s) ON CONFLICT (ts_code,effective_date) DO UPDATE SET industry=EXCLUDED.industry,effective_to=EXCLUDED.effective_to", (r.ts_code,r.industry,r.effective_date,r.effective_to))
            valuation_sql = "INSERT INTO valuation_bars VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ts_code,trade_date) DO UPDATE SET pe_ttm=EXCLUDED.pe_ttm,pb=EXCLUDED.pb,ps_ttm=EXCLUDED.ps_ttm,dividend_yield=EXCLUDED.dividend_yield,turnover_rate=EXCLUDED.turnover_rate,data_version=EXCLUDED.data_version"
            if hasattr(provider, "iter_valuation_batches"):
                for code, batch in provider.iter_valuation_batches(skip_codes=valuation_skip):
                    for r in batch:
                        conn.execute(valuation_sql, (r.ts_code, r.trade_date, r.pe_ttm, r.pb, r.ps_ttm, r.dividend_yield, r.turnover_rate, r.data_version))
                    if hasattr(provider, "raw_market_records_for"):
                        self._persist_raw_records(conn, provider.raw_market_records_for(code))
                    self._record_sync_checkpoint(conn, sync_key, "valuations", code)
                    conn.commit()
            elif hasattr(provider, "fetch_valuations"):
                for r in provider.fetch_valuations():
                    conn.execute(valuation_sql, (r.ts_code, r.trade_date, r.pe_ttm, r.pb, r.ps_ttm, r.dividend_yield, r.turnover_rate, r.data_version))
                conn.commit()
            if hasattr(provider, "fetch_benchmark"):
                for r in provider.fetch_benchmark(): conn.execute("INSERT INTO benchmark_bars (ts_code,trade_date,close,open) VALUES (%s,%s,%s,%s) ON CONFLICT (ts_code,trade_date) DO UPDATE SET close=EXCLUDED.close,open=EXCLUDED.open", (r.ts_code, r.trade_date, r.close, r.open))

    def sync_benchmark(self, provider, ts_code: str = "000300.SH") -> int:
        """Replace one benchmark's requested window only when every row has a real open."""
        rows = _benchmark_rows_with_open(provider, ts_code)
        self.initialize()
        with self._connect() as conn:
            for row in rows:
                conn.execute(
                    "INSERT INTO benchmark_bars (ts_code,trade_date,close,open) VALUES (%s,%s,%s,%s) ON CONFLICT (ts_code,trade_date) DO UPDATE SET close=EXCLUDED.close,open=EXCLUDED.open",
                    (row.ts_code, row.trade_date, row.close, row.open),
                )
        return len(rows)

    def load_memory(self) -> InMemoryStore:
        """Materialize a read-only research snapshot for PIT and analytics services."""
        store = InMemoryStore()
        with self._connect() as conn:
            for row in conn.execute("SELECT ts_code,name,list_date,is_st,market_id,currency FROM securities"):
                record = Security(*row); store.securities[record.ts_code] = record
            for row in conn.execute("SELECT ts_code,effective_from,effective_to,is_st,status_name FROM security_statuses"):
                record = SecurityStatusRecord(*row); store.security_statuses[(record.ts_code, record.effective_from)] = record
            for row in conn.execute("SELECT ts_code,trade_date,close,adj_factor,suspended,limit_up,limit_down,volume,open,high,low FROM price_bars"):
                record = PriceBar(*row); store.prices[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT ts_code,report_period,ann_date,revenue,net_profit,roe,gross_margin,operating_cashflow,debt_ratio,roic,current_ratio,free_cashflow,deduct_net_profit,data_version,first_ann_date,available_at,source_version FROM financials"):
                record = FinancialRecord(*row); store.financials[(record.ts_code, record.report_period, record.ann_date)] = record
            for row in conn.execute("SELECT ts_code,industry,effective_date,effective_to FROM industries"):
                record = IndustryRecord(*row); store.industries[(record.ts_code, record.effective_date)] = record
            for row in conn.execute("SELECT index_code,ts_code,effective_date FROM index_members"):
                index_code, ts_code, effective_date = row
                store.index_members[(index_code, ts_code, effective_date)] = row
            for row in conn.execute("SELECT ts_code,trade_date,close,open FROM benchmark_bars"):
                record = BenchmarkBar(*row); store.benchmarks[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT ts_code,trade_date,pe_ttm,pb,ps_ttm,dividend_yield,turnover_rate,data_version FROM valuation_bars"):
                record = ValuationBar(*row); store.valuations[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT ts_code,trade_date,up_limit,down_limit FROM price_limits"):
                record = PriceLimitRecord(*row); store.price_limits[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT ts_code,suspend_date,resume_date,reason FROM trading_suspensions"):
                record = TradingSuspensionRecord(*row); store.trading_suspensions[(record.ts_code, record.suspend_date)] = record
            for row in conn.execute("SELECT dataset,source,status,row_count,created_at,error FROM ingestion_audit ORDER BY created_at"):
                store.audit.append(AuditRun(*row))
        return store

    def upsert_market_calendar_session(self, calendar_id: str, trade_date, is_open: bool, source: str) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO market_calendar_sessions (calendar_id,trade_date,is_open,source) VALUES (%s,%s,%s,%s) ON CONFLICT (calendar_id,trade_date) DO UPDATE SET is_open=EXCLUDED.is_open,source=EXCLUDED.source,updated_at=NOW()",
                (calendar_id, trade_date, bool(is_open), source),
            )

    def market_calendar_sessions(self, calendar_id: str, start=None, end=None) -> list[dict]:
        self.initialize()
        clauses, params = ["calendar_id=%s"], [calendar_id]
        if start is not None:
            clauses.append("trade_date>=%s")
            params.append(start)
        if end is not None:
            clauses.append("trade_date<=%s")
            params.append(end)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT calendar_id,trade_date,is_open,source,updated_at FROM market_calendar_sessions WHERE {' AND '.join(clauses)} ORDER BY trade_date",
                params,
            ).fetchall()
        return [
            {"calendar_id": row[0], "trade_date": row[1], "is_open": row[2], "source": row[3], "updated_at": row[4]}
            for row in rows
        ]

    def load_page_memory(self, *, price_days: int = 0, codes: list[str] | None = None, include_financials: bool = False, include_valuations: bool = False) -> InMemoryStore:
        """Load only the small snapshot required by an interactive page.

        Full materialization remains available for PIT research jobs; page reads
        must never deserialize the complete historical price table.
        """
        store = InMemoryStore()
        with self._connect() as conn:
            for row in conn.execute("SELECT ts_code,name,list_date,is_st FROM securities ORDER BY ts_code"):
                record = Security(*row); store.securities[record.ts_code] = record
            industry_clause, industry_params = "", []
            if codes:
                industry_clause, industry_params = " WHERE ts_code = ANY(%s)", [codes]
            for row in conn.execute(
                f"SELECT ts_code,industry,effective_date,effective_to FROM industries{industry_clause} ORDER BY ts_code,effective_date",
                industry_params,
            ):
                record = IndustryRecord(*row); store.industries[(record.ts_code, record.effective_date)] = record
            if price_days:
                horizon = max(2, int(price_days))
                code_clause, params = "", []
                if codes:
                    code_clause, params = " AND ts_code = ANY(%s)", [codes]
                rows = conn.execute(
                    f"WITH days AS (SELECT DISTINCT trade_date FROM price_bars ORDER BY trade_date DESC LIMIT {horizon}) SELECT ts_code,trade_date,close,adj_factor,suspended,limit_up,limit_down,volume,open,high,low FROM price_bars WHERE trade_date >= (SELECT min(trade_date) FROM days){code_clause} ORDER BY ts_code,trade_date",
                    params,
                )
                for row in rows:
                    record = PriceBar(*row); store.prices[(record.ts_code, record.trade_date)] = record
                for row in conn.execute(f"WITH days AS (SELECT DISTINCT trade_date FROM benchmark_bars ORDER BY trade_date DESC LIMIT {horizon}) SELECT ts_code,trade_date,close,open FROM benchmark_bars WHERE trade_date >= (SELECT min(trade_date) FROM days) ORDER BY trade_date"):
                    record = BenchmarkBar(*row); store.benchmarks[(record.ts_code, record.trade_date)] = record
            if include_financials and codes:
                for row in conn.execute("SELECT ts_code,report_period,ann_date,revenue,net_profit,roe,gross_margin,operating_cashflow,debt_ratio,roic,current_ratio,free_cashflow,deduct_net_profit,data_version FROM financials WHERE ts_code = ANY(%s) ORDER BY report_period,ann_date", (codes,)):
                    record = FinancialRecord(*row); store.financials[(record.ts_code, record.report_period, record.ann_date)] = record
            if include_valuations and codes:
                for row in conn.execute("SELECT ts_code,trade_date,pe_ttm,pb,ps_ttm,dividend_yield,turnover_rate,data_version FROM valuation_bars WHERE ts_code = ANY(%s) ORDER BY trade_date", (codes,)):
                    record = ValuationBar(*row); store.valuations[(record.ts_code, record.trade_date)] = record
        return store

    def load_portfolio_page_memory(self, codes: list[str], valuation_date) -> InMemoryStore:
        """Load only holdings evidence required for one portfolio valuation date."""
        store = InMemoryStore()
        if not codes or valuation_date is None:
            return store
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT ts_code,name,list_date,is_st FROM securities WHERE ts_code = ANY(%s) ORDER BY ts_code",
                (codes,),
            ):
                record = Security(*row)
                store.securities[record.ts_code] = record
            for row in conn.execute(
                "SELECT ts_code,industry,effective_date,effective_to FROM industries WHERE ts_code = ANY(%s) AND effective_date<=%s ORDER BY ts_code,effective_date",
                (codes, valuation_date),
            ):
                record = IndustryRecord(*row)
                store.industries[(record.ts_code, record.effective_date)] = record
            for row in conn.execute(
                "SELECT ts_code,trade_date,close,adj_factor,suspended,limit_up,limit_down,volume,open,high,low FROM price_bars WHERE ts_code = ANY(%s) AND trade_date=%s ORDER BY ts_code",
                (codes, valuation_date),
            ):
                record = PriceBar(*row)
                store.prices[(record.ts_code, record.trade_date)] = record
        return store

    def sync_index_members(self, index_code: str, members: list[tuple[str, object]]) -> None:
        self.initialize()
        with self._connect() as conn:
            for ts_code, effective_date in members:
                conn.execute("INSERT INTO index_members (index_code,ts_code,effective_date) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (index_code, ts_code, effective_date))

    def index_member_codes(self, index_code: str) -> list[str]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT ts_code FROM index_members WHERE index_code=%s ORDER BY ts_code", (index_code,)).fetchall()
        return [row[0] for row in rows]

    def record_run(self, run_type: str, status: str, parameters: dict | None = None, payload: dict | None = None, error: str | None = None) -> str:
        self.initialize()
        run_id = str(uuid4())
        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO research_runs (run_id,run_type,status,parameters,payload,error,created_at,completed_at) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)",
                (
                    run_id,
                    run_type,
                    status,
                    json.dumps(sanitize_for_storage(parameters or {}), default=str),
                    json.dumps(sanitize_for_storage(payload or {}), default=str),
                    sanitize_sensitive_text(error),
                    now,
                    now if status != "running" else None,
                ),
            )
        return run_id

    def get_factor_snapshot(self, as_of_date, factor_version: str, pit_version: str, universe_version: str, context=None) -> dict | None:
        self.initialize()
        context_values = (
            (context.market_id, context.currency, context.calendar_id, context.universe_id)
            if context is not None else None
        )
        with self._connect() as conn:
            query = "SELECT snapshot_id::text,status,coverage,audit,error,market_id,currency,calendar_id,universe_id FROM factor_snapshots WHERE as_of_date=%s AND factor_version=%s AND pit_version=%s AND universe_version=%s"
            params = [as_of_date, factor_version, pit_version, universe_version]
            if context_values is not None:
                query += " AND market_id=%s AND currency=%s AND calendar_id=%s AND universe_id=%s"
                params.extend(context_values)
            query += " ORDER BY created_at DESC LIMIT 1"
            row = conn.execute(query, tuple(params)).fetchone()
            if not row:
                return None
            items = conn.execute("SELECT ts_code,factors,availability,audit FROM factor_snapshot_items WHERE snapshot_id=%s ORDER BY ts_code", (row[0],)).fetchall()
        return {"snapshot_id": row[0], "as_of_date": as_of_date, "factor_version": factor_version, "pit_version": pit_version, "universe_version": universe_version, "status": row[1], "coverage": row[2], "audit": row[3] or {}, "error": row[4], "market_id": row[5], "currency": row[6], "calendar_id": row[7], "universe_id": row[8], "items": [{"ts_code": item[0], "factors": item[1], "availability": item[2], "audit": item[3] or {}} for item in items]}

    def record_factor_snapshot(self, snapshot: dict, items: list[dict]) -> str:
        self.initialize()
        snapshot_id = str(uuid4())
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO factor_snapshots (snapshot_id,as_of_date,factor_version,pit_version,universe_version,status,coverage,audit,completed_at,error,market_id,currency,calendar_id,universe_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING snapshot_id::text",
                (snapshot_id, snapshot["as_of_date"], snapshot["factor_version"], snapshot["pit_version"], snapshot["universe_version"], snapshot.get("status", "completed"), snapshot.get("coverage"), json.dumps(sanitize_for_storage(snapshot.get("audit") or {}), default=str), sanitize_sensitive_text(snapshot.get("error")), snapshot.get("market_id"), snapshot.get("currency"), snapshot.get("calendar_id"), snapshot.get("universe_id")),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT snapshot_id::text FROM factor_snapshots WHERE as_of_date=%s AND factor_version=%s AND pit_version=%s AND universe_version=%s AND market_id IS NOT DISTINCT FROM %s AND currency IS NOT DISTINCT FROM %s AND calendar_id IS NOT DISTINCT FROM %s AND universe_id IS NOT DISTINCT FROM %s",
                    (snapshot["as_of_date"], snapshot["factor_version"], snapshot["pit_version"], snapshot["universe_version"], snapshot.get("market_id"), snapshot.get("currency"), snapshot.get("calendar_id"), snapshot.get("universe_id")),
                ).fetchone()
            actual_id = row[0]
            if actual_id == snapshot_id:
                for item in items:
                    conn.execute("INSERT INTO factor_snapshot_items (snapshot_id,ts_code,factors,availability,audit) VALUES (%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)", (actual_id, item["ts_code"], json.dumps(sanitize_for_storage(item.get("factors") or {}), default=str), json.dumps(sanitize_for_storage(item.get("availability") or {}), default=str), json.dumps(sanitize_for_storage(item.get("audit") or {}), default=str)))
        return actual_id

    def update_run_progress(self, run_id: str, progress: dict) -> None:
        """Persist the latest long-running sync progress so refreshes retain it."""
        self.initialize()
        with self._connect() as conn:
            conn.execute("UPDATE research_runs SET payload = payload || %s::jsonb WHERE run_id=%s", (json.dumps(sanitize_for_storage({"progress": progress}), default=str), run_id))

    def fail_stale_sync_runs(self, max_age_seconds: int = 900) -> int:
        """Mark runs with no heartbeat as failed instead of showing them forever."""
        self.initialize()
        reason = f"同步超过 {max_age_seconds // 60} 分钟没有进度更新，已标记失败；请重新发起同步"
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE research_runs
                   SET status='failed', error=%s,
                       payload=payload || %s::jsonb, completed_at=NOW()
                 WHERE run_type='sync' AND status='running'
                   AND COALESCE(
                         CASE WHEN (payload->'progress'->>'updated_at') ~ '^[0-9]{4}-'
                              THEN (payload->'progress'->>'updated_at')::timestamptz
                         END,
                         created_at
                       ) < NOW() - (%s * INTERVAL '1 second')
                """,
                (reason, json.dumps({"status_reason": "stale_progress_timeout"}), max_age_seconds),
            )
            return result.rowcount

    def finish_run(self, run_id: str, status: str, payload: dict | None = None, error: str | None = None) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute("UPDATE research_runs SET status=%s,payload=payload || %s::jsonb,error=%s,completed_at=%s WHERE run_id=%s", (status, json.dumps(sanitize_for_storage(payload or {}), default=str), sanitize_sensitive_text(error), datetime.now(UTC), run_id))

    def list_runs(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id::text,run_type,status,parameters,payload,error,created_at,completed_at FROM research_runs ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
        return [{"run_id": row[0], "run_type": row[1], "status": row[2], "parameters": row[3], "payload": row[4], "error": row[5], "created_at": row[6], "completed_at": row[7]} for row in rows]

    def latest_run_summary(self, run_type: str, status: str | None = None) -> dict | None:
        """Return the latest run without deserializing historical factor rows for page reads."""
        status_clause = "AND status=%s" if status else ""
        params = [run_type] + ([status] if status else [])
        payload = "jsonb_build_object('rankings', payload->'rankings', 'metadata', payload->'metadata')" if run_type == "factors" else "payload"
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT run_id::text,run_type,status,parameters,{payload},error,created_at,completed_at FROM research_runs WHERE run_type=%s {status_clause} ORDER BY created_at DESC LIMIT 1",
                params,
            ).fetchone()
        if not row:
            return None
        return {"run_id": row[0], "run_type": row[1], "status": row[2], "parameters": row[3], "payload": row[4], "error": row[5], "created_at": row[6], "completed_at": row[7]}

    def latest_trade_date(self):
        with self._connect() as conn:
            row = conn.execute("SELECT max(trade_date) FROM price_bars").fetchone()
        return row[0] if row else None

    def fast_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute("SELECT (SELECT count(*) FROM securities), (SELECT count(*) FROM price_bars), (SELECT count(*) FROM financials), (SELECT count(*) FROM valuation_bars), (SELECT count(*) FROM benchmark_bars)").fetchone()
        return {"securities": row[0], "prices": row[1], "financials": row[2], "valuation_count": row[3], "benchmark_count": row[4]}

    def data_quality(self, universe: str = "hs300") -> dict:
        """Persistable data-health report used by the CLI and management page."""
        self.initialize()
        with self._connect() as conn:
            latest = conn.execute("SELECT max(trade_date) FROM price_bars").fetchone()[0]
            codes = [row[0] for row in conn.execute("SELECT ts_code FROM securities ORDER BY ts_code")]
            priced = {row[0] for row in conn.execute("SELECT ts_code FROM price_bars WHERE trade_date=%s", (latest,))} if latest else set()
            financial = {row[0] for row in conn.execute("SELECT DISTINCT ts_code FROM financials")}
            valuation_count = conn.execute("SELECT count(*) FROM valuation_bars").fetchone()[0]
        report = {
            "universe": universe, "security_count": len(codes), "latest_trade_date": latest,
            "latest_price_coverage": len(priced), "missing_latest_price_codes": sorted(set(codes) - priced) if latest else codes,
            "missing_financial_codes": sorted(set(codes) - financial), "valuation_count": valuation_count,
        }
        report["is_complete"] = bool(codes and latest and not report["missing_latest_price_codes"] and not report["missing_financial_codes"] and valuation_count)
        return report

    def get_run(self, run_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT run_id::text,run_type,status,parameters,payload,error,created_at,completed_at FROM research_runs WHERE run_id=%s", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown research run: {run_id}")
        return {"run_id": row[0], "run_type": row[1], "status": row[2], "parameters": row[3], "payload": row[4], "error": row[5], "created_at": row[6], "completed_at": row[7]}

    def status(self) -> dict:
        store = self.load_memory()
        return {"counts": store.counts(), "audit": [audit.__dict__ for audit in store.audit[-10:]], "runs": self.list_runs(20)}

    def _ensure_portfolio(self, portfolio_id: str = "default", name: str = "我的研究组合") -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute("INSERT INTO research_portfolios (portfolio_id, name) VALUES (%s, %s) ON CONFLICT (portfolio_id) DO NOTHING", (portfolio_id, name))
            conn.execute(
                "INSERT INTO portfolio_cash_flows (cash_flow_id,portfolio_id,flow_date,flow_type,amount,reference_type,reference_id,note) SELECT %s,%s,COALESCE((SELECT max(trade_date) FROM price_bars),CURRENT_DATE),'INITIAL_CAPITAL',initial_capital,'PORTFOLIO',%s,'组合初始模拟资金' FROM research_portfolios WHERE portfolio_id=%s ON CONFLICT (portfolio_id,reference_type,reference_id) DO NOTHING",
                (str(uuid4()), portfolio_id, portfolio_id, portfolio_id),
            )

    def create_portfolio(self, name: str, initial_capital: float = 1_000_000.0, transaction_cost_bps: float = 5.0, benchmark_code: str = "000300.SH", *, portfolio_id: str | None = None, market_id: str | None = None, currency: str | None = None) -> str:
        initial_capital = float(initial_capital)
        transaction_cost_bps = float(transaction_cost_bps)
        if not str(name).strip():
            raise ValueError("组合名称不能为空")
        if not isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("初始模拟资金必须大于 0")
        if not isfinite(transaction_cost_bps) or transaction_cost_bps < 0:
            raise ValueError("交易费率不能为负数")
        self.initialize()
        actual_id = portfolio_id or str(uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO research_portfolios (portfolio_id,name,initial_capital,benchmark_code,transaction_cost_bps,status,market_id,currency) VALUES (%s,%s,%s,%s,%s,'ACTIVE',%s,%s)",
                (actual_id, str(name).strip(), initial_capital, benchmark_code, transaction_cost_bps, market_id, currency),
            )
            conn.execute(
                "INSERT INTO portfolio_cash_flows (cash_flow_id,portfolio_id,flow_date,flow_type,amount,reference_type,reference_id,note) VALUES (%s,%s,COALESCE((SELECT max(trade_date) FROM price_bars),CURRENT_DATE),'INITIAL_CAPITAL',%s,'PORTFOLIO',%s,'组合初始模拟资金')",
                (str(uuid4()), actual_id, initial_capital, actual_id),
            )
        return actual_id

    def get_portfolio(self, portfolio_id: str) -> dict:
        self._ensure_portfolio(portfolio_id)
        with self._connect() as conn:
            row = conn.execute("SELECT portfolio_id,name,initial_capital,benchmark_code,transaction_cost_bps,status,created_at,updated_at,market_id,currency FROM research_portfolios WHERE portfolio_id=%s", (portfolio_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown portfolio: {portfolio_id}")
        return {"portfolio_id": row[0], "name": row[1], "initial_capital": row[2], "benchmark_code": row[3], "transaction_cost_bps": row[4], "status": row[5], "created_at": row[6], "updated_at": row[7], "market_id": row[8], "currency": row[9]}

    def list_portfolios(self, include_archived: bool = False) -> list[dict]:
        self.initialize()
        where = "" if include_archived else "WHERE status='ACTIVE'"
        with self._connect() as conn:
            rows = conn.execute(f"SELECT portfolio_id,name,initial_capital,benchmark_code,transaction_cost_bps,status,created_at,updated_at,market_id,currency FROM research_portfolios {where} ORDER BY created_at,portfolio_id").fetchall()
        return [{"portfolio_id": row[0], "name": row[1], "initial_capital": row[2], "benchmark_code": row[3], "transaction_cost_bps": row[4], "status": row[5], "created_at": row[6], "updated_at": row[7], "market_id": row[8], "currency": row[9]} for row in rows]

    def archive_portfolio(self, portfolio_id: str) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute("UPDATE research_portfolios SET status='ARCHIVED',updated_at=NOW() WHERE portfolio_id=%s", (portfolio_id,))

    def copy_portfolio(self, portfolio_id: str, name: str | None = None) -> str:
        source = self.get_portfolio(portfolio_id)
        copied_id = self.create_portfolio(name or f"{source['name']} 副本", source["initial_capital"], source["transaction_cost_bps"], source["benchmark_code"], market_id=source.get("market_id"), currency=source.get("currency"))
        revisions = self.list_portfolio_target_revisions(portfolio_id)
        targets = self.get_portfolio_target_items(revisions[-1]["revision_id"]) if revisions else self.get_portfolio_positions(portfolio_id)
        with self._connect() as conn:
            for row in targets:
                weight = row.get("target_weight", row.get("weight", 0.0))
                conn.execute("INSERT INTO research_portfolio_positions (portfolio_id,ts_code,weight,note) VALUES (%s,%s,%s,%s) ON CONFLICT (portfolio_id,ts_code) DO UPDATE SET weight=EXCLUDED.weight,note=EXCLUDED.note,updated_at=NOW()", (copied_id, row["ts_code"], weight, "复制的目标权重草案"))
        return copied_id

    def list_portfolio_cash_flows(self, portfolio_id: str) -> list[dict]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT cash_flow_id::text,portfolio_id,flow_date,flow_type,amount,reference_type,reference_id,note,created_at FROM portfolio_cash_flows WHERE portfolio_id=%s ORDER BY flow_date,created_at", (portfolio_id,)).fetchall()
        return [{"cash_flow_id": row[0], "portfolio_id": row[1], "flow_date": row[2], "flow_type": row[3], "amount": row[4], "reference_type": row[5], "reference_id": row[6], "note": row[7], "created_at": row[8]} for row in rows]

    def add_portfolio_cash_flow(self, portfolio_id: str, flow_date, amount: float, note: str | None = None) -> str:
        self.initialize()
        amount = float(amount)
        if not isfinite(amount) or abs(amount) <= 1e-12:
            raise ValueError("资金调整金额必须是非零有限值")
        cash_flow_id = str(uuid4())
        with self._connect() as conn:
            portfolio = conn.execute("SELECT 1 FROM research_portfolios WHERE portfolio_id=%s FOR UPDATE", (portfolio_id,)).fetchone()
            if not portfolio:
                raise KeyError(f"Unknown portfolio: {portfolio_id}")
            inception_date = conn.execute("SELECT flow_date FROM portfolio_cash_flows WHERE portfolio_id=%s AND flow_type='INITIAL_CAPITAL'", (portfolio_id,)).fetchone()[0]
            if flow_date < inception_date:
                raise ValueError("资金流水不能早于组合成立日")
            net_invested = float(conn.execute("SELECT COALESCE(sum(amount),0) FROM portfolio_cash_flows WHERE portfolio_id=%s", (portfolio_id,)).fetchone()[0])
            if net_invested + amount <= 0:
                raise ValueError("资金流出不能使累计净投入小于等于 0")
            conn.execute("INSERT INTO portfolio_cash_flows (cash_flow_id,portfolio_id,flow_date,flow_type,amount,reference_type,reference_id,note) VALUES (%s,%s,%s,%s,%s,'MANUAL_CASH_FLOW',%s,%s)", (cash_flow_id, portfolio_id, flow_date, "CAPITAL_IN" if amount > 0 else "CAPITAL_OUT", amount, cash_flow_id, note))
        return cash_flow_id

    def save_portfolio_target_revision(self, portfolio_id: str, signal_date, targets: Mapping[str, float], note: str | None = None) -> str:
        from .portfolio import validate_target_weights

        weights = validate_target_weights(targets)
        self.initialize()
        revision_id = str(uuid4())
        with self._connect() as conn:
            portfolio = conn.execute("SELECT status FROM research_portfolios WHERE portfolio_id=%s FOR UPDATE", (portfolio_id,)).fetchone()
            if not portfolio:
                raise KeyError(f"Unknown portfolio: {portfolio_id}")
            if portfolio[0] != "ACTIVE":
                raise ValueError("已归档组合不能保存新权重")
            unknown = [code for code in weights if not conn.execute("SELECT 1 FROM securities WHERE ts_code=%s", (code,)).fetchone()]
            if unknown:
                raise ValueError("组合成员必须属于当前证券池")
            previous = conn.execute("SELECT revision_id FROM portfolio_target_revisions WHERE portfolio_id=%s ORDER BY revision_no DESC LIMIT 1", (portfolio_id,)).fetchone()
            if previous:
                rows = conn.execute("SELECT ts_code,target_weight FROM portfolio_target_items WHERE revision_id=%s", (previous[0],)).fetchall()
            else:
                rows = conn.execute("SELECT ts_code,weight FROM research_portfolio_positions WHERE portfolio_id=%s", (portfolio_id,)).fetchall()
            previous_map = {row[0]: float(row[1]) for row in rows}
            revision_no = conn.execute("SELECT COALESCE(max(revision_no),0)+1 FROM portfolio_target_revisions WHERE portfolio_id=%s", (portfolio_id,)).fetchone()[0]
            conn.execute("UPDATE portfolio_rebalance_orders SET status='SUPERSEDED',reason='已由更新的目标权重版本取代',updated_at=NOW() WHERE portfolio_id=%s AND status IN ('PENDING','PARTIAL')", (portfolio_id,))
            conn.execute("INSERT INTO portfolio_target_revisions (revision_id,portfolio_id,revision_no,signal_date,status,note) VALUES (%s,%s,%s,%s,'PENDING',%s)", (revision_id, portfolio_id, revision_no, signal_date, note))
            for code, weight in sorted(weights.items()):
                conn.execute("INSERT INTO portfolio_target_items (revision_id,ts_code,target_weight) VALUES (%s,%s,%s)", (revision_id, code, weight))
            for code in sorted(set(previous_map) | set(weights)):
                old_weight, target_weight = previous_map.get(code, 0.0), weights.get(code, 0.0)
                if abs(target_weight - old_weight) <= 1e-12:
                    continue
                conn.execute("INSERT INTO portfolio_rebalance_orders (order_id,portfolio_id,revision_id,ts_code,side,target_weight,status) VALUES (%s,%s,%s,%s,%s,%s,'PENDING')", (str(uuid4()), portfolio_id, revision_id, code, "BUY" if target_weight > old_weight else "SELL", target_weight))
            conn.execute("UPDATE research_portfolios SET updated_at=NOW() WHERE portfolio_id=%s", (portfolio_id,))
        return revision_id

    def list_portfolio_target_revisions(self, portfolio_id: str) -> list[dict]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT revision_id::text,portfolio_id,revision_no,signal_date,status,note,created_at FROM portfolio_target_revisions WHERE portfolio_id=%s ORDER BY revision_no", (portfolio_id,)).fetchall()
        return [{"revision_id": row[0], "portfolio_id": row[1], "revision_no": row[2], "signal_date": row[3], "status": row[4], "note": row[5], "created_at": row[6]} for row in rows]

    def get_portfolio_target_items(self, revision_id: str) -> list[dict]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT revision_id::text,ts_code,target_weight FROM portfolio_target_items WHERE revision_id=%s ORDER BY ts_code", (revision_id,)).fetchall()
        return [{"revision_id": row[0], "ts_code": row[1], "target_weight": row[2]} for row in rows]

    def list_portfolio_orders(self, portfolio_id: str, revision_id: str | None = None) -> list[dict]:
        self.initialize()
        clause, params = (" AND revision_id=%s", (portfolio_id, revision_id)) if revision_id else ("", (portfolio_id,))
        with self._connect() as conn:
            rows = conn.execute(f"SELECT order_id::text,portfolio_id,revision_id::text,ts_code,side,target_weight,target_quantity,remaining_quantity,planned_trade_date,status,reason,created_at,updated_at FROM portfolio_rebalance_orders WHERE portfolio_id=%s{clause} ORDER BY created_at,ts_code", params).fetchall()
        keys = ("order_id", "portfolio_id", "revision_id", "ts_code", "side", "target_weight", "target_quantity", "remaining_quantity", "planned_trade_date", "status", "reason", "created_at", "updated_at")
        return [dict(zip(keys, row)) for row in rows]

    def update_portfolio_order(self, order_id: str, **changes) -> None:
        allowed = {"side", "target_quantity", "remaining_quantity", "planned_trade_date", "status", "reason"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise ValueError(f"Unsupported portfolio order fields: {sorted(unexpected)}")
        if not changes:
            return
        assignments = ",".join(f"{key}=%s" for key in changes)
        with self._connect() as conn:
            conn.execute(f"UPDATE portfolio_rebalance_orders SET {assignments},updated_at=NOW() WHERE order_id=%s", (*changes.values(), order_id))

    def update_portfolio_revision_status(self, revision_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE portfolio_target_revisions SET status=%s WHERE revision_id=%s", (status, revision_id))

    def record_portfolio_trade(self, trade: Mapping) -> str:
        trade_id = str(trade.get("trade_id") or uuid4())
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO portfolio_trades (trade_id,order_id,portfolio_id,revision_id,ts_code,trade_date,side,quantity,price,gross_amount,fee_bps,fee_amount) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (order_id,trade_date) DO UPDATE SET trade_id=portfolio_trades.trade_id RETURNING trade_id::text",
                (trade_id, trade["order_id"], trade["portfolio_id"], trade["revision_id"], trade["ts_code"], trade["trade_date"], trade["side"], trade["quantity"], trade["price"], trade["gross_amount"], trade["fee_bps"], trade["fee_amount"]),
            ).fetchone()
        return row[0]

    def list_portfolio_trades(self, portfolio_id: str) -> list[dict]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT trade_id::text,order_id::text,portfolio_id,revision_id::text,ts_code,trade_date,side,quantity,price,gross_amount,fee_bps,fee_amount,created_at FROM portfolio_trades WHERE portfolio_id=%s ORDER BY trade_date,created_at", (portfolio_id,)).fetchall()
        keys = ("trade_id", "order_id", "portfolio_id", "revision_id", "ts_code", "trade_date", "side", "quantity", "price", "gross_amount", "fee_bps", "fee_amount", "created_at")
        return [dict(zip(keys, row)) for row in rows]

    def upsert_portfolio_nav(self, row: Mapping) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute("INSERT INTO portfolio_daily_nav (portfolio_id,valuation_date,cash,market_value,total_value,nav,benchmark_nav,status,coverage,reason,calculation_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (portfolio_id,valuation_date) DO UPDATE SET cash=EXCLUDED.cash,market_value=EXCLUDED.market_value,total_value=EXCLUDED.total_value,nav=EXCLUDED.nav,benchmark_nav=EXCLUDED.benchmark_nav,status=EXCLUDED.status,coverage=EXCLUDED.coverage,reason=EXCLUDED.reason,calculation_version=EXCLUDED.calculation_version,updated_at=NOW()", (row["portfolio_id"], row["valuation_date"], row["cash"], row.get("market_value"), row.get("total_value"), row.get("nav"), row.get("benchmark_nav"), row["status"], row.get("coverage"), row.get("reason"), row["calculation_version"]))

    def list_portfolio_nav(self, portfolio_id: str) -> list[dict]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute("SELECT portfolio_id,valuation_date,cash,market_value,total_value,nav,benchmark_nav,status,coverage,reason,calculation_version,updated_at FROM portfolio_daily_nav WHERE portfolio_id=%s ORDER BY valuation_date", (portfolio_id,)).fetchall()
        keys = ("portfolio_id", "valuation_date", "cash", "market_value", "total_value", "nav", "benchmark_nav", "status", "coverage", "reason", "calculation_version", "updated_at")
        return [dict(zip(keys, row)) for row in rows]

    def latest_factor_snapshot_before(self, as_of_date, status: str = "completed") -> dict | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute("SELECT snapshot_id::text,as_of_date,factor_version,pit_version,universe_version,status,coverage,audit,error FROM factor_snapshots WHERE as_of_date<=%s AND lower(status)=lower(%s) ORDER BY as_of_date DESC,created_at DESC LIMIT 1", (as_of_date, status)).fetchone()
            if not row:
                return None
            items = conn.execute("SELECT ts_code,factors,availability,audit FROM factor_snapshot_items WHERE snapshot_id=%s ORDER BY ts_code", (row[0],)).fetchall()
        return {"snapshot_id": row[0], "as_of_date": row[1], "factor_version": row[2], "pit_version": row[3], "universe_version": row[4], "status": row[5], "coverage": row[6], "audit": row[7] or {}, "error": row[8], "items": [{"ts_code": item[0], "factors": item[1], "availability": item[2], "audit": item[3] or {}} for item in items]}

    def upsert_portfolio_position(self, portfolio_id: str, ts_code: str, weight: float, note: str | None = None) -> None:
        if not isfinite(float(weight)) or float(weight) < 0 or float(weight) > 1:
            raise ValueError("组合权重必须在 0 到 100% 之间")
        self._ensure_portfolio(portfolio_id)
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM securities WHERE ts_code=%s", (ts_code,)).fetchone()
            if not exists:
                raise ValueError("组合成员必须属于当前证券池")
            conn.execute(
                "INSERT INTO research_portfolio_positions (portfolio_id,ts_code,weight,note,updated_at) VALUES (%s,%s,%s,%s,NOW()) ON CONFLICT (portfolio_id,ts_code) DO UPDATE SET weight=EXCLUDED.weight,note=EXCLUDED.note,updated_at=NOW()",
                (portfolio_id, ts_code, float(weight), note),
            )

    def delete_portfolio_position(self, portfolio_id: str, ts_code: str) -> None:
        self._ensure_portfolio(portfolio_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM research_portfolio_positions WHERE portfolio_id=%s AND ts_code=%s", (portfolio_id, ts_code))

    def get_portfolio_positions(self, portfolio_id: str = "default") -> list[dict]:
        self._ensure_portfolio(portfolio_id)
        with self._connect() as conn:
            rows = conn.execute("SELECT portfolio_id,ts_code,weight,note FROM research_portfolio_positions WHERE portfolio_id=%s ORDER BY ts_code", (portfolio_id,)).fetchall()
        return [{"portfolio_id": row[0], "ts_code": row[1], "weight": row[2], "note": row[3]} for row in rows]
