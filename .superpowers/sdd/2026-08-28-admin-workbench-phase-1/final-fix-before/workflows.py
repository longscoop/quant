"""Database-backed research workflows shared by CLI and Streamlit."""
from __future__ import annotations

from datetime import UTC, date, datetime
import time

from .backtest import BacktestConfig, run_backtest
from .factors import FactorEngine
from .factors_v1 import build_rankings
from .model import ModelTrainer
from .pit import PITRepository
from .providers import TushareProvider
from .ingestion import SyncMode, sync_window
from .scheduler import DailySchedule, SHANGHAI
from .types import RawRecord
from .storage import PostgresStore
from .types import FactorRow, FeatureSnapshot, PredictionRow, PredictionSnapshot


def resume_datasets_for_sync(run: dict) -> set[str]:
    """Return datasets known to be complete before a failed sync stopped."""
    if run.get("status") != "failed":
        return set()
    progress = (run.get("payload") or {}).get("progress") or {}
    phase = progress.get("phase")
    completed = set()
    if phase in {"财务报表", "每日估值", "写入数据库"}:
        completed.add("prices")
    if phase in {"每日估值", "写入数据库"}:
        completed.add("financials")
    if phase == "写入数据库":
        completed.add("valuations")
    return completed


def resume_code_count_for_sync(run: dict, dataset: str) -> int:
    """Return the committed prefix length for a legacy interrupted phase."""
    if run.get("status") != "failed":
        return 0
    progress = (run.get("payload") or {}).get("progress") or {}
    if dataset != "prices" or progress.get("phase") != "行情与复权":
        return 0
    try:
        return max(0, int(progress.get("current", 0)))
    except (TypeError, ValueError):
        return 0


def sync_hs300(store: PostgresStore, token: str, start_date: date, end_date: date, progress=None, request_interval: float = 0.35) -> str:
    return sync_universe(store, token, universe="hs300", mode=SyncMode.BACKFILL, start_date=start_date, end_date=end_date, progress=progress, request_interval=request_interval)


