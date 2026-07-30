"""研究假设 — 把你的投资想法绑定到数据和新闻主题"""
from datetime import date

import streamlit as st

from db.schema import init_db
from db.repository import (
    add_research_hypothesis,
    add_viewpoint_log,
    add_watchlist_item,
    query_research_hypotheses,
    query_viewpoint_logs,
    query_watchlist_items,
    update_research_hypothesis,
    update_watchlist_status,
)
from services.research_linker import infer_research_links


st.set_page_config(page_title="研究假设", page_icon="🧭", layout="wide")
st.title("🧭 研究假设")
st.caption("把你的长期框架、当前观点和临时观察项沉淀下来，并绑定到指标、资产和新闻主题。")

init_db()

with st.expander("新增长期假设", expanded=False):
    with st.form("add_hypothesis"):
        title = st.text_input("假设名称", placeholder="例如：BTC 主要受美元流动性和实际利率驱动")
        thesis = st.text_area("核心逻辑", placeholder="这条假设为什么成立？", height=110)
        c1, c2, c3 = st.columns(3)
        with c1:
            assets = st.text_input("相关资产", placeholder="BTC,DXY,NASDAQ")
        with c2:
            indicators = st.text_input("相关指标", placeholder="DX-Y.NYB,DFII10,RRPONTSYD")
        with c3:
            news_topics = st.text_input("新闻主题", placeholder="liquidity,crypto,fed_policy")
        falsification = st.text_area("证伪条件", placeholder="什么情况说明这个假设可能错了？", height=80)
        c4, c5 = st.columns([1, 1])
        with c4:
            confidence = st.slider("当前置信度", 0.0, 1.0, 0.5, 0.05)
        with c5:
            status = st.selectbox("状态", ["active", "watching", "paused", "retired"])
        submitted = st.form_submit_button("保存假设", type="primary")
        if submitted:
            if title and thesis:
                add_research_hypothesis(
                    title=title, thesis=thesis, assets=assets, indicators=indicators,
                    news_topics=news_topics, falsification=falsification,
                    status=status, confidence=confidence,
                )
                st.success("假设已保存")
            else:
                st.warning("假设名称和核心逻辑不能为空")

tab_h, tab_v, tab_w = st.tabs(["长期假设", "观点日志", "观察项"])

with tab_h:
    rows = query_research_hypotheses(limit=100)
    if not rows:
        st.info("还没有长期假设。先添加一条你的核心投资框架。")
    else:
        for row in rows:
            with st.container():
                top = st.columns([3, 1, 1])
                with top[0]:
                    st.markdown(f"**{row['title']}**")
                    st.caption(f"资产：{row['assets'] or '—'} | 指标：{row['indicators'] or '—'} | 主题：{row['news_topics'] or '—'}")
                    inferred = infer_research_links(
                        row["title"], row["thesis"], row["assets"], row["indicators"],
                        row["news_topics"], row["falsification"],
                    )
                    if inferred["inferred_assets"] or inferred["inferred_indicators"] or inferred["inferred_news_topics"]:
                        st.caption(
                            "自动关联："
                            f"资产 {','.join(inferred['inferred_assets']) or '—'} | "
                            f"指标 {','.join(inferred['inferred_indicators']) or '—'} | "
                            f"主题 {','.join(inferred['inferred_news_topics']) or '—'}"
                        )
                with top[1]:
                    st.metric("状态", row["status"])
                with top[2]:
                    st.metric("置信度", f"{row['confidence']:.0%}")
                st.write(row["thesis"])
                if row["falsification"]:
                    st.caption(f"证伪条件：{row['falsification']}")

                with st.expander("编辑这条假设", expanded=False):
                    with st.form(f"edit_hypothesis_{row['id']}"):
                        e_title = st.text_input("假设名称", value=row["title"])
                        e_thesis = st.text_area("核心逻辑", value=row["thesis"], height=100)
                        e1, e2, e3 = st.columns(3)
                        with e1:
                            e_assets = st.text_input("相关资产", value=row["assets"])
                        with e2:
                            e_indicators = st.text_input("相关指标", value=row["indicators"])
                        with e3:
                            e_topics = st.text_input("新闻主题", value=row["news_topics"])
                        e_falsification = st.text_area("证伪条件", value=row["falsification"], height=70)
                        e4, e5 = st.columns(2)
                        with e4:
                            e_confidence = st.slider("置信度", 0.0, 1.0, float(row["confidence"]), 0.05, key=f"conf_{row['id']}")
                        with e5:
                            status_options = ["active", "watching", "paused", "retired"]
                            e_status = st.selectbox(
                                "状态", status_options,
                                index=status_options.index(row["status"]) if row["status"] in status_options else 0,
                                key=f"status_{row['id']}",
                            )
                        if st.form_submit_button("更新"):
                            update_research_hypothesis(
                                row["id"], e_title, e_thesis, e_assets, e_indicators,
                                e_topics, e_falsification, e_status, e_confidence,
                            )
                            st.success("已更新，刷新页面后可见")
                st.divider()

