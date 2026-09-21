"""Deterministic public stock-detail summary fixture."""

from __future__ import annotations

from datetime import date

import streamlit as st

from quant.research_ui import render_research_stock_summary
from quant.storage import InMemoryStore
from quant.types import IndustryRecord, Security


store = InMemoryStore()
code = "000001.SZ"
store.securities[code] = Security(code, "研究样本1", date(2010, 1, 1))
store.industries[(code, date(2020, 1, 1))] = IndustryRecord(code, "测试行业", date(2020, 1, 1))
model_run = {
    "status": "completed",
    "payload": {"rows": [{"ts_code": code, "as_of_date": "2026-08-27", "score": 0.9}]},
}
factor_run = {
    "status": "completed",
    "payload": {
        "rankings": [{
            "ts_code": code,
            "as_of_date": "2026-08-27",
            "score": {"score": 86, "coverage": 0.95},
            "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
            "metrics": {"q_debt": 99, "v_pe": 1},
        }],
    },
}

st.title("个股研究")
render_research_stock_summary(st, store, code, factor_run=factor_run, model_run=model_run)
