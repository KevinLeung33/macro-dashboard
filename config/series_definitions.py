"""FRED 经济数据指标定义 (v3 - 带国旗emoji + 校验区间)"""

FRED_SERIES = {
    # --- 货币政策 🇺🇸 ---
    "FEDFUNDS": {
        "display_name": "🇺🇸 联邦基金利率", "unit": "%", "frequency": "monthly",
        "category": "monetary", "yaxis_label": "%", "valid_range": (0, 25), "is_pct": True,
    },
    "WALCL": {
        "display_name": "🇺🇸 美联储总资产", "unit": "百万美元", "frequency": "weekly",
        "category": "monetary", "yaxis_label": "万亿美元",
        "transform": lambda x: x / 1e6, "is_pct": False,
    },
    "DGS10": {
        "display_name": "🇺🇸 10年期美债收益率", "unit": "%", "frequency": "daily",
        "category": "monetary", "yaxis_label": "%", "valid_range": (0, 20), "is_pct": True,
    },
    "DGS2": {
        "display_name": "🇺🇸 2年期美债收益率", "unit": "%", "frequency": "daily",
        "category": "monetary", "yaxis_label": "%", "valid_range": (0, 20), "is_pct": True,
    },
    "T10Y2Y": {
        "display_name": "🇺🇸 10Y-2Y利差", "unit": "%", "frequency": "daily",
        "category": "monetary", "yaxis_label": "bp", "is_pct": True,
    },
    "DGS3MO": {
        "display_name": "🇺🇸 3个月国债收益率", "unit": "%", "frequency": "daily",
        "category": "monetary", "yaxis_label": "%", "valid_range": (0, 20), "is_pct": True,
    },
    "T10Y3M": {
        "display_name": "🇺🇸 10Y-3M利差", "unit": "%", "frequency": "daily",
        "category": "monetary", "yaxis_label": "%", "valid_range": (-5, 5), "is_pct": True,
    },
    "DFII10": {
        "display_name": "🇺🇸 10年期TIPS实际利率", "unit": "%", "frequency": "daily",
        "category": "monetary", "yaxis_label": "%", "valid_range": (-5, 10), "is_pct": True,
    },

    # --- 财政 🇺🇸 ---
    "GFDEBTN": {
        "display_name": "🇺🇸 联邦债务总额", "unit": "百万美元", "frequency": "quarterly",
        "category": "fiscal", "yaxis_label": "万亿美元",
        "transform": lambda x: x / 1e6, "is_pct": False,
    },
    "A091RC1Q027SBEA": {
        "display_name": "🇺🇸 联邦利息支出(年化)", "unit": "十亿美元", "frequency": "quarterly",
        "category": "fiscal", "yaxis_label": "千亿美元",
        "transform": lambda x: x / 1000, "is_pct": False,
    },

    # --- 就业 🇺🇸 ---
    "UNRATE": {
        "display_name": "🇺🇸 失业率", "unit": "%", "frequency": "monthly",
        "category": "employment", "yaxis_label": "%", "valid_range": (2, 20), "is_pct": True,
    },
    "PAYEMS": {
        "display_name": "🇺🇸 非农就业人数", "unit": "千人", "frequency": "monthly",
        "category": "employment", "yaxis_label": "百万人",
        "transform": lambda x: x / 1000, "is_pct": False,
    },
    "JTSJOL": {
        "display_name": "🇺🇸 JOLTS职位空缺", "unit": "千人", "frequency": "monthly",
        "category": "employment", "yaxis_label": "百万人",
        "transform": lambda x: x / 1000, "is_pct": False,
    },
    "ICSA": {
        "display_name": "🇺🇸 初请失业金人数", "unit": "人", "frequency": "weekly",
        "category": "employment", "yaxis_label": "万人",
        "transform": lambda x: x / 10000, "is_pct": False,
    },
    "CIVPART": {
        "display_name": "🇺🇸 劳动参与率", "unit": "%", "frequency": "monthly",
        "category": "employment", "yaxis_label": "%", "valid_range": (50, 70), "is_pct": True,
    },
    "AHETPI": {
        "display_name": "🇺🇸 平均时薪", "unit": "美元/小时", "frequency": "monthly",
        "category": "employment", "yaxis_label": "美元/小时", "valid_range": (5, 60), "is_pct": False,
    },
    "JTSQUR": {
        "display_name": "🇺🇸 自主离职率(Quits)", "unit": "%", "frequency": "monthly",
        "category": "employment", "yaxis_label": "%", "valid_range": (0, 5), "is_pct": True,
    },

    # --- 通胀 🇺🇸 ---
    "CPIAUCSL": {
        "display_name": "🇺🇸 CPI指数", "unit": "指数", "frequency": "monthly",
        "category": "inflation", "yaxis_label": "指数", "is_pct": False,
    },
    "CPILFESL": {
        "display_name": "🇺🇸 核心CPI指数", "unit": "指数", "frequency": "monthly",
        "category": "inflation", "yaxis_label": "指数", "is_pct": False,
    },
    "PCEPILFE": {
        "display_name": "🇺🇸 核心PCE指数", "unit": "指数", "frequency": "monthly",
        "category": "inflation", "yaxis_label": "指数", "is_pct": False,
    },
    "T10YIE": {
        "display_name": "🇺🇸 10年期盈亏平衡通胀率", "unit": "%", "frequency": "daily",
        "category": "inflation", "yaxis_label": "%", "valid_range": (-2, 8), "is_pct": True,
    },
    "T5YIE": {
        "display_name": "🇺🇸 5年期盈亏平衡通胀率", "unit": "%", "frequency": "daily",
        "category": "inflation", "yaxis_label": "%", "valid_range": (-2, 8), "is_pct": True,
    },

    # --- GDP 🇺🇸 ---
    "GDP": {
        "display_name": "🇺🇸 GDP", "unit": "十亿美元", "frequency": "quarterly",
        "category": "gdp", "yaxis_label": "万亿美元",
        "transform": lambda x: x / 1000, "is_pct": False,
    },

    # --- 市场 🇺🇸 ---
    "SP500": {
        "display_name": "🇺🇸 标普500", "unit": "点", "frequency": "daily",
        "category": "us_equities", "yaxis_label": "点", "is_pct": False,
    },
    "NASDAQCOM": {
        "display_name": "🇺🇸 纳斯达克综合", "unit": "点", "frequency": "daily",
        "category": "us_equities", "yaxis_label": "点", "is_pct": False,
    },
    "DJIA": {
        "display_name": "🇺🇸 道琼斯工业", "unit": "点", "frequency": "daily",
        "category": "us_equities", "yaxis_label": "点", "is_pct": False,
    },
    "VIXCLS": {
        "display_name": "😱 VIX恐慌指数", "unit": "点", "frequency": "daily",
        "category": "volatility", "yaxis_label": "点", "valid_range": (5, 100), "is_pct": False,
    },

    # --- 汇率 ---
    "DEXUSEU": {
        "display_name": "💱 USD/EUR", "unit": "", "frequency": "daily",
        "category": "fx", "yaxis_label": "USD/EUR", "valid_range": (0.5, 2.0), "is_pct": False,
    },
    "DEXJPUS": {
        "display_name": "💱 JPY/USD", "unit": "", "frequency": "daily",
        "category": "fx", "yaxis_label": "JPY/USD", "valid_range": (0.005, 0.02), "is_pct": False,
    },

    # --- 大宗 🌐 ---
    "DCOILWTICO": {
        "display_name": "🛢️ WTI原油", "unit": "美元/桶", "frequency": "daily",
        "category": "commodities", "yaxis_label": "美元", "valid_range": (-10, 200), "is_pct": False,
    },
    "GOLDAMGBD228NLBR": {
        "disabled": True,
        "display_name": "🥇 黄金(伦敦定盘)", "unit": "美元/盎司", "frequency": "daily",
        "category": "commodities", "yaxis_label": "美元", "is_pct": False,
    },
    "DHHNGSP": {
        "display_name": "🇺🇸 天然气(Henry Hub)", "unit": "美元/百万BTU", "frequency": "daily",
        "category": "commodities", "yaxis_label": "美元", "valid_range": (0, 20), "is_pct": False,
    },
    "PCOPPUSDM": {
        "display_name": "🪙 铜价(全球)", "unit": "美元/吨", "frequency": "monthly",
        "category": "commodities", "yaxis_label": "美元", "is_pct": False,
    },

    # --- 增长与情绪 🇺🇸 ---
    "INDPRO": {
        "display_name": "🇺🇸 工业产出指数", "unit": "指数", "frequency": "monthly",
        "category": "growth", "yaxis_label": "指数", "is_pct": False,
    },
    "NAPM": {
        "disabled": True,
        "display_name": "🇺🇸 ISM制造业PMI", "unit": "", "frequency": "monthly",
        "category": "growth", "yaxis_label": "", "valid_range": (20, 80), "is_pct": False,
    },
    "UMCSENT": {
        "display_name": "🇺🇸 密歇根消费者信心", "unit": "", "frequency": "monthly",
        "category": "sentiment", "yaxis_label": "", "valid_range": (30, 150), "is_pct": False,
    },
    "RSAFS": {
        "display_name": "🇺🇸 零售销售", "unit": "百万美元", "frequency": "monthly",
        "category": "growth", "yaxis_label": "十亿美元",
        "transform": lambda x: x / 1000, "valid_range": (200, 2000), "is_pct": False,
    },

    # --- 信用 🇺🇸 ---
    "BAMLH0A0HYM2": {
        "display_name": "🇺🇸 高收益债利差(OAS)", "unit": "bp", "frequency": "daily",
        "category": "credit", "yaxis_label": "bp",
        "transform": lambda x: x * 100, "valid_range": (100, 3000), "is_pct": False,
    },
    "NFCI": {
        "display_name": "🇺🇸 芝加哥金融条件NFCI", "unit": "", "frequency": "weekly",
        "category": "credit", "yaxis_label": "", "valid_range": (-2, 5), "is_pct": False,
    },
    # --- 美元流动性 ---
    "BAMLC0A0CM": {
        "display_name": "🇺🇸 投资级信用利差(OAS)", "unit": "bp", "frequency": "daily",
        "category": "credit", "yaxis_label": "bp",
        "transform": lambda x: x * 100, "valid_range": (50, 1000), "is_pct": False,
    },
    "RRPONTSYD": {
        "display_name": "🇺🇸 隔夜逆回购RRP", "unit": "十亿美元", "frequency": "daily",
        "category": "liquidity", "yaxis_label": "十亿$",
        "transform": lambda x: x / 1000, "valid_range": (0, 5000), "is_pct": False,
    },
    "WRESBAL": {
        "display_name": "🇺🇸 银行准备金", "unit": "十亿美元", "frequency": "weekly",
        "category": "liquidity", "yaxis_label": "十亿$",
        "transform": lambda x: x / 1000, "valid_range": (500, 5000), "is_pct": False,
    },
    "WTREGEN": {
        "display_name": "🇺🇸 TGA财政部账户", "unit": "十亿美元", "frequency": "weekly",
        "category": "liquidity", "yaxis_label": "十亿$",
        "transform": lambda x: x / 1000, "valid_range": (0, 2000), "is_pct": False,
    },
    "SOFR": {
        "display_name": "🇺🇸 SOFR隔夜利率", "unit": "%", "frequency": "daily",
        "category": "liquidity", "yaxis_label": "%", "valid_range": (0, 10), "is_pct": True,
    },

    # --- 加密 🌐 ---
    "CBBTCUSD": {
        "display_name": "₿ BTC (Coinbase)", "unit": "美元", "frequency": "daily",
        "category": "crypto", "yaxis_label": "美元", "is_pct": False,
    },
}

