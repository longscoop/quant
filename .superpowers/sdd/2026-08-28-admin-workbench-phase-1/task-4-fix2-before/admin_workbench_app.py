from datetime import date
import os
from types import SimpleNamespace

import streamlit as st

import quant.admin_ui as admin_ui
from quant.admin_ui import render_admin_workbench


MODE = os.getenv("ADMIN_FIXTURE_MODE", "guarded")


def _completed_factor(run_id, day):
    return {
        "run_id": run_id,
        "run_type": "factors",
        "status": "completed",
        "parameters": {"as_of_dates": [day]},
        "payload": {"metadata": {"date_end": day.isoformat()}},
        "error": None,
        "created_at": f"{day.isoformat()}T00:00:00+00:00",
        "completed_at": f"{day.isoformat()}T00:01:00+00:00",
    }


class FakeAdminStore:
    def __init__(self):
        self.factor_dates = []
        self.model_factor_run_id = None
        self.runs = self._initial_runs()

    def _initial_runs(self):
        failed_sync = {
            "run_id": "failed-sync-1",
            "run_type": "sync",
            "status": "failed",
            "parameters": {"start_date": "2026-08-01", "end_date": "2026-08-27"},
            "payload": {},
            "error": "provider rejected token=secret-token-value",
            "created_at": "2026-08-28T00:00:00+00:00",
            "completed_at": "2026-08-28T00:01:00+00:00",
        }
        if MODE == "model-retry":
            return [
                {
                    "run_id": "failed-model-1",
                    "run_type": "model",
                    "status": "failed",
                    "parameters": {
                        "factor_run_id": "factor-retry",
                        "prediction_dates": ["2026-08-27"],
                    },
                    "payload": {},
                    "error": "model failure dsn=postgresql://user:pass@db.example/research",
                    "created_at": "2026-08-28T03:00:00+00:00",
                    "completed_at": "2026-08-28T03:01:00+00:00",
                },
                _completed_factor("factor-newest", date(2026, 8, 27)),
                _completed_factor("factor-retry", date(2026, 8, 26)),
            ]
        return [failed_sync] if MODE == "guarded" else [_completed_factor("factor-newest", date(2026, 8, 27))]

    def data_quality(self, universe):
        assert universe == "hs300"
        return {
            "security_count": 300,
            "latest_trade_date": date(2026, 8, 27),
            "missing_latest_price_codes": ["000001.SZ"] if MODE == "guarded" else [],
            "missing_financial_codes": [],
            "valuation_count": 300,
            "is_complete": MODE != "guarded",
        }

    def fast_counts(self):
        return {"securities": 300, "prices": 6000, "financials": 300, "valuation_count": 300}

    def fail_stale_sync_runs(self):
        return 0

    def is_trade_day(self, day):
        return MODE == "stale" and day.weekday() < 5

    def list_runs(self, limit=100):
        return self.runs[:limit]

    def get_run(self, run_id):
        return next(run for run in self.runs if run["run_id"] == run_id)

    def load_memory(self):
        return SimpleNamespace(
            prices={
                ("000001.SZ", date(2026, 8, 25)): SimpleNamespace(trade_date=date(2026, 8, 25)),
                ("000001.SZ", date(2026, 8, 26)): SimpleNamespace(trade_date=date(2026, 8, 26)),
                ("000001.SZ", date(2026, 8, 27)): SimpleNamespace(trade_date=date(2026, 8, 27)),
            }
        )


def _fake_sync(store, token, start, end, progress):
    assert token == os.environ["TUSHARE_TOKEN"]
    store.runs.insert(0, {
        "run_id": "sync-completed", "run_type": "sync", "status": "completed",
        "parameters": {"start_date": start, "end_date": end}, "payload": {}, "error": None,
        "created_at": "2026-08-28T04:00:00+00:00", "completed_at": "2026-08-28T04:01:00+00:00",
    })
    return "sync-completed"


def _fake_build_factor(store, dates):
    store.factor_dates = list(dates)
    store.runs.insert(0, _completed_factor("factor-created", dates[-1]))
    return "factor-created"


def _fake_train_model(store, factor_run_id, prediction_dates):
    store.model_factor_run_id = factor_run_id
    store.runs.insert(0, {
        "run_id": "model-completed", "run_type": "model", "status": "completed",
        "parameters": {"factor_run_id": factor_run_id, "prediction_dates": prediction_dates},
        "payload": {}, "error": None,
        "created_at": "2026-08-28T04:00:00+00:00", "completed_at": "2026-08-28T04:01:00+00:00",
    })
    return "model-completed"


admin_ui.sync_hs300 = _fake_sync
admin_ui.build_factor_run = _fake_build_factor
admin_ui.train_model_run = _fake_train_model
store = FakeAdminStore()
render_admin_workbench(st, store)
if store.factor_dates:
    st.caption("测试因子日期：" + ",".join(day.isoformat() for day in store.factor_dates))
if store.model_factor_run_id:
    st.caption("测试模型因子运行：" + store.model_factor_run_id)
