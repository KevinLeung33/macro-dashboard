"""新闻抓取器 — 官方 RSS/媒体 RSS + Alpha Vantage + 选择漏斗。

RSS 只负责快速入库，AI 分析由较低频的完整新闻任务执行。这样既能减少
漏掉一小时内较早新闻的概率，也不会因为每次刷新都调用 AI 而放大成本和
限频风险。
"""
import hashlib
import logging
import os
from datetime import datetime, timezone

import feedparser
import requests

from db.repository import get_news_feed_state, insert_news_article, update_news_feed_state
from services.time_utils import app_now

logger = logging.getLogger("news_fetcher")

RSS_FEEDS = {
    # 美国官方
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "SEC Press Releases": "https://www.sec.gov/news/pressreleases.rss",
    "EIA Today in Energy": "https://www.eia.gov/rss/todayinenergy.xml",
    "EIA Press Releases": "https://www.eia.gov/rss/press_rss.xml",
    # 已在生产服务器实测可用的官方宏观发布源
    "国家统计局·数据发布": "https://www.stats.gov.cn/sj/zxfb/rss.xml",
    "国家统计局·数据解读": "https://www.stats.gov.cn/sj/sjjd/rss.xml",
    "ECB Press Releases": "https://www.ecb.europa.eu/rss/press.html",
    "ECB Statistical Releases": "https://www.ecb.europa.eu/rss/statpress.html",
    # 金融媒体
    "CNBC Top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    # Crypto
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    # Crypto 中文官方资讯
    "Wu Blockchain": "https://www.wublock123.com/feed",
}

# 财新没有在本机可直连的官方 RSS；只允许显式启用已经实测通过的
# 第三方镜像。它是补充媒体源，绝不作为宏观数据或告警的基础依赖。
_caixin_rss_mirror = os.getenv("CAIXIN_RSS_MIRROR_URL", "").strip()
if _caixin_rss_mirror:
    RSS_FEEDS["Caixin (RSS mirror)"] = _caixin_rss_mirror

RSS_SOURCE_PRIORITY = {
    "Federal Reserve": 0,
    "SEC Press Releases": 0,
    "EIA Today in Energy": 0,
    "EIA Press Releases": 0,
    "国家统计局·数据发布": 0,
    "国家统计局·数据解读": 0,
    "ECB Press Releases": 0,
    "ECB Statistical Releases": 0,
    "Wu Blockchain": 1,
    "CNBC Top": 2,
    "MarketWatch": 2,
    "Caixin (RSS mirror)": 2,
    "CoinDesk": 3,
    "Cointelegraph": 3,
}

# These are useful breadth sources, but one publisher timing out must not make
# the whole macro/news pipeline look unhealthy. Their state remains visible in
# the dashboard and database; the health monitor only pages on core releases.
RSS_OPTIONAL_SOURCES = {
    "CNBC Top", "MarketWatch", "Caixin (RSS mirror)",
    "CoinDesk", "Cointelegraph", "Wu Blockchain",
}


def _env_int(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}

