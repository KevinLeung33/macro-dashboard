# Macro Dashboard 使用说明

这个看板是一个面向中美宏观、全球市场和 crypto 研究的 Streamlit 仪表盘。它不只是展示图表，也会把数据变化、新闻事件、用户研究假设和规则信号组织成每日研究上下文，供人工复盘或 AI 生成日报。

## 1. 快速使用

本地运行：

```bash
streamlit run app.py
```

服务器自动更新入口：

```bash
python server.py --with-api
```

### 访问权限

看板默认是只读访问，朋友可以直接浏览市场数据、研究页面和历史报告。刷新数据、生成日报、重试新闻、保存研究假设、修改通知规则等写操作需要管理员会话解锁。

在服务器 `.env` 中配置管理员密码，示例：

```env
DASHBOARD_ADMIN_PASSWORD=请替换为随机长密码
DASHBOARD_ADMIN_SESSION_MINUTES=60
```

更推荐配置 PBKDF2 哈希，而不是保存明文密码：

```bash
python -m services.access_control
```

将命令输出写入：

```env
DASHBOARD_ADMIN_PASSWORD_HASH=pbkdf2_sha256$...
```

配置后重启 Streamlit：

```bash
sudo systemctl restart macro-dashboard-streamlit
```

打开网页后，在侧边栏的“管理员操作”中输入密码。管理员权限只保存在当前浏览器会话，超时或服务重启后失效。管理员登录请使用 HTTPS 地址（例如 cpolar 提供的 HTTPS 地址），不要在公共网络中通过明文 HTTP 输入密码。

手动刷新全部数据：

```bash
python -c "from data.pipeline import fetch_all; fetch_all(incremental=False)"
```

日常刷新建议使用增量更新：

```bash
python -c "from data.pipeline import fetch_all; fetch_all(incremental=True)"
```

## 2. 页面结构

- 首页：研究驾驶舱、市场概览、数据健康、近期变化、组合信号。
- 货币政策：联邦基金利率、美债收益率、实际利率、通胀预期。
- 市场数据：美股指数、DXY、汇率、大宗商品。
- 全球市场：中国 PMI/CPI/PPI/社融/LPR，全球汇率和商品联动。
- 加密资产：BTC、MSTR、稳定币流动性、USDT/USDC、ETH/BTC。
- 信用与风险：高收益利差、投资级利差、NFCI、VIX、增长压力。
- 就业市场：失业率、初请、JOLTS、工资、Sahm Rule。
- 历史对比：主要宏观指标在历史阶段中的位置。
- 流动性：Fed 资产负债表、RRP、TGA、准备金、利率和信用。
- 新闻雷达：新闻抓取、AI 分析、事件聚类。
- 每日沉淀：保存每日研究包和 AI 趋势日报。
- 研究假设：维护你的长期假设、观点日志和观察项。
- 信号复盘：保存组合信号，跟踪资产后续表现，并统计信号有效性。
- 交易复盘：OKX 只读交易看板；展示跨币种保证金账户、持仓、订单、成交和 K 线；交易计划会记录入场价、挂单类型、触发价、计划数量和状态，并可关联已同步订单；支持保存计划当时的环境快照、可选 AI 计划反馈、独立 AI 影子计划与事后点评。
- AI 影子账户：集中查看 AI 的不交易/观察决策、本地虚拟挂单、模拟成交、R 倍数、虚拟净盈亏和订单事件；不连接任何真实交易写接口。

## 3. 数据源

数据源配置集中在 `config/data_sources.py`。

当前主要数据源：

- `fred`：美国宏观、利率、信用、市场和 BTC Coinbase 数据。
- `akshare`：中国宏观数据。
- `akshare_hk_index`：恒生科技指数（`HSTECH`）精确日线；使用 AKShare 的新浪港股指数历史接口，与中国宏观抓取和健康状态分开记录。
- `tic`：美国财政部 TIC 美债持有数据。
- `alpha_vantage`：MSTR/NVDA/MU 等美股可选备选源；受免费额度限制，默认关闭。
- `stooq`：免 key 市场数据可选备选源；部分环境会返回空数据，默认关闭。
- `yfinance`：Yahoo Finance 市场数据源，负责股票、指数、外汇和商品。
- `binance_spot`：Binance 公共 K 线接口，负责 BTC/ETH 日线现货价格。
- `crypto_liquidity`：DefiLlama 稳定币数据，Kraken/Coinbase ETH/BTC。
- `crypto_market`：Binance 公共接口的 BTC 资金费率和持仓量历史。
- `crypto_flows`：可选配置的 BTC ETF flows 和交易所净流入 CSV/JSON 适配器。
- `news`：官方/媒体 RSS（含美联储、SEC、EIA、国家统计局、ECB、吴说）；RSS 原文快速刷新，AI 分析单独低频运行。官方源是健康告警的基础；吴说、CoinDesk 等第三方媒体源为可降级补充，失败会保留状态但默认不触发 P1/P2 推送。Alpha Vantage 新闻默认关闭，避免免费 Key 的 25 次/日额度被定时任务耗尽。BLS、Reuters 和 The Block 如在服务器被 403/404 拦截，会从正式订阅中退役而保留历史状态；这不影响 FRED 宏观数据。

Crypto 内生流动性目前包括：

- 稳定币总市值：DefiLlama。
- USDT 市值：DefiLlama，CoinGecko 只做备选。
- USDC 市值：DefiLlama，CoinGecko 只做备选。
- USDT+USDC：本地派生代理指标。
- USDT/USDC 在主流稳定币中的占比：基于 USDT+USDC 市值派生。
- ETH/BTC：Kraken 主源，Coinbase 备源，CoinGecko/本地价格最后兜底。

Crypto 资金流配置：

```env
# 可选。URL 返回 CSV 或 JSON，至少包含 date 和 flow/net_flow/value 字段。
BTC_ETF_FLOWS_URL=https://your-provider.example/btc-etf-flows.csv
BTC_EXCHANGE_NETFLOW_URL=https://your-provider.example/btc-exchange-netflow.csv
```

ETF flows 和交易所净流入没有统一稳定的免费公共接口；未配置 URL 时系统会记录 `skipped`，不会生成伪造数据。

生产环境接入后需要重点核对：ETF flows 的发布日期与净流入单位、交易所净流入的正负号口径，以及供应商是否包含周末数据。供应商返回空数据时页面会显示“未配置或暂无数据”，不会把空值当作零流入。

## 4. 增量更新

`fetch_all(incremental=True)` 是默认推荐模式。

增量策略：

