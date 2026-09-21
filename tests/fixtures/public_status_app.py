import streamlit as st

from quant.public_status_ui import render_public_data_status


render_public_data_status(
    st,
    quality={"latest_trade_date": "2026-08-27"},
    counts={"securities": 300, "prices": 600, "financials": 300, "valuation_count": 300},
    runs=[
        {"run_id": "run-secret-123", "run_type": "sync", "status": "completed"},
        {"run_id": "run-secret-456", "run_type": "factors", "status": "completed"},
        {
            "run_id": "run-secret-789",
            "run_type": "factors",
            "status": "failed",
            "error": "dsn=postgresql://user:pass@db.example/research",
        },
    ],
)
