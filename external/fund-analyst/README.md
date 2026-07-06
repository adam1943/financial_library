# 基金智能筛选与行业轮动分析师 v6.23 - Python API 脚本集

> 配套 `SKILL.md` 的实战数据接口脚本集合
> 覆盖实时数据、宏观熊市风险预警、热点板块基金推荐、夏普比率/波动率横向筛选、回撤/涨跌幅/同赛道平均/排名四维闸门、季报滞后估值偏差、重仓股趋势共振、大盘总开关、外围局势风险雷达、量化验证闸门和持仓控亏逻辑

---

## 📁 文件结构

```
fund_api_scripts/
├── config.py                      # 公共配置（缓存/日志/常量）
├── requirements.txt               # 依赖清单
│
├── 00_main.py                     # ★ 统一入口（一键完整分析）
│
├── 01_fund_screening.py           # Step 1.1 基金筛选数据
├── 02_fund_holdings.py            # Step 1.2 持仓数据
├── 03_sector_data.py              # Step 1.3 行业板块数据
├── 04_technical_analysis.py       # Step 1.4 量价技术 + 8项企稳信号
├── 05_global_market.py            # Step 1.5 全球市场数据
├── 06_fundamental_data.py         # Step 1.6 基本面锚定
├── 07_fund_trend.py               # Step 1.7 基金走势 + 买卖时机
├── 08_holiday_risk.py             # ★ Step 1.8 节假日风险
├── 09_position_tracking.py        # ★ Step 1.9 持仓跟踪决策（v6.0控亏）
├── 10_hardcore_screening.py       # Step 0 商业本质/硬核财务筛选
├── 11_clock_trend.py              # 五时钟趋势方向
├── 12_chip_distribution.py        # 筹码峰分布
├── 13_quant_validation.py         # ★ v6.1 量化验证闸门
├── 14_macro_geopolitical_risk.py  # ★ v6.2 外围局势/中美贸易风险雷达
├── 15_sector_rotation.py          # v6.4 行业/主题轮动软闸门
├── 16_quarterly_drift.py          # ★ v6.17 季报滞后与估值偏差识别
├── 17_hot_sector_fund_recommendation.py # ★ v6.18 热点板块基金推荐
├── 18_risk_return_screener.py     # ★ v6.19 夏普比率/波动率横向筛选
├── 19_macro_bear_signal.py        # ★ v6.23 宏观熊市风险预警
├── 20_strong_fund_screener.py     # ★ v6.24 强势基金趋势跟随筛选
├── fund_drawdown_report.py        # ★ v6.17 回撤画像 + 四维严格闸门
│
├── cache/                         # 数据缓存（自动生成）
├── output/                        # 分析结果输出（自动生成）
└── README.md                      # 本文件
```

---

## 🚀 快速开始

### 0. 固定基准验证基金

后续所有 skill 升级和预测逻辑调整，都优先用 `011892 易方达先锋成长混合C` 做回归验证：

```bash
python 13_quant_validation.py 011892 normal
python 07_fund_trend.py 011892
python 14_macro_geopolitical_risk.py 011892
python 19_macro_bear_signal.py
```

### 1. 安装依赖

```bash
cd fund_api_scripts
pip install -r requirements.txt
```

核心依赖说明：
- **akshare**：免费开源金融数据库，覆盖 A股/港股/美股/基金/宏观
- **chinese-calendar**：中国节假日识别（Step 1.8 必需）
- **pandas / numpy**：数据处理

### 2. 一键完整分析（推荐）

```bash
# 对某只基金执行完整 v6.23 分析
python 00_main.py 001938
```

### 3. 单模块独立运行

每个脚本都可独立运行，不依赖其他脚本（除 `config.py`）：

