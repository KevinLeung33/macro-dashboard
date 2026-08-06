"""Curated explanations and relationships for the dashboard's core research themes."""

from db.repository import query_series_snapshot


THEMES = {
    "美元流动性": {
        "question": "全球可用美元是在变得更充裕，还是更稀缺？",
        "why": "美元融资成本和流动性会影响风险资产估值、资本流向与加密资产的边际买盘。",
        "chain": "Fed资产负债表 / 准备金 -> 美元融资条件 -> DXY与实际利率 -> 美股、黄金和BTC",
        "signals": [
            ("fred", "WALCL", "美联储总资产", "流动性供给的总量代理。扩表通常缓和金融条件，缩表则相反。"),
            ("fred", "WRESBAL", "银行准备金", "银行体系即时可用流动性的近似指标，需与TGA、RRP联读。"),
            ("fred", "WTREGEN", "TGA财政部账户", "TGA上升会从银行体系吸收现金；下降通常释放现金。"),
            ("market", "DX-Y.NYB", "美元指数 DXY", "美元相对主要货币的强弱。上行常代表全球美元压力上升，但不是流动性的唯一指标。"),
            ("fred", "DFII10", "10年期实际利率", "剔除通胀预期后的长期融资成本。上升会压低久期资产和黄金的估值。"),
        ],
        "watch": "观察 DXY 和实际利率是否同时上行；若同时上行，通常比单一指标更能说明金融条件收紧。",
        "keywords": ["liquidity", "dollar", "fed", "美元", "流动性", "准备金", "tga"],
    },
    "增长与衰退": {
        "question": "实体经济仍在扩张，还是正在进入放缓/衰退阶段？",
        "why": "增长决定企业盈利、就业与风险资产的基本面，也会改变货币政策预期。",
        "chain": "订单与生产 -> 就业和收入 -> 消费 -> 企业盈利与信用风险 -> 股票/利率",
        "signals": [
            ("fred", "INDPRO", "工业产出", "制造和采矿等实体产出的月度代理。建议看同比和趋势，而不是单月水平。"),
            ("fred", "UNRATE", "失业率", "劳动力市场松紧程度。失业率连续上行常比单月波动更有研究价值。"),
            ("fred", "ICSA", "初请失业金", "较高频的就业压力指标。持续抬升比一次性跳升更值得关注。"),
            ("fred", "RSAFS", "零售销售", "居民商品消费的名义口径，解释时需注意价格因素会影响同比变化。"),
        ],
        "watch": "重点观察 PMI 下行、初请失业金上升与信用利差走阔是否共振；三者同步比单项变化更具衰退含义。",
        "keywords": ["growth", "recession", "employment", "增长", "衰退", "就业", "pmi"],
    },
    "通胀与货币政策": {
        "question": "通胀是否回落到可容忍区间，政策约束是在增强还是减弱？",
        "why": "通胀路径决定利率预期和实际利率，进而影响估值、美元与风险偏好。",
        "chain": "需求/工资/供给 -> CPI与PCE -> 政策利率预期 -> 实际利率与美元 -> 资产估值",
        "signals": [
            ("fred", "CPIAUCSL", "CPI", "消费者价格总指数。应优先观察同比、环比年化及核心分项，而不是仅看指数水平。"),
            ("fred", "CPILFESL", "核心CPI", "剔除食品和能源后的通胀，通常更适合判断内生价格压力。"),
            ("fred", "PCEPILFE", "核心PCE", "美联储更关注的通胀口径之一，发布频率较低且可能修订。"),
            ("fred", "FEDFUNDS", "联邦基金利率", "当前政策利率水平，不等于市场对未来路径的全部预期。"),
            ("fred", "T10YIE", "10年盈亏平衡通胀率", "市场隐含的长期通胀补偿，包含流动性与风险溢价，不能当成纯通胀预测。"),
        ],
        "watch": "若核心通胀黏性、通胀预期和实际利率同时走高，政策宽松空间通常会收窄。",
        "keywords": ["inflation", "fed_policy", "通胀", "货币政策", "加息", "降息"],
    },
    "信用与风险偏好": {
        "question": "市场是在为风险定价，还是仍处于宽松的风险偏好环境？",
        "why": "信用市场对融资压力较敏感，是识别风险从宏观传导到资产价格的重要桥梁。",
        "chain": "增长/利率冲击 -> 企业现金流与融资成本 -> 信用利差 -> 波动率 -> 股票与高贝塔资产",
        "signals": [
            ("fred", "BAMLH0A0HYM2", "高收益债利差", "低评级企业债相对国债的额外补偿。快速走阔通常表示风险偏好下降或违约担忧上升。"),
            ("fred", "BAMLC0A0CM", "投资级信用利差", "质量较高企业的融资压力代理，可与高收益债利差比较风险扩散范围。"),
            ("fred", "NFCI", "金融条件指数", "利率、信用利差和杠杆等金融环境的综合指标；数值上升通常代表条件收紧。"),
            ("fred", "VIXCLS", "VIX", "标普期权隐含波动率。它反映定价的波动，而不是对未来跌幅的直接预测。"),
            ("fred", "T10Y3M", "10Y-3M利差", "期限结构代理。倒挂提示未来增长压力，但领先时间不固定，不能单独用于择时。"),
        ],
        "watch": "高收益债利差走阔加上 VIX 上升，通常比任一指标单独上行更值得优先核验。",
        "keywords": ["credit", "risk", "volatility", "信用", "风险", "波动", "vix"],
    },
    "Crypto流动性": {
        "question": "BTC 的边际资金来自宏观流动性，还是来自加密市场内部杠杆与现货需求？",
        "why": "BTC 同时受美元/实际利率影响，也受 ETF、稳定币和衍生品杠杆等内部力量驱动。",
        "chain": "美元与实际利率 -> 风险偏好 -> ETF/稳定币现货资金 + Funding/OI杠杆 -> BTC与相关股票",
        "signals": [
            ("fred", "CBBTCUSD", "BTC 现货价格", "结果变量而非完整解释。需与宏观条件、ETF资金和杠杆指标一起看。"),
            ("market", "DX-Y.NYB", "美元指数 DXY", "美元走强常压制全球风险偏好，对 BTC 构成宏观逆风，但相关性会随阶段改变。"),
            ("fred", "DFII10", "10年期实际利率", "实际利率上行会提高无现金流资产的机会成本，是 BTC 的重要宏观背景变量。"),
            ("crypto_flows", "BTC_ETF_NETFLOW", "BTC ETF净流入", "受监管通道的净买卖需求代理。口径、发布日期和是否包含全部产品必须核对。"),
            ("crypto_market", "BTC_FUNDING_RATE", "BTC资金费率", "永续合约多空持仓成本。持续偏高提示多头拥挤，不等于价格会立即下跌。"),
        ],
        "watch": "区分“价格涨、现货资金改善、杠杆温和”和“价格涨、资金费率/OI过热”。两者的后续风险并不相同。",
        "keywords": ["crypto", "bitcoin", "btc", "加密", "比特币", "etf", "stablecoin"],
    },
}


def list_themes():
    return list(THEMES.keys())


def get_theme(name):
    return THEMES.get(name, THEMES["美元流动性"])


def theme_snapshots(name):
    """Return latest stored values for a theme without treating missing data as zero."""
    rows = []
    for source, series_id, label, explanation in get_theme(name)["signals"]:
        snapshot = query_series_snapshot(source, series_id)
        rows.append({
            "source": source,
            "series_id": series_id,
            "label": label,
            "explanation": explanation,
            "snapshot": snapshot,
        })
    return rows
