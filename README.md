# 金融交易知识库

本项目是一个本地运行的股票、基金投研知识库和可视化看板。它会抓取公开行情、财经新闻和舆情信息，结合自定义观察池、交易方法和持仓记录，生成候选池、热点热词、数据源状态、基金分析和持仓监控视图。

> 仅用于个人研究和交易复盘，不构成投资建议。

## 功能概览

- 股票、ETF、基金观察池配置
- 东方财富、天天基金、新浪财经、36Kr、AKShare、TuShare Pro 等数据源接入
- 热点舆情新闻摘要、热度分、热词匹配和来源状态
- 候选池评分、行动建议和风险提示
- 持仓录入、交易记录、盈亏统计和交易风格分析
- 基金分析师脚本集成
- 本地 Web 看板，默认地址 `http://127.0.0.1:8765/`

## 目录结构

```text
finance-dashboard/              # 本地可视化界面和 API 服务
finance-knowledge-updater/      # 知识库更新器和 Codex Skill
knowledge_base/config.json      # 数据源、观察池、主题关键词配置
external/fund-analyst/          # 基金分析师脚本集成
```

运行数据、数据库、抓取原文、个人持仓、交易记录和 Token 默认不会提交到 Git。

## 快速启动

```bash
python3 finance-dashboard/server.py --host 127.0.0.1 --port 8765
```

然后打开：

```text
http://127.0.0.1:8765/
```

## 更新知识库

```bash
python3 finance-knowledge-updater/scripts/update_knowledge_base.py \
  --config knowledge_base/config.json \
  --output knowledge_base
```

也可以在页面左下角点击更新按钮触发。

## 可选依赖

基础更新器尽量使用 Python 标准库。若需要 AKShare 新闻、基金分析等增强能力，建议在本地虚拟环境安装：

```bash
python3 -m venv .venv
.venv/bin/pip install akshare pandas numpy requests beautifulsoup4 lxml openpyxl
```

基金分析师集成的依赖可参考：

```bash
.venv/bin/pip install -r external/fund-analyst/requirements.txt
```

## TuShare Token

TuShare Pro 是可选数据源。Token 不应写入代码或提交到 Git。可以任选一种方式配置：

```bash
export TUSHARE_TOKEN="你的token"
```

或写入本地忽略文件：

```text
knowledge_base/input/tushare_token.txt
```

## 数据安全

`.gitignore` 已默认排除：

- `.venv/`
- `__pycache__/`
- `knowledge_base/input/*` 中的个人持仓、交易记录和 Token
- `knowledge_base/data/`
- `knowledge_base/raw/`
- `knowledge_base/reports/`
- `knowledge_base/fund_analyst/`
- 运行生成的候选池和最新报告

上传 GitHub 前建议再执行一次：

```bash
git status --short --untracked-files=all
```
