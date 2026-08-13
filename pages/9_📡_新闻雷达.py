"""新闻雷达 — 结构化情报流 + 原始新闻"""
import json
import os
import streamlit as st
import pandas as pd

from db.repository import (
    query_analyzed_news, query_cluster_articles, query_cluster_research_links,
    query_news_clusters, query_news_feed_states, query_news_processing_summary, retry_failed_articles,
    query_recent_newsflash,
)
from db.schema import get_db
from services.ai_review import ai_review_statistics, refresh_ai_analysis_reviews
from services.news_clusterer import build_news_clusters
from services.news_fetcher import RSS_FEEDS
from services.news_research_links import refresh_news_research_links
from services.dashboard_overview import render_quality_strip
from utils.navigation import render_research_target
from services.time_utils import app_now
from services.access_control import render_admin_access, require_admin

st.set_page_config(page_title="新闻雷达", page_icon="📡", layout="wide")
admin_access = render_admin_access()
try:
    cluster_warn_article_count = max(5, int(os.getenv("NEWS_CLUSTER_WARN_ARTICLES", "25")))
except ValueError:
    cluster_warn_article_count = 25
st.title("📡 新闻雷达")
target = render_research_target()
render_quality_strip(["news"], title="新闻摘要数据质量")

processing = query_news_processing_summary()
status_labels = {
    "fetched": "已抓取", "queued": "待分析", "analyzing": "分析中",
    "analyzed": "已分析", "clustered": "已聚类", "deduplicated": "去重跳过", "failed": "失败",
}
status_cols = st.columns(7)
for index, status in enumerate(("fetched", "queued", "analyzing", "analyzed", "clustered", "deduplicated", "failed")):
    with status_cols[index]:
        st.metric(status_labels[status], processing["counts"].get(status, 0))

if processing["failed"]:
    retry_col, link_col = st.columns([1, 3])
    with retry_col:
        if st.button("重试失败文章", use_container_width=True, disabled=not admin_access) and require_admin("重试失败文章"):
            count = retry_failed_articles()
            st.success(f"已重新入队 {count} 篇失败文章")
            st.rerun()
    with link_col:
        st.caption("失败文章不会自动无限重试；重新入队后会在下一次新闻分析任务中处理。")
    with st.expander("查看失败原因", expanded=False):
        for item in processing["failed"]:
            st.caption(
                f"**{item['source']}** · 尝试 {item['processing_attempts']} 次 · "
                f"{item['title'][:100]}\n{item['processing_error'] or '未记录错误'}"
            )

# Translation helper
def t(title):
    try:
        from services.translator import translate_title
        return translate_title(title)
    except Exception:
        return title

tab0, tab1, tab2, tab3, tab4 = st.tabs(["⚡ 重要快讯", "🧩 事件流", "🧠 AI 分析", "📈 AI复盘", "📰 原始文章"])

# ====== TAB 0: Fast newsflash lane ======
with tab0:
    st.caption("快讯用于抢先发现事件；重要快讯可能尚未完成多源确认，不直接等同于交易结论。")
    flash_rows = query_recent_newsflash(limit=40, minutes=24 * 60)
    if not flash_rows:
        st.info("最近24小时暂无快讯，或 Odaily 快讯源尚未完成首次抓取。")
    else:
        important_terms = ("暂停提现", "暂停提币", "被盗", "黑客", "脱锚", "破产", "清算", "ETF获批", "hacked", "depeg", "bankrupt")
        important = [
            row for row in flash_rows
            if any(term.lower() in f"{row['title']} {row['summary']}".lower() for term in important_terms)
        ]
        st.metric("24小时快讯", len(flash_rows), f"重要候选 {len(important)} 条")
        for row in flash_rows[:20]:
            text = f"{row['title']} {row['summary']}".lower()
            is_important = any(term.lower() in text for term in important_terms)
            icon = "🔴" if is_important else "⚪"
            link = f"[打开原文]({row['url']})" if row["url"] else ""
            st.markdown(f"**{icon} {row['source']} · {row['published_at'] or '—'}**")
            st.write(row["title"])
            st.caption(f"{row['summary'][:360] if row['summary'] else '暂无摘要'} · {link}")
            st.divider()