TOPIC_KEYWORDS = {
    "fed": ["fed", "fomc", "federal reserve", "powell", "warsh", "rate hike", "rate cut", "interest rate", "monetary", "美联储", "联储", "降息", "加息", "利率", "鲍威尔"],
    "inflation": ["inflation", "cpi", "ppi", "pce", "price index", "cost of living", "通胀", "通缩", "物价", "居民消费价格", "生产者价格", "工业品出厂"],
    "growth": ["gdp", "recession", "economic growth", "slowdown", "manufacturing", "pmi", "增长", "衰退", "制造业", "经济", "工业增加值", "固定资产投资", "社会消费品", "零售", "房地产", "经济运行"],
    "employment": ["jobs", "unemployment", "payroll", "jolts", "layoff", "wage", "就业", "失业", "非农", "工资"],
    "geopolitics": ["iran", "middle east", "war", "sanction", "trade war", "tariff", "hormuz", "opec", "伊朗", "中东", "战争", "制裁", "关税", "地缘"],
    "crypto": ["bitcoin", "btc", "crypto", "ethereum", "defi", "stablecoin", "blockchain", "miner", "比特币", "以太坊", "加密", "区块链", "稳定币", "交易所", "链上", "web3"],
    "china": ["china", "chinese", "beijing", "pboc", "yuan", "rmb", "onshore", "offshore", "中国", "央行", "人民币", "国家统计局", "外汇储备"],
    "energy": ["oil", "crude", "natural gas", "energy", "petroleum", "shale", "原油", "天然气", "能源", "石油"],
    "credit": ["bond", "yield", "credit", "spread", "default", "bank", "financial stability", "债券", "收益率", "信用", "利差", "银行"],
    "liquidity": ["repo", "sofr", "reserve", "tga", "rrp", "balance sheet", "qt", "qe", "流动性", "回购", "准备金", "逆回购", "缩表", "m2", "社会融资", "社融", "信贷"],
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
    max_entries = _env_int("RSS_MAX_ENTRIES_PER_FEED", 30)
    timeout = _env_float("NEWS_HTTP_TIMEOUT_SECONDS", 20)
    optional_timeout = min(timeout, _env_float("NEWS_OPTIONAL_HTTP_TIMEOUT_SECONDS", 8))
    for source_name, url in feeds.items():
        try:
            # 直接用 requests 设置超时和 UA；feedparser.parse(url) 没有可靠的
            # 超时控制，源站卡住时会拖住整个定时任务。
            state = get_news_feed_state(source_name)
            headers = {"User-Agent": os.getenv("NEWS_USER_AGENT", "macro-dashboard/1.0 RSS reader")}
            if state:
                if state["etag"]:
                    headers["If-None-Match"] = state["etag"]
                if state["last_modified"]:
                    headers["If-Modified-Since"] = state["last_modified"]
            response = requests.get(
                url,
                headers=headers,
                timeout=optional_timeout if source_name in RSS_OPTIONAL_SOURCES else timeout,
            )
            if response.status_code == 304:
                update_news_feed_state(
                    source_name, url,
                    etag=state["etag"] if state else "",
                    last_modified=state["last_modified"] if state else "",
                    last_success_at=app_now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    last_error="",
                )
                continue
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if getattr(feed, "bozo", False) and not feed.entries:
                raise ValueError(f"invalid RSS/Atom feed: {feed.bozo_exception}")

            update_news_feed_state(
                source_name, url,
                etag=response.headers.get("ETag", state["etag"] if state else ""),
                last_modified=response.headers.get("Last-Modified", state["last_modified"] if state else ""),
                last_success_at=app_now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                last_error="",
            )

            for entry in feed.entries[:max_entries]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                topic = _classify_topic(title, summary)
                if topic == "other":
                    continue

                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_str = (
                    datetime(*pub[:6], tzinfo=timezone.utc).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    if pub else app_now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                )

                rid = insert_news_article(
                    source=source_name, source_type="rss", url=link,
                    title=title, summary=summary[:500],
                    published_at=pub_str, topic=topic,
                )
                if rid:
                    added += 1
        except Exception as e:
            try:
                state = get_news_feed_state(source_name)
                update_news_feed_state(
                    source_name, url,
                    etag=state["etag"] if state else "",
                    last_modified=state["last_modified"] if state else "",
                    last_error=str(e)[:500],
                )
            except Exception:
                logger.debug("Could not save RSS state for %s", source_name, exc_info=True)
            logger.warning(f"RSS {source_name}: {e}")

    return added


def fetch_alpha_vantage(api_key=None):
    """Alpha Vantage NEWS_SENTIMENT"""
    import os
    key = api_key or os.getenv("ALPHA_VANTAGE_KEY")
    if not key:
        return 0

    # A free Alpha Vantage key allows only 25 requests/day.  The old five-topic
    # hourly loop made 120 requests/day and therefore guaranteed a rate-limit
    # response.  This optional source is deliberately narrow when enabled;
    # primary news coverage comes from RSS and official feeds.
    topics = [
        item.strip()
        for item in os.getenv("ALPHA_VANTAGE_NEWS_TOPICS", "financial_markets").split(",")
        if item.strip()
    ]
    max_requests = _env_int("ALPHA_VANTAGE_NEWS_MAX_REQUESTS_PER_RUN", 1)
    added = 0

    for topic in topics[:max_requests]:
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
                pub_str = app_now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
    from db.repository import (
        backfill_news_article_identities,
        get_recent_analyzed_title_fingerprints,
        get_unanalyzed_articles,
        mark_articles_deduplicated,
    )
    from services.news_identity import title_fingerprint

    # Legacy rows predate the identity columns.  Backfilling is metadata-only
    # and makes the same rules work immediately after an upgrade.
    backfill_news_article_identities(limit=max(200, limit * 8))
    articles = get_unanalyzed_articles(limit * 5)
    if not articles:
        return []

    try:
        dedup_days = max(1, int(os.getenv("NEWS_ANALYSIS_DEDUP_DAYS", "3")))
    except ValueError:
        dedup_days = 3
    analyzed_titles = get_recent_analyzed_title_fingerprints(dedup_days)
    selected_by_title = {}
    already_analyzed_ids = []
    for art in articles:
        fingerprint = art["title_fingerprint"] or title_fingerprint(art["title"])
        if not fingerprint or fingerprint in analyzed_titles:
            already_analyzed_ids.append(art["id"])
            continue
        previous = selected_by_title.get(fingerprint)
        if previous is None:
            selected_by_title[fingerprint] = art
            continue
        # Prefer the more authoritative source if several feeds carry the
        # exact same headline. The lower priority number wins.
        previous_priority = RSS_SOURCE_PRIORITY.get(previous["source"], 9)
        current_priority = RSS_SOURCE_PRIORITY.get(art["source"], 9)
        if current_priority < previous_priority:
            selected_by_title[fingerprint] = art

    # Do not discard same-batch alternatives yet: if the preferred source's AI
    # request fails, they remain available as a fallback on the next run. Once
    # any one succeeds, the next pass marks the exact-title copies as skipped.
    if already_analyzed_ids:
        mark_articles_deduplicated(already_analyzed_ids)

    # 按来源优先级排序：官方源 > 金融媒体 > crypto > 其他
    deduped = list(selected_by_title.values())
    deduped.sort(key=lambda x: RSS_SOURCE_PRIORITY.get(x["source"], 9))

    return deduped[:limit]


def fetch_all_news(av_key=None):
    total = fetch_rss()
    if av_key and _env_bool("ALPHA_VANTAGE_NEWS_ENABLED", False):
        total += fetch_alpha_vantage(av_key)
    elif av_key:
        logger.info("Alpha Vantage news is configured but disabled; RSS remains the primary news source")
    logger.info(f"Total new articles: {total}")

    # Select top N for AI analysis
    selected = select_for_analysis(limit=20)
    logger.info(f"Selected {len(selected)} for AI analysis")
    return total