```bash
# 基金筛选
python 01_fund_screening.py 001938

# 持仓分析
python 02_fund_holdings.py 001938

# 行业板块全景扫描
python 03_sector_data.py

# 具体板块分析
python 03_sector_data.py 人工智能

# 个股技术分析（含 v4.0 八项企稳信号）
python 04_technical_analysis.py 600519

# 全球市场
python 05_global_market.py

# 个股基本面
python 06_fundamental_data.py 600519

# v6.1 量化验证闸门（回测/交易成本/滚动稳定性）
python 13_quant_validation.py 001938 normal

# v6.2 外围局势与中美贸易风险雷达
python 14_macro_geopolitical_risk.py 011892

# v6.17 回撤/涨跌幅/同赛道平均/排名四维闸门
python fund_drawdown_report.py 001438 519771

# v6.17 季报滞后与估值偏差识别
python 16_quarterly_drift.py 001438

# v6.18 热点板块基金推荐；可不传代码只看全市场热点候选
python 17_hot_sector_fund_recommendation.py 001438
python 17_hot_sector_fund_recommendation.py

# v6.19 夏普比率/波动率横向筛选
python 18_risk_return_screener.py --fund-code 001438
python 18_risk_return_screener.py --compare 001438 519771 012920
python 18_risk_return_screener.py 混合型 all 2.0

# v6.23 宏观熊市风险预警（PMI/社融/估值/ROE/数据新鲜度）
python 19_macro_bear_signal.py
python 19_macro_bear_signal.py --no-save

# v6.24 强势基金趋势跟随筛选（用英文CLI参数，避免Windows中文编码问题）
python 20_strong_fund_screener.py --types mixed stock --top 10 --macro-state closed
python 20_strong_fund_screener.py --types mixed stock --top 10 --format json

# 基金走势 + 买卖决策
python 07_fund_trend.py 001938

# ★ 节假日风险评估
python 08_holiday_risk.py
python 08_holiday_risk.py --history   # 附带历史数据

# ★ 持仓跟踪（买入后管理）
python 09_position_tracking.py 001938 2.5 2025-10-15 100000
# 或交互式：
python 09_position_tracking.py
```

---

## 📊 各脚本功能详解

### `00_main.py` - 统一入口

一键按 v4.0 skill 的 Step 1-7 顺序执行完整分析，输出结构化 JSON 报告 + 终端摘要。

```bash
python 00_main.py <基金代码>                # 完整分析
python 00_main.py --macro                  # 仅宏观扫描（不针对具体基金）
python 00_main.py --track <代码> <买入价> <买入日期> [金额]  # 持仓跟踪
```

### `01_fund_screening.py` - 基金筛选数据（Step 1.1）

**输出**：
- 基金基础信息（名称、类型、经理、公司、成立日期）
- 近3年业绩排名百分位
- 近3年最大回撤、年化收益、夏普比率、年化波动率
- 近1/3/6月同赛道强势参考：排名前5%-10%、涨跌幅为正、且涨幅高于基准赛道
- 四维严格闸门：回撤、基金涨跌幅、同赛道平均涨跌幅、同赛道排名前5；未通过时不支持强买入/强加仓
- 持有回撤与同赛道排名闸门：当前回撤、近1年/近3年/成立以来最大回撤、平均修复天数、同类排名和基金经理近期调仓有效性；历史回撤用于仓位上限，不机械触发卖出
- 夏普比率/波动率横向闸门：近1/3/6月夏普、年化波动率、阶段收益、最大回撤和同类样本排名；夏普前5%且≥2作为优先候选证据
- 6项量化门槛检验结果

### `fund_drawdown_report.py` - 回撤画像与同赛道排名风控

**输出**：
- 近1/3/6月收益、同赛道排名、同类平均、沪深300对比
- 近1/3/6月阶段最大回撤
- 近1年/近3年/成立以来最大回撤、当前回撤、平均修复天数、最近5次>5%回撤
- `four_dimension_gate`：基金涨跌幅必须为正、同赛道平均必须为正、同赛道排名必须进入前5名；回撤作为仓位上限和风险画像记录
- `drawdown_profile` 与 `recent_strength`，供 `01_fund_screening.py` 生成 `drawdown_guard`，区分继续持有、控仓持有、降级持有和减仓/止损

```bash
python fund_drawdown_report.py 001438 519771
python fund_drawdown_report.py 001438 --format json
```

### `16_quarterly_drift.py` - 季报滞后与估值偏差识别（Step 1.6bis）

吸收 `D:\.temp\fund_analyzer.py` 中有价值的数据层思路，但不依赖 Claude/Anthropic API。脚本会获取基金实时估算涨幅、最新季报前十大持仓、重仓股实时涨跌幅，并计算“基金实时估算涨幅 - 季报持仓理论加权涨幅”。

**输出**：
- 基金实时估算涨幅、估算时间、上一日净值
- 最新季报前十大持仓覆盖仓位、理论加权涨幅、缺失行情数量
- `valuation_bias.level`：`aligned` / `low_drift` / `medium_drift` / `high_drift` / `data_weak` / `unknown`
- 当 `medium_drift` 或 `high_drift` 出现时，提示基金经理可能已调仓，降低对季报持仓推演的依赖

