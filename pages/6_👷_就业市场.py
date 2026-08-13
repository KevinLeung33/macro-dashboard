import streamlit as st

from db.repository import query_series
from services.dashboard_overview import render_horizon_guidance, render_quality_strip, render_snapshot_cards
from utils.chart_utils import line_chart, multi_line_chart, add_range_selector, plotly_config, render_chart_controls
from utils.indicators import latest_value, yoy_series
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="就业市场", page_icon="👷", layout="wide")
st.title("👷 就业市场")
cfg = plotly_config(); target = render_research_target(); render_chart_controls()


def _q(sid): return apply_target_window(query_series("fred", sid), target)
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)


def _summary():
    render_horizon_guidance("employment")
    rows = []
    for sid, label, unit in (("UNRATE", "失业率", "%"), ("PAYEMS", "非农就业", "千人"), ("JTSJOL", "职位空缺", "千人"), ("ICSA", "初请", "人"), ("AHETPI", "平均时薪", "$")):
        df = _q(sid)
        if not df.empty:
            current = latest_value(df); previous = df.iloc[-6]["value"] if len(df) > 5 else None
            change = None if previous in (None, 0) else (current / previous - 1) * 100
            rows.append({"label": label, "unit": unit, "value": current, "change_5_pct": change, "date": str(df.iloc[-1]["date"])[:10], "source": "fred", "status": "ok"})
    render_snapshot_cards(rows, columns=4)
    render_quality_strip(["fred"], title="就业摘要数据质量")


def _details():
    st.subheader("失业与初请")
    unrate, claims = _q("UNRATE"), _q("ICSA")
    if not unrate.empty: _show(add_range_selector(line_chart(unrate, "失业率", "%", color="#d62728")), "失业率是滞后指标，需结合初请和职位空缺看拐点。")
    if not claims.empty: _show(add_range_selector(line_chart(claims, "初请失业金人数", "万人", color="#1f77b4")), "初请比失业率更快反映劳动力市场变化。")
    st.subheader("职位空缺与自愿离职")
    jolts, quits = _q("JTSJOL"), _q("JTSQUR")
    frames = {label: df for label, df in (("职位空缺", jolts), ("自主离职率", quits)) if not df.empty}
    if frames: _show(add_range_selector(multi_line_chart(frames, "职位空缺与离职", "指标")), "空缺和离职率下降通常代表劳动力市场降温。")
    st.subheader("工资与就业总量")
    payems, wage = _q("PAYEMS"), _q("AHETPI")
    frames = {label: df for label, df in (("非农就业", payems), ("平均时薪", wage)) if not df.empty}
    if frames: _show(add_range_selector(multi_line_chart(frames, "工资与就业", "指标")), "工资压力要结合通胀和生产率判断。")
    st.subheader("劳动参与率与增长")
    participation, indpro = _q("CIVPART"), _q("INDPRO")
    if not participation.empty: _show(add_range_selector(line_chart(participation, "劳动参与率", "%", color="#2ca02c")))
    if not indpro.empty:
        growth = yoy_series(indpro)
        _show(add_range_selector(line_chart(growth, "工业产出同比", "%", color="#9467bd")), "就业与工业产出同步走弱时，衰退信号更有一致性。")


def _evidence():
    st.info("就业数据发布频率不同，摘要中不要把月度和周度变化直接比较；详细数据会显示各自日期。")
    if st.button("查看历史衰退对比", use_container_width=True):
        go_to_research("pages/7_⏳_历史对比.py", "历史对比", "3M")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab: _summary()
with detail_tab: _details()
with evidence_tab: _evidence()
