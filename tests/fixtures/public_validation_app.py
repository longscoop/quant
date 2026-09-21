"""Deterministic public historical-validation states for Phase 2 Task 5."""

from __future__ import annotations

import os

import streamlit as st
from datetime import date

from quant.public_validation_ui import render_historical_validation
from quant.types import BenchmarkBar


class ValidationFixtureStore:
    def __init__(self, state: str):
        self.state = state
        self.factor_snapshots = {}
        self.securities = {}
        self.prices = {}
        self.financials = {}
        self.industries = {}
        newest_model = {
            "run_id": "model-newest-secret",
            "run_type": "model",
            "status": "completed",
            "payload": {"rows": [], "metadata": {"model_version": "internal-v2"}},
        }
        older_model = {
            "run_id": "model-older-secret",
            "run_type": "model",
            "status": "completed",
            "payload": {"rows": [], "metadata": {"model_version": "internal-v1"}},
        }
        completed = {
            "run_id": "validation-secret-123",
            "run_type": "backtest",
            "status": "completed",
            "parameters": {
                "strategy_type": "FACTOR",
                "top_n": 12,
                "cost_bps": 15.0,
                "frequency": "monthly",
                "template_id": "quality_growth",
                "experiment_name": "测试验证",
            },
            "payload": {
                "metrics": {
                    "total_return": 0.12,
                    "benchmark_return": 0.06,
                    "excess_return": 0.06,
                    "max_drawdown": -0.08,
                    "sharpe": 1.2,
                    "calmar": 1.5,
                    "annualized_turnover": 3.0,
                },
                "equity_curve": [
                    {"date": "2026-01-31", "value": 1.0},
                    {"date": "2026-02-28", "value": 1.12},
                ],
                "benchmark_curve": [
                    {"date": "2026-01-31", "value": 1.0},
                    {"date": "2026-02-28", "value": 1.06},
                ],
                "excess_curve": [
                    {"date": "2026-01-31", "value": 1.0},
                    {"date": "2026-02-28", "value": 1.06},
                ],
                "annual_returns": [{"year": 2026, "strategy": 0.12, "benchmark": 0.06}],
                "trades": [
                    {"date": "2026-01-31", "ts_code": "000001.SZ", "weight": 0.08, "cost_bps": 15.0}
                ],
            },
        }
        failed = {
            "run_id": "validation-secret-456",
            "run_type": "backtest",
            "status": "failed",
            "error": "TUSHARE_TOKEN=fixture-token postgresql://reader:password@db.example/research",
            "parameters": {"strategy_type": "FACTOR"},
            "payload": {},
        }
        partial = {
            "run_id": "factor-validation-partial",
            "run_type": "backtest",
            "status": "partial",
            "parameters": {"strategy_type": "FACTOR", "template_id": "quality_growth", "top_n": 30, "cost_bps": 10.0},
            "payload": {"coverage_summary": {"requested_periods": 44, "valid_periods": 31, "skipped_periods": 13}},
        }
        if state == "empty":
            self.runs = []
        elif state == "completed":
            self.runs = [completed, newest_model, older_model]
        elif state == "failed":
            self.runs = [failed, newest_model, older_model]
        elif state == "factor_partial":
            self.runs = [partial]
        elif state == "factor_ready_without_model":
            self.runs = []
        else:
            self.runs = [newest_model, older_model]

    def load_memory(self):
        return self

    def members_for(self, *_args):
        return []

    def prices_for(self, _code):
        return []

    def financials_for(self, _code):
        return []

    def industry_for(self, _code):
        return []

    def valuations_for(self, _code):
        return []

    def benchmark_for(self, _code="000300.SH"):
        return [
            BenchmarkBar("000300.SH", date(2026, 1, 30), 4000.0, 3990.0),
            BenchmarkBar("000300.SH", date(2026, 2, 27), 4100.0, 4050.0),
        ]

    def get_factor_snapshot(self, as_of_date, factor_version, pit_version, universe_version):
        return self.factor_snapshots.get((as_of_date, factor_version, pit_version, universe_version))

    def record_factor_snapshot(self, snapshot, items):
        key = (snapshot["as_of_date"], snapshot["factor_version"], snapshot["pit_version"], snapshot["universe_version"])
        self.factor_snapshots[key] = {**snapshot, "snapshot_id": f"snapshot-{len(self.factor_snapshots) + 1}", "items": items}
        return self.factor_snapshots[key]["snapshot_id"]

    def record_run(self, run_type, status, parameters, payload=None, error=None):
        run = {"run_id": f"run-{len(self.runs) + 1}", "run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
        self.runs.insert(0, run)
        return run["run_id"]

    def get_run(self, run_id):
        return next(run for run in self.runs if run["run_id"] == run_id)

    def list_runs(self):
        return list(self.runs)

    def latest_run_summary(self, run_type: str, status: str | None = None):
        return next(
            (
                run
                for run in self.runs
                if run["run_type"] == run_type and (status is None or run["status"] == status)
            ),
            None,
        )


state = os.getenv("PUBLIC_VALIDATION_FIXTURE_STATE", "ready")
store = ValidationFixtureStore(state)
render_historical_validation(st, store)