- FRED：从数据库最新日期附近开始请求，保留几天 overlap 以处理修订。
- Stooq：已有历史时只请求最近窗口。
- Alpha Vantage：已有历史时使用 compact 输出。
- yfinance/Binance spot：已有历史时只拉短窗口。
- AKShare 港股指数：每次读取完整历史表后只写入最近重叠窗口；若完整历史少于 250 条或最新交易日超过 14 天，会保留旧数据并记录失败，而不是把不完整响应写入看板。
- AKShare：接口通常返回全量表，本地过滤新记录。
- DefiLlama：接口通常返回全量历史，本地过滤新记录。

需要全量回填时使用：

```python
fetch_all(incremental=False)
```

## 4.1 数据血缘与质量状态

时间序列现在额外记录：

- `release_at`：数据发布日期或发布时间，数据源未提供时为空。
- `fetched_at`：系统抓取时间。
- `source_url`：数据来源页面或接口入口。
- `vintage_at`、`revision_number`、`is_revised`：修订和版本信息，当前数据源未提供时使用默认值。
- `quality_status`、`quality_message`：写入时的数据质量结果。

写入层会检查：

- 日期是否可解析。
- 数值是否为空、非数字或非有限值。
- 指标是否超出配置的有效范围。
- 抓取记录是否出现日期倒退。

被拒绝的记录不会覆盖正常数据，并会写入 `data_quality_issues` 表。首页数据健康区会显示未解决的质量提醒数量；出现提醒时，应先检查来源和数据口径，再决定是否恢复或修正数据。显式执行一次全量刷新会重新验证该序列：旧问题先标记为已解决，若当前响应仍有相同问题会立即重新打开，因此历史解析遗留不会永久告警。

## 5. AI 日报

AI 日报入口在“每日沉淀”页面，也可以由 `server.py` 定时生成。

需要配置：

```env
OPENAI_API_KEY=你的key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
# 结构化摘要默认关闭 DeepSeek thinking mode，避免短 JSON 输出被推理预算耗尽后截断。
AI_THINKING_MODE=disabled
AI_ANALYZE_MAX_TOKENS=4096
AI_DAILY_MAX_TOKENS=8192
AI_MARKET_BRIEF_MAX_TOKENS=1200
AI_MARKET_BRIEF_CACHE_SECONDS=1800
```

AI 日报会读取：

- 数据健康。
- 近期最大变化。
- 7/30/90 天多窗口趋势。
- 组合信号。
- 告警和极端分位。
- 新闻事件流和新闻主题动向。
- 你的研究假设、观点日志、观察项。
- 自动关联到假设的指标和新闻证据。

如果没有配置 AI key，或 AI 调用失败，系统会保存包含“核心判断、短中期对比、新闻传导和接下来观察”的规则结论版日报，不会退化成只有数字的研究包；同时在 `macro-dashboard-server` 日志记录失败原因。AI 恢复后，下一次日报会自动切回 AI 文字报告。

新闻 AI 分析和 AI 日报要求模型返回 JSON。系统已经做了代码块/多余文字提取和字段归一化；日报还会在 JSON 模式空响应或解析失败时自动改用普通文本模式重试。如果仍出现 `Unterminated string`、`Expecting value`、`empty AI response` 或超时日志，通常是请求侧输出上限太小、模型输出被截断、网络/API 配置异常或模型返回了非 JSON。可以查看 `journalctl -u macro-dashboard-server -n 200 --no-pager | grep -Ei "AI daily report|openai|deepseek|timeout|json"` 定位原因。

## 5.1 首页简报与定期报告

首页的“市场简报”分成三个阅读层次：

- **今日**：当前最新数据、最高优先级信号和近期新闻主线，回答“现在发生了什么”。
- **本周**：近 5 个数据点的主要变化和近 7 天新闻主题，回答“这周发生的变化是否已经传导到资产”。
- **中期**：重点指标的 30D 与 90D 变化，回答“短期变化是否得到更长背景支持”。

每个层次在数字证据上方还会显示一段 AI 短解读：包括一句判断、最多两句依据和一句后续观察。模型只读取当前驾驶舱中的数据、组合信号和近 7 天新闻主题，不会补充外部事实，也不输出买卖建议。原有规则归纳和数字证据始终保留在解读下方；AI key 未配置、调用失败或返回格式异常时，页面会自动回退到规则归纳。

首页 AI 解读按当前数据内容做进程内缓存，默认 30 分钟；数据内容没有变化时刷新页面不会重复调用。可在 `.env` 调整 `AI_MARKET_BRIEF_CACHE_SECONDS` 和 `AI_MARKET_BRIEF_MAX_TOKENS`。该缓存不写数据库，因此不会把只读访问变成写操作；服务重启后会重新建立缓存。

如果模型返回空内容，首页会记录 `finish_reason`，自动再尝试一次不带 JSON 模式的请求，并继续用本地解析器提取 JSON。若仍失败，请查看 `macro-dashboard-streamlit` 日志中的 `AI homepage brief attempt`，不要只根据页面上的通用提示判断原因。

首页只展示四个主题结论，不为每张图单独生成结论，避免读者在几十条结论中失去重点。每个主题仍保留“继续观察”的指标，点击相关研究入口后再查看完整图表和证据。

中短期比较报告建议以**周报为主**：周一上午生成一份详细文字报告，使用近 7 天新闻、近 5 个数据点以及 7D/30D/90D 趋势，适合做一周研究计划和假设复盘。当前调度时间为：

- 每日 08:00：AI 趋势日报，重点回答当天变化和待跟进事项。
- 每周一 09:00：周度中短期对比报告，保存到“每日沉淀”并发送通知。

不建议每天生成同样详细的中短期报告：宏观数据不会每天更新，频繁生成只会重复旧结论并增加 AI 调用成本。月度报告可以在后续数据发布日历稳定后增加，用于判断 3-6 个月的宏观 regime；在此之前，周报中的 30D/90D 对比已经足够支持中短期研究。

各页面的“研究窗口”位于图表上方，统一控制 1/3/6 个月、1/3 年或全部历史。Plotly 工具栏改为悬停显示，图例移动到图表下方，避免时间选择器、图例和工具栏挤在同一行。

## 5.2 新闻处理、事件关联与 AI 复盘

新闻文章依次经过 `fetched`、`queued`、`analyzing`、`analyzed`、`clustered` 状态。URL 仅存在跟踪参数差异或近期已经完成 AI 分析的相同标题，会保留在原始文章库中并标记为 `deduplicated`，不会重复消耗 AI 调用额度。AI 请求、结构化 JSON 或写库失败会进入 `failed`，新闻雷达会显示失败原因和尝试次数；点击“重试失败文章”后，文章回到 `fetched`，等待下一次新闻分析任务处理。

