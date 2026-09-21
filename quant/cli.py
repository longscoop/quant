"""Command-line entry points for scheduled, database-backed research jobs."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path

from .storage import PostgresStore
from .markets import DateSegment, ResearchContext, TimeSplitConfig
from .markets.cn import CN_DEFAULT_CONTEXT, build_cn_market_config
from .workflows import build_factor_run, run_backtest_run, run_model_inference, run_scheduler, run_scheduled_once, sync_benchmark, sync_hs300, sync_universe, train_model_run


def _date(value: str) -> date:
    return date.fromisoformat(value)


DEFAULT_DATABASE_URL = "postgresql://quant:quant@localhost:5432/quant"


def dotenv_value(name: str, env_file: Path | None = None) -> str | None:
    path = env_file or Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def database_url(dsn: str | None, env_file: Path | None = None) -> str:
    """Resolve the local database URL without requiring users to source `.env`."""
    return dsn or os.getenv("DATABASE_URL") or dotenv_value("DATABASE_URL", env_file) or DEFAULT_DATABASE_URL


def tushare_token(value: str | None, env_file: Path | None = None) -> str | None:
    return value or os.getenv("TUSHARE_TOKEN") or dotenv_value("TUSHARE_TOKEN", env_file)


def _store(dsn: str | None) -> PostgresStore:
    return PostgresStore(database_url(dsn))


def main() -> None:
    parser = argparse.ArgumentParser(description="沪深300量化研究工作台任务入口")
    parser.add_argument("--database-url", help="PostgreSQL DSN; defaults to DATABASE_URL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="初始化研究数据库")

    sync = subparsers.add_parser("sync-hs300", help="同步沪深300成分与真实市场数据")
    sync.add_argument("--token", default=tushare_token(None), help="Tushare Token; defaults to TUSHARE_TOKEN")
    sync.add_argument("--start-date", type=_date, default=date.today() - timedelta(days=400))
    sync.add_argument("--end-date", type=_date, default=date.today())

    benchmark = subparsers.add_parser("sync-benchmark", help="补齐指定基准的历史开盘价与收盘价")
    benchmark.add_argument("--token", default=tushare_token(None), help="Tushare Token; defaults to TUSHARE_TOKEN")
    benchmark.add_argument("--benchmark", default="000300.SH")
    benchmark.add_argument("--start-date", type=_date, required=True)
    benchmark.add_argument("--end-date", type=_date, default=date.today())
    benchmark.add_argument("--request-interval", type=float, default=0.35, help="Tushare 请求最小间隔（秒）；默认 0.35")

    universe = subparsers.add_parser("sync-universe", help="按证券池执行历史补数或增量同步")
    universe.add_argument("--token", default=tushare_token(None))
    universe.add_argument("--universe", choices=["hs300"], default="hs300")
    universe.add_argument("--mode", choices=["backfill", "incremental"], default="incremental")
    universe.add_argument("--start-date", type=_date)
    universe.add_argument("--end-date", type=_date, default=date.today())
    universe.add_argument("--request-interval", type=float, default=0.35, help="Tushare 请求最小间隔（秒）；默认 0.35")
    audit = subparsers.add_parser("audit-data", help="输出证券池数据完整性报告")
    audit.add_argument("--universe", choices=["hs300"], default="hs300")
    scheduler = subparsers.add_parser("run-scheduler", help="运行交易日18:30自动同步调度器")
    scheduler.add_argument("--token", default=tushare_token(None))
    scheduler.add_argument("--once", action="store_true", help="仅检查并执行一次，不进入常驻循环")
    scheduler.add_argument("--poll-seconds", type=int, default=60)

    factors = subparsers.add_parser("build-factors", help="从已落库数据构建因子")
    factors.add_argument("--start-date", type=_date, required=True)
    factors.add_argument("--end-date", type=_date, required=True)

    train = subparsers.add_parser("train", help="训练模型并生成预测")
    train.add_argument("--factor-run-id", required=True)
    train.add_argument("--market-id", default=CN_DEFAULT_CONTEXT.market_id)
    train.add_argument("--currency", default=CN_DEFAULT_CONTEXT.currency)
    train.add_argument("--calendar-id", default=CN_DEFAULT_CONTEXT.calendar_id)
    train.add_argument("--universe-id", default=CN_DEFAULT_CONTEXT.universe_id)
    train.add_argument("--benchmark-id", default=CN_DEFAULT_CONTEXT.benchmark_id)
    train.add_argument("--train-start", type=_date, required=True)
    train.add_argument("--train-end", type=_date, required=True)
    train.add_argument("--valid-start", type=_date, required=True)
    train.add_argument("--valid-end", type=_date, required=True)
    train.add_argument("--test-start", type=_date, required=True)
    train.add_argument("--test-end", type=_date, required=True)

    inference = subparsers.add_parser("infer", help="从 Qlib Recorder 加载模型并预测最新 PIT 特征")
    inference.add_argument("--model-run-id", required=True)
    inference.add_argument("--factor-run-id", required=True)
    inference.add_argument("--date", dest="inference_dates", action="append", type=_date, required=True)
    inference.add_argument("--market-id", default=CN_DEFAULT_CONTEXT.market_id)
    inference.add_argument("--currency", default=CN_DEFAULT_CONTEXT.currency)
    inference.add_argument("--calendar-id", default=CN_DEFAULT_CONTEXT.calendar_id)
    inference.add_argument("--universe-id", default=CN_DEFAULT_CONTEXT.universe_id)
    inference.add_argument("--benchmark-id", default=CN_DEFAULT_CONTEXT.benchmark_id)

    backtest = subparsers.add_parser("backtest", help="运行月度 Top-N 多头回测")
    backtest.add_argument("--model-run-id", required=True)
    backtest.add_argument("--top-n", type=int, default=30)
    backtest.add_argument("--cost-bps", type=float, default=10.0)

    args = parser.parse_args()
    store = _store(args.database_url)
    if args.command == "init-db":
        store.initialize(); print(json.dumps({"status": "initialized"})); return
    if args.command == "audit-data":
        print(json.dumps(store.data_quality(args.universe), default=str, ensure_ascii=False)); return
    if args.command == "run-scheduler":
        if not args.token:
            parser.error("run-scheduler requires --token or TUSHARE_TOKEN")
        if args.once:
            print(json.dumps(run_scheduled_once(store, args.token), default=str, ensure_ascii=False)); return
        run_scheduler(store, args.token, args.poll_seconds); return
    if args.command == "sync-hs300":
        if not args.token:
            parser.error("sync-hs300 requires --token or TUSHARE_TOKEN")
        run_id = sync_hs300(store, args.token, args.start_date, args.end_date)
    elif args.command == "sync-benchmark":
        if not args.token:
            parser.error("sync-benchmark requires --token or TUSHARE_TOKEN")
        run_id = sync_benchmark(store, args.token, args.start_date, args.end_date, benchmark=args.benchmark, request_interval=args.request_interval)
    elif args.command == "sync-universe":
        if not args.token:
            parser.error("sync-universe requires --token or TUSHARE_TOKEN")
        run_id = sync_universe(store, args.token, universe=args.universe, mode=args.mode, start_date=args.start_date, end_date=args.end_date, request_interval=args.request_interval)
    elif args.command == "build-factors":
        dates, cursor = [], args.start_date
        while cursor <= args.end_date:
            dates.append(cursor); cursor += timedelta(days=1)
        run_id = build_factor_run(
            store,
            dates,
            CN_DEFAULT_CONTEXT,
            market_config=build_cn_market_config(store),
        )
    elif args.command == "train":
        context = ResearchContext(
            args.market_id,
            args.currency,
            args.calendar_id,
            args.universe_id,
            args.benchmark_id,
        )
        split = TimeSplitConfig(
            calendar_id=args.calendar_id,
            train=DateSegment(args.train_start, args.train_end),
            valid=DateSegment(args.valid_start, args.valid_end),
            test=DateSegment(args.test_start, args.test_end),
        )
        run_id = train_model_run(
            store,
            args.factor_run_id,
            context,
            split,
            market_config=build_cn_market_config(store) if args.market_id.upper() == "CN" else None,
        )
    elif args.command == "infer":
        context = ResearchContext(
            args.market_id,
            args.currency,
            args.calendar_id,
            args.universe_id,
            args.benchmark_id,
        )
        run_id = run_model_inference(
            store,
            args.model_run_id,
            args.factor_run_id,
            args.inference_dates,
            context,
            market_config=build_cn_market_config(store) if args.market_id.upper() == "CN" else None,
        )
    else:
        run_id = run_backtest_run(
            store,
            args.model_run_id,
            args.top_n,
            args.cost_bps,
            market_config=build_cn_market_config(store),
        )
    print(json.dumps(store.get_run(run_id), default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
