# 金融交易知识库

一个本地运行的股票、基金投研知识库和可视化看板。项目会抓取公开行情、财经新闻和舆情信息，结合自定义观察池、交易方法、持仓记录和基金分析脚本，生成候选池、热点热词、数据源状态、基金分析和持仓监控视图。

> 仅用于个人研究、交易复盘和信息整理，不构成投资建议，也不保证收益。

## 最近更新

- 新增盘后分析页：接入 AKShare 主力资金流、行业资金流、概念资金流，支持手动刷新和打开页面后的自动刷新。
- 新增个人持仓盘后复核：持仓股票会独立计算资金流命中、资金确认、资金试探、弱势流出等信号，不再被候选池排序挤掉。
- 优化资金流数据质量：扩大个股资金流覆盖范围，统一金额口径为“元”，前端自动显示“万/亿”，并标注实际资金源和排名。
- 优化来源状态：主接口失败、备用源可用、定向查询跳过会分别显示为 `FAIL`、`备用`、`跳过`。
- 新增盘后缓存目录 `knowledge_base/after_close/`，失败时可保留最近可用盘后结果，运行产物默认不提交 Git。

## 功能简介

### 1. 总览看板

- 展示候选标的数量、股票/基金数量、有效支撑数量、失败来源数量。
- 汇总“今日结论”，包括优先研究标的、舆情热词和当前数据状态。
- 展示高支撑候选、热词雷达、市场脉冲、热点主题、数据质量和最新舆情新闻。
- 默认访问地址：`http://127.0.0.1:8765/`

### 2. 候选池

- 基于观察池、新闻舆情、行情动量、交易方法标签和风险关键词生成候选标的。
- 支持股票、指数、ETF、场外基金等类型。
- 输出总分、热度、建议动作、数据状态、行情源、新闻源、情绪分、动量分、方法匹配分、风险扣分和相关新闻。
- 建议动作包括：`积极跟踪`、`建仓观察`、`持续关注`、`仅作线索`、`谨慎回避`、`暂缓`。

### 3. 舆情新闻

- 支持 RSS、AKShare、Sina Roll、东方财富快讯等来源。
- 自动提炼新闻重点，识别主题、标的、风险关键词和热度。
- 支持点击新闻卡片查看详情。
- 保留最近 7 天来源失败历史，用于判断数据源稳定性。

### 4. 盘后分析

- 尝试通过 AKShare 获取个股主力资金流、行业资金流和概念资金流。
- 将候选池股票与资金流排名交叉，输出“资金确认”“舆情资金背离”“风险复核”等标签。
- 单独展示“个人持仓盘后复核”，包含持仓市值、浮盈亏、仓位、涨跌幅、主力净流入、资金源和复核说明。
- 展示板块净流入/净流出方向、个股资金榜和资金流来源状态。
- 资金流接口波动时会保留上一次可用缓存，并在页面标注失败来源、备用来源和跳过状态。
- 当前为试验性复核因子，不参与候选池总分。

### 5. 配置中心

- 可在页面中开关新闻源和观察池标的。
- 支持添加标准数据源模板。
- 支持添加股票、ETF、指数、基金观察标的。
- 配置文件位于 `knowledge_base/config.json`。

### 6. 持仓监控

- 支持手动录入持仓和交易记录。
- 支持文本导入持仓。
- 持仓可关联候选池研究分、风险扣分和舆情命中。
- 统计市值、盈亏、胜率、盈亏比，并生成交易风格提示。
- 盘后分析会基于个人持仓生成独立资金流复核，不会因为候选池数量过多而漏掉持仓。

### 7. 基金分析

- 集成 `external/fund-analyst/` 下的基金分析脚本。
- 支持强势基金筛选、行业轮动、回撤画像、宏观风险、节假日风险等模块。
- 分析结果写入 `knowledge_base/fund_analyst/`，该目录为运行产物，默认不提交 Git。

