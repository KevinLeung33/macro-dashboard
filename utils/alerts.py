"""阈值告警系统 — 扫描关键指标，越过阈值时触发告警"""

import pandas as pd
from db.repository import query_series, query_latest_values


def _last(df):
    return df["value"].iloc[-1] if not df.empty and len(df) > 0 else None


def check_alerts():
    alerts = []

    # -- VIX --
    vix = query_series("fred", "VIXCLS")
    v = _last(vix)
    if v and v > 30:
        alerts.append(("🔴", "VIX", f"恐慌区间: {v:.0f}", "VIX>30=危机模式。2008年峰值89，2020年峰值82。"))
    elif v and v > 25:
        alerts.append(("🟡", "VIX", f"偏高: {v:.0f}", "25-30=市场紧张，需关注是否会升级。"))

    # -- HY OAS --
    hy = query_series("fred", "BAMLH0A0HYM2")
    h = _last(hy)
    if h and h > 500:
        alerts.append(("🔴", "信用利差", f"危机水平: {h:.0f}bp", ">500bp=企业融资困难。2008年>2000bp。"))
    elif h and h > 400:
        alerts.append(("🟡", "信用利差", f"偏高: {h:.0f}bp", "400-500bp=信用条件收紧中。"))

    # -- 10Y-3M --
    t10 = query_series("fred", "T10Y3M")
    ts = _last(t10)
    if ts and ts < -0.5:
        alerts.append(("🔴", "收益率倒挂", f"深度倒挂: {ts:.2f}%", "<-0.5%=严重衰退预警。"))
    elif ts and ts < 0:
        alerts.append(("🟡", "收益率倒挂", f"轻度倒挂: {ts:.2f}%", "<0=衰退信号，需关注持续时间。"))

    # -- Consumer confidence --
    conf = query_series("fred", "UMCSENT")
    c = _last(conf)
    if c and c < 50:
        alerts.append(("🔴", "消费者信心", f"极低: {c:.0f}", "<50=衰退风险区域。信心弱→消费减速→GDP承压。"))
    elif c and c < 70:
        alerts.append(("🟡", "消费者信心", f"偏低: {c:.0f}", "70以下=谨慎消费。"))

    # -- NFCI --
    nfci = query_series("fred", "NFCI")
    n = _last(nfci)
    if n and n > 0.5:
        alerts.append(("🔴", "金融条件", f"收紧: {n:.2f}", "NFCI>0.5=金融条件明显收紧。>3=危机。"))
    elif n and n > 0:
        alerts.append(("🟡", "金融条件", f"略紧: {n:.2f}", "NFCI>0=条件偏紧，但与历史危机水平还有距离。"))

    # -- Unemployment trend --
    unemp = query_series("fred", "UNRATE")
    if not unemp.empty and len(unemp) > 12:
        recent = unemp["value"].iloc[-1]
        avg_12m = unemp["value"].iloc[-13:-1].mean()
        if recent > avg_12m + 0.3:
            alerts.append(("🔴", "失业率", f"上升中: {recent:.1f}% (3月均{avg_12m:.1f})",
                           "Sahm法则：失业率12月低点+0.5%=衰退确认。"))

    # -- BTC drawdown --
    btc = query_series("fred", "CBBTCUSD")
    if not btc.empty and len(btc) > 252:
        btc["52w_high"] = btc["value"].rolling(252).max()
        dd = (btc["value"].iloc[-1] / btc["52w_high"].iloc[-1] - 1) * 100
        if dd < -40:
            alerts.append(("🔴", "BTC", f"深跌: {dd:.0f}%", ">40%回撤=熊市确认。MSTR永动机叙事的破产加剧了抛压。"))

    return alerts
