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

## 3. 数据源

数据源配置集中在 `config/data_sources.py`。

当前主要数据源：

- `fred`：美国宏观、利率、信用、市场和 BTC Coinbase 数据。
- `akshare`：中国宏观数据。
- `tic`：美国财政部 TIC 美债持有数据。
- `alpha_vantage`：MSTR/NVDA/MU 等美股备选源，需要 `ALPHA_VANTAGE_KEY`。
- `stooq`：免 key 市场数据备选源。
- `yfinance`：Yahoo Finance 备选源。
- `crypto_liquidity`：DefiLlama 稳定币数据，Kraken/Coinbase ETH/BTC。
- `crypto_market`：Binance 公共接口的 BTC 资金费率和持仓量历史。
- `crypto_flows`：可选配置的 BTC ETF flows 和交易所净流入 CSV/JSON 适配器。
- `news`：RSS 和可选 Alpha Vantage 新闻源。

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
- yfinance/CoinGecko：已有历史时只拉短窗口。
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

被拒绝的记录不会覆盖正常数据，并会写入 `data_quality_issues` 表。首页数据健康区会显示未解决的质量提醒数量；出现提醒时，应先检查来源和数据口径，再决定是否恢复或修正数据。

## 5. AI 日报

AI 日报入口在“每日沉淀”页面，也可以由 `server.py` 定时生成。

需要配置：

```env
OPENAI_API_KEY=你的key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
AI_ANALYZE_MAX_TOKENS=4096
AI_DAILY_MAX_TOKENS=8192
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

如果没有配置 AI key，系统会保存本地研究包，不会中断流程。

新闻 AI 分析要求模型返回 JSON。系统已经做了代码块/多余文字提取和字段归一化；如果仍出现 `Unterminated string`、`Expecting value` 之类日志，通常是请求侧输出上限太小、模型输出被截断，或模型返回了非 JSON。若使用长上下文/长输出模型，可以继续调大 `AI_ANALYZE_MAX_TOKENS` 和 `AI_DAILY_MAX_TOKENS`。

## 5.1 新闻处理、事件关联与 AI 复盘

新闻文章依次经过 `fetched`、`queued`、`analyzing`、`analyzed`、`clustered` 状态。AI 请求、结构化 JSON 或写库失败会进入 `failed`，新闻雷达会显示失败原因和尝试次数；点击“重试失败文章”后，文章回到 `fetched`，等待下一次新闻分析任务处理。

事件流会把同类已分析文章聚成事件簇，并根据 AI 的 `follow_up_data`、宏观传导渠道、影响资产和事件主题，关联：

- 需要继续验证的指标。
- 匹配的活跃研究假设及匹配理由。

新闻雷达的“AI复盘”页会按新闻发布时间或分析创建日期作为起点，记录明确 `bullish` / `bearish` 判断对应资产后续 1/3/7/30 个交易日的表现。统计按模型、Prompt 版本、新闻源、事件类型和资产拆分；样本不足时不显示方向准确率，避免把少量结果当成结论。

## 5.2 证据工作台与指标解读

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

- Yahoo Finance：确认 `USDCNH=X`、`USDCNY=X`、`000300.SS`、`399006.SZ`、`^HSTECH` 返回非空数据；部分符号不可用时应记录失败，不影响其他指标。
- AKShare：确认 M2 同比、社融存量同比、DR007 的接口版本和字段名称匹配；AKShare 升级后优先查看抓取日志中的字段错误。
- Binance：确认资金费率和 OI 接口可访问，核对 OI 返回值的单位，并留意公共接口的限频响应。
- ETF/交易所资金流：在 `.env` 配置 `BTC_ETF_FLOWS_URL`、`BTC_EXCHANGE_NETFLOW_URL` 后，确认返回 CSV/JSON 至少包含 `date` 和 `flow`、`net_flow`、`value` 或 `amount` 字段。
- 空数据降级：移除可选资金流 URL 或模拟接口失败，确认页面仍可打开，并显示“未配置或暂无数据”。

这些验收需要在服务器的真实依赖、网络和供应商数据环境中完成，不能仅用本地 AST 检查替代。

`NOTIFY_CHANNELS` 支持 `telegram,lark,email,webhook`，并会自动忽略逗号两侧空格。Alpha Vantage 新闻和行情使用同一个 `ALPHA_VANTAGE_KEY`；不再读取 `FINNHUB_API_KEY`。

当任务重试耗尽后，若 `NOTIFY_ON_TASK_FAILURE=true`，系统会通过 `NOTIFY_CHANNELS` 配置的 Telegram、飞书、Email 或 Webhook 渠道发送失败通知。通知发送本身不会阻塞任务状态记录；即使通知渠道不可用，也可以在 `runtime/task_status.json` 和日志中查看失败原因。

### 9.4 飞书日报卡片与故障告警

在飞书群中添加“自定义机器人”，复制 Webhook 地址后写入服务器 `.env`：

```env
NOTIFY_CHANNELS=lark
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的机器人标识
LARK_WEBHOOK_SECRET=飞书签名校验密钥   # 在机器人安全设置启用签名校验时填写
DASHBOARD_PUBLIC_URL=https://你的看板域名
```

飞书卡片由每日定时报告自动发送，标题为“宏观看板今日内容”；当数据抓取、新闻、AI、日报或备份任务在重试耗尽后失败时，会发送红色“宏观看板任务告警”卡片，包含任务名称和错误摘要。`DASHBOARD_PUBLIC_URL` 配置后，卡片会附带“打开宏观看板”按钮。

Webhook 地址和签名密钥与 API Token 一样属于敏感配置，只保存在服务器 `.env`，不要提交到仓库。飞书自定义机器人支持交互式卡片；若开启签名校验，程序会按飞书要求附带时间戳和 HMAC-SHA256 签名。

### 9.5 紧急新闻推送规则

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
