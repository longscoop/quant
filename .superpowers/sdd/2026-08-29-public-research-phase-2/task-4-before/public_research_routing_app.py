"""Deterministic end-to-end routing fixture for public research actions."""

from __future__ import annotations

from datetime import date

import streamlit as st

from quant.research_ui import (
    SELECTED_SECURITY_KEY,
    consume_research_target,
    render_research_home,
    research_security_index,
)
from quant.storage import InMemoryStore
from quant.types import IndustryRecord, Security


class RoutingFixtureStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        for index in range(1, 16):
            code = f"{index:06d}.SZ"
            self.securities[code] = Security(code, f"研究样本{index}", date(2010, 1, 1))
            self.industries[(code, date(2020, 1, 1))] = IndustryRecord(
                code, "测试行业", date(2020, 1, 1)
            )
        self.runs = self._completed_runs()

    def _completed_runs(self) -> list[dict]:
        rows, rankings = [], []
        for index in range(1, 16):
            code = f"{index:06d}.SZ"
            rows.append({"ts_code": code, "as_of_date": "2026-08-27", "score": 1 - index / 100})
            rankings.append(
                {
                    "ts_code": code,
                    "as_of_date": "2026-08-27",
                    "score": {"score": 90 - index, "coverage": 0.95},
                    "factors": {"quality": {"score": 85 - index}, "risk": {"score": 80}},
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

    def is_trade_day(self, _day):
        return False

    def data_quality(self, _universe="hs300"):
        return {
            "security_count": len(self.securities),
            "latest_trade_date": self.latest_trade_date(),
            "is_complete": True,
        }


if "public_research_routing_store" not in st.session_state:
    st.session_state.public_research_routing_store = RoutingFixtureStore()
store = st.session_state.public_research_routing_store
pages = ["今日机会", "股票池", "个股研究"]
target_page = consume_research_target(st.session_state, pages)
if target_page is not None:
    st.session_state["research_navigation_page"] = target_page
page = st.sidebar.radio("研究模块", pages, key="research_navigation_page")

if page == "今日机会":
    render_research_home(st, store, today=date(2026, 8, 29))
elif page == "股票池":
    from quant.research_ui import render_research_candidate_pool

    render_research_candidate_pool(st, store, today=date(2026, 8, 29))
else:
    codes = sorted(store.securities)
    code = st.selectbox(
        "证券",
        codes,
        index=research_security_index(codes, st.session_state.get(SELECTED_SECURITY_KEY)),
        key="route_stock_security",
    )
    st.title("个股研究")
    st.caption(f"已选择：{code}")