```bash
python 16_quarterly_drift.py 001438
```

### `02_fund_holdings.py` - 持仓数据（Step 1.2）

**输出**：
- 最新季度前10大重仓股（代码 + 名称 + 占比）
- 上一季度对比：新进/退出/加仓/减仓
- 前10持仓集中度
- 行业暴露分布（自动调用 akshare 查询每只重仓股所属行业）
- 行业集中度警告（>40% 提示）

### `03_sector_data.py` - 行业板块数据（Step 1.3）

**输出**：
- 全市场行业涨跌幅排名（TOP10 涨/TOP10 跌）
- 北向资金近30日净流入 + 评分（对应 skill 3.1 维度③）
- 两融余额30日趋势
- 指定单板块时，输出近5日/30日/90日涨跌幅

### `17_hot_sector_fund_recommendation.py` - 热点板块基金推荐（Step 1.3bis）

**输出**：
- 当前行业板块 + 概念板块热点 TopN，例如光伏、芯片、储能、通信、AI 等
- 按热点关键词匹配开放基金/场内基金，形成主题基金候选池
- 对候选基金叠加近1/3/6月涨跌幅、同赛道排名、四维严格闸门和回撤画像
- 如传入当前持有基金代码，输出当前基金季报持仓与热点板块的贴合度
- 推荐分层：`strong_recommend` / `watch_candidate` / `only_watch` / `data_weak`
- v6.19 叠加候选基金近1/3/6月夏普比率和年化波动率横向对比，风险收益排名不足时自动降级

```bash
python 17_hot_sector_fund_recommendation.py 001438
python 17_hot_sector_fund_recommendation.py --top-sectors 5 --top-funds 5
```

### `18_risk_return_screener.py` - 夏普比率/波动率横向筛选（Step 4.3penta）

吸收 `D:\.temp\fund_screener.py` 的有效部分：先按同类近1/3/6月收益预筛，再拉日净值计算夏普比率、年化波动率、年化收益和最大回撤。

**输出**：
- 同类样本夏普排名前5%的基金，默认要求夏普≥2
- 单只基金近1/3/6月风险收益画像与横向排名
- 多只持有基金的夏普/波动率横向对比，用于持有、减仓、换基判断

```bash
python 18_risk_return_screener.py --fund-code 001438
python 18_risk_return_screener.py --compare 001438 519771 012920
python 18_risk_return_screener.py 混合型 all 2.0
```

### `19_macro_bear_signal.py` - 宏观熊市风险预警（Step 2.7）

吸收 `D:\.temp\13_macro_bear_signal.py` 的有效部分，并修正 Windows 输出编码、数据新鲜度和估值口径。脚本优先使用中证指数沪深300真实 PE 分位；失败时才退回价格分位代理，并明确标记。

**输出**：
- PMI、社融/信贷脉冲代理、沪深300估值分位、ROE周期
- 每个宏观因子的数据日期和新鲜度
- 综合宏观风险得分、风险等级和权益仓位上限
- 已降级单因子：国家队减仓、北向流出、ETF份额下降都不能单独当硬信号

```bash
python 19_macro_bear_signal.py
python 19_macro_bear_signal.py --no-save
```

### `20_strong_fund_screener.py` - 强势基金趋势跟随筛选（Step 6.1ter）

对应 v6.24，解决“强势基金不一定给 10%-15% 深回撤，可能小回调后继续走高”的筛选与执行问题。脚本命令行使用英文参数，脚本内部使用 UTF-8 中文列名，避免 Windows PowerShell 管道把中文参数转为 `???`。

**输出**：
- A股主动混合/股票基金中，近1/3/6月收益均为正的强势候选
- 同类排名百分位、近1/3/6月最大回撤、夏普、年化波动率
- MA10/MA20/MA60 趋势状态、申购状态、日累计限额
- v6.24 三类买点：强势不回调观察仓、3%-6%小回调确认仓、8%-12%健康回撤主仓

```bash
python 20_strong_fund_screener.py --types mixed stock --top 10 --macro-state closed
python 20_strong_fund_screener.py --types mixed stock --top 10 --format json
python 20_strong_fund_screener.py --types mixed stock --top 20 --max-candidates 60 --macro-state open
```

### `04_technical_analysis.py` - 量价技术分析（Step 1.4 + 3.2 + ★6.1第三步）