YFINANCE_SYMBOLS = {
    "^GSPC": {"display_name": "🇺🇸 标普500", "category": "us_equities", "yaxis_label": "点"},
    "^IXIC": {"display_name": "🇺🇸 纳斯达克综合", "category": "us_equities", "yaxis_label": "点"},
    "^DJI": {"display_name": "🇺🇸 道琼斯工业", "category": "us_equities", "yaxis_label": "点"},
    "^RUT": {"display_name": "🇺🇸 罗素2000", "category": "us_equities", "yaxis_label": "点"},
    "^VIX": {"display_name": "😱 VIX恐慌指数", "category": "volatility", "yaxis_label": "点"},
    "DX-Y.NYB": {"display_name": "💱 美元指数DXY", "category": "fx", "yaxis_label": "点"},
    "GC=F": {"display_name": "🥇 黄金期货", "category": "commodities", "yaxis_label": "美元/盎司"},
    "CL=F": {"display_name": "🛢️ WTI原油期货", "category": "commodities", "yaxis_label": "美元/桶"},
    "MSTR": {"display_name": "🇺🇸 Strategy(MSTR)", "category": "crypto_equity", "yaxis_label": "美元"},
    "MU": {"display_name": "🇺🇸 美光科技", "category": "semiconductors", "yaxis_label": "美元"},
    "NVDA": {"display_name": "🇺🇸 英伟达", "category": "semiconductors", "yaxis_label": "美元"},
    "^N225": {"display_name": "🇯🇵 日经225", "category": "international", "yaxis_label": "点"},
    "^KS11": {"display_name": "🇰🇷 韩国KOSPI", "category": "international", "yaxis_label": "点"},
    "^HSI": {"display_name": "🇭🇰 恒生指数", "category": "international", "yaxis_label": "点"},
    "USDCNH=X": {"display_name": "🇨🇳 美元/离岸人民币", "category": "fx", "yaxis_label": "CNH"},
    "USDCNY=X": {"display_name": "🇨🇳 美元/在岸人民币", "category": "fx", "yaxis_label": "CNY"},
    "000300.SS": {"display_name": "🇨🇳 沪深300", "category": "international", "yaxis_label": "点"},
    "399006.SZ": {"display_name": "🇨🇳 创业板指", "category": "international", "yaxis_label": "点"},
    "^HSTECH": {"display_name": "🇭🇰 恒生科技", "category": "international", "yaxis_label": "点"},
    "^GDAXI": {"display_name": "🇩🇪 德国DAX", "category": "international", "yaxis_label": "点"},
    "^FTSE": {"display_name": "🇬🇧 英国富时100", "category": "international", "yaxis_label": "点"},
}