事件流会把同类已分析文章聚成事件簇，并根据 AI 的 `follow_up_data`、宏观传导渠道、影响资产和事件主题，关联：

- 需要继续验证的指标。
- 匹配的活跃研究假设及匹配理由。

事件簇有自己的生命周期：当前窗口内的事件为 `active`，超过重建窗口的事件会变为 `inactive`；被判定为同一具体事件的旧记录会变为 `merged`，并通过 `merged_into` 保留合并去向。重建事件流时会以本轮文章分配为准：成功且未超出处理上限时，本轮没有重新生成的旧活动簇会退出主事件流，因此不会因历史重建结果残留而重复展示。原始文章和历史关联不会被删除。

规则聚类完成后，系统会为高相似候选簇调用一次事件级 AI：模型需要区分“同一具体事件的多家报道”和“只是同一宏观主题的不同事件”。确认合并后，事件流只展示一个活动事件，并保存一条统一的标题、结论、影响解读和下一步观察。AI 不可用时，完全相同或高度相似的候选标题仍会使用规则兜底合并，不会因为 AI 故障阻塞新闻入库。

相关配置示例：

```dotenv
AI_NEWS_CLUSTER_MERGE_ENABLED=true
AI_NEWS_CLUSTER_MAX_TOKENS=1600
NEWS_CLUSTER_MAX_ARTICLES=1000
NEWS_CLUSTER_EVENT_MAX_HOURS=96
NEWS_CLUSTER_MATCH_THRESHOLD=3.0
NEWS_CLUSTER_WARN_ARTICLES=25
NEWS_ANALYSIS_DEDUP_DAYS=3
```

聚类不会仅因为“同为 `other`、同一资产或 48 小时内”而合并；必须存在共享标题实体或足够语义重合。部署新版本后，打开“新闻雷达”并以管理员身份点击一次“重建事件流”，即可整理最近 3 天的历史事件。若提示文章数超过 `NEWS_CLUSTER_MAX_ARTICLES`，先调高该值后再重建，避免在输入不完整时隐藏旧事件。之后新闻分析流水线会在每轮分析后自动执行同样的生命周期整理和事件级合并。

也可以在服务器上用下面的只处理最近 3 天事件流的命令完成首次整理；它会把未在本轮重建中出现的旧活动簇标记为 `inactive`，不会删除原始文章：

```bash
source .venv/bin/activate
python -c "from db.schema import init_db; from services.news_clusterer import build_news_clusters; init_db(); print(build_news_clusters(days=3))"
```

新闻雷达的“AI复盘”页会按新闻发布时间或分析创建日期作为起点，记录明确 `bullish` / `bearish` 判断对应资产后续 1/3/7/30 个交易日的表现。统计按模型、Prompt 版本、新闻源、事件类型和资产拆分；样本不足时不显示方向准确率，避免把少量结果当成结论。

## 5.3 证据工作台与指标解读

侧边栏的“证据工作台”按研究问题组织核心信息，而非按数据源罗列。当前支持美元流动性、增长与衰退、通胀与货币政策、信用与风险偏好、Crypto 流动性五个主题。

每个主题包含：研究问题、传导关系、核心指标的当前读数、数据含义、使用提示、关联事件和关联研究假设。阅读时先检查指标的数据日期和频率，再判断多个指标是否同向变化；不要把单个指标或新闻直接当作资产结论。

驾驶舱的“打开证据工作台”会根据当前最高优先级信号带入相应主题。证据工作台中的新闻雷达和研究假设按钮会保留该主题作为研究焦点。

## 6. 研究假设

“研究假设”页面用于沉淀你的判断框架。

长期假设示例：

- BTC 主要受美元流动性、实际利率和稳定币供给驱动。
- MSTR 是 BTC beta 和融资条件的放大器。
- DXY 快速上行会压制 NASDAQ 和 crypto 风险偏好。

系统会自动从文本中识别：

- 相关资产：BTC、ETH、MSTR、NASDAQ、SP500、DXY、Gold、Oil、CNH。
- 相关指标：DXY、实际利率、10Y 美债、信用利差、NFCI、BTC、稳定币市值、ETH/BTC、VIX、中国 PMI 等。
- 新闻主题：crypto、liquidity、fed_policy、credit、china_macro、growth、energy。

保存或更新假设时，如果你没有手动填完整资产、指标、新闻主题，系统会自动补全一部分。日报里也会把这些关联指标的近期变化作为证据喂给 AI。

## 7. 组合信号与复盘

当前组合信号包括：

- 美元流动性收紧。
- Fed 约束增强。
- 信用风险扩散。
- 美国增长放缓。
- Crypto 宏观压力。
- Crypto 内生流动性改善。

信号复盘会保存当天触发的信号，并跟踪相关资产后续 1/3/7 日表现。这个模块用于逐步判断哪些规则真的有解释力，而不是只看起来合理。

当前统计包括：

- 按信号统计：总样本、1D/3D/7D 有效样本、平均收益、中位数、上涨占比。
- 按信号×资产统计：判断某个信号对 BTC、MSTR、NASDAQ、DXY 等资产是否更有解释力。
- 首页研究驾驶舱会展示当前样本中 7D 平均变化最大的信号，作为规则系统有效性的提醒。

上涨占比只是“资产后续上涨的比例”，还不是方向校准后的胜率。对于“压力/收紧”类信号，需要结合资产方向解释。

## 8. 首页研究驾驶舱

首页顶部是日常打开看板的第一屏，聚合最需要先看的信息：

- 数据状态：是否有数据源过期、失败。
- 最高优先级组合信号：当前最值得关注的宏观/crypto 信号。
- 近期最大变化：近几期变化最大的指标。
- 新闻主线：近期新闻中最集中的事件类型。
- 研究框架：当前活跃假设和观察项数量。
- 信号摘要、趋势变化、信号复盘摘要。
- 重要事件流折叠区。

它的目标是先回答“今天应该看哪里”，下面的详细页面再回答“为什么”。

首页的“多资产研究偏向”把现有组合信号转换为 BTC、MSTR、DXY、美股、中国资产和黄金的研究方向。每张卡都会显示置信度、近五期变化、最新数据日期和主要驱动；它是研究排序工具，不是交易建议。点击“查看证据”会带着对应焦点和 3 个月或 6 个月窗口跳转到相关页面。

