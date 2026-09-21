"""Read-only public research data status renderer."""

from __future__ import annotations

import pandas as pd

from .ui import metric_cards, page_header, research_disclaimer


_STAGES = (
    ("sync", "数据同步"),
    ("factors", "因子构建"),
    ("model", "模型预测"),
    ("backtest", "回测验证"),
)
_STATUS_LABELS = {
    "completed": "已完成",
    "not_trainable": "不可训练",
    "failed": "失败",
    "running": "进行中",
}


def render_public_data_status(st, quality: dict, counts: dict, runs: list[dict]) -> None:
    """Render freshness and user-facing workflow stages without operational controls."""
    page_header(st, "数据状态", "确认数据新鲜度、覆盖范围和研究运行状态。")
    metric_cards(
        st,
        [
            ("最新行情", str(quality.get("latest_trade_date") or "不可用"), "交易日"),
            ("证券", str(counts.get("securities", 0)), "当前研究范围"),
            ("行情记录", str(counts.get("prices", 0)), "price_bars"),
            ("财务记录", str(counts.get("financials", 0)), "已披露记录"),
            ("估值记录", str(counts.get("valuation_count", 0)), "daily_basic"),
        ],
    )
    st.subheader("研究状态")
    latest_runs = {run.get("run_type"): run for run in runs}
    rows = [
        {
            "阶段": label,
            "状态": _STATUS_LABELS.get((latest_runs.get(run_type) or {}).get("status"), "待执行"),
        }
        for run_type, label in _STAGES
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    research_disclaimer(st)
