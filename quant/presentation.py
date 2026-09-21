"""Pure display helpers for Chinese research-workbench renderers."""

from __future__ import annotations

from math import isfinite
from numbers import Real

from .insights import run_status_summary


UNAVAILABLE = "不可用"

_PUBLIC_RUN_TYPES = {
    "sync": "数据准备",
    "factors": "研究准备",
    "model": "研究结果",
    "backtest": "历史验证",
    "portfolio_backtest": "组合历史模拟",
}
_PUBLIC_RUN_STATUSES = {
    "completed": "已完成",
    "not_trainable": "不可训练",
    "failed": "失败",
    "running": "进行中",
}


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


def public_run_type_label(run: dict | None) -> str:
    """Return a user-facing run-type label without operational identifiers."""
    return _PUBLIC_RUN_TYPES.get((run or {}).get("run_type"), "研究运行")


def public_run_status_label(run: dict | None) -> str:
    """Return a user-facing status label without propagating stored details."""
    return _PUBLIC_RUN_STATUSES.get((run or {}).get("status"), "待执行")


def public_run_label(run: dict | None) -> str:
    """Return the safe display label for a selected public research run."""
    return f"{public_run_type_label(run)} · {public_run_status_label(run)}"


def public_run_feedback(run: dict, completed_message: str) -> tuple[str, str]:
    """Map a persisted run to public feedback without exposing stored errors or IDs."""
    if run.get("status") == "completed":
        return "success", completed_message
    run_type = public_run_type_label(run)
    if run.get("status") == "not_trainable":
        return "warning", f"{run_type}当前没有可用结果。请检查数据覆盖后重试。"
    if run.get("status") == "running":
        return "info", f"{run_type}正在进行。"
    return "error", f"{run_type}未能完成。请稍后重试或联系管理员。"


def public_run_rows(runs: list[dict]) -> list[dict]:
    """Project public run history without raw IDs, parameters, or stored errors."""
    return [
        {"研究类型": public_run_type_label(run), "状态": public_run_status_label(run)}
        for run in runs
    ]


def status_feedback(run, completed_message: str) -> tuple[str, str]:
    """Map a persisted research run to the only truthful Streamlit feedback level."""
    if run.get("status") == "completed":
        return "success", completed_message
    if run.get("status") == "not_trainable":
        if run.get("run_type") == "factors":
            return "warning", "因子运行没有可用行。请检查数据覆盖、财务披露和截止日期。"
        return "warning", run_status_summary(run)["next_step"]
    return public_run_feedback(run, completed_message)