首页的最高优先级信号、近期最大变化、新闻主线和实时告警也可跳转。跳转状态保存在当前 Streamlit 会话中，因此适用于从首页继续追踪；需要可分享的永久链接属于后续可扩展能力。

## 9. 服务器生产化

本项目只提供网页访问方式：通过 Streamlit 启动看板，由浏览器或 Nginx 反向代理访问。已经移除本地桌面窗口、`pywebview` 和 Windows EXE 打包脚本。

服务器入口：

```bash
python server.py --with-api
```

常用环境变量：

```env
# 生产环境建议使用 127.0.0.1，再由 Nginx 反向代理到外部
API_HOST=127.0.0.1
API_PORT=8080
STREAMLIT_PORT=8501
API_AUTH_TOKEN=请替换为随机长字符串
ALPHA_VANTAGE_KEY=可选，用于新闻与美股备选数据源
NOTIFY_CHANNELS=telegram
NOTIFY_ON_TASK_FAILURE=true
NOTIFY_ON_RUNTIME_ERROR=true
RUNTIME_ERROR_NOTIFY_COOLDOWN_SECONDS=900
API_STATUS_COOLDOWN_SECONDS=5
API_REFRESH_COOLDOWN_SECONDS=300
API_CONTEXT_COOLDOWN_SECONDS=300
API_REPORT_COOLDOWN_SECONDS=300
API_BACKUP_COOLDOWN_SECONDS=300
SCHEDULER_TIMEZONE=Asia/Shanghai
RUNTIME_LOCK_DIR=runtime/locks
TASK_LOCK_STALE_SECONDS=3600
TASK_RETRY_ATTEMPTS=2
TASK_RETRY_BASE_SECONDS=5
DATA_REFRESH_TIMEOUT_SECONDS=1800
NEWS_REFRESH_TIMEOUT_SECONDS=600
DAILY_CONTEXT_TIMEOUT_SECONDS=900
DAILY_REPORT_TIMEOUT_SECONDS=1200
WEEKLY_REPORT_TIMEOUT_SECONDS=1200
BACKUP_TIMEOUT_SECONDS=300
STARTUP_RECOVERY_ENABLED=true
STARTUP_RECOVERY_DATA_MAX_AGE_SECONDS=28800
STARTUP_RECOVERY_NEWS_MAX_AGE_SECONDS=7200
LOG_LEVEL=INFO
LOG_DIR=logs
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=5
BACKUP_DIR=backups
BACKUP_AFTER_DAILY_REPORT=true
BACKUP_RETENTION_COUNT=14
BACKUP_RETENTION_DAYS=30
BACKUP_MIN_FREE_BYTES=104857600
SQLITE_TIMEOUT_SECONDS=60
SQLITE_BUSY_TIMEOUT_MS=60000
SQLITE_RETRY_ATTEMPTS=5
SQLITE_JOURNAL_MODE=WAL
SQLITE_SYNCHRONOUS=NORMAL
```

### 9.1 API 接口和鉴权

启动服务：

```bash
cd /opt/macro-dashboard
source .venv/bin/activate
python server.py --with-api
```

Windows PowerShell：

```powershell
Set-Location E:\macro-dashboard
& .\.venv\Scripts\Activate.ps1
python server.py --with-api
```

生成 Token：

```bash
openssl rand -hex 32
```

将结果写入服务器 `.env`：

```env
API_AUTH_TOKEN=这里填写上面生成的随机字符串
```

不要把 Token 写进 URL、提交到 Git，或直接放进前端页面。调用受保护接口时使用 HTTP `Authorization` Header：

```text
Authorization: Bearer <API_AUTH_TOKEN>
```

新增 API：

- `GET /api/health`：数据源健康。
- `GET /api/status`：运行状态、数据库大小、关键环境配置，需要 API Token。
- `POST /api/data/refresh?incremental=true`：手动触发数据刷新，需要 API Token。
- `POST /api/context/daily`：生成本地每日研究包，需要 API Token。
- `POST /api/report/ai-daily`：生成 AI 日报，需要 API Token。
- `GET /api/report/daily`：读取或生成 AI 日报，需要 API Token。
- `GET /api/report/weekly`：生成周报，需要 API Token。
- `POST /api/maintenance/backup`：备份 SQLite 数据库，需要 API Token。

除 `/api/health` 外，API 使用 Bearer Token 鉴权。配置：

```env
API_AUTH_TOKEN=随机生成的长字符串
```

调用示例：

```bash
# 健康检查：不需要 Token
curl http://127.0.0.1:8080/api/health

# 运行状态：需要 Token
curl -H "Authorization: Bearer $API_AUTH_TOKEN" \\
  http://127.0.0.1:8080/api/status

# 增量刷新：需要 Token
curl -X POST \\
  -H "Authorization: Bearer $API_AUTH_TOKEN" \\
  "http://127.0.0.1:8080/api/data/refresh?incremental=true"

# 生成每日研究包：需要 Token
curl -X POST \\
  -H "Authorization: Bearer $API_AUTH_TOKEN" \\
  http://127.0.0.1:8080/api/context/daily

# 生成 AI 日报：需要 Token
curl -X POST \\
  -H "Authorization: Bearer $API_AUTH_TOKEN" \\
  http://127.0.0.1:8080/api/report/ai-daily

# 备份数据库：需要 Token
curl -X POST \\
  -H "Authorization: Bearer $API_AUTH_TOKEN" \\
  http://127.0.0.1:8080/api/maintenance/backup
```

PowerShell 调用时请使用 `curl.exe`，避免被 PowerShell 的 `curl` 别名替换：

```powershell
$headers = @{ Authorization = "Bearer $env:API_AUTH_TOKEN" }
curl.exe -H "Authorization: Bearer $env:API_AUTH_TOKEN" `
  http://127.0.0.1:8080/api/status

curl.exe -X POST -H "Authorization: Bearer $env:API_AUTH_TOKEN" `
  "http://127.0.0.1:8080/api/data/refresh?incremental=true"
```

常见响应：

| 状态码 | 含义 | 处理方式 |
|---|---|---|
| `200` | 调用成功 | 检查返回的 `status` 和数据内容 |
| `401` | Token 缺失或错误 | 检查是否使用 `Authorization: Bearer ...`，以及 Token 是否和服务器 `.env` 一致 |
| `409` | 同类任务正在运行 | 等待当前刷新、报告或备份任务结束后再试 |
| `429` | API 仍在冷却期 | 等待响应头 `Retry-After` 指定的秒数 |
| `503` | 服务器没有配置 `API_AUTH_TOKEN` | 在服务器 `.env` 配置 Token 后重启服务 |
| `500` | 业务处理失败 | 查看 `logs/server.log` 和 systemd 日志 |

