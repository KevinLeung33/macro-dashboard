"""Compact, deterministic research brief for the dashboard home page."""
from services.time_utils import app_now


THEME_NAMES = {
    "liquidity": "流动性与利率",
    "fed": "流动性与利率",
    "growth": "增长与通胀",
    "credit": "信用与风险偏好",
    "crypto": "美元、Crypto 与风险资产",
}


def _pct(value):
    return "—" if value is None else f"{value:+.2f}%"


def _move_line(item):
    pct = item.get("change_n_pct")
    change = _pct(pct) if pct is not None else (
        "—" if item.get("change_n") is None else f"{item['change_n']:+.2f}{item.get('unit', '')}"
    )
    value = item.get("value")
    value_text = "—" if value is None else f"{value:,.2f}{item.get('unit', '')}"
    return f"{item.get('name', '指标')} 当前 {value_text}，近5期 {change}"


def _trend_line(item):
    windows = item.get("windows", {})
    parts = []
    for key in ("30d", "90d"):
        data = windows.get(key, {})
        if data.get("change_pct") is not None:
            parts.append(f"{key.upper()} {_pct(data['change_pct'])}")
        elif data.get("change") is not None:
            parts.append(f"{key.upper()} {data['change']:+.2f}{item.get('unit', '')}")
    if not parts:
        parts.append("暂无足够的中期历史")
    return f"{item.get('name', '指标')}：{'；'.join(parts)}"


def _theme_conclusions(signals):
    grouped = {}
    for signal in signals or []:
        theme = THEME_NAMES.get(signal.get("category"), "其他")
        current = grouped.get(theme)
        if current is None or signal.get("score", 0) > current.get("score", 0):
            grouped[theme] = signal

    output = []
    for theme in ("流动性与利率", "增长与通胀", "信用与风险偏好", "美元、Crypto 与风险资产"):
        signal = grouped.get(theme)
        if not signal:
            continue
        level = signal.get("level")
        if level == "unknown":
            conclusion = "关键数据不足，暂不下结论"
        else:
            conclusion = signal.get("summary") or "暂无明确方向"
        output.append({
            "theme": theme,
            "title": signal.get("name", theme),
            "conclusion": conclusion,
            "level": level,
            "watch_next": signal.get("watch_next", [])[:4],
        })
    return output


def build_home_brief(cockpit):
    """Build three reading horizons without calling an AI model on page load."""
    moves = cockpit.get("moves", [])
    trends = cockpit.get("trends", [])
    news = cockpit.get("news_trends", [])
    signals = cockpit.get("signals", [])
    health = cockpit.get("health", [])
    top_signal = cockpit.get("top_signal")

    today = []
    if top_signal:
        today.append(f"当前最需要关注的是「{top_signal['name']}」：{top_signal.get('summary') or '暂无明确解释'}。")
    if moves:
        today.append(f"最新数据中，{_move_line(moves[0])}。")
    if news:
        top_news = news[0]
        assets = "、".join(top_news.get("top_assets") or []) or "未明确资产"
        today.append(f"新闻侧主线是「{top_news.get('event_type', '其他')}」，近期开篇约 {top_news.get('count', 0)} 篇，主要涉及 {assets}。")
    if not today:
        today.append("当前没有足够的新数据或新闻形成今日结论。")

    week = []
    for item in moves[:3]:
        week.append(_move_line(item) + "。")
    if news:
        themes = "、".join(item.get("event_type", "其他") for item in news[:3])
        week.append(f"近一周新闻主题集中在：{themes}。需要结合数据确认新闻影响是否已经传导到资产价格。")
    if not week:
        week.append("近一周暂无足够的市场变化和新闻分析记录。")

    medium = [_trend_line(item) + "。" for item in trends[:4]]
    if not medium:
        medium.append("当前没有足够的 30D/90D 历史数据生成中期比较。")
    if len(trends) >= 1:
        medium.append("中期判断优先看 30D 与 90D 是否同向：同向代表趋势更稳定，分化则应降低单一指标的解释权重。")

    return {
        "generated_at": app_now().strftime("%Y-%m-%d %H:%M"),
        "data_dates": sorted({item.get("date") for item in moves if item.get("date")}, reverse=True)[:3],
        "health_warning_count": sum(
            1 for item in health if item.get("status") in ("quality_warning", "stale", "old", "error", "unavailable")
        ),
        "today": today,
        "week": week,
        "medium": medium,
        "themes": _theme_conclusions(signals),
    }
