from __future__ import annotations

import os

import streamlit as st

from quant.portfolio_ui import render_portfolio_workbench
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore
from quant.workflows import save_portfolio_targets


class PortfolioFixtureStore(InMemoryStore):
    def __init__(self, state: str):
        super().__init__()
        self.sync(FixtureProvider())
        self.portfolio_id = self.create_portfolio("研究组合A")
        self.upsert_portfolio_position(self.portfolio_id, "000001.SZ", 0.6)
        self.upsert_portfolio_position(self.portfolio_id, "000002.SZ", 0.3)
        if state == "pending":
            save_portfolio_targets(self, self.portfolio_id, {"000001.SZ": 0.6, "000002.SZ": 0.3})

    def list_runs(self, limit=100):
        return []


state = os.getenv("PUBLIC_PORTFOLIO_STATE", "draft")
store_key = f"portfolio_fixture_store_{state}"
if store_key not in st.session_state:
    st.session_state[store_key] = PortfolioFixtureStore(state)

render_portfolio_workbench(st, st.session_state[store_key])