**输出**：
- MA5/10/20/60 / MACD / RSI14 / 布林带
- **25 分制量价趋势评分**（对应 skill 3.2）
- **★ v4.0 八项技术企稳信号检查**（对应 skill 6.1 第三步强化）：
  - 止跌信号 / 缩量企稳 / MA5上穿MA10 / MA20拐头
  - MACD底背离/金叉 / RSI脱离超卖 / K线企稳形态 / 量价配合
- 加权触发率 + 四级确认等级（🟢强企稳 / 🟡弱企稳 / 🟠观察 / 🔴禁止）
- 位置诊断（高位区/中位区/回调区/深跌区）

### `05_global_market.py` - 全球市场（Step 1.5）

**输出**：
- 美股：纳指100 / 标普500 / 道琼斯（+30日涨跌）
- 港股：恒生指数 / 恒生科技
- 汇率：美元/人民币、港币/人民币
- 10Y 美债收益率 + 解读
- VIX 恐慌指数 + 情绪判定
- AH 溢价指数

### `06_fundamental_data.py` - 基本面（Step 1.6）

**输出**：
- 个股估值：PE(TTM)、PB、股息率、市值
- 最近4季度净利润
- TTM 自由现金流（经营现金流 - 资本开支）
- **真金白银估值**：真实PE、回本年限、FCF收益率
- 估值判定（低估/合理/偏高/高估/亏损）
- 统计局工业增加值同比（行业基本面锚定）

### `07_fund_trend.py` - 基金走势 + 买卖时机（Step 1.7 + 6.1）

**输出**：
- 近3年日净值序列
- **历史回撤分析**：每次回撤幅度、修复天数、当前回撤状态
- 位置诊断（4级）
- 近5日/20日/60日涨跌幅
- **综合买卖决策**（对应 skill 6.1 第五步）：
  - 估值+技术二维矩阵判定
  - 具体买入价格区间
  - 止损价 / 三级止盈价（+15%/+25%/+40%）
  - 三批建仓方案

### `08_holiday_risk.py` - ★ 节假日风险（v4.0 新增，Step 1.8 + 6.5）

**输出**：
- 下一个节假日识别（基于 chinese_calendar + akshare 交易日历）
- 风险等级（🔴极高 / 🟠高 / 🟡中 / 🟢低）
- **节前 6 项风险信号检查**（对应 skill 6.5.3）：
  1. 两融余额连续3日下降
  2. 北向资金连续3日净流出>30亿
  3. 成交额萎缩>20%
  4. VIX>20
  5. 重大政策/财报窗口（人工确认）
  6. 地缘热点事件发酵（人工确认）
- 历史节前节后10日走势分析（过去10年样本）
- 综合操作建议（含 skill 6.5.4 硬性规则）

### `09_position_tracking.py` - ★ 持仓跟踪与决策（v6.0 控亏版，Step 1.9 + 6.6/6.7/6.8）

**输出**：
- 当前盈亏 + 持仓天数
- 关键价位（初始止损/保本/三级止盈）
- **移动止损动态计算**（对应 skill 6.8.2）：
  - 浮盈<5%：v6.0初始止损（正常-5%/震荡-4%/下跌-3%）
  - 浮盈5-10%：成本+1%
  - 浮盈10-15%：成本+3%
  - 浮盈15-25%：成本+8%
  - 浮盈25-40%：成本+15%
  - 浮盈>40%：跟随MA20
- **浮盈决策**（对应 skill 6.6.2 情景A）：
  - +15% 首次止盈30%
  - +25% 二次止盈40%
  - +40% 评估顶部信号
- **亏损控制决策**（对应 skill 6.7）：
  - 预警区(-3~-5%)：策略1 预警复核
  - 止损执行区(-5~-8%)：策略2 减仓/止损优先
  - 硬止损后(<-8%)：策略3 止损换仓
  - 纪律失效区(<-15%)：策略4 纪律复盘
  - 被套诊断四问 + 亏损不补仓禁忌

---

## 💡 使用建议

### 典型工作流

**【场景1：评估某基金是否值得买】**
```bash
python 00_main.py 001938
```
完整执行 Step 1-7，输出是否买入 + 具体价格区间。

**【场景2：判断整体市场环境】**
```bash
python 00_main.py --macro
```
获取行业全景 + 全球市场 + 节假日风险。

**【场景3：跟踪已持仓基金】**
```bash
python 00_main.py --track 001938 2.5 2025-10-15 100000
```
输出当前盈亏、移动止损位、下一步动作。

