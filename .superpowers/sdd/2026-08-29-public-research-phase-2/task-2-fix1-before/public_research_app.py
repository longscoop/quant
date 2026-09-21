"""Deterministic Streamlit states for the public research homepage tests."""

from __future__ import annotations

from datetime import date
import os

import streamlit as st

from quant.research_ui import render_research_home
from quant.storage import InMemoryStore
from quant.types import IndustryRecord, Security


FIXTURE_TODAY = date(2026, 8, 29)


class PublicResearchFixtureStore(InMemoryStore):
    def __init__(self, state: str):
        super().__init__()
        self.state = state
        for index in range(1, 16):
            code = f"{index:06d}.SZ"
            self.securities[code] = Security(code, f"研究样本{index}", date(2010, 1, 1))
            self.industries[(code, date(2020, 1, 1))] = IndustryRecord(
                code, "测试行业", date(2020, 1, 1)
            )
        self.runs = [] if state == "empty" else self._completed_runs()

    def _completed_runs(self) -> list[dict]:
        rows = []
        rankings = []
        for index in range(1, 16):
            code = f"{index:06d}.SZ"
            rows.append({"ts_code": code, "as_of_date": "2026-08-27", "score": 1 - index / 100})
            rankings.append(
                {
                    "ts_code": code,
                    "as_of_date": "2026-08-27",
                    "score": {"score": 90 - index, "coverage": 0.95},
                    "factors": {
                        "quality": {"score": 85 - index, "coverage": 1.0},
                        "risk": {"score": 80, "coverage": 1.0},
                    },
                }
            )
        return [
            {"run_type": "model", "status": "completed", "payload": {"rows": rows}},
            {"run_type": "factors", "status": "completed", "payload": {"rankings": rankings}},
        ]

    def load_memory(self):
        return self

    def list_runs(self):
        return list(self.runs)

    def latest_trade_date(self):
        return date(2026, 8, 27)

    def is_trade_day(self, day):
        return self.state == "stale" and day == date(2026, 8, 28)

    def data_quality(self, _universe="hs300"):
        return {
            "security_count": len(self.securities),
            "latest_trade_date": self.latest_trade_date(),
            "is_complete": True,
        }


state = os.getenv("PUBLIC_RESEARCH_FIXTURE_STATE", "healthy")
store_key = f"public_research_fixture_store_{state}"
if store_key not in st.session_state:
    st.session_state[store_key] = PublicResearchFixtureStore(state)
store = st.session_state[store_key]
render_research_home(st, store, today=FIXTURE_TODAY)
