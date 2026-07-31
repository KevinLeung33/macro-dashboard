import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import streamlit as st


CHART_WINDOWS = {
    "1M": ("1个月", pd.DateOffset(months=1)),
    "3M": ("3个月", pd.DateOffset(months=3)),
    "6M": ("6个月", pd.DateOffset(months=6)),
    "1Y": ("1年", pd.DateOffset(years=1)),
    "3Y": ("3年", pd.DateOffset(years=3)),
    "ALL": ("全部", None),
}


def render_chart_controls(key="chart_window"):
    """Render the page-level time window selector used by all charts.

    The selector lives outside Plotly so it cannot collide with the legend or
    the Plotly mode bar. ``add_range_selector`` remains as a compatibility
    wrapper for existing page code and applies the selected window to figures.
    """
    target = st.session_state.get("research_navigation", {})
    target_window = target.get("window")
    if target_window in CHART_WINDOWS and st.session_state.get("_chart_target_window") != target_window:
        st.session_state[key] = target_window
        st.session_state["_chart_target_window"] = target_window

    current = st.session_state.get(key, "3M")
    if current not in CHART_WINDOWS:
        current = "3M"

    st.markdown("**研究窗口**")
    selected = st.radio(
        "选择图表时间窗口",
        options=list(CHART_WINDOWS),
        index=list(CHART_WINDOWS).index(current),
        format_func=lambda value: CHART_WINDOWS[value][0],
        horizontal=True,
        key=key,
        label_visibility="collapsed",
    )
    st.caption("窗口只改变图表可视范围，不会删除历史数据；详细的 7D/30D/90D 对比见首页简报和周报。")
    return selected


def _figure_date_range(fig):
    values = []
    for trace in fig.data:
        if trace.x is None:
            continue
        values.extend(list(trace.x))
    if not values:
        return None
    dates = pd.to_datetime(pd.Series(values), errors="coerce").dropna()
    if dates.empty:
        return None
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_localize(None)
    return dates.min(), dates.max()


def add_source_annotation(fig):
    fig.update_layout(
        margin=dict(l=48, r=48, t=48, b=64),
        hovermode="x unified",
        font=dict(size=12),
        legend=dict(orientation="h", yanchor="top", y=-0.16, xanchor="left", x=0),
    )
    return fig


def line_chart(df, title, yaxis_title=None, color="#1f77b4", height=350):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["value"],
        mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy",
        fillcolor=f"rgba({','.join(str(int(color.lstrip('#')[i:i+2], 16)) for i in (0, 2, 4))}, 0.1)",
    ))
    fig.update_layout(
        title=title,
        height=height,
        yaxis_title=yaxis_title or "",
    )
    return add_source_annotation(fig)


def multi_line_chart(dfs, title, yaxis_title=None, height=400):
    fig = go.Figure()
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    for i, (label, sub_df) in enumerate(dfs.items()):
        fig.add_trace(go.Scatter(
            x=sub_df["date"], y=sub_df["value"],
            mode="lines",
            name=label,
            line=dict(color=colors[i % len(colors)], width=2),
        ))
    fig.update_layout(
        title=title,
        height=height,
        yaxis_title=yaxis_title or "",
    )
    return add_source_annotation(fig)


def metric_card(value, label, delta=None, delta_color="normal"):
    from streamlit import metric as st_metric
    display_val = value
    if isinstance(value, float):
        display_val = f"{value:,.2f}"
    elif isinstance(value, int):
        display_val = f"{value:,}"
    st_metric(label=label, value=display_val, delta=delta, delta_color=delta_color)


def dual_axis_chart(dfs, title, y1_title="", y2_title="", height=400):
    fig = go.Figure()
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e"]
    keys = list(dfs.keys())

    for i, (label, sub_df) in enumerate(dfs.items()):
        yaxis = "y1" if i == 0 else "y2"
        fig.add_trace(go.Scatter(
            x=sub_df["date"], y=sub_df["value"],
            mode="lines",
            name=label,
            yaxis=yaxis,
            line=dict(color=colors[i % len(colors)], width=2),
        ))

    fig.update_layout(
        title=title,
        height=height,
        yaxis=dict(title=y1_title, side="left", showgrid=True),
        yaxis2=dict(
            title=y2_title,
            side="right",
            overlaying="y",
            showgrid=False,
        ),
        hovermode="x unified",
        font=dict(size=12),
        legend=dict(orientation="h", yanchor="top", y=-0.16, xanchor="left", x=0),
        margin=dict(l=48, r=48, t=48, b=64),
    )
    return fig


def horizontal_bar(df, title, x_label="", height=400):
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=df.iloc[:, 0],
        x=df.iloc[:, 1],
        orientation="h",
        marker=dict(color="#1f77b4"),
    ))
    fig.update_layout(
        title=title,
        height=height,
        xaxis_title=x_label,
        yaxis=dict(autorange="reversed"),
    )
    return add_source_annotation(fig)


def add_range_selector(fig):
    """Apply the page-level time window to a figure.

    The function name is retained because all pages already use it. The old
    embedded Plotly buttons were removed: they competed for the same top row
    as the legend and mode bar on wide charts.
    """
    selected = st.session_state.get("chart_window", "3M")
    date_range = _figure_date_range(fig)
    if date_range and selected in CHART_WINDOWS:
        earliest, latest = date_range
        offset = CHART_WINDOWS[selected][1]
        if offset is None:
            fig.update_xaxes(autorange=True)
        else:
            fig.update_xaxes(range=[latest - offset, latest])
    fig.update_xaxes(
        rangeslider=dict(visible=False),
    )
    # Make plot background transparent for dark mode
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def plotly_config():
    """Plotly 交互配置"""
    return {
        "displayModeBar": "hover",
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "displaylogo": False,
        "scrollZoom": True,
    }
