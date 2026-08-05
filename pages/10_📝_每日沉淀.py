"""每日沉淀 — 自动研究包与历史归档"""
import streamlit as st

from db.schema import init_db
from db.repository import query_daily_reports
from services.daily_context import save_daily_context
from services.daily_ai_report import save_ai_trend_report
from services.report_builder import build_report
from services.access_control import render_admin_access, require_admin


st.set_page_config(page_title="每日沉淀", page_icon="📝", layout="wide")
admin_access = render_admin_access()
st.title("📝 每日沉淀")

init_db()

top_left, top_mid, top_right, top_weekly = st.columns([2, 1, 1, 1])
with top_left:
    st.caption("这里保存每日研究包和 AI 趋势日报：数据健康度、近期变化、告警、极端分位、重要新闻，以及模型对一段时间变化的归纳。")
with top_mid:
    if st.button("生成AI趋势日报", use_container_width=True, type="primary", disabled=not admin_access) and require_admin("生成 AI 趋势日报"):
        with st.spinner("正在汇总研究包并调用AI..."):
            result, markdown, _context = save_ai_trend_report(session="ai_daily")
        if result:
            st.success("AI趋势日报已保存")
        else:
            st.warning("AI暂不可用，已保存包含文字结论的规则版日报；请查看服务日志确认 AI 调用原因")
        st.markdown(markdown)
with top_right:
    if st.button("生成研究包", use_container_width=True, disabled=not admin_access) and require_admin("生成研究包"):
        with st.spinner("正在汇总数据、告警和新闻..."):
            _context, markdown = save_daily_context(session="daily")
        st.success("研究包已保存")
        st.markdown(markdown)
with top_weekly:
    if st.button("生成周度报告", use_container_width=True, disabled=not admin_access) and require_admin("生成周度中短期报告"):
        with st.spinner("正在生成 7D/30D/90D 对比报告..."):
            markdown = build_report("weekly")
        st.success("周度中短期报告已保存")
        st.markdown(markdown)

st.divider()

reports = query_daily_reports(limit=30)
if not reports:
    st.info("还没有日报。点击右上角「生成今日研究包」创建第一份沉淀。")
else:
    st.subheader("历史归档")
    dates = [f"{r['report_date']} · {r['session']} · {r['created_at']}" for r in reports]
    selected = st.selectbox("选择报告", dates)
    report = reports[dates.index(selected)]

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("日期", report["report_date"])
    with c2:
        st.metric("类型", report["session"])
    with c3:
        st.metric("生成时间", report["created_at"])

    if report["summary"]:
        st.info(report["summary"])

    st.markdown(report["raw_markdown"] or "这份报告没有正文。")