# ====== TAB 1: Event Clusters ======
with tab1:
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        st.caption("事件流按具体事实合并多篇报道；同主题但事实不同的新闻会保留为独立事件。")
    with c2:
        min_cluster_sev = st.selectbox("最低严重度", [1, 2, 3, 4], index=2)
    with c3:
        if st.button("重建事件流", use_container_width=True, disabled=not admin_access) and require_admin("重建事件流"):
            with st.spinner("正在聚类并归并重复事件..."):
                result = build_news_clusters(days=3)
            st.success(
                f"事件流完成：{result['articles']}篇文章 → {result['clusters']}个规则事件，"
                f"退休 {result.get('retired', 0)} 个旧事件，合并 {result.get('merged', 0)} 个重复事件，"
                f"生成 {result.get('ai_conclusions', 0)} 条统一结论"
            )
            if result.get("truncated"):
                st.warning(
                    f"最近 {result.get('total_articles', result['articles'])} 篇已分析文章超过本轮处理上限；"
                    "为避免误隐藏旧事件，本轮没有回收旧簇。可提高 NEWS_CLUSTER_MAX_ARTICLES 后重建。"
                )
    with c4:
        if st.button("刷新研究关联", use_container_width=True, disabled=not admin_access) and require_admin("刷新研究关联"):
            result = refresh_news_research_links()
            st.success(f"已关联 {result['indicator_links']} 个指标、{result['hypothesis_links']} 条假设")

    clusters = query_news_clusters(limit=50, min_severity=min_cluster_sev)
    if not clusters:
        st.info("暂无事件流。先刷新新闻并完成AI分析，或点击「重建事件流」。")
    else:
        type_labels = {
            "fed_policy": "Fed", "inflation": "通胀", "growth": "增长",
            "employment": "就业", "geopolitics": "地缘", "china_macro": "中国",
            "crypto": "Crypto", "energy": "能源", "credit": "信用", "liquidity": "流动性",
            "other": "其他",
        }
        for row in clusters:
            sev_icon = {5: "🔴", 4: "🔴", 3: "🟡", 2: "⚪", 1: "⚪"}.get(row["severity"], "⚪")
            label = type_labels.get(row["event_type"], row["event_type"])
            display_title = row["ai_title"] or row["title"]
            display_summary = row["ai_summary"] or row["summary"]
            st.markdown(f"**{sev_icon} {label} · {row['article_count']}篇 · {display_title}**")
            st.caption(
                f"{row['first_seen_at'] or '—'} → {row['last_seen_at'] or '—'} | "
                f"来源 {row['primary_source'] or '—'}（{row['source_count'] or 0}个来源） | "
                f"资产 {row['assets_impacted'] or '—'} | "
                f"置信 {row['confidence']:.0%}"
            )
            if int(row["article_count"] or 0) > cluster_warn_article_count:
                st.caption("⚠️ 该事件证据数量异常偏大，建议展开相关文章核验是否需要拆分。")
            if display_summary:
                if row["ai_summary"]:
                    st.caption("AI 事件结论")
                st.write(display_summary)
            if row["ai_implications"]:
                st.caption(f"影响解读：{row['ai_implications']}")
            if row["ai_watch_next"]:
                st.caption(f"下一步观察：{row['ai_watch_next']}")
            research_links = query_cluster_research_links(row["id"])
            if research_links["indicators"]:
                indicator_text = " · ".join(
                    f"{item['label'] or item['series_id']}" for item in research_links["indicators"]
                )
                st.caption(f"关联指标：{indicator_text}")
            if research_links["hypotheses"]:
                hypothesis_text = " · ".join(
                    f"{item['title']} ({item['match_reason']})" for item in research_links["hypotheses"][:3]
                )
                st.caption(f"关联假设：{hypothesis_text}")
            with st.expander("相关文章", expanded=False):
                articles = query_cluster_articles(row["id"])
                for art in articles:
                    link = f"[🔗]({art['url']})" if art["url"] else ""
                    st.caption(
                        f"**{art['source']}** · {art['published_at'] or ''} · "
                        f"{t(art['summary_cn'] or art['title'])} {link}"
                    )
            st.divider()

