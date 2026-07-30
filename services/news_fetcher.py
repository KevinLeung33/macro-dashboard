"""新闻抓取器 v2 — 扩展RSS + Alpha Vantage + 选择漏斗"""
import hashlib
import logging
from datetime import datetime, timezone

import feedparser
import requests

from db.repository import insert_news_article
from services.time_utils import app_now

logger = logging.getLogger("news_fetcher")

RSS_FEEDS = {
    # 美国官方
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    # 金融媒体
    "CNBC Top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Reuters Business": "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best&best-sectors=business-finance",
    # Crypto
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss.xml",
    "Cointelegraph": "https://cointelegraph.com/rss",
    # 中国
    "Caixin": "https://rsshub.app/caixin/latest",
}

TOPIC_KEYWORDS = {
    "fed": ["fed", "fomc", "federal reserve", "powell", "warsh", "rate hike", "rate cut", "interest rate", "monetary"],
    "inflation": ["inflation", "cpi", "ppi", "pce", "price index", "cost of living"],
    "growth": ["gdp", "recession", "economic growth", "slowdown", "manufacturing", "pmi"],
    "employment": ["jobs", "unemployment", "payroll", "jolts", "layoff", "wage"],
    "geopolitics": ["iran", "middle east", "war", "sanction", "trade war", "tariff", "hormuz", "opec"],
    "crypto": ["bitcoin", "btc", "crypto", "ethereum", "defi", "stablecoin", "blockchain", "miner"],
    "china": ["china", "chinese", "beijing", "pboc", "yuan", "rmb", "onshore", "offshore"],
    "energy": ["oil", "crude", "natural gas", "energy", "petroleum", "shale"],
    "credit": ["bond", "yield", "credit", "spread", "default", "bank", "financial stability"],
    "liquidity": ["repo", "sofr", "reserve", "tga", "rrp", "balance sheet", "qt", "qe"],
}


def _classify_topic(title, summary=""):
    text = (title + " " + summary).lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return topic
    return "other"


def _hash(url, title):
    return hashlib.md5((url or title).encode()).hexdigest()[:16]


def fetch_rss(feeds=None):
    if feeds is None:
        feeds = RSS_FEEDS

    added = 0
    for source_name, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                topic = _classify_topic(title, summary)
                if topic == "other":
                    continue

                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_str = datetime(*pub[:6]).strftime("%Y-%m-%d %H:%M") if pub else app_now().strftime("%Y-%m-%d %H:%M")

                rid = insert_news_article(
                    source=source_name, source_type="rss", url=link,
                    title=title, summary=summary[:500],
                    published_at=pub_str, topic=topic,
                )
                if rid:
                    added += 1
        except Exception as e:
            logger.warning(f"RSS {source_name}: {e}")

    return added


def fetch_alpha_vantage(api_key=None):
    """Alpha Vantage NEWS_SENTIMENT"""
    import os
    key = api_key or os.getenv("ALPHA_VANTAGE_KEY")
    if not key:
        return 0

    topics = ["crypto", "forex", "economy_macro", "energy", "financial_markets"]
    added = 0

    for topic in topics:
        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "topics": topic,
                    "apikey": key,
                    "limit": 5,
                },
                timeout=20,
            )
            data = resp.json()
            for item in data.get("feed", []):
                title = item.get("title", "")
                summary = item.get("summary", "")
                url = item.get("url", "")
                source = item.get("source", "")
                pub = item.get("time_published", "")
                if pub:
                    pub_str = f"{pub[:4]}-{pub[4:6]}-{pub[6:8]} {pub[9:11]}:{pub[11:13]}"

                # Pre-classified sentiment from AV
                av_sentiment = item.get("overall_sentiment_label", "")
                av_score = item.get("overall_sentiment_score", 0)

                # Extract tickers
                tickers = [t.get("ticker") for t in item.get("ticker_sentiment", [])[:5]]
                ticker_str = ",".join(tickers) if tickers else ""

                av_topic = _classify_topic(title, summary)
                if av_topic == "other":
                    continue

                rid = insert_news_article(
                    source=source, source_type="alpha_vantage", url=url,
                    title=title, summary=summary[:500],
                    published_at=pub_str, topic=av_topic,
                )
                if rid:
                    added += 1
        except Exception as e:
            logger.warning(f"Alpha Vantage {topic}: {e}")

    return added


def select_for_analysis(limit=15):
    """选择漏斗：从未分析的文章中选出最值得送AI分析的"""
    from db.repository import get_unanalyzed_articles

    articles = get_unanalyzed_articles(limit * 3)
    if not articles:
        return []

    # 去重：同源+同标题摘要相似 → 只留一条
    seen_titles = set()
    deduped = []
    for art in articles:
        short = art["title"][:50].lower().strip()
        if short not in seen_titles:
            seen_titles.add(short)
            deduped.append(art)

    # 按来源优先级排序：官方源 > 金融媒体 > crypto > 其他
    priority = {"Federal Reserve": 0, "Caixin": 0, "CNBC Top": 1, "MarketWatch": 1,
                "Reuters Business": 1, "CoinDesk": 2, "The Block": 2, "Cointelegraph": 2}
    deduped.sort(key=lambda x: priority.get(x["source"], 3))

    return deduped[:limit]


def fetch_all_news(av_key=None):
    total = fetch_rss()
    if av_key:
        total += fetch_alpha_vantage(av_key)
    logger.info(f"Total new articles: {total}")

    # Select top N for AI analysis
    selected = select_for_analysis(limit=20)
    logger.info(f"Selected {len(selected)} for AI analysis")
    return total
