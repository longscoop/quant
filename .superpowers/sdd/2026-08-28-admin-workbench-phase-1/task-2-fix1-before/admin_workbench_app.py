from datetime import date

import streamlit as st

from quant.admin_ui import render_admin_workbench


class FakeAdminStore:
    def data_quality(self, universe):
        assert universe == "hs300"
        return {
            "security_count": 300,
            "latest_trade_date": date(2026, 8, 27),
            "missing_latest_price_codes": ["000001.SZ"],
            "missing_financial_codes": [],
            "valuation_count": 300,
            "is_complete": False,
        }

    def fast_counts(self):
        return {
            "securities": 300,
            "prices": 6000,
            "financials": 300,
            "valuation_count": 300,
        }

    def fail_stale_sync_runs(self):
        return 0

    def list_runs(self, limit=100):
        return [
            {
                "run_id": "failed-sync-1",
                "run_type": "sync",
                "status": "failed",
                "parameters": {
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-27",
                },
                "payload": {},
                "error": "provider rejected token=secret-token-value",
                "created_at": "2026-08-28T00:00:00+00:00",
                "completed_at": "2026-08-28T00:01:00+00:00",
            }
        ]

    def get_run(self, run_id):
        return next(run for run in self.list_runs() if run["run_id"] == run_id)


render_admin_workbench(st, FakeAdminStore())
