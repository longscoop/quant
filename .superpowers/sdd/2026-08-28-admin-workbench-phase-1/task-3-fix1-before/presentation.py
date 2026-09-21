"""Pure display helpers for Chinese research-workbench renderers."""

from __future__ import annotations

from math import isfinite
from numbers import Real

from .insights import run_status_summary


UNAVAILABLE = "不可用"


def sanitize_display_value(value):
    """Return a value safe for display, marking absent or non-finite data unavailable."""
    if value is None:
        return UNAVAILABLE
    if isinstance(value, Real) and not isinstance(value, bool) and not isfinite(float(value)):
        return UNAVAILABLE
    return value


def sanitize_display_rows(rows):
    """Sanitize every value in a table without mutating its source rows."""
    return [{key: sanitize_display_value(value) for key, value in row.items()} for row in rows]


def format_metric(value, percent: bool = False) -> str:
    """Format a numeric metric or explicitly mark unavailable source data."""
    clean = sanitize_display_value(value)
    if clean == UNAVAILABLE or not isinstance(clean, Real) or isinstance(clean, bool):
        return UNAVAILABLE
    return f"{clean:.2%}" if percent else f"{clean:.2f}"


def status_feedback(run, completed_message: str) -> tuple[str, str]:
    """Map a persisted research run to the only truthful Streamlit feedback level."""
    if run.get("status") == "completed":
        return "success", completed_message
    if run.get("status") == "not_trainable":
        if run.get("run_type") == "factors":
            return "warning", "因子运行没有可用行。请检查数据覆盖、财务披露和截止日期。"
        return "warning", run_status_summary(run)["next_step"]
    return "error", run.get("error") or "运行失败"
