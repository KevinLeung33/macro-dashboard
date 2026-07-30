"""Session-scoped research navigation between Streamlit pages."""
import pandas as pd
import streamlit as st


STATE_KEY = "research_navigation"


def go_to_research(page, focus, window="3M", asset=None, topic=None, indicators=None):
    st.session_state[STATE_KEY] = {
        "focus": focus,
        "window": window,
        "asset": asset,
        "topic": topic,
        "indicators": indicators or [],
    }
    switch_page = getattr(st, "switch_page", None)
    if switch_page is None:
        st.warning("当前 Streamlit 版本不支持页内跳转，请从侧边栏打开目标页面。")
        return
    switch_page(page)


def current_research_target():
    return st.session_state.get(STATE_KEY, {})


def render_research_target():
    target = current_research_target()
    if target.get("focus"):
        window = target.get("window") or "全部"
        cols = st.columns([4, 1, 1])
        with cols[0]:
            st.caption(f"研究焦点：{target['focus']} · 时间窗口：{window}")
        with cols[1]:
            if st.button("证据工作台", key="open_evidence_from_target", use_container_width=True):
                go_to_research(
                    "pages/13_🔎_证据工作台.py",
                    target["focus"],
                    target.get("window") or "3M",
                    asset=target.get("asset"),
                    topic=target.get("topic") or target["focus"],
                    indicators=target.get("indicators"),
                )
        with cols[2]:
            if st.button("清除焦点", key="clear_research_target", use_container_width=True):
                st.session_state.pop(STATE_KEY, None)
                st.rerun()
    return target


def apply_target_window(frame, target=None):
    """Filter a chart to the selected window, anchored on its latest observation."""
    if frame.empty:
        return frame
    target = target or current_research_target()
    window = target.get("window")
    months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36}.get(window)
    if not months:
        return frame
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"])
    cutoff = out["date"].max() - pd.DateOffset(months=months)
    return out[out["date"] >= cutoff]