## 评分逻辑

评分是一个“研究优先级”启发式，不是收益预测。高分只表示值得优先研究，不代表应该买入。

### 候选池总分

候选池总分范围为 `0..100`，核心公式：

```text
total_score = 50 + sentiment_component + momentum_component + method_fit_score - risk_penalty
```

最终会被裁剪到 `0..100`。

各部分含义：

- `50`：基础分。
- `sentiment_component`：舆情支撑分，由相关新闻情绪分和提及次数计算。
- `momentum_component`：行情动量分，由最新涨跌幅计算。
- `method_fit_score`：交易方法匹配分，观察池主题与本地交易方法标签匹配时加分。
- `risk_penalty`：风险扣分，由风险关键词和明显下跌惩罚构成。

实现细节：

```text
sentiment_component = clamp(sentiment_sum + mention_count * 0.7, -12, 12) * 3
momentum_component  = clamp(change_pct, -8, 8) * 2
method_fit_score    = min(匹配方法加分, 8)
risk_penalty        = min(risk_keyword_count * 4, 24)
```

如果最新涨跌幅 `<= -3%`，额外增加 `4` 分风险扣分。

### 新闻情绪分

每条新闻会从标题和摘要中匹配关键词：

- 命中正向关键词：每个约 `+1.2`
- 命中风险关键词：每个约 `-1.8`，并增加风险计数
- 命中主题关键词：按命中数量加分，单个主题最多约 `+2.5`

主题、风险和正向关键词配置在 `knowledge_base/config.json` 的 `keyword_sets`。

### 热度分

热度分用于排序“舆情热词”和“热点新闻”，更偏向信息关注度，不等同于候选池总分。

候选标的热度大致考虑：

```text
heat_score =
  mention_count * 9
  + source_count * 7
  + theme_count * 3
  + positive_sentiment * 0.45
  + positive_momentum * 0.3
  - risk_penalty * 0.8
```

热词热度大致考虑：

```text
heat_score =
  mention_count * 6
  + source_count * 7
  + positive_sentiment * 0.9
  - risk_count * 5
```

新闻卡片热度大致考虑：

```text
heat_score =
  18
  + theme_count * 13
  + symbol_count * 8
  + positive_sentiment * 6
  - risk_count * 8
```

### 建议动作规则

候选池建议动作由总分、提及次数、涨跌幅、风险扣分和是否有行情数据共同决定：

| 动作 | 触发条件 | 含义 |
| --- | --- | --- |
| 积极跟踪 | 总分 `>= 68`，提及次数 `>= 2`，涨跌幅非负，风险扣分 `<= 8`，且有行情 | 进入重点研究清单 |
| 建仓观察 | 总分 `>= 58`，风险扣分 `<= 10` | 有一定综合支撑，但仍需等待买点、仓位计划和回撤条件 |
| 持续关注 | 总分 `>= 45` | 有线索但信号不够集中 |
| 仅作线索 | 缺少行情数据 | 只有舆情命中，需补数据核验 |
| 谨慎回避 | 风险扣分 `>= 16` | 负面或风险事件较多，先核验公告和基本面 |
| 暂缓 | 其他低优先级情况 | 暂不进入重点研究 |

### 盘后资金流信号

盘后分析不会改变 `total_score`。它只把资金流作为候选池和个人持仓的复核标签：

| 信号 | 大致含义 |
| --- | --- |
| 资金确认 | 主力净流入为正，且价格表现未明显背离 |
| 舆情资金背离 | 候选热度/分数较高，但主力资金净流出 |
| 风险复核 | 风险扣分偏高且资金净流出 |
| 弱势流出 | 价格下跌叠加资金净流出 |
| 资金试探 | 价格偏弱但有资金净流入，等待价格结构确认 |
| 缺资金数据 | 资金流接口未命中该标的，只看行情和舆情 |

