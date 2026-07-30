import plotly.graph_objects as go
import plotly.express as px
import pandas as pd


def add_source_annotation(fig):
    fig.update_layout(
        margin=dict(l=20, r=20, t=40, b=20),
        hovermode="x unified",
        font=dict(size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
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
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=20, r=20, t=40, b=20),
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
    """添加时间范围快捷按钮 — 颜色自适应暗色模式"""
    fig.update_xaxes(
        rangeselector=dict(
            buttons=list([
                dict(count=1, label="1月", step="month", stepmode="backward"),
                dict(count=3, label="3月", step="month", stepmode="backward"),
                dict(count=6, label="6月", step="month", stepmode="backward"),
                dict(count=1, label="1年", step="year", stepmode="backward"),
                dict(count=3, label="3年", step="year", stepmode="backward"),
                dict(step="all", label="全部"),
            ]),
            bgcolor="rgba(128,128,128,0.1)",
            activecolor="rgba(31,119,180,0.5)",
        ),
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
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "displaylogo": False,
        "scrollZoom": True,
    }
