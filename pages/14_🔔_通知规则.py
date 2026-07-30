"""Configure urgent-news notification rules without exposing delivery secrets."""
import streamlit as st

from db.schema import init_db
from services.news_alerts import (
    ASSETS,
    CHANNELS,
    EVENT_TYPES,
    ensure_news_alert_config,
    save_news_alert_config,
)
from services.access_control import render_admin_access, require_admin


st.set_page_config(page_title="通知规则", page_icon="🔔", layout="wide")
admin_access = render_admin_access()
st.title("🔔 通知规则")
st.caption("日报负责汇总；紧急推送只用于首次出现的高影响事件。")
init_db()
config = ensure_news_alert_config()
severity_options = [3, 4, 5]
window_options = [30, 60, 90, 120, 180]
severity_value = config["min_severity"] if config["min_severity"] in severity_options else 4
confidence_value = min(1.0, max(0.5, float(config["min_confidence"])))
article_value = min(10, max(1, int(config["min_articles"])))
window_value = config["max_age_minutes"] if config["max_age_minutes"] in window_options else 90
widget_defaults = {
    "alert_enabled": config["enabled"],
    "alert_min_severity": severity_value,
    "alert_min_confidence": confidence_value,
    "alert_min_articles": article_value,
    "alert_max_age_minutes": window_value,
    "alert_event_types": config["event_types"],
    "alert_assets": config["assets"],
    "alert_channels": config["channels"],
}
for key, value in widget_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


def _save_rule_changes():
    if not require_admin("修改通知规则"):
        return
    save_news_alert_config({
        "enabled": st.session_state.alert_enabled,
        "min_severity": st.session_state.alert_min_severity,
        "min_confidence": st.session_state.alert_min_confidence,
        "min_articles": st.session_state.alert_min_articles,
        "max_age_minutes": st.session_state.alert_max_age_minutes,
        "event_types": st.session_state.alert_event_types,
        "assets": st.session_state.alert_assets,
        "channels": st.session_state.alert_channels,
    })
    st.session_state["alert_saved"] = True

enabled = st.toggle("启用紧急新闻推送", key="alert_enabled", on_change=_save_rule_changes, disabled=not admin_access)
c1, c2, c3, c4 = st.columns(4)
with c1:
    min_severity = st.selectbox("最低严重度", severity_options, key="alert_min_severity", on_change=_save_rule_changes, disabled=not admin_access)
with c2:
    min_confidence = st.slider("最低置信度", 0.5, 1.0, key="alert_min_confidence", step=0.05, on_change=_save_rule_changes, disabled=not admin_access)
with c3:
    min_articles = st.number_input("最少来源文章", min_value=1, max_value=10, key="alert_min_articles", step=1, on_change=_save_rule_changes, disabled=not admin_access)
with c4:
    max_age_minutes = st.selectbox("事件有效窗口", window_options, key="alert_max_age_minutes", format_func=lambda value: f"{value} 分钟", on_change=_save_rule_changes, disabled=not admin_access)

left, right = st.columns(2)
with left:
    event_types = st.multiselect("关注的关键事件", EVENT_TYPES, key="alert_event_types", on_change=_save_rule_changes, disabled=not admin_access)
with right:
    assets = st.multiselect("关注的关键资产", ASSETS, key="alert_assets", on_change=_save_rule_changes, disabled=not admin_access)
channels = st.multiselect("推送渠道", CHANNELS, key="alert_channels", on_change=_save_rule_changes, disabled=not admin_access)

if enabled and not channels:
    st.warning("紧急推送已启用，但尚未选择推送渠道。")
elif st.session_state.get("alert_saved"):
    st.caption("已自动保存，下一轮新闻分析将使用新规则。")

st.subheader("当前规则说明")
st.markdown(
    "- 修改任意规则后会自动保存。  \n"
    "- 同一事件簇成功推送后不会重复发送。  \n"
    "- AI 未判定为新信息、超过有效窗口或不满足阈值的事件不会推送。  \n"
    "- 飞书 Webhook、签名密钥和 API Token 仅能在服务器 `.env` 配置。"
)
