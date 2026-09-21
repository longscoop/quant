from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import uuid4


def security_option_label(ts_code: str, name: str) -> str:
    return f"{name}（{ts_code}）"


def page_header(st, title: str, subtitle: str, status: str | None = None):
    """Render a native page heading without custom HTML or CSS."""
    st.title(title)
    st.caption(subtitle)
    if status:
        st.caption(f"状态：{status}")


def metric_cards(st, metrics: list[tuple[str, str, str | None]]):
    """Render responsive native metric groups, never exceeding four items per row."""
    for offset in range(0, len(metrics), 4):
        with st.container(horizontal=True, gap="small"):
            for label, value, caption in metrics[offset : offset + 4]:
                with st.container(border=True):
                    st.metric(label, value)
                    if caption:
                        st.caption(caption)


def empty_state(st, title: str, detail: str, next_step: str | None = None):
    """Render one bounded, reader-facing empty state."""
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.write(detail)
        if next_step:
            st.caption(f"下一步：{next_step}")


def render_public_page(st, renderer: Callable, /, *args, **kwargs) -> None:
    """Contain unexpected public-page failures behind an opaque support reference."""
    try:
        renderer(*args, **kwargs)
    except Exception as exc:
        reference = uuid4().hex[:8].upper()
        logging.getLogger(__name__).warning(
            "public page failed; reference=%s exception_type=%s",
            reference,
            type(exc).__name__,
        )
        st.error("页面暂时无法显示。请稍后刷新；若问题持续，请联系管理员。")
        st.caption(f"参考编号：{reference}")
        research_disclaimer(st)


def research_disclaimer(st):
    st.caption("研究信号与历史结果仅用于研究，不构成投资建议、个性化交易指令或收益保证。")
