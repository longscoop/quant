"""Read-only public research data status renderer."""

from __future__ import annotations

import pandas as pd
from .ui import metric_cards, page_header, research_disclaimer


_STAGES = (
    ("sync", "数据准备"),
    ("factors", "研究准备"),
    ("model", "研究结果"),
    ("backtest", "历史验证"),
)
_STATUS_LABELS = {
    "completed": "可用",
    "not_trainable": "暂不可用",
    "failed": "暂不可用",
    "running": "准备中",
}


def public_status_rows(runs: list[dict]) -> list[dict]:
    """Project newest-first run history into safe public workflow stages."""
    latest_runs = {}
    for run in runs:
        run_type = run.get("run_type")
        if run_type not in latest_runs:
            latest_runs[run_type] = run
    return [
        {
            "阶段": label,
            "状态": _STATUS_LABELS.get(
                (latest_runs.get(run_type) or {}).get("status"), "准备中"
            ),
        }
        for run_type, label in _STAGES
    ]


def render_public_data_status(st, quality: dict, counts: dict, runs: list[dict]) -> None:
    """Render freshness and user-facing workflow stages without operational controls."""
    page_header(st, "数据状态", "确认数据新鲜度、覆盖范围和研究可用性。")
    st.markdown("**数据覆盖范围**：日行情记录、财务披露记录与估值数据记录。")
    metric_cards(
        st,
        [
            ("最新行情", str(quality.get("latest_trade_date") or "不可用"), "交易日"),
            ("证券", str(counts.get("securities", 0)), "当前研究范围"),
            ("行情记录", str(counts.get("prices", 0)), "日行情记录"),
            ("财务记录", str(counts.get("financials", 0)), "已披露记录"),
            ("估值记录", str(counts.get("valuation_count", 0)), "估值数据记录"),
        ],
    )
    st.subheader("研究状态")
    rows = public_status_rows(runs)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    research_disclaimer(st)
