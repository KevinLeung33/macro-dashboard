"""Research cockpit aggregation for the dashboard home page."""
from db.repository import query_research_context
from services.composite_signals import compute_composite_signals
from services.daily_context import get_data_health, get_market_moves, get_multi_window_trends, get_news_trends
from services.news_clusterer import get_important_clusters
from services.signal_stats import cockpit_signal_stats
from services.asset_bias import build_asset_biases


def build_cockpit():
    health = get_data_health()
    signals = compute_composite_signals(lookback=5)
    moves = get_market_moves(lookback_points=5, limit=6)
    trends = get_multi_window_trends()
    news_trends = get_news_trends()
    clusters = get_important_clusters(limit=5, min_severity=3)
    research = query_research_context(limit=20)
    review = cockpit_signal_stats()
    asset_biases = build_asset_biases(signals)

    stale = [x for x in health if x.get("status") in ("stale", "old", "error", "unavailable")]
    active_signals = [s for s in signals if s.get("level") in ("red", "yellow", "blue") or s.get("score", 0) > 0]
    top_signal = active_signals[0] if active_signals else None
    top_move = moves[0] if moves else None
    top_trend = trends[0] if trends else None
    top_news = news_trends[0] if news_trends else None

    research_count = len(research.get("active_hypotheses", []))
    watch_count = len(research.get("active_watchlist", []))

    return {
        "health": health,
        "stale_sources": stale,
        "signals": signals,
        "top_signal": top_signal,
        "moves": moves,
        "top_move": top_move,
        "trends": trends,
        "top_trend": top_trend,
        "news_trends": news_trends,
        "top_news": top_news,
        "clusters": clusters,
        "research_count": research_count,
        "watch_count": watch_count,
        "signal_review": review,
        "asset_biases": asset_biases,
    }