`/api/report/daily` 会生成或刷新日报，可能调用 AI 并产生费用，不建议把它作为前端轮询接口；日常查看优先读取页面或保存后的报告。

数据库备份使用 SQLite 在线备份 API，不会直接复制正在写入的数据库文件。每次备份会执行完整性检查，并根据以下配置清理旧备份：

- `BACKUP_RETENTION_COUNT`：最多保留的备份数量，默认 14。
- `BACKUP_RETENTION_DAYS`：超过该天数的备份会被清理，默认 30 天。
- `BACKUP_MIN_FREE_BYTES`：备份前要求目录所在磁盘至少剩余的空间，默认 100 MB。

备份接口返回 `integrity=ok` 才表示备份文件通过 SQLite 完整性检查。服务器部署后应定期把 `backups/` 复制到另一块磁盘或对象存储，并至少做一次恢复演练。

恢复演练建议恢复到临时文件，不要直接覆盖线上数据库：

```bash
python -c "from services.maintenance import restore_database; print(restore_database('backups/macro_data_YYYYMMDD_HHMMSS.db', 'runtime/restore_test.db'))"
```

`restore_database()` 默认不会覆盖已经存在的目标文件，返回 `integrity=ok` 后才表示临时恢复文件可用。

### 9.2 上线后验收清单

首次部署完成后，建议按以下顺序验证：

1. 配置完整依赖并运行 `python server.py --with-api`，确认日志显示正确的调度时区。
2. 调用 `/api/health`，再分别用无 Token、错误 Token、正确 Token 调用 `/api/status`。
3. 连续调用刷新接口，确认第二次请求受到 `429` 冷却限制。
4. 同时从 API 和 Streamlit 点击刷新，确认任务锁生效，不会并发写 SQLite。
5. 临时制造一次任务失败，确认重试、`runtime/task_status.json` 和 Telegram/Webhook 通知。
6. 停止服务并错过一次数据或日报任务，重启后确认只补执行一次。
7. 调用备份接口，确认返回 `integrity=ok`，再恢复到临时数据库文件并查询。
8. 通过 Nginx 访问看板，确认外部不能直接访问 API 内部端口。

以上验收通过后，才建议把 systemd 服务设置为长期运行，并开放外部访问。

### 9.3 P1 数据源上线验收

P1 新增数据源依赖外部接口，部署后先执行一次手动刷新，再检查页面、抓取日志和数据库中的 `source_url`、`fetched_at`、`release_at`：

- Yahoo Finance：确认 `USDCNH=X`、`USDCNY=X`、`000300.SS`、`399006.SZ` 返回非空数据；部分符号不可用时应记录失败，不影响其他指标。`HSTECH.HK` 在生产环境虽然可返回报价但只返回一根历史 K 线，因此不作为正式历史源。
- AKShare：确认 CPI/PPI 同比和 M2 同比的接口版本及字段名称匹配；恒生科技指数使用 `stock_hk_index_daily_sina(symbol="HSTECH")`，上线验收要求历史记录数不少于 250 条且最新交易日不超过 14 天。社融存量同比在验证到具备该字段的来源前不展示。AKShare 升级后优先查看抓取日志中的字段错误。
- Binance：确认现货 K 线、资金费率和 OI 接口可访问，核对 OI 返回值的单位，并留意公共接口的限频响应。
- ETF/交易所资金流：在 `.env` 配置 `BTC_ETF_FLOWS_URL`、`BTC_EXCHANGE_NETFLOW_URL` 后，确认返回 CSV/JSON 至少包含 `date` 和 `flow`、`net_flow`、`value` 或 `amount` 字段。
- 空数据降级：移除可选资金流 URL 或模拟接口失败，确认页面仍可打开，并显示“未配置或暂无数据”。

这些验收需要在服务器的真实依赖、网络和供应商数据环境中完成，不能仅用本地 AST 检查替代。

### 9.4 候选数据源只读探测

在把候选源写入 `RSS_FEEDS` 或正式数据管道之前，先在服务器执行：

```bash
source .venv/bin/activate
python probe_candidate_sources.py
```

脚本不会写数据库、不会修改 `.env`、不会调用 AI。它会复测当前失败的 BLS、Reuters、Caixin、The Block 路径，并对吴说的 `www` 与非 `www` RSS 地址做服务器实测，测试国家统计局和 ECB 的官方 RSS，并测试 BLS API、AKShare 的替代宏观接口；如在 `.env` 临时配置 `FINNHUB_API_KEY` 或 `TUSHARE_TOKEN`，还会各发起一次最小 API 请求。只有在服务器实测通过后才接入；国家统计局和 ECB 是优先的正式官方源，财新 RSS 镜像则需要显式设置 `CAIXIN_RSS_MIRROR_URL` 才会启用。

`NOTIFY_CHANNELS` 支持 `telegram,lark,email,webhook`，并会自动忽略逗号两侧空格。服务器任务失败和运行告警读取这里的渠道配置；网页“通知规则”中的渠道主要用于紧急新闻推送。Alpha Vantage 新闻和行情使用同一个 `ALPHA_VANTAGE_KEY`；不再读取 `FINNHUB_API_KEY`。

### 9.5 中国宏观数据质量探测

中国宏观指标在 AKShare 上既有历史表，也有经济日历事件表；两者字段和含义并不总能直接互换。每次升级 AKShare 或替换来源前，先在服务器执行以下只读探测：

```bash
source .venv/bin/activate
python probe_china_macro_sources.py
```

它会检查 CPI/PPI 同比列、M2 候选表和社融增量的实际列名、可用数值与最新日期；不会写数据库、不会刷新看板。只有逻辑指标、字段含义和服务器实测均通过后，才将候选源接入正式抓取器。

如果探测显示某个实时宏观序列已经超过预期发布窗口，页面会先暂停展示该序列，而不是把历史值伪装成当前信号。当前 CPI/PPI 及 Sina M2 同比已通过生产探测；财新 PMI 和社融增量需验证新鲜来源后才会恢复。FDR007/DR007 暂不在看板和默认探测中使用。

如需把中国流动性数据提高到更高稳定性，可在 `.env` 配置只读的 `TUSHARE_TOKEN` 后重跑探测。`cn_m` 可提供 M2 同比；`sf_month` 提供社融存量水平（系统可据连续月度值计算同比）；`repo_daily` 可直接返回 `DR007.IB`。这些接口有积分门槛，探测会如实报告权限不足，不会输出 Token 或写入数据库。

