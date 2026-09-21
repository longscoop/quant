from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isfinite
from uuid import uuid4
from .providers import DataProvider
from .types import AuditRun, BenchmarkBar, FinancialRecord, IndustryRecord, PriceBar, RawRecord, Security, ValuationBar


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS securities (ts_code text PRIMARY KEY, name text NOT NULL, list_date date NOT NULL, is_st boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS price_bars (ts_code text NOT NULL, trade_date date NOT NULL, close double precision NOT NULL, adj_factor double precision NOT NULL, suspended boolean NOT NULL, limit_up boolean NOT NULL, limit_down boolean NOT NULL, volume double precision NOT NULL, open double precision, high double precision, low double precision, PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS financials (ts_code text NOT NULL, report_period date NOT NULL, ann_date date NOT NULL, revenue double precision NOT NULL, net_profit double precision NOT NULL, roe double precision NOT NULL, gross_margin double precision NOT NULL, operating_cashflow double precision NOT NULL, debt_ratio double precision NOT NULL, pe double precision NOT NULL, pb double precision NOT NULL, ps double precision NOT NULL, dividend_yield double precision NOT NULL, PRIMARY KEY (ts_code, report_period, ann_date));
CREATE TABLE IF NOT EXISTS industries (ts_code text NOT NULL, industry text NOT NULL, effective_date date NOT NULL, PRIMARY KEY (ts_code, effective_date));
CREATE TABLE IF NOT EXISTS ingestion_audit (dataset text NOT NULL, source text NOT NULL, status text NOT NULL, row_count integer NOT NULL, created_at timestamptz NOT NULL, error text);
CREATE TABLE IF NOT EXISTS index_members (index_code text NOT NULL, ts_code text NOT NULL, effective_date date NOT NULL, PRIMARY KEY (index_code, ts_code, effective_date));
CREATE TABLE IF NOT EXISTS benchmark_bars (ts_code text NOT NULL, trade_date date NOT NULL, close double precision NOT NULL, PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS research_runs (run_id uuid PRIMARY KEY, run_type text NOT NULL, status text NOT NULL, parameters jsonb NOT NULL DEFAULT '{}'::jsonb, payload jsonb NOT NULL DEFAULT '{}'::jsonb, error text, created_at timestamptz NOT NULL, completed_at timestamptz);
CREATE TABLE IF NOT EXISTS valuation_bars (ts_code text NOT NULL, trade_date date NOT NULL, pe_ttm double precision, pb double precision, ps_ttm double precision, dividend_yield double precision, turnover_rate double precision, data_version text NOT NULL DEFAULT 'pit_v1.0', PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS sync_checkpoints (sync_key text NOT NULL, dataset text NOT NULL, ts_code text NOT NULL, completed_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (sync_key, dataset, ts_code));
CREATE TABLE IF NOT EXISTS source_records (dataset text NOT NULL, ts_code text, record_key text NOT NULL, report_period date, ann_date date, first_ann_date date, source_version text, payload jsonb NOT NULL, retrieved_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (dataset, record_key));
CREATE TABLE IF NOT EXISTS sync_state (universe text NOT NULL, dataset text NOT NULL, watermark date, status text NOT NULL, run_id uuid, updated_at timestamptz NOT NULL DEFAULT NOW(), error text, PRIMARY KEY (universe, dataset));
CREATE TABLE IF NOT EXISTS sync_units (run_id uuid NOT NULL, dataset text NOT NULL, ts_code text NOT NULL, status text NOT NULL, range_start date, range_end date, row_count integer NOT NULL DEFAULT 0, error text, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (run_id, dataset, ts_code));
CREATE TABLE IF NOT EXISTS data_quality_checks (check_id uuid PRIMARY KEY, universe text NOT NULL, report jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS price_limits (ts_code text NOT NULL, trade_date date NOT NULL, up_limit double precision, down_limit double precision, PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS trading_suspensions (ts_code text NOT NULL, suspend_date date NOT NULL, resume_date date, reason text, PRIMARY KEY (ts_code, suspend_date));
CREATE TABLE IF NOT EXISTS research_portfolios (portfolio_id text PRIMARY KEY, name text NOT NULL, updated_at timestamptz NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS research_portfolio_positions (portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id) ON DELETE CASCADE, ts_code text NOT NULL REFERENCES securities(ts_code) ON DELETE CASCADE, weight double precision NOT NULL, note text, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY (portfolio_id, ts_code));
CREATE INDEX IF NOT EXISTS price_bars_code_date_idx ON price_bars (ts_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS industries_code_effective_idx ON industries (ts_code, effective_date DESC);
CREATE INDEX IF NOT EXISTS source_records_dataset_code_idx ON source_records (dataset, ts_code);
"""


class InMemoryStore:
    def __init__(self):
        self.securities: dict[str, Security] = {}
        self.prices: dict[tuple[str, object], PriceBar] = {}
        self.financials: dict[tuple[str, object, object], FinancialRecord] = {}
        self.industries: dict[tuple[str, object], IndustryRecord] = {}
        self.index_members: dict[tuple[str, str, object], tuple[str, str, object]] = {}
        self.benchmarks: dict[tuple[str, object], BenchmarkBar] = {}
        self.valuations: dict[tuple[str, object], ValuationBar] = {}
        self.audit: list[AuditRun] = []
        self.portfolio_positions: dict[tuple[str, str], dict] = {}

    def sync(self, provider: DataProvider) -> None:
        datasets = (("securities", provider.fetch_securities(), self.securities, lambda r: r.ts_code), ("prices", provider.fetch_prices(), self.prices, lambda r: (r.ts_code, r.trade_date)), ("financials", provider.fetch_financials(), self.financials, lambda r: (r.ts_code, r.report_period, r.ann_date)), ("industries", provider.fetch_industries(), self.industries, lambda r: (r.ts_code, r.effective_date)))
        for name, rows, target, key in datasets:
            for row in rows:
                target[key(row)] = row
            self.audit.append(AuditRun(name, provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "fetch_valuations"):
            rows = provider.fetch_valuations()
            for row in rows:
                self.valuations[(row.ts_code, row.trade_date)] = row
            self.audit.append(AuditRun("valuations", provider.__class__.__name__, "success", len(rows)))
        if hasattr(provider, "fetch_benchmark"):
            rows = provider.fetch_benchmark()
            for row in rows:
                self.benchmarks[(row.ts_code, row.trade_date)] = row
            self.audit.append(AuditRun("benchmark", provider.__class__.__name__, "success", len(rows)))

    def counts(self) -> dict[str, int]:
        return {"securities": len(self.securities), "prices": len(self.prices), "financials": len(self.financials), "industries": len(self.industries), "valuation_count": len(self.valuations), "benchmark_count": len(self.benchmarks)}

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

    def sync_index_members(self, index_code: str, members: list[tuple[str, object]]) -> None:
        for ts_code, effective_date in members:
            self.index_members[(index_code, ts_code, effective_date)] = (index_code, ts_code, effective_date)

    def members_for(self, index_code: str, as_of_date):
        dates = [day for index, _, day in self.index_members if index == index_code and day <= as_of_date]
        if not dates:
            return []
        snapshot_date = max(dates)
        return [code for index, code, day in self.index_members if index == index_code and day == snapshot_date]

    def benchmark_for(self, code: str = "000300.SH"):
        return sorted((bar for (c, _), bar in self.benchmarks.items() if c == code), key=lambda bar: bar.trade_date)

    def valuations_for(self, code: str):
        return sorted((bar for (c, _), bar in self.valuations.items() if c == code), key=lambda bar: bar.trade_date)

    def upsert_portfolio_position(self, portfolio_id: str, ts_code: str, weight: float, note: str | None = None) -> None:
        if not isfinite(float(weight)) or float(weight) < 0 or float(weight) > 1:
            raise ValueError("组合权重必须在 0 到 100% 之间")
        if ts_code not in self.securities:
            raise ValueError("组合成员必须属于当前证券池")
        self.portfolio_positions[(portfolio_id, ts_code)] = {"portfolio_id": portfolio_id, "ts_code": ts_code, "weight": float(weight), "note": note}

    def delete_portfolio_position(self, portfolio_id: str, ts_code: str) -> None:
        self.portfolio_positions.pop((portfolio_id, ts_code), None)

    def get_portfolio_positions(self, portfolio_id: str = "default") -> list[dict]:
        return sorted((dict(row) for (pid, _), row in self.portfolio_positions.items() if pid == portfolio_id), key=lambda row: row["ts_code"])


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
            for column in ("revenue", "net_profit", "roe", "gross_margin", "operating_cashflow", "debt_ratio", "pe", "pb", "ps", "dividend_yield"):
                conn.execute(f"ALTER TABLE financials ALTER COLUMN {column} DROP NOT NULL")
            for column in ("roic double precision", "current_ratio double precision", "free_cashflow double precision", "deduct_net_profit double precision", "data_version text NOT NULL DEFAULT 'legacy-v0'"):
                conn.execute(f"ALTER TABLE financials ADD COLUMN IF NOT EXISTS {column}")
            for column in ("open double precision", "high double precision", "low double precision"):
                conn.execute(f"ALTER TABLE price_bars ADD COLUMN IF NOT EXISTS {column}")
            for column in ("metadata jsonb NOT NULL DEFAULT '{}'::jsonb",):
                conn.execute(f"ALTER TABLE securities ADD COLUMN IF NOT EXISTS {column}")
            conn.execute("ALTER TABLE index_members ADD COLUMN IF NOT EXISTS weight double precision")
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
                (universe, dataset, watermark, status, run_id, error),
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
            for r in rows: conn.execute("INSERT INTO securities (ts_code,name,list_date,is_st) VALUES (%s,%s,%s,%s) ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, list_date=EXCLUDED.list_date, is_st=EXCLUDED.is_st", (r.ts_code, r.name, r.list_date, r.is_st))
            if hasattr(provider, "raw_base_records"):
                self._persist_raw_records(conn, provider.raw_base_records())
            if hasattr(provider, "fetch_reference_records"):
                self._persist_raw_records(conn, provider.fetch_reference_records())
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
                    self._record_sync_checkpoint(conn, sync_key, "prices", code)
                    conn.commit()
            else:
                for r in provider.fetch_prices():
                    conn.execute(price_sql, (r.ts_code, r.trade_date, r.close, r.adj_factor, r.suspended, r.limit_up, r.limit_down, r.volume, r.open, r.high, r.low))
                conn.commit()
            financial_sql = "INSERT INTO financials (ts_code,report_period,ann_date,revenue,net_profit,roe,gross_margin,operating_cashflow,debt_ratio,pe,pb,ps,dividend_yield,roic,current_ratio,free_cashflow,deduct_net_profit,data_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ts_code,report_period,ann_date) DO UPDATE SET revenue=EXCLUDED.revenue, net_profit=EXCLUDED.net_profit, roe=EXCLUDED.roe, gross_margin=EXCLUDED.gross_margin, operating_cashflow=EXCLUDED.operating_cashflow, debt_ratio=EXCLUDED.debt_ratio, pe=EXCLUDED.pe, pb=EXCLUDED.pb, ps=EXCLUDED.ps, dividend_yield=EXCLUDED.dividend_yield, roic=EXCLUDED.roic, current_ratio=EXCLUDED.current_ratio, free_cashflow=EXCLUDED.free_cashflow, deduct_net_profit=EXCLUDED.deduct_net_profit, data_version=EXCLUDED.data_version"
            if hasattr(provider, "iter_financial_batches"):
                for code, batch in provider.iter_financial_batches(skip_codes=financial_skip):
                    for r in batch:
                        conn.execute(financial_sql, (r.ts_code, r.report_period, r.ann_date, r.revenue, r.net_profit, r.roe, r.gross_margin, r.operating_cashflow, r.debt_ratio, r.pe, r.pb, r.ps, r.dividend_yield, r.roic, r.current_ratio, r.free_cashflow, r.deduct_net_profit, r.data_version))
                    if hasattr(provider, "raw_financial_records_for"):
                        self._persist_raw_records(conn, provider.raw_financial_records_for(code))
                    self._record_sync_checkpoint(conn, sync_key, "financials", code)
                    conn.commit()
            else:
                for r in provider.fetch_financials():
                    conn.execute(financial_sql, (r.ts_code, r.report_period, r.ann_date, r.revenue, r.net_profit, r.roe, r.gross_margin, r.operating_cashflow, r.debt_ratio, r.pe, r.pb, r.ps, r.dividend_yield, r.roic, r.current_ratio, r.free_cashflow, r.deduct_net_profit, r.data_version))
                conn.commit()
            for r in provider.fetch_industries(): conn.execute("INSERT INTO industries VALUES (%s,%s,%s) ON CONFLICT (ts_code,effective_date) DO UPDATE SET industry=EXCLUDED.industry", (r.ts_code,r.industry,r.effective_date))
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
                for r in provider.fetch_benchmark(): conn.execute("INSERT INTO benchmark_bars VALUES (%s,%s,%s) ON CONFLICT (ts_code,trade_date) DO UPDATE SET close=EXCLUDED.close", (r.ts_code, r.trade_date, r.close))

    def load_memory(self) -> InMemoryStore:
        """Materialize a read-only research snapshot for PIT and analytics services."""
        store = InMemoryStore()
        with self._connect() as conn:
            for row in conn.execute("SELECT ts_code,name,list_date,is_st FROM securities"):
                record = Security(*row); store.securities[record.ts_code] = record
            for row in conn.execute("SELECT ts_code,trade_date,close,adj_factor,suspended,limit_up,limit_down,volume,open,high,low FROM price_bars"):
                record = PriceBar(*row); store.prices[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT ts_code,report_period,ann_date,revenue,net_profit,roe,gross_margin,operating_cashflow,debt_ratio,pe,pb,ps,dividend_yield,roic,current_ratio,free_cashflow,deduct_net_profit,data_version FROM financials"):
                record = FinancialRecord(*row); store.financials[(record.ts_code, record.report_period, record.ann_date)] = record
            for row in conn.execute("SELECT ts_code,industry,effective_date FROM industries"):
                record = IndustryRecord(*row); store.industries[(record.ts_code, record.effective_date)] = record
            for row in conn.execute("SELECT index_code,ts_code,effective_date FROM index_members"):
                index_code, ts_code, effective_date = row
                store.index_members[(index_code, ts_code, effective_date)] = row
            for row in conn.execute("SELECT ts_code,trade_date,close FROM benchmark_bars"):
                record = BenchmarkBar(*row); store.benchmarks[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT ts_code,trade_date,pe_ttm,pb,ps_ttm,dividend_yield,turnover_rate,data_version FROM valuation_bars"):
                record = ValuationBar(*row); store.valuations[(record.ts_code, record.trade_date)] = record
            for row in conn.execute("SELECT dataset,source,status,row_count,created_at,error FROM ingestion_audit ORDER BY created_at"):
                store.audit.append(AuditRun(*row))
        return store

    def load_page_memory(self, *, price_days: int = 0, codes: list[str] | None = None, include_financials: bool = False, include_valuations: bool = False) -> InMemoryStore:
        """Load only the small snapshot required by an interactive page.

        Full materialization remains available for PIT research jobs; page reads
        must never deserialize the complete historical price table.
        """
        store = InMemoryStore()
        with self._connect() as conn:
            for row in conn.execute("SELECT ts_code,name,list_date,is_st FROM securities ORDER BY ts_code"):
                record = Security(*row); store.securities[record.ts_code] = record
            for row in conn.execute("SELECT DISTINCT ON (ts_code) ts_code,industry,effective_date FROM industries ORDER BY ts_code,effective_date DESC"):
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
                for row in conn.execute(f"WITH days AS (SELECT DISTINCT trade_date FROM benchmark_bars ORDER BY trade_date DESC LIMIT {horizon}) SELECT ts_code,trade_date,close FROM benchmark_bars WHERE trade_date >= (SELECT min(trade_date) FROM days) ORDER BY trade_date"):
                    record = BenchmarkBar(*row); store.benchmarks[(record.ts_code, record.trade_date)] = record
            if include_financials and codes:
                for row in conn.execute("SELECT ts_code,report_period,ann_date,revenue,net_profit,roe,gross_margin,operating_cashflow,debt_ratio,pe,pb,ps,dividend_yield,roic,current_ratio,free_cashflow,deduct_net_profit,data_version FROM financials WHERE ts_code = ANY(%s) ORDER BY report_period,ann_date", (codes,)):
                    record = FinancialRecord(*row); store.financials[(record.ts_code, record.report_period, record.ann_date)] = record
            if include_valuations and codes:
                for row in conn.execute("SELECT ts_code,trade_date,pe_ttm,pb,ps_ttm,dividend_yield,turnover_rate,data_version FROM valuation_bars WHERE ts_code = ANY(%s) ORDER BY trade_date", (codes,)):
                    record = ValuationBar(*row); store.valuations[(record.ts_code, record.trade_date)] = record
        return store

    def sync_index_members(self, index_code: str, members: list[tuple[str, object]]) -> None:
        self.initialize()
        with self._connect() as conn:
            for ts_code, effective_date in members:
                conn.execute("INSERT INTO index_members (index_code,ts_code,effective_date) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (index_code, ts_code, effective_date))

    def record_run(self, run_type: str, status: str, parameters: dict | None = None, payload: dict | None = None, error: str | None = None) -> str:
        self.initialize()
        run_id = str(uuid4())
        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO research_runs (run_id,run_type,status,parameters,payload,error,created_at,completed_at) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)",
                (run_id, run_type, status, json.dumps(parameters or {}, default=str), json.dumps(payload or {}, default=str), error, now, now if status != "running" else None),
            )
        return run_id

    def update_run_progress(self, run_id: str, progress: dict) -> None:
        """Persist the latest long-running sync progress so refreshes retain it."""
        self.initialize()
        with self._connect() as conn:
            conn.execute("UPDATE research_runs SET payload = payload || %s::jsonb WHERE run_id=%s", (json.dumps({"progress": progress}, default=str), run_id))

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
            conn.execute("UPDATE research_runs SET status=%s,payload=payload || %s::jsonb,error=%s,completed_at=%s WHERE run_id=%s", (status, json.dumps(payload or {}, default=str), error, datetime.now(UTC), run_id))

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
