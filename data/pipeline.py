from data.fetchers.fred_fetcher import fetch_and_store_fred
from data.fetchers.tic_fetcher import fetch_and_store_tic
from config.data_sources import source_enabled
from db.schema import init_db


def fetch_all(include_tic=True, include_crypto=True, include_global=True,
              include_alpha_vantage=True, include_stooq=True,
              include_yfinance=True, include_news=True,
              include_crypto_liquidity=True, incremental=True):
    init_db()
    if source_enabled("fred"):
        print(f"Fetching FRED (经济+市场数据, incremental={incremental})...")
        fetch_and_store_fred(incremental=incremental)
    if source_enabled("stooq", include_stooq):
        print("Fetching Stooq (market fallback)...")
        try:
            from data.fetchers.stooq_fetcher import fetch_and_store_stooq_market
            fetch_and_store_stooq_market(incremental=incremental)
        except Exception as e:
            print(f"  Stooq skipped: {e}")
    if source_enabled("alpha_vantage", include_alpha_vantage):
        print("Fetching Alpha Vantage market fallback...")
        try:
            from data.fetchers.alpha_vantage_fetcher import fetch_and_store_alpha_vantage_market
            fetch_and_store_alpha_vantage_market(incremental=incremental)
        except Exception as e:
            print(f"  Alpha Vantage market skipped: {e}")
    if source_enabled("yfinance", include_yfinance):
        print("Fetching yfinance (DXY/全球指数/股票)...")
        try:
            from data.fetchers.yfinance_fetcher import fetch_and_store_yfinance_market
            fetch_and_store_yfinance_market(incremental=incremental)
        except Exception as e:
            print(f"  yfinance skipped: {e}")
    if source_enabled("binance_spot", include_crypto):
        print("Fetching crypto spot (Binance)...")
        try:
            from data.fetchers.binance_spot_fetcher import fetch_and_store_binance_spot
            fetch_and_store_binance_spot(incremental=incremental)
        except Exception as e:
            print(f"  Binance spot skipped: {e}")
    if source_enabled("crypto_liquidity", include_crypto_liquidity):
        print("Fetching crypto liquidity (DefiLlama + CoinGecko fallback)...")
        try:
            from data.fetchers.crypto_liquidity_fetcher import fetch_and_store_crypto_liquidity
            fetch_and_store_crypto_liquidity(incremental=incremental)
        except Exception as e:
            print(f"  Crypto liquidity skipped: {e}")
    if source_enabled("crypto_market", include_crypto):
        print("Fetching crypto derivatives and configured fund flows...")
        try:
            from data.fetchers.crypto_market_fetcher import fetch_and_store_crypto_market
            fetch_and_store_crypto_market(incremental=incremental)
        except Exception as e:
            print(f"  Crypto derivatives/flows skipped: {e}")
    if source_enabled("akshare", include_global):
        print("Fetching global (AKShare: China PMI/CPI/社融)...")
        try:
            from data.fetchers.global_fetcher import fetch_global_data
            fetch_global_data(incremental=incremental)
        except Exception as e:
            print(f"  Global skipped: {e}")
    if source_enabled("akshare_hk_index", include_global):
        print("Fetching Hong Kong index (AKShare: exact HSTECH)...")
        try:
            from data.fetchers.hk_index_fetcher import fetch_and_store_hk_index_market
            fetch_and_store_hk_index_market(incremental=incremental)
        except Exception as e:
            print(f"  Hong Kong index skipped: {e}")
    if source_enabled("tic", include_tic):
        print("Fetching TIC (美债持有)...")
        fetch_and_store_tic()
    if source_enabled("news", include_news):
        print("Fetching news (RSS + Alpha Vantage)...")
        try:
            from services.news_fetcher import fetch_all_news
            count = fetch_all_news()
            print("Running AI analysis...")
            try:
                from services.ai_analyzer import run_analysis_pipeline
                analyzed = run_analysis_pipeline(limit=15)
                print(f"  AI analyzed {analyzed} articles")
            except Exception as e:
                print(f"  AI skipped: {e}")
        except Exception as e:
            print(f"  News skipped: {e}")
    print("All done.")