# ====== TAB 2: AI Analysis ======
with tab2:
    f1, f2, f3 = st.columns(3)
    with f1:
        event_options = ["全部", "fed_policy", "inflation", "growth", "employment",
                         "geopolitics", "china_macro", "crypto", "energy", "credit", "liquidity"]
        target_topic = target.get("topic")
        event_filter = st.selectbox(
            "主题", event_options,
            index=event_options.index(target_topic) if target_topic in event_options else 0,
            format_func=lambda x: {
                "全部":"全部", "fed_policy":"Fed政策", "inflation":"通胀", "growth":"增长",
                "employment":"就业", "geopolitics":"地缘", "china_macro":"中国宏观",
                "crypto":"Crypto", "energy":"能源", "credit":"信用", "liquidity":"流动性"
            }.get(x, x)
        )
    with f2:
        asset_options = ["全部", "BTC", "DXY", "SP500", "NASDAQ", "Oil", "CNH", "HSTECH", "Gold"]
        target_asset = target.get("asset")
        asset_filter = st.selectbox(
            "影响资产", asset_options,
            index=asset_options.index(target_asset) if target_asset in asset_options else 0,
        )
    with f3:
        sev_filter = st.selectbox("严重度", ["全部", "🔴 ≥4", "🟡 3", "⚪ 1-2"])

    et = None if event_filter == "全部" else event_filter
    assets = None if asset_filter == "全部" else [asset_filter]
    min_sev = {"全部": 1, "🔴 ≥4": 4, "🟡 3": 3, "⚪ 1-2": 1}[sev_filter]
    rows = query_analyzed_news(event_type=et, min_severity=min_sev, assets=assets, limit=50)

    if not rows:
        st.info("点击首页「🔄 刷新数据」拉取新闻+AI分析")
        # Diagnostics
        with get_db() as diag_conn:
            articles_n = diag_conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
            ai_n = diag_conn.execute("SELECT COUNT(*) FROM ai_analyses").fetchone()[0]
        ds_ok = bool(os.getenv("OPENAI_API_KEY") and "sk-your" not in os.getenv("OPENAI_API_KEY", ""))
        av_ok = bool(os.getenv("ALPHA_VANTAGE_KEY"))
        st.caption(f"📋 新闻入库 {articles_n}篇 | AI已分析 {ai_n}篇 | DeepSeek {'✅' if ds_ok else '⚠️占位符'} | AV {'✅' if av_ok else '⚠️'}")
        if not ds_ok and av_ok:
            st.warning("DeepSeek key 还是占位符，替换 `.env` 中的 `sk-your-deepseek-key` 后点刷新")
    else:
        st.caption(f"共 {len(rows)} 条 | {event_filter} × {asset_filter} × {sev_filter}")

        type_colors = {
            "fed_policy": "#1f77b4", "inflation": "#d62728", "growth": "#2ca02c",
            "employment": "#9467bd", "geopolitics": "#8c564b", "china_macro": "#e377c2",
            "crypto": "#f7931a", "energy": "#bcbd22", "credit": "#ff7f0e", "liquidity": "#17becf",
        }
        type_labels = {
            "fed_policy": "Fed", "inflation": "通胀", "growth": "增长",
            "employment": "就业", "geopolitics": "地缘", "china_macro": "中国",
            "crypto": "Crypto", "energy": "能源", "credit": "信用", "liquidity": "流动性",
        }

        for r in rows:
            sev_icon = {5:"🔴",4:"🔴",3:"🟡",2:"⚪",1:"⚪"}.get(r["severity"],"⚪")
            tag_color = type_colors.get(r["event_type"],"#999")
            tag_label = type_labels.get(r["event_type"],r["event_type"])
            assets_str = r["assets_impacted"] or ""
            assets_list = [a.strip() for a in assets_str.split(",") if a.strip()]
            asset_tags = " ".join([f"`{a}`" for a in assets_list[:4]])
            try:
                direction = json.loads(r["direction"] or "{}")
                dir_tags = " ".join([
                    f"<span style='color:{'#2ca02c' if v=='bullish' else '#d62728' if v=='bearish' else '#999'}'>{k}:{v}</span>"
                    for k,v in list(direction.items())[:3]
                ])
            except (json.JSONDecodeError, TypeError):
                dir_tags = ""
            new_badge = "🆕" if r["is_new_information"] else ""

            st.markdown(f"""
            <div style="border-left:4px solid {tag_color};padding:10px 14px;margin:8px 0;border-radius:4px;background:rgba(128,128,128,0.05)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <span>{sev_icon} <b style="color:{tag_color}">{tag_label}</b> {new_badge}
                    <span style="color:#888;font-size:0.85em;margin-left:8px">{r['published_at'] or r['created_at']}</span></span>
                    <span style="color:#888;font-size:0.8em">{r['source']}</span>
                </div>
                <div style="font-size:0.95em;margin:4px 0">{t(r['summary_cn'] or r['title'])}</div>
                <div style="display:flex;gap:8px;margin-top:6px;font-size:0.85em">
                    <span>影响:{asset_tags}</span><span>{dir_tags}</span>
                    <span style="color:#888">置信:{r['confidence']:.0%}</span>
                </div>
                <div style="color:#666;font-size:0.82em;margin-top:2px">📖 {r['why_it_matters'] or ''}</div>
            </div>
            """, unsafe_allow_html=True)