当任务重试耗尽后，若 `NOTIFY_ON_TASK_FAILURE=true`，系统会通过 `NOTIFY_CHANNELS` 配置的 Telegram、飞书、Email 或 Webhook 渠道发送失败通知。通知发送本身不会阻塞任务状态记录；即使通知渠道不可用，也可以在 `runtime/task_status.json` 和日志中查看失败原因。

对于“任务没有失败、但内部发生了可恢复异常”的情况，例如 AI 日报接口失败后自动生成规则版日报，系统会在 `NOTIFY_ON_RUNTIME_ERROR=true` 时发送“宏观看板运行告警”。日报和新闻分析会包含具体错误摘要以及已经采取的回退动作；同一个错误默认在 `RUNTIME_ERROR_NOTIFY_COOLDOWN_SECONDS=900` 秒内只推送一次，避免接口连续超时造成通知刷屏。服务器更新代码后需要重启 `macro-dashboard-server`，使 systemd 重新加载代码和 `.env`。

### 9.5 cpolar 与看板可用性监控

服务器会每 5 分钟执行一次 P0/P1/P2 健康检查：P0 检查本机 Streamlit、FastAPI 和 cpolar 公网 URL；P1 检查定时任务新鲜度、数据源/RSS 状态、SQLite 完整性和磁盘空间；P2 检查数据库备份新鲜度与完整性、新闻 AI 失败堆积和通知渠道投递状态。已停用的备用源和未配置的可选源不会升级成严重告警；只有 `HEALTH_CRITICAL_DATA_SOURCES` 中、且没有新鲜有效数据的源才会升级。健康状态会写入运行目录，服务重启后同一问题不会重新推送；状态变化或恢复时才发送告警。

在服务器 `.env` 中配置 cpolar 为 8501 隧道生成的公网 URL，并启用飞书通知：

```env
CPOLAR_HEALTH_ENABLED=true
CPOLAR_PUBLIC_URL=https://你的-cpolar-8501-公网地址
CPOLAR_LOCAL_URL=http://127.0.0.1:8501
CPOLAR_HEALTH_CHECK_MINUTES=5
CPOLAR_HEALTH_TIMEOUT_SECONDS=15
NOTIFY_CHANNELS=lark

# 可选阈值
# 重启后等待端口和隧道就绪再执行首个 P0 检查，避免重启瞬间误报。
HEALTH_INITIAL_CHECK_DELAY_SECONDS=45
HEALTH_STARTUP_GRACE_MINUTES=10
HEALTH_DATA_MAX_AGE_SECONDS=43200
HEALTH_NEWS_MAX_AGE_SECONDS=10800
HEALTH_DATA_SOURCE_MAX_AGE_SECONDS=43200
HEALTH_CRITICAL_DATA_SOURCES=fred,crypto_liquidity,crypto_market
HEALTH_RSS_SOURCE_MAX_AGE_SECONDS=21600
HEALTH_MIN_FREE_BYTES=524288000
HEALTH_MIN_FREE_PERCENT=10
HEALTH_BACKUP_MAX_AGE_SECONDS=129600
HEALTH_AI_FAILED_ARTICLES_MAX=5
HEALTH_AI_FAILURE_RECENT_SECONDS=86400
HEALTH_NOTIFICATION_STATUS_MAX_AGE_SECONDS=86400
```

如果没有配置 `CPOLAR_PUBLIC_URL`，系统只能检查本机 8501，无法确认 cpolar 隧道状态。修改 `.env` 后重启 `macro-dashboard-server`。

### 9.6 OKX 只读交易看板

在服务器 `.env` 中配置 OKX API，但在 OKX 后台只勾选 `Read` 权限，并设置 IP 白名单；不要开启 Trade 或 Withdraw，也不要把密钥写入数据库、日志或 Git：

```env
OKX_API_KEY=你的只读Key
OKX_API_SECRET=你的Secret
OKX_API_PASSPHRASE=你的Passphrase
OKX_API_BASE_URL=https://www.okx.com
OKX_API_DEMO=false
OKX_ACCOUNT_LABEL=main
OKX_INST_TYPE=SWAP
OKX_REQUIRED_ACCOUNT_LEVEL=3
OKX_SYNC_LIMIT=100
OKX_READONLY_SYNC_ENABLED=true
OKX_READONLY_SYNC_INTERVAL_MINUTES=1
```

`OKX_REQUIRED_ACCOUNT_LEVEL=3` 对应 OKX 的 Multi-currency margin。交易复盘页的“同步 OKX 账户”只调用只读接口，写入账户快照、当前非零持仓、待成交订单、近 7 天订单历史、归档订单和成交；“刷新 K 线”读取公开行情，并在图上标记已同步成交、已同步的待成交挂单及本地交易计划入场线。页面不提供下单、撤单或资金操作。

`OKX_READONLY_SYNC_ENABLED=true` 时，服务端会在配置的间隔内只读同步持仓、待成交订单、近 7 天订单历史和成交，并把已关联计划的订单状态、累计成交量变化写入本地执行时间线。这样已撤销订单会从“当前挂单”中移除并保留在订单历史；该任务不写入 OKX。未配置完整 OKX 只读密钥时不会启动。

### 9.7 交易计划环境反馈

交易复盘页的“创建交易计划”会记录交易类型、预期持仓周期、宏观判断周期、技术周期、入场方式、计划入场价、条件触发价、计划数量、入场触发、价格止损、时间止损和计划到期时间。计划可以先不挂单；保存后才从已同步的 OKX 订单中选择关联，不需要手填订单 ID。保存时，系统会生成一份不可自动覆盖的基础快照，内容包括：本地宏观组合信号、相关 AI 新闻、数据源新鲜度，以及 OKX 公开 K 线（网络不可用时会记录缺口而不会阻止保存）。

一条计划可关联多笔订单，并标记为入场、止盈退出、止损退出、手动退出或其他退出。执行状态始终由已关联订单的累计成交量推导：入场累计成交减去退出累计成交，得到“本计划归属仓位”。因此“部分成交后撤掉剩余挂单”会显示为“持仓中，入场余单已撤”，而不是误判为没有持仓。账户交易对总仓位只用于核对；若同一交易对存在其他计划或未关联成交，系统不会自动归因。

页面可在明确输入确认文字后清除全部本地计划研究数据，包括计划、环境反馈、AI 点评、影子计划、虚拟订单和计划—订单关联。此操作保留只读同步的 OKX 订单、成交和持仓缓存，且不会影响交易所。

