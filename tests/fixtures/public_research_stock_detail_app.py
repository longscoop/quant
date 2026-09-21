"""Deterministic public stock-detail and navigation fixture for Phase 2 Task 4."""

from __future__ import annotations

from datetime import date
import os

import streamlit as st

from quant import research_ui
from quant.research_ui import (
    SELECTED_SECURITY_KEY,
    consume_research_target,
    render_research_candidate_pool,
    render_research_home,
)
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, FinancialRecord, IndustryRecord, PriceBar, Security, ValuationBar


class StockDetailFixtureStore(InMemoryStore):
    def __init__(self, state: str):
        super().__init__()
        self.state = state
        first, second = "000001.SZ", "000002.SZ"
        self.securities[first] = Security(first, "研究样本1", date(2010, 1, 1))
        self.securities[second] = Security(second, "研究样本2", date(2010, 1, 1))
        self.industries[(first, date(2020, 1, 1))] = IndustryRecord(first, "银行", date(2020, 1, 1))
        self.industries[(first, date(2026, 1, 1))] = IndustryRecord(first, "金融服务", date(2026, 1, 1))
        self.industries[(second, date(2020, 1, 1))] = IndustryRecord(second, "制造", date(2020, 1, 1))
        self._add_evidence(first)
        self._add_evidence(second)
        if state == "insufficient":
            self._make_evidence_insufficient(first)
        if state == "joined":
            self.upsert_portfolio_position("default", first, 0.2)
        if state == "weight_limit":
            self.upsert_portfolio_position("default", first, 0.0)
            self.upsert_portfolio_position("default", second, 0.9)

    def _add_evidence(self, code: str) -> None:
        for offset, stock_close, benchmark_close in ((0, 10.0, 3000.0), (1, 11.0, 3030.0), (2, 12.0, 3300.0)):
            day = date(2026, 8, 25 + offset)
            self.prices[(code, day)] = PriceBar(code, day, stock_close if code == "000001.SZ" else stock_close * 0.8)
            self.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, benchmark_close)
        self.financials[(code, date(2025, 12, 31), date(2026, 3, 30))] = FinancialRecord(
            code, date(2025, 12, 31), date(2026, 3, 30), 100.0, 20.0, 0.15, 0.30,
            18.0, 0.45,
        )
        self.valuations[(code, date(2026, 8, 27))] = ValuationBar(code, date(2026, 8, 27), 11.0, 1.1, 1.9, 0.03)
        self.runs = self._runs()

    def _runs(self) -> list[dict]:
        rankings = []
        rows = []
        for index, code in enumerate(sorted(self.securities), start=1):
            rows.append({"ts_code": code, "as_of_date": "2026-08-27", "score": 1 - index / 10})
            rankings.append({
                "ts_code": code,
                "as_of_date": "2026-08-27",
                "score": {"score": 86 - index, "coverage": 0.95},
                "factors": {
                    "quality": {"score": 82, "coverage": 0.95},
                    "growth": {"score": 50, "coverage": 0.9},
                    "valuation": {"score": 25, "coverage": 0.9},
                    "momentum": {"score": 70, "coverage": 0.9},
                    "risk": {"score": 78, "coverage": 0.95},
                },
                "metrics": {"q_debt": 99, "v_pe": 1},
            })
        return [
            {
                "run_type": "model",
                "status": "completed",
                "payload": {
                    "rows": rows,
                    "metadata": {"model_version": "演示模型v1"},
                },
            },
            {
                "run_type": "factors",
                "status": "completed",
                "payload": {
                    "rankings": rankings,
                    "metadata": {
                        "factor_version": "演示因子v2",
                        "factor_model_version": "演示因子模型v1",
                    },
                },
            },
        ]

    def _make_evidence_insufficient(self, code: str) -> None:
        self.prices = {(key, day): row for (key, day), row in self.prices.items() if key != code or day == date(2026, 8, 25)}
        self.benchmarks = {(key, day): row for (key, day), row in self.benchmarks.items() if day == date(2026, 8, 25)}
        factor_run = self.runs[1]
        factor_run["payload"]["rankings"] = [{
            "ts_code": code,
            "as_of_date": "2026-08-27",
            "score": {"score": None, "coverage": None},
            "factors": {},
        }]

    def load_memory(self):
        return self

    def list_runs(self):
        return list(self.runs)


if "stock_detail_fixture_state" not in st.session_state:
    st.session_state.stock_detail_fixture_state = os.getenv("PUBLIC_RESEARCH_DETAIL_STATE", "healthy")
state = st.session_state.stock_detail_fixture_state
store_key = f"stock_detail_fixture_store_{state}"
if store_key not in st.session_state:
    st.session_state[store_key] = StockDetailFixtureStore(state)
store = st.session_state[store_key]

pages = ["研究首页", "候选池", "个股详情", "我的组合"]
target_page = consume_research_target(st.session_state, pages)
if target_page is not None:
    st.session_state["research_navigation_page"] = target_page
if "research_navigation_page" not in st.session_state:
    st.session_state["research_navigation_page"] = "个股详情"
page = st.sidebar.radio("研究模块", pages, key="research_navigation_page")

if page == "研究首页":
    render_research_home(st, store, today=date(2026, 8, 29))
elif page == "候选池":
    render_research_candidate_pool(st, store, today=date(2026, 8, 29))
elif page == "个股详情":
    renderer = getattr(research_ui, "render_research_stock_detail", None)
    if renderer is None:
        codes = sorted(store.securities)
        code = st.selectbox("选择证券", codes, key="research_detail_security")
        st.session_state[SELECTED_SECURITY_KEY] = code
        st.title("个股详情")
        st.info("个股详情尚未准备。")
    else:
        renderer(st, store)
else:
    st.title("我的组合")
    st.caption("我的研究组合")