AKSHARE_SERIES = {
    "CN_PMI": {
        "display_name": "🇨🇳 中国官方制造业PMI", "unit": "", "category": "global_cycle",
        "fetch_func": "macro_china_pmi", "post_process": "pmi",
    },
    "CN_CAIXIN_PMI": {
        "display_name": "🇨🇳 中国财新制造业PMI", "unit": "", "category": "global_cycle",
        "fetch_func": "macro_china_cx_pmi_yearly", "post_process": "event_current",
    },
    "CN_LPR_1Y": {
        "display_name": "🇨🇳 中国LPR 1年期", "unit": "%", "category": "global_cycle",
        "fetch_func": "macro_china_lpr", "post_process": "lpr",
    },
    "CN_SOCIAL_FINANCING": {
        "display_name": "🇨🇳 中国社融增量", "unit": "亿元", "category": "global_cycle",
        "fetch_func": "macro_china_shrzgm", "post_process": "second_col",
    },
    "CN_CPI": {
        "display_name": "🇨🇳 中国CPI同比", "unit": "%", "category": "global_cycle",
        "fetch_func": "macro_china_cpi_monthly", "post_process": "cpi",
    },
    "CN_PPI": {
        "display_name": "🇨🇳 中国PPI同比", "unit": "%", "category": "global_cycle",
        "fetch_func": "macro_china_ppi", "post_process": "second_col",
    },
    "CN_M2_YOY": {
        "display_name": "🇨🇳 中国M2同比", "unit": "%", "category": "china_liquidity",
        "fetch_func": "macro_china_m2_yearly", "post_process": "event_current",
    },
    "CN_SOCIAL_FINANCING_STOCK_YOY": {
        "display_name": "🇨🇳 社融存量同比", "unit": "%", "category": "china_liquidity",
        "fetch_func": "macro_china_shrzgm", "post_process": "keyword_stock",
    },
    "CN_DR007": {
        "display_name": "🇨🇳 DR007", "unit": "%", "category": "china_liquidity",
        "fetch_func": "repo_rate_hist", "post_process": "repo_fdr007",
        "fetch_window_days": 365,
    },
}

ALL_CATEGORIES = [
    "monetary", "fiscal", "employment", "inflation", "gdp",
    "us_equities", "volatility", "fx", "commodities",
    "crypto", "crypto_equity", "semiconductors", "international",
    "growth", "sentiment", "credit", "liquidity", "global_cycle", "china_liquidity",
]