之后可主动点击“生成计划环境反馈”。默认只分析创建计划时的基础快照；勾选“按当前环境重新采集”后会保存一份新的反馈上下文，但不会覆盖原始快照。反馈只说明宏观/实时/技术条件之间的支持、矛盾、时间周期匹配、风险和数据缺口，不构成下单批准、阻止或交易指令。

交易复盘页会将“AI 独立影子计划”“计划环境反馈”“AI 点评历史”绑定到当前选择的一条交易计划之下，并以三个独立折叠区展示。折叠标题会保留各自的最新状态；刚生成内容时，以及 AI 有进行中的本地虚拟订单时，影子计划区会自动展开一次，方便核对后再收起。

交易计划的 AI 输出和事后点评共享同一组 AI 配置；如需调整计划反馈的输出长度，可在 `.env` 设置：

```env
AI_TRADE_PLAN_MAX_TOKENS=2600
```

点击“生成 AI 交易点评”时，系统会将原计划、计划环境反馈、实际订单/成交和当前 K 线摘要一起交给 AI，用于比较计划、执行与结果。所有 OKX 操作仍然只读；页面不提供下单、撤单或资金操作。

#### 9.7.1 AI 影子账户与本地虚拟订单

在每一条用户交易计划下，可以主动点击“生成独立 AI 影子计划”。生成阶段只把交易对和一份新鲜的宏观、新闻、数据健康、OKX 公开 K 线快照交给 AI；不会传入用户的方向、入场价、触发价、仓位、止损、目标、交易理由或真实订单。AI 计划保存后，系统才使用确定性规则把两份计划做差异比较，因此“AI 与用户的方向是否相同、入场价差多少、风险收益比是否完整”可以被长期统计，而不是衡量 AI 是否复述了用户。

AI 可以选择：`no_trade`、`watch`、限价挂单、条件限价单、条件市价单或市价虚拟单。前两种决策不会创建虚拟订单，但会保留到统计中，避免只统计参与交易的样本。可执行计划必须有方向、止损、目标和最低风险收益比；系统会按虚拟账户风险上限反算数量，并限制单笔名义金额。

虚拟订单仅写入 SQLite：

- 限价单：1 分钟 K 线触及价格时，按限价全额模拟成交。
- 条件限价单：先触发，下一根 1 分钟 K 线起再等待限价成交，避免假设同一根 K 线内的未知成交顺序。
- 条件/普通市价单：按触发或快照价格加入固定方向性滑点。
- 进出场均计固定费率；止盈和止损同根 K 线同时触发时，按更不利的止损结算。
- 待成交订单会到期；已成交虚拟仓位会根据止损、目标或时间止损关闭。虚拟挂单可在页面取消，绝不会取消 OKX 真实订单。
- 虚拟数量按基础币与 USDT 名义金额反算，不能当作 OKX 合约张数；长期比较应优先使用 R 倍数、收益期望和最大回撤。

默认参数可在服务器 `.env` 调整：

```env
AI_SHADOW_PAPER_ENABLED=true
AI_SHADOW_PAPER_INTERVAL_MINUTES=1
AI_SHADOW_PAPER_CANDLE_LIMIT=300
AI_SHADOW_ACCOUNT_EQUITY_USD=10000
AI_SHADOW_MAX_RISK_PCT=0.01
AI_SHADOW_MAX_NOTIONAL_USD=2500
AI_SHADOW_MIN_RR=1.5
AI_SHADOW_FEE_BPS=5
AI_SHADOW_SLIPPAGE_BPS=2
```

定时任务只在存在活动虚拟订单时调用 OKX 公共行情，不需要 OKX API Key。默认每次读取最多 300 根 1 分钟 K 线（约 5 小时），以便服务短暂停顿后补齐模拟；超过可回看窗口的缺口会写入订单事件，避免把缺失行情误当作“未触价”。影子账户是计划质量与执行逻辑的研究工具，不是实盘收益承诺；初期尤其要关注 R 倍数、样本量、最大回撤和“不交易”的校准，而不是短期胜率。

### 9.8 飞书日报卡片与故障告警

在飞书群中添加“自定义机器人”，复制 Webhook 地址后写入服务器 `.env`：

```env
NOTIFY_CHANNELS=lark
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的机器人标识
LARK_WEBHOOK_SECRET=飞书签名校验密钥   # 在机器人安全设置启用签名校验时填写
DASHBOARD_PUBLIC_URL=https://你的看板域名
```

飞书卡片由每日定时报告自动发送，标题为“宏观看板今日内容”；当数据抓取、新闻、AI、日报或备份任务在重试耗尽后失败时，会发送红色“宏观看板任务告警”卡片，包含任务名称和错误摘要。AI 调用虽回退成功但出现异常时，会发送红色“宏观看板运行告警”卡片。`DASHBOARD_PUBLIC_URL` 配置后，卡片会附带“打开宏观看板”按钮。

Webhook 地址和签名密钥与 API Token 一样属于敏感配置，只保存在服务器 `.env`，不要提交到仓库。飞书自定义机器人支持交互式卡片；若开启签名校验，程序会按飞书要求附带时间戳和 HMAC-SHA256 签名。

### 9.9 紧急新闻推送规则

日报用于汇总，紧急推送用于及时提醒。新闻抓取后会经过 AI 分析和事件聚类；只有首次出现的事件簇满足以下条件时才推送，避免同一事件被多家媒体重复报道时反复打扰：

- AI 判定为新信息。
- 严重度达到 `NEWS_ALERT_MIN_SEVERITY`，默认 `4/5`。
- 置信度达到 `NEWS_ALERT_MIN_CONFIDENCE`，默认 `0.75`。
- 首次出现时间在 `NEWS_ALERT_MAX_AGE_MINUTES` 内，默认 90 分钟。
- 同一事件簇成功推送后永久去重；推送失败会在有效时间窗口内重试。

推荐起步配置：

```env
NEWS_ALERT_ENABLED=true
NEWS_ALERT_MIN_SEVERITY=4
NEWS_ALERT_MIN_CONFIDENCE=0.75
NEWS_ALERT_MAX_AGE_MINUTES=90
```

如果只希望关注特定风险，可进一步限定事件类型或资产：

```env
NEWS_ALERT_EVENT_TYPES=geopolitics,fed_policy,credit,crypto
NEWS_ALERT_ASSETS=BTC,Oil,DXY
```

留空表示不过滤。阈值建议先维持默认值运行一到两周，再根据实际推送数量调整：消息过多时提高严重度到 `5` 或置信度到 `0.85`；漏掉重要消息时再逐步降低阈值。紧急新闻仍是研究提醒，需要回到看板核对原始来源、关联指标和后续市场反应。