候选池复核和个人持仓复核分开计算：

- `watchlist_flows`：候选池股票的盘后资金流交叉结果。
- `portfolio_flows`：个人持仓股票的盘后资金流交叉结果，包含持仓市值、浮盈亏、仓位、资金流和信号。
- `metrics.portfolio_flow_found`：个人持仓资金流命中数量。
- `metrics.portfolio_confirmed`：个人持仓中资金确认数量。
- `metrics.portfolio_diverged`：个人持仓中需要复核的数量。

资金流接口来自公开网页封装，稳定性不如正式付费数据源。页面会展示来源状态，并在失败时沿用最近一次可用盘后结果。

## 数据源

当前支持的数据源类型包括：

- 东方财富行情：A 股、指数、ETF、LOF 等实时快照。
- 天天基金估值：场外基金估算净值和估算涨跌幅。
- AKShare：东方财富财经新闻等增强数据。
- 新浪财经滚动新闻：财经新闻和 A 股市场新闻。
- 东方财富 7x24 快讯：快讯类信息。
- 36Kr RSS：科技、产业、公司动态。
- TuShare Pro：A 股日线数据，可选，需要 token。

数据源可在 `knowledge_base/config.json` 的 `news_sources` 和 `watchlist` 中维护，也可以在页面“配置”中调整。

## 目录结构

```text
finance-dashboard/              # 本地可视化界面和 API 服务
finance-dashboard/static/       # 前端页面、样式和交互脚本
finance-knowledge-updater/      # 知识库更新器和 Codex Skill
knowledge_base/config.json      # 数据源、观察池、主题关键词配置
external/fund-analyst/          # 基金分析师脚本集成
```

运行数据、数据库、抓取原文、个人持仓、交易记录和 Token 默认不会提交到 Git。
盘后分析缓存位于 `knowledge_base/after_close/`，同样不会提交。

## 部署步骤

### 1. 克隆仓库

```bash
git clone git@github.com:adam1943/financial_library.git
cd financial_library
```

如果使用 HTTPS：

```bash
git clone https://github.com/adam1943/financial_library.git
cd financial_library
```

### 2. 创建虚拟环境

```bash
python3 -m venv .venv
```

macOS / Linux：

```bash
source .venv/bin/activate
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

### 3. 安装依赖

基础看板和更新器主要使用 Python 标准库，可以直接运行。若要启用 AKShare、基金分析等增强能力，建议安装：

```bash
pip install akshare pandas numpy requests beautifulsoup4 lxml openpyxl
```

基金分析师脚本依赖：

```bash
pip install -r external/fund-analyst/requirements.txt
```

### 4. 配置 TuShare Token（可选）

TuShare Pro 是可选行情源。不要把 token 写入代码或提交到 Git。

方式一：环境变量

```bash
export TUSHARE_TOKEN="你的token"
```

方式二：本地文件

```bash
mkdir -p knowledge_base/input
printf '%s\n' '你的token' > knowledge_base/input/tushare_token.txt
```

`knowledge_base/input/tushare_token.txt` 已在 `.gitignore` 中排除。

### 5. 首次更新知识库

联网更新：

```bash
python3 finance-knowledge-updater/scripts/update_knowledge_base.py \
  --config knowledge_base/config.json \
  --output knowledge_base
```

无网络烟测：

```bash
python3 finance-knowledge-updater/scripts/update_knowledge_base.py \
  --config knowledge_base/config.json \
  --output knowledge_base \
  --offline-sample