def sync_universe(store: PostgresStore, token: str, *, universe: str = "hs300", mode: SyncMode | str = SyncMode.INCREMENTAL, start_date: date | None = None, end_date: date | None = None, progress=None, request_interval: float = 0.35, _locked: bool = False) -> str:
    """Synchronize a bounded HS300 universe; failures are persisted for the UI."""
    if universe != "hs300":
        raise ValueError("首期仅支持 universe=hs300")
    if not _locked and hasattr(store, "sync_lock"):
        with store.sync_lock(universe) as acquired:
            if not acquired:
                raise RuntimeError(f"证券池 {universe} 已有同步任务在运行")
            return sync_universe(store, token, universe=universe, mode=mode, start_date=start_date, end_date=end_date, progress=progress, request_interval=request_interval, _locked=True)
    mode = SyncMode(mode)
    end_date = end_date or date.today()
    market_window = sync_window(mode, "market", end_date, start_date=start_date)
    financial_window = sync_window(mode, "financial", end_date, start_date=start_date)
    parameters = {"index": "000300.SH", "universe": universe, "mode": mode.value, "start_date": market_window.start, "end_date": end_date, "windows": {"market": market_window.__dict__, "financial": financial_window.__dict__}}
    previous = next(
        (
            run for run in store.list_runs()
            if run.get("run_type") == "sync"
            and run.get("status") == "failed"
            and str((run.get("parameters") or {}).get("index")) == "000300.SH"
            and str((run.get("parameters") or {}).get("start_date")) == market_window.start.isoformat()
            and str((run.get("parameters") or {}).get("end_date")) == end_date.isoformat()
        ),
        None,
    )
    resume_datasets = resume_datasets_for_sync(previous) if previous else set()
    run_id = store.record_run("sync", "running", parameters, {"progress": {"phase": "准备同步", "current": 0, "total": 1}, "resume_from_run_id": previous.get("run_id") if previous else None, "resume_datasets": sorted(resume_datasets)})
    try:
        def report(phase, current, total, detail=None):
            store.update_run_progress(run_id, {"phase": phase, "current": current, "total": total, "detail": detail, "updated_at": datetime.now(UTC).isoformat()})
            if progress:
                try:
                    progress(phase, current, total, detail)
                except TypeError:
                    progress(phase, current, total)

        report("获取沪深300成分", 0, 1)
        bootstrap = TushareProvider(token, market_window.start, end_date, request_interval=request_interval)
        weights = bootstrap.index_weight(index_code="000300.SH", start_date=market_window.start.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
        members = [(getattr(row, "con_code"), date.fromisoformat(getattr(row, "trade_date"))) for row in weights.itertuples() if getattr(row, "con_code", None) and getattr(row, "trade_date", None)]
        codes = sorted({code for code, _ in members})
        if not codes:
            raise RuntimeError("沪深300成分接口未返回证券代码；请检查 Tushare Token 权限或日期范围")
        report("获取沪深300成分", 1, 1)
        provider = TushareProvider(token, market_window.start, end_date, universe=codes, progress=report, request_interval=request_interval, windows={"market": market_window, "financial": financial_window})
        sync_key = f"hs300:{mode.value}:{market_window.start.isoformat()}:{end_date.isoformat()}"
        skip_codes = {dataset: set(codes) for dataset in resume_datasets}
        price_prefix = min(resume_code_count_for_sync(previous, "prices") if previous else 0, len(codes))
        if price_prefix:
            skip_codes["prices"] = set(codes[:price_prefix])
        store.sync(provider, sync_key=sync_key, skip_codes=skip_codes)
        store.sync_index_members("000300.SH", members)
        if hasattr(store, "persist_raw_records"):
            store.persist_raw_records([RawRecord("index_weight", code, {"index_code": "000300.SH", "trade_date": effective.isoformat()}, report_period=effective) for code, effective in members])
        report("写入数据库", 1, 1)
        counts = store.status()["counts"] if hasattr(store, "status") else {}
        quality = store.data_quality(universe) if hasattr(store, "data_quality") else {}
        status = "partial" if provider.errors else "completed"
        for dataset, watermark in (("market", market_window.end), ("financial", financial_window.end), ("base", end_date)):
            if hasattr(store, "set_sync_state"):
                store.set_sync_state(universe, dataset, watermark if not provider.errors else None, status, run_id, "; ".join(item["error"] for item in provider.errors) or None)
        store.finish_run(run_id, status, {"securities": len(codes), "member_versions": len(members), "row_counts": counts, "quality": quality, "dataset_errors": provider.errors})
        return run_id
    except Exception as exc:
        try:
            counts = store.status().get("counts", {}) if hasattr(store, "status") else {}
        except Exception:
            counts = {}
        store.finish_run(run_id, "failed", {"row_counts": counts, "status_reason": "sync_failed"}, error=str(exc))
        return run_id


def run_scheduled_once(store: PostgresStore, token: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(SHANGHAI)
    schedule = DailySchedule()
    last = next((item for item in store.sync_states("hs300") if item["dataset"] == "scheduler"), None) if hasattr(store, "sync_states") else None
    last_date = last.get("watermark") if last else None
    if hasattr(store, "persist_raw_records"):
        calendar = TushareProvider(token, now.date(), now.date()).fetch_trade_calendar(now.date())
        store.persist_raw_records(calendar)
    is_open = store.is_trade_day(now.date()) if hasattr(store, "is_trade_day") else now.weekday() < 5
    if not schedule.is_due(now, is_trade_day=is_open, last_run_date=last_date):
        return {"status": "not_due", "date": now.date(), "is_trade_day": is_open}
    run_id = sync_universe(store, token, universe="hs300", mode=SyncMode.INCREMENTAL, end_date=now.date())
    if hasattr(store, "set_sync_state"):
        store.set_sync_state("hs300", "scheduler", now.date(), "completed", run_id)
    return {"status": "started", "run_id": run_id}


def run_scheduler(store: PostgresStore, token: str, poll_seconds: int = 60) -> None:
    while True:
        run_scheduled_once(store, token)
        time.sleep(max(1, poll_seconds))


def build_factor_run(store: PostgresStore, as_of_dates: list[date]) -> str:
    try:
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        trading_dates = {bar.trade_date for bar in memory.prices.values()}
        usable_dates = sorted(set(as_of_dates) & trading_dates)
        has_v1_financials = any(record.data_version == "pit_v1.0" for record in memory.financials.values())
        features = FeatureSnapshot([], {"pit_safe": True, "factor_version": "pit_v1.0", "row_count": 0}) if has_v1_financials else FactorEngine(PITRepository(memory)).build_features(usable_dates)
        rankings = build_rankings(memory, usable_dates[-1]) if usable_dates else []
        metadata = {
            **features.metadata,
            "date_start": usable_dates[0].isoformat() if usable_dates else None,
            "date_end": usable_dates[-1].isoformat() if usable_dates else None,
            "security_count": len({row.ts_code for row in features.rows}) or len({row["ts_code"] for row in rankings}),
            "factor_model_version": "pit_v1.0" if has_v1_financials else "legacy-v0",
            "strategy_template_id": "quality_growth",
            "data_snapshot_id": f"audit-{len(memory.audit)}",
            "universe_id": "hs300",
        }
        payload = {"rows": [{"as_of_date": row.as_of_date, "ts_code": row.ts_code, "values": row.values} for row in features.rows], "rankings": rankings, "metadata": metadata}
        status = "completed" if features.rows or rankings else "not_trainable"
        return store.record_run("factors", status, {"as_of_dates": usable_dates}, payload)
    except Exception as exc:
        return store.record_run("factors", "failed", {"as_of_dates": as_of_dates}, error=str(exc))


def _features_from_run(run: dict) -> FeatureSnapshot:
    if run["run_type"] != "factors" or run["status"] != "completed":
        raise ValueError("请选择已完成的因子运行")
    payload = run["payload"]
    def parse_date(value):
        return value if isinstance(value, date) else date.fromisoformat(value)

    return FeatureSnapshot([FactorRow(parse_date(row["as_of_date"]), row["ts_code"], row["values"]) for row in payload["rows"]], payload["metadata"])


def forward_excess_return_labels(memory, horizon: int = 20, benchmark: str = "000300.SH") -> dict[tuple[date, str], float]:
    """Generate labels only where both security and HS300 prices exist in the future."""
    benchmark_by_date = {bar.trade_date: bar.close for bar in memory.benchmark_for(benchmark)}
    labels: dict[tuple[date, str], float] = {}
    for code in memory.securities:
        bars = memory.prices_for(code)
        for index, bar in enumerate(bars[:-horizon]):
            future = bars[index + horizon]
            if bar.trade_date not in benchmark_by_date or future.trade_date not in benchmark_by_date:
                continue
            stock_return = future.adjusted_close / bar.adjusted_close - 1
            benchmark_return = benchmark_by_date[future.trade_date] / benchmark_by_date[bar.trade_date] - 1
            labels[(bar.trade_date, code)] = stock_return - benchmark_return
    return labels


def train_model_run(store: PostgresStore, factor_run_id: str, prediction_dates: list[date]) -> str:
    try:
        features = _features_from_run(store.get_run(factor_run_id))
        labels = forward_excess_return_labels(store.load_memory())
        prediction = ModelTrainer().fit_predict(features, prediction_dates, labels)
        training_row_count = 0
        for prediction_date in prediction_dates:
            current = [row for row in features.rows if row.as_of_date == prediction_date]
            if not current:
                continue
            train = [row for row in features.rows if row.as_of_date < prediction_date and (row.as_of_date, row.ts_code) in labels]
            if train:
                training_row_count += len(train)
        metadata = {
            **prediction.metadata,
            "status_reason": prediction.metadata.get("status_reason"),
            "training_row_count": training_row_count,
            "prediction_row_count": len(prediction.rows),
        }
        payload = {"rows": [{"as_of_date": row.as_of_date, "ts_code": row.ts_code, "score": row.score} for row in prediction.rows], "metadata": metadata}
        return store.record_run("model", metadata["status"], {"factor_run_id": factor_run_id, "prediction_dates": prediction_dates, "label": "20d_hs300_excess_return", "label_count": len(labels)}, payload)
    except Exception as exc:
        return store.record_run("model", "failed", {"factor_run_id": factor_run_id, "prediction_dates": prediction_dates}, error=str(exc))


def run_backtest_run(
    store: PostgresStore,
    model_run_id: str,
    top_n: int = 30,
    cost_bps: float = 10.0,
    template_id: str = "quality_growth",
    experiment_name: str | None = None,
) -> str:
    try:
        run = store.get_run(model_run_id)
        if run["run_type"] != "model" or run["status"] != "completed":
            raise ValueError("请选择已完成的模型运行")
        payload = run["payload"]
        prediction = PredictionSnapshot([PredictionRow(date.fromisoformat(row["as_of_date"]), row["ts_code"], float(row["score"])) for row in payload["rows"]], payload["metadata"])
        result = run_backtest(BacktestConfig(top_n=top_n, transaction_cost_bps=cost_bps), prediction, PITRepository(store.load_memory()))
        output = {"metrics": result.metrics, "equity_curve": [{"date": day, "value": value} for day, value in result.equity_curve], "benchmark_curve": [{"date": day, "value": value} for day, value in result.benchmark_curve], "excess_curve": [{"date": day, "value": value} for day, value in result.excess_curve], "annual_returns": result.annual_returns, "positions": [position.__dict__ for position in result.positions], "trades": result.trades, "status_reason": result.status_reason}
        status = "completed" if result.status_reason is None else "not_trainable"
        parameters = {"model_run_id": model_run_id, "top_n": top_n, "cost_bps": cost_bps, "frequency": "monthly", "benchmark": "000300.SH", "factor_model_version": "pit_v1.0", "template_id": template_id, "experiment_name": experiment_name or "未命名实验"}
        return store.record_run("backtest", status, parameters, output, error=result.status_reason)
    except Exception as exc:
        return store.record_run("backtest", "failed", {"model_run_id": model_run_id, "top_n": top_n, "cost_bps": cost_bps}, error=str(exc))


def run_portfolio_backtest_run(store: PostgresStore, portfolio_id: str = "default") -> str:
    """Persist a transparent, buy-and-hold research portfolio history separately from strategy experiments."""
    try:
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        positions = store.get_portfolio_positions(portfolio_id)
        from .insights import portfolio_history_rows
        rows = portfolio_history_rows(memory, positions)
        if len(rows) < 2:
            return store.record_run("portfolio_backtest", "not_trainable", {"portfolio_id": portfolio_id, "benchmark": "000300.SH"}, {"equity_curve": [], "benchmark_curve": [], "positions": positions}, error="组合成员与沪深300缺少足够的共同交易日")
        equity = [float(row["研究组合"]) for row in rows]
        benchmark = [float(row["沪深300"]) for row in rows]
        peak, drawdown = 1.0, 0.0
        for value in equity:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1)
        metrics = {"total_return": equity[-1] - 1, "benchmark_return": benchmark[-1] - 1, "excess_return": equity[-1] - benchmark[-1], "max_drawdown": drawdown}
        payload = {
            "metrics": metrics,
            "equity_curve": [{"date": row["日期"], "value": row["研究组合"]} for row in rows],
            "benchmark_curve": [{"date": row["日期"], "value": row["沪深300"]} for row in rows],
            "positions": positions,
        }
        return store.record_run("portfolio_backtest", "completed", {"portfolio_id": portfolio_id, "benchmark": "000300.SH", "method": "buy_and_hold_saved_weights"}, payload)
    except Exception as exc:
        return store.record_run("portfolio_backtest", "failed", {"portfolio_id": portfolio_id}, error=str(exc))