网页侧边栏的“通知规则”页面可以直接修改启用状态、严重度、置信度、最少来源文章数、事件有效窗口、关键事件、关键资产和推送渠道。首次打开页面会把默认规则写入数据库；之后每次修改任一项都会自动保存，下一轮新闻任务立即读取新规则，不需要重启服务。关键事件和关键资产留空时不做筛选。`.env` 中的 `NEWS_ALERT_*` 仅作为首次部署或尚未在网页保存时的默认值；飞书 Webhook、签名密钥和 API Token 始终只在服务器 `.env` 中配置。

刷新、日报、上下文和备份接口默认有冷却时间，配置项为 `API_*_COOLDOWN_SECONDS`。API 限频是单个 server 进程内的保护；如果部署多个 server 实例，应在 Nginx、网关或 Redis 层增加集中式限频。

调度器、日报、研究包和备份文件统一使用 `SCHEDULER_TIMEZONE`，默认是 `Asia/Shanghai`。如果修改该配置，请使用 IANA 时区名称，例如 `UTC` 或 `America/New_York`；配置错误时服务会在启动阶段直接报错。

Windows 默认支持 `Asia/Shanghai` 和 `UTC`，其他带夏令时的时区依赖 `tzdata` 包；依赖安装完成后再配置例如 `America/New_York`。

任务执行控制：

- `TASK_RETRY_ATTEMPTS`：失败后的重试次数，默认 2 次，也就是最多执行 3 次。
- `TASK_RETRY_BASE_SECONDS`：重试基础等待时间，默认 5 秒，后续按 2 倍递增。
- `*_TIMEOUT_SECONDS`：任务 watchdog 超时时间。任务完成后如果超过该时间，会记录为超时并按幂等任务策略重试。
- 任务状态保存到 `runtime/task_status.json`，可用于定位最后一次运行、重试和失败原因。

当前任务默认值：数据刷新 1800 秒、新闻刷新 600 秒、每日研究包 900 秒、日报/周报 1200 秒、备份 300 秒。

Python 线程不能被安全强制终止，因此 watchdog 超时不会粗暴杀掉正在执行的线程；网络请求自身的 timeout 仍由各数据源 fetcher 控制。生产环境应优先调小单个 HTTP 请求的 timeout，再根据完整任务耗时调整 `*_TIMEOUT_SECONDS`。

服务启动时会读取 `runtime/task_status.json` 做补偿判断：数据刷新超过 8 小时、新闻刷新超过 2 小时，或日报/周报已经过计划时间但没有成功记录时，会自动补执行一次。可以设置 `STARTUP_RECOVERY_ENABLED=false` 关闭启动补偿；如果服务器资源较小，可适当增大两个 `STARTUP_RECOVERY_*_MAX_AGE_SECONDS`。

数据刷新、新闻刷新、日报、周报和备份使用共享锁。锁文件默认位于 `runtime/locks/`，用于阻止 API、定时任务和 Streamlit 同时执行同一类长任务。进程异常退出后，超过 `TASK_LOCK_STALE_SECONDS` 的锁会被视为过期并自动清理。

服务端日志使用轮转文件，默认写入 `logs/server.log`。数据库备份默认写入 `backups/`。

如果在刷新数据时遇到 `sqlite3.OperationalError: database is locked`：

- 优先确认是否同时开着 Streamlit 页面、server 定时任务、手动刷新脚本。
- 当前数据库连接已经启用 `busy_timeout`、提交重试和 WAL。
- 如果仍频繁锁库，可以把项目放到 WSL 原生文件系统，例如 `~/macro-dashboard`，不要放在 `/mnt/c` 或 `/mnt/e`。SQLite 在 WSL 访问 Windows 盘时锁语义更敏感。
- 也可以临时调大 `SQLITE_TIMEOUT_SECONDS` 和 `SQLITE_BUSY_TIMEOUT_MS`。

## 10. Python 依赖安装和 PyPI 访问问题

### 10.1 当前问题的原因

本机曾出现两类不同问题：

1. `uv` 默认缓存目录位于 `C:\Users\...\AppData\Local\uv\cache`，当前用户对该目录没有写权限。
2. 当前运行环境禁止访问 `https://pypi.org`，因此即使缓存目录可写，也无法在线下载缺少的依赖。

第一类是缓存路径问题，第二类是网络策略问题，不能用同一种方式解决。

### 10.2 推荐的在线安装方式

在可以正常访问 Python 包索引的服务器上，建议使用虚拟环境：

```bash
cd /opt/macro-dashboard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果使用 `uv`，把缓存放在项目或用户有权限的目录：

```bash
export UV_CACHE_DIR=/opt/macro-dashboard/.uv-cache
uv pip install --python .venv/bin/python -r requirements.txt
```

Windows PowerShell：

```powershell
Set-Location E:\macro-dashboard
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
$env:UV_CACHE_DIR="$PWD\.uv-cache"
python -m pip install -r requirements.txt
```

如果公司或服务器网络不能直接访问 PyPI，应配置组织允许的 Python 包镜像，并通过 `PIP_INDEX_URL` 或 `UV_INDEX_URL` 指定。不要把不确定的镜像地址硬编码进项目配置。

### 10.3 完全离线安装方式

如果服务器完全不能联网，在一台可以联网、且操作系统和 Python 版本尽量一致的机器上下载 wheel：

```bash
python -m pip download -r requirements.txt -d wheelhouse
```

把 `wheelhouse/` 和项目一起复制到服务器，然后离线安装：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --no-index --find-links=wheelhouse -r requirements.txt
```

注意：Windows、Linux、CPU 架构和 Python 主版本不同，wheel 可能不能通用；最好在与目标服务器相同的环境中准备离线包。

### 10.4 长期依赖管理建议

当前 `requirements.txt` 使用较多 `>=` 版本范围，重装时可能得到不同版本。服务器稳定运行后建议：

- 固定一套经过验证的依赖版本。
- 生成 `requirements.lock.txt` 或使用带锁文件的项目管理方式。
- 升级依赖前先在测试环境运行启动、数据刷新和 API 检查。
- 保留可复用的 `wheelhouse/` 或内部包缓存。
- 不要因为安装失败就直接关闭 TLS 校验或使用不可信下载源。

## 11. 维护约定

以后每新增一类功能，需要同步更新本文档：

- 新页面：更新“页面结构”。
- 新数据源：更新“数据源”和“增量更新”。
- 新 AI 上下文：更新“AI 日报”。
- 新研究功能：更新“研究假设”。
- 新信号：更新“组合信号与复盘”。

这份文档是看板的使用说明，也是一份功能地图。