**【场景4：临近节假日】**
```bash
python 08_holiday_risk.py --history
```
获取节前风险评估 + 历史同期走势参考。

### 与大模型（Claude）配合使用

1. 运行脚本生成 JSON 报告（保存在 `output/` 目录）
2. 将 JSON 报告作为上下文喂给 Claude
3. 让 Claude 依据 v4.0 skill 规则进行综合分析和投资建议

```bash
# 生成报告
python 00_main.py 001938

# output/00_full_report/full_analysis_001938_20260422_143000.json
# 将此 JSON 贴给 Claude，并引用 v4.0 skill 进行完整研判
```

---

## ⚙️ 配置说明

### 缓存策略（`config.py`）

| 数据类型 | 缓存时长 | 适用场景 |
| :------- | :------- | :------- |
| realtime | 5 分钟   | 实时净值、即时行情 |
| daily    | 4 小时   | 日线 K线、行业数据 |
| quarterly| 1 天     | 季报、财报 |
| static   | 7 天     | 历史长周期数据 |

清空缓存：直接删除 `cache/` 目录下的 JSON 文件即可。

### 数据源说明

**主数据源：akshare**
- 免费开源，无需注册 API Key
- 覆盖 A股/港股/美股/基金/宏观/汇率
- 官方文档：https://akshare.akfamily.xyz/

**辅助数据源：yfinance**
- 备用美股数据源（当 akshare 失效时）

**注意**：akshare 的数据源本质是爬虫聚合（东方财富/新浪/同花顺等），偶尔字段会变化。若某个函数报错，优先检查 akshare 版本是否为最新：
```bash
pip install akshare --upgrade
```

---

## 🔧 常见问题

### Q1：运行时报错"字段不匹配"
A：akshare 库更新后某些返回字段名可能变化。检查对应脚本中的字段名容错列表，必要时添加新字段名。

### Q2：美股/港股数据获取慢或失败
A：境内网络访问海外数据源不稳定。可以：
- 设置代理
- 使用 yfinance 作为备选
- 仅依赖 A 股数据（关闭 Step 1.5）

### Q3：节假日识别不准
A：确保安装了 `chinese-calendar`：
```bash
pip install chinese-calendar --upgrade
```
这个库每年会更新一次中国法定节假日，请保持最新。

### Q4：可否用于实盘自动交易？
A：**不建议**。本脚本集仅用于**数据获取和辅助决策**，不包含交易执行模块。
实盘交易应结合：
- 券商/基金公司官方 API
- 严格的风控系统
- 人工最终确认

### Q5：脚本的 v4.0 八项企稳信号如何解读？
A：严格对应 skill 文件的 6.1 第三步。核心逻辑：
- ≥6 项触发（或加权≥70%）→ 🟢 强企稳，可首笔建仓30%
- 4-5 项触发 → 🟡 弱企稳，小仓位试探（≤10%）
- 2-3 项 → 🟠 观察等待
- ≤1 项 → 🔴 禁止买入（哪怕估值再低也不接飞刀）

---

## 📝 版本记录

| 版本 | 日期       | 更新内容 |
| :--- | :--------- | :------- |
| v1.0 | 2026-04-22 | 初版：配套 skill v4.0 全 9 脚本 + 统一入口 |
| v6.18 | 2026-06-16 | 新增热点板块基金推荐：扫描行业/概念板块涨幅，匹配主题基金，并叠加回撤、同赛道排名和四维闸门过滤后输出候选基金 |
| v6.19 | 2026-06-17 | 新增夏普比率/波动率横向筛选：近1/3/6月计算风险收益指标，优先筛选同类前5%且夏普≥2的基金，并接入持仓横向对比和热点候选排序 |
| v6.23 | 2026-06-17 | 新增宏观熊市风险预警：PMI、社融/信贷脉冲代理、沪深300PE分位、ROE周期和数据新鲜度共同决定权益仓位上限；国家队减仓、北向流出、ETF份额下降均降级为辅助因子 |

---

## ⚠️ 免责声明

本脚本集仅供学习研究和辅助决策使用，不构成任何投资建议。

- 所有数据来自第三方 API，准确性不做保证
- 算法逻辑为简化版实现，实际市场远比模型复杂
- 基金投资有风险，过往业绩不代表未来收益
- 任何决策须结合个人财务状况、风险承受能力独立判断

**使用本脚本进行投资决策产生的任何损失，开发者不承担任何责任。**