```

更新后会生成：

```text
knowledge_base/data/finance_kb.sqlite
knowledge_base/raw/YYYY-MM-DD/*.jsonl
knowledge_base/reports/YYYY-MM-DD.md
knowledge_base/latest.md
knowledge_base/candidates.csv
knowledge_base/source_status.json
```

这些都是运行产物，默认不提交 Git。

### 6. 启动本地看板

```bash
python3 finance-dashboard/server.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

页面左下角“更新任务”也可以手动触发联网更新或离线样例更新。

盘后分析可以在页面“盘后分析”中点击“刷新盘后分析”触发。页面打开后如果当天尚未生成盘后报告，也会尝试自动触发一次。结果缓存写入：

```text
knowledge_base/after_close/YYYY-MM-DD.json
```

### 7. 后台运行（可选）

macOS / Linux 可用：

```bash
nohup python3 finance-dashboard/server.py --host 127.0.0.1 --port 8765 > dashboard.log 2>&1 &
```

查看进程：

```bash
lsof -i :8765
```

停止服务：

```bash
kill <PID>
```

## 配置说明

主要配置文件：`knowledge_base/config.json`

常用字段：

| 字段 | 说明 |
| --- | --- |
| `lookback_days` | 舆情和来源状态的回看天数 |
| `top_n` | 报告中输出的候选数量 |
| `request_timeout_seconds` | 单个数据源请求超时时间 |
| `request_attempts` | 请求重试次数 |
| `akshare_python` | AKShare 使用的 Python 解释器路径 |
| `news_sources` | 新闻和舆情来源 |
| `watchlist` | 股票、指数、ETF、基金观察池 |
| `keyword_sets.positive` | 正向关键词 |
| `keyword_sets.risk` | 风险关键词 |
| `keyword_sets.themes` | 主题关键词 |
| `fund_analyst` | 基金分析师集成配置 |

## 个人数据格式

这些文件在本地使用，默认不提交：

```text
knowledge_base/input/portfolio_holdings.csv
knowledge_base/input/portfolio_trades.csv
knowledge_base/input/trading_methods.csv
knowledge_base/input/tushare_token.txt
```

交易方法 CSV 字段：

```text
user_label,method_name,timeframe,asset_scope,tags,rule_text,risk_control,source
```

`tags` 会参与 `method_fit_score` 计算。例如：

```text
me,趋势跟随,中短线,stock,AI算力;趋势,放量突破后分批跟踪,单票亏损超过5%复盘,local
```

## 数据安全

`.gitignore` 已默认排除：

- `.venv/`
- `__pycache__/`
- `knowledge_base/input/` 中的个人持仓、交易记录、交易方法和 Token
- `knowledge_base/data/`
- `knowledge_base/raw/`
- `knowledge_base/reports/`
- `knowledge_base/fund_analyst/`
- `knowledge_base/latest.md`
- `knowledge_base/candidates.csv`
- `knowledge_base/source_status.json`

上传 GitHub 前建议检查：

```bash
git status --short --untracked-files=all
git diff --cached --name-only
```

## 常见问题

### 1. 页面打开后没有数据

先运行一次更新：

```bash
python3 finance-knowledge-updater/scripts/update_knowledge_base.py \
  --config knowledge_base/config.json \
  --output knowledge_base
```

### 2. AKShare 不可用

确认已安装依赖，并且 `knowledge_base/config.json` 中 `akshare_python` 指向正确解释器：

```bash
.venv/bin/python -c "import akshare as ak; print(ak.__version__)"
```

### 3. TuShare 报 missing token

设置环境变量或写入本地 token 文件：

```bash
export TUSHARE_TOKEN="你的token"
```

### 4. 端口被占用

检查占用：

```bash
lsof -i :8765
```

或换端口启动：

```bash
python3 finance-dashboard/server.py --host 127.0.0.1 --port 8766
```

### 5. 分数高但建议不是“积极跟踪”

总分只是其中一个条件。若缺少行情数据、风险扣分较高、涨跌幅为负或提及次数不足，建议动作会被降级。

## 免责声明

本项目输出是信息聚合和研究优先级排序，不是投资建议、买卖信号或收益预测。所有候选、分数、热词和建议都需要结合公告、财报、估值、仓位、风险承受能力和个人交易计划进一步核验。