with tab_v:
    hypotheses = query_research_hypotheses(limit=100)
    h_options = {"不绑定假设": None}
    h_options.update({f"{r['id']} · {r['title']}": r["id"] for r in hypotheses})
    with st.form("add_viewpoint"):
        c1, c2, c3 = st.columns(3)
        with c1:
            view_date = st.date_input("日期", value=date.today())
        with c2:
            area = st.selectbox("领域", ["general", "us_macro", "china_macro", "liquidity", "crypto", "risk_assets"])
        with c3:
            stance = st.selectbox("观点", ["positive", "neutral", "negative", "mixed", "watching"])
        hypothesis_label = st.selectbox("绑定假设", list(h_options.keys()))
        rationale = st.text_area("判断", placeholder="今天你的观点是什么？为什么？", height=90)
        evidence = st.text_area("证据", placeholder="引用哪些指标、新闻或价格变化？", height=70)
        watch_next = st.text_area("后续观察", placeholder="接下来要看什么？", height=70)
        if st.form_submit_button("记录观点", type="primary"):
            add_viewpoint_log(
                hypothesis_id=h_options[hypothesis_label],
                view_date=view_date.strftime("%Y-%m-%d"),
                area=area,
                stance=stance,
                rationale=rationale,
                evidence=evidence,
                watch_next=watch_next,
            )
            st.success("观点已记录")

    st.subheader("最近观点")
    logs = query_viewpoint_logs(limit=30)
    if not logs:
        st.info("还没有观点日志。")
    else:
        for row in logs:
            with st.container():
                st.markdown(f"**{row['view_date']} · {row['area']} · `{row['stance']}`**")
                if row["hypothesis_title"]:
                    st.caption(f"绑定假设：{row['hypothesis_title']}")
                if row["rationale"]:
                    st.write(row["rationale"])
                if row["evidence"]:
                    st.caption(f"证据：{row['evidence']}")
                if row["watch_next"]:
                    st.caption(f"后续观察：{row['watch_next']}")
                st.divider()

with tab_w:
    with st.form("add_watchlist"):
        title = st.text_input("观察项", placeholder="例如：USDCNH 是否突破 7.35")
        trigger = st.text_input("触发条件", placeholder="USDCNH > 7.35 且 HSTECH 走弱")
        why = st.text_area("为什么重要", placeholder="触发后会改变什么判断？", height=80)
        c1, c2 = st.columns(2)
        with c1:
            linked_assets = st.text_input("相关资产", placeholder="CNH,HSTECH,DXY")
        with c2:
            linked_indicators = st.text_input("相关指标", placeholder="USDCNH,DX-Y.NYB")
        if st.form_submit_button("加入观察项", type="primary"):
            if title:
                add_watchlist_item(title, trigger, why, linked_assets, linked_indicators)
                st.success("观察项已添加")
            else:
                st.warning("观察项标题不能为空")

    st.subheader("当前观察项")
    items = query_watchlist_items(limit=50)
    if not items:
        st.info("还没有观察项。")
    else:
        for item in items:
            with st.container():
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.markdown(f"**{item['title']}**")
                    st.caption(f"触发：{item['trigger'] or '—'}")
                    if item["why"]:
                        st.write(item["why"])
                    st.caption(f"资产：{item['linked_assets'] or '—'} | 指标：{item['linked_indicators'] or '—'}")
                    inferred = infer_research_links(
                        item["title"], item["why"], item["linked_assets"], item["linked_indicators"],
                        extra_text=item["trigger"],
                    )
                    if inferred["inferred_assets"] or inferred["inferred_indicators"]:
                        st.caption(
                            "自动关联："
                            f"资产 {','.join(inferred['inferred_assets']) or '—'} | "
                            f"指标 {','.join(inferred['inferred_indicators']) or '—'}"
                        )
                with cols[1]:
                    st.metric("状态", item["status"])
                with cols[2]:
                    watch_status_options = ["active", "triggered", "done", "paused"]
                    new_status = st.selectbox(
                        "更新状态",
                        watch_status_options,
                        index=watch_status_options.index(item["status"]) if item["status"] in watch_status_options else 0,
                        key=f"watch_{item['id']}",
                    )
                    if new_status != item["status"]:
                        update_watchlist_status(item["id"], new_status)
                        st.success("已更新")
                st.divider()