# ====== TAB 3: AI Review ======
with tab3:
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("刷新 AI 复盘", use_container_width=True, disabled=not admin_access) and require_admin("刷新 AI 复盘"):
            result = refresh_ai_analysis_reviews()
            st.success(f"已更新 {result['reviewed']} 条资产复盘，跳过 {result['skipped']} 条无方向或无行情记录")
    with c2:
        st.caption("以新闻发布时间或 AI 创建日期为起点，按后续交易日计算 1/3/7/30 日表现；准确率只评价明确的 bullish/bearish 判断。")

    review_stats = ai_review_statistics(min_samples=2)
    if not review_stats["summary"]:
        st.info("暂无可复盘样本。先完成新闻 AI 分析，待资产数据产生后点击“刷新 AI 复盘”。")
    else:
        st.metric("AI 复盘样本", review_stats["sample_count"])
        review_df = pd.DataFrame(review_stats["summary"])
        columns = [
            "model", "prompt_version", "source", "event_type", "asset", "sample_count",
            "valid_return_7d", "accuracy_return_7d", "avg_return_7d",
            "valid_return_30d", "accuracy_return_30d", "avg_return_30d",
        ]
        for column in ("accuracy_return_7d", "avg_return_7d", "accuracy_return_30d", "avg_return_30d"):
            if column in review_df:
                review_df[column] = review_df[column].map(lambda value: None if value is None else round(value, 2))
        available_columns = [column for column in columns if column in review_df.columns]
        if not available_columns:
            st.info("当前复盘结果没有可展示的统计字段。请刷新 AI 复盘后再试。")
        else:
            missing_columns = [column for column in columns if column not in review_df.columns]
            if missing_columns:
                st.caption("部分复盘周期尚未生成，当前仅展示已有统计字段。")
            st.dataframe(
                review_df[available_columns].rename(columns={
                    "model": "模型", "prompt_version": "Prompt版本", "source": "新闻源",
                    "event_type": "事件", "asset": "资产", "sample_count": "样本",
                    "valid_return_7d": "7D有效", "accuracy_return_7d": "7D方向准确率%",
                    "avg_return_7d": "7D平均收益%", "valid_return_30d": "30D有效",
                    "accuracy_return_30d": "30D方向准确率%", "avg_return_30d": "30D平均收益%",
                }),
                use_container_width=True, hide_index=True,
            )

# ====== TAB 4: Raw Articles ======
with tab4:
    st.caption("原始文章（AI分析前），按时间倒序")
    with get_db() as raw_conn:
        raw_rows = raw_conn.execute(
            """SELECT title, source, source_type, feed_kind, topic, published_at, url, is_analyzed,
                      processing_status, processing_error, processing_attempts
               FROM news_articles ORDER BY published_at DESC LIMIT 50"""
        ).fetchall()

    if not raw_rows:
        st.info("暂无原始文章。点击首页🔄刷新按钮拉取新闻。")
    else:
        topics = sorted(set(r["topic"] for r in raw_rows if r["topic"]))
        tf = st.multiselect("按话题过滤", topics, default=[])
        filtered = [r for r in raw_rows if not tf or r["topic"] in tf]
        st.caption(f"共 {len(raw_rows)} 篇，筛选后 {len(filtered)} 篇")
        for r in filtered:
            status_icon = {"fetched": "📥", "queued": "⏳", "analyzing": "🧠", "analyzed": "✅", "clustered": "🧩", "deduplicated": "🧹", "failed": "🔴"}
            badge = status_icon.get(r["processing_status"], "⚪")
            tt = f"`{r['topic']}`" if r["topic"] else ""
            link = f"[🔗]({r['url']})" if r["url"] else ""
            kind_label = {"newsflash": "快讯", "article": "文章", "official_release": "官方公告", "official_data": "官方数据"}.get(r["feed_kind"], "综合")
            st.caption(f"{badge} `{kind_label}` {tt} **{t(r['title'][:100])}** _{r['source_type']}/{r['source']}_ {r['published_at'] or ''} {link}")

# Sidebar stats
with st.sidebar:
    st.subheader("📊 统计")
    with get_db() as sconn:
        total = sconn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
        analyzed = sconn.execute("SELECT COUNT(*) FROM ai_analyses").fetchone()[0]
        today = app_now().strftime("%Y-%m-%d")
        today_n = sconn.execute("SELECT COUNT(*) FROM news_articles WHERE published_at >= ?", (today,)).fetchone()[0]
        today_a = sconn.execute("SELECT COUNT(*) FROM ai_analyses WHERE created_at >= ?", (today,)).fetchone()[0]
    st.metric("历史文章", total)
    st.metric("已分析", analyzed)
    st.metric("今日新文章", today_n)
    st.metric("今日分析", today_a)
    # Old feed-state rows are retained in SQLite for audit but are not active
    # sources after a source replacement, so do not present them as failures.
    feed_states = [item for item in query_news_feed_states() if item["source"] in RSS_FEEDS]
    with st.expander("RSS 源状态", expanded=False):
        if not feed_states:
            st.caption("尚未抓取 RSS。")
        for feed in feed_states:
            icon = "✅" if not feed["last_error"] else "⚠️"
            st.caption(f"{icon} {feed['source']} · {feed['last_success_at'] or '未成功'}")
            if feed["last_error"]:
                st.caption(f"　{feed['last_error'][:160]}")
