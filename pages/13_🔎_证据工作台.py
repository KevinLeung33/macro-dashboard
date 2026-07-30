"""Research-oriented view that explains what core data means and how it connects."""
import streamlit as st

from db.schema import init_db
from db.repository import query_news_clusters, query_research_hypotheses
from services.research_guide import get_theme, list_themes, theme_snapshots
from utils.navigation import current_research_target, go_to_research


st.set_page_config(page_title="证据工作台", page_icon="🔎", layout="wide")
st.title("🔎 证据工作台")
st.caption("围绕一个研究问题组织数据、传导关系、事件和假设。数值是证据，不是结论本身。")
init_db()

target = current_research_target()
default_topic = target.get("topic") or target.get("focus")
themes = list_themes()
default_index = themes.index(default_topic) if default_topic in themes else 0
theme_name = st.selectbox("研究主题", themes, index=default_index)
theme = get_theme(theme_name)

st.subheader(theme["question"])
st.write(theme["why"])
st.info(f"传导关系：{theme['chain']}")

st.subheader("核心证据")
st.caption("先看这些指标共同指向什么；不要用单一读数直接推导资产结论。")
snapshots = theme_snapshots(theme_name)
for index in range(0, len(snapshots), 2):
    columns = st.columns(2)
    for column, item in zip(columns, snapshots[index:index + 2]):
        snapshot = item["snapshot"]
        with column:
            if snapshot:
                value = snapshot["value"]
                change = snapshot.get("change_n_pct")
                delta = f"近{snapshot.get('lookback_points', 5)}期 {change:+.2f}%" if change is not None else "近期变化暂无"
                st.metric(item["label"], f"{value:,.2f}", delta)
                st.caption(f"数据日期：{snapshot['date']} · {item['source']}/{item['series_id']}")
            else:
                st.metric(item["label"], "暂无数据")
                st.caption(f"尚未获得 {item['source']}/{item['series_id']} 的有效数据。")
            with st.expander("这个数据在看什么？", expanded=False):
                st.write(item["explanation"])
                st.caption("使用提示：优先看趋势、变化速度和与关联指标是否共振；先核对数据日期与频率。")

st.warning(f"本主题的重点观察：{theme['watch']}")

st.subheader("关联事件与研究假设")
topic_tokens = set(theme["keywords"])
clusters = query_news_clusters(limit=30, min_severity=1)
matched_clusters = [
    cluster for cluster in clusters
    if any(token and token in " ".join(str(cluster[key] or "") for key in ("title", "summary", "event_type", "assets_impacted")).lower()
           for token in topic_tokens)
]
hypotheses = query_research_hypotheses(limit=100)
matched_hypotheses = [
    row for row in hypotheses
    if any(token and token in " ".join(str(row[key] or "") for key in ("title", "thesis", "assets", "indicators", "news_topics")).lower()
           for token in topic_tokens)
]

left, right = st.columns(2)
with left:
    st.markdown("**近期关联事件**")
    if matched_clusters:
        for cluster in matched_clusters[:5]:
            st.caption(f"[{cluster['severity']}] {cluster['event_type']} · {cluster['article_count']}篇 · {cluster['title']}")
    else:
        st.caption("暂未按主题自动匹配到事件。可在新闻雷达中查看全部事件流，并持续完善主题标签。")
    if st.button("打开新闻雷达", use_container_width=True):
        go_to_research("pages/9_📡_新闻雷达.py", theme_name, "3M", topic=theme_name)
with right:
    st.markdown("**当前研究假设**")
    if matched_hypotheses:
        for hypothesis in matched_hypotheses[:5]:
            st.caption(f"{hypothesis['status']} · {hypothesis['confidence']:.0%} · {hypothesis['title']}")
    else:
        st.caption("暂未匹配到研究假设。建议在研究假设页记录自己的判断与证伪条件。")
    if st.button("打开研究假设", use_container_width=True):
        go_to_research("pages/11_🧭_研究假设.py", theme_name, "3M", topic=theme_name)

st.subheader("下一步研究动作")
st.markdown(
    "1. 核对核心指标是否同向变化。  \n"
    "2. 查看新闻是否解释了变化，还是只是同期噪声。  \n"
    "3. 更新研究假设：写明判断、反证条件和下一项要观察的数据。"
)
