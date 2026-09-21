def security_option_label(ts_code: str, name: str) -> str:
    return f"{name}（{ts_code}）"


STATUS_TONES = {
    "已完成": "positive",
    "数据完整": "positive",
    "研究候选": "positive",
    "高景气": "positive",
    "中性": "neutral",
    "中性偏多": "neutral",
    "中性偏空": "neutral",
    "低优先级": "muted",
    "低景气": "muted",
    "数据不足": "warning",
    "证据不足": "warning",
    "不可训练": "warning",
    "数据不可用": "warning",
    "失败": "danger",
}


def inject_workspace_css(st):
    """Install the visual system once per Streamlit rerun."""
    st.markdown(
        """
        <style>
        :root { --ink:#17202a; --muted:#667085; --line:#e7eaee; --surface:#ffffff; --canvas:#f7f8fa; --accent:#176b4d; --accent-soft:#e9f5ef; --warning:#b54708; --warning-soft:#fff4e5; }
        .stApp { background:var(--canvas); color:var(--ink); }
        [data-testid="stSidebar"] { background:#13271f; }
        [data-testid="stSidebar"] * { color:#effaf3 !important; }
        [data-testid="stSidebar"] .stRadio label { padding:7px 8px; border-radius:8px; }
        [data-testid="stSidebar"] .stRadio label:hover { background:rgba(255,255,255,.08); }
        .workbench-header { display:flex; justify-content:space-between; align-items:flex-start; margin:4px 0 22px; }
        .workbench-kicker { color:var(--muted); font-size:.82rem; margin-top:3px; }
        .workbench-card { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:18px; box-shadow:0 1px 2px rgba(16,24,40,.03); }
        .workbench-card h4 { margin:0 0 8px; font-size:.85rem; color:var(--muted); font-weight:600; }
        .workbench-value { font-size:1.55rem; font-weight:700; line-height:1.2; }
        .workbench-caption { color:var(--muted); font-size:.78rem; margin-top:7px; }
        .status-pill { display:inline-flex; align-items:center; border-radius:999px; padding:3px 9px; font-size:.75rem; font-weight:600; }
        .status-positive { color:#12633f; background:#e9f5ef; }
        .status-neutral { color:#475467; background:#eef1f4; }
        .status-muted { color:#667085; background:#f1f3f5; }
        .status-warning { color:#9b4a06; background:#fff4e5; }
        .status-danger { color:#b42318; background:#feeceb; }
        .next-step { border-left:3px solid var(--accent); padding:10px 12px; background:#f0f8f4; color:#315443; border-radius:0 8px 8px 0; font-size:.85rem; }
        .research-note { color:var(--muted); font-size:.78rem; }
        div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(st, title: str, subtitle: str, status: str | None = None):
    badge = status_badge(status) if status else ""
    st.markdown(f'<div class="workbench-header"><div><h1 style="margin:0">{title}</h1><div class="workbench-kicker">{subtitle}</div></div><div>{badge}</div></div>', unsafe_allow_html=True)


def status_badge(status: str | None) -> str:
    if not status:
        return ""
    tone = STATUS_TONES.get(status, "neutral")
    return f'<span class="status-pill status-{tone}">{status}</span>'


def metric_cards(st, metrics: list[tuple[str, str, str | None]]):
    columns = st.columns(len(metrics))
    for column, (label, value, caption) in zip(columns, metrics):
        with column:
            caption_html = f'<div class="workbench-caption">{caption}</div>' if caption else ""
            st.markdown(f'<div class="workbench-card"><h4>{label}</h4><div class="workbench-value">{value}</div>{caption_html}</div>', unsafe_allow_html=True)


def empty_state(st, title: str, detail: str, next_step: str | None = None):
    st.markdown(f'<div class="workbench-card"><h3 style="margin:0 0 6px">{title}</h3><div class="research-note">{detail}</div></div>', unsafe_allow_html=True)
    if next_step:
        st.markdown(f'<div class="next-step" style="margin-top:12px">下一步：{next_step}</div>', unsafe_allow_html=True)


def research_disclaimer(st):
    st.caption("研究信号与历史结果仅用于研究，不构成投资建议、个性化交易指令或收益保证。")
