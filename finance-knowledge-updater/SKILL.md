---
name: finance-knowledge-updater
description: Build and periodically update a stock/fund research knowledge base from public internet sentiment, market snapshots, user-supplied trading methods, and watchlist performance. Use when asked to create, refresh, schedule, or analyze a finance/investment knowledge base, screen candidate stocks/funds, collect market hot topics, ingest trading playbooks, or maintain recurring investment research notes.
---

# Finance Knowledge Updater

## Purpose

Maintain a local research knowledge base for stocks, ETFs, and funds. The workflow collects public news/RSS sentiment, market snapshots, local trading-method notes, and watchlist performance, then produces ranked research candidates.

Treat every output as research support, not investment advice. Do not imply guaranteed returns, do not place trades, and do not scrape private/logged-in sources unless the user explicitly provides compliant access and asks for that source.

## Quick Start

Run the bundled updater from the workspace that owns the knowledge base:

```bash
python3 finance-knowledge-updater/scripts/update_knowledge_base.py \
  --config knowledge_base/config.json \
  --output knowledge_base
```

If `knowledge_base/config.json` does not exist, the script creates it from the default template. It also creates:

- `knowledge_base/data/finance_kb.sqlite`
- `knowledge_base/input/trading_methods.csv`
- `knowledge_base/raw/YYYY-MM-DD/*.jsonl`
- `knowledge_base/reports/YYYY-MM-DD.md`
- `knowledge_base/latest.md`
- `knowledge_base/candidates.csv`

For a no-network smoke test:

```bash
python3 finance-knowledge-updater/scripts/update_knowledge_base.py \
  --config knowledge_base/config.json \
  --output knowledge_base \
  --offline-sample
```

## Workflow

1. Inspect or create `knowledge_base/config.json`.
2. Ask the user for preferred markets only if unclear and risky. Otherwise default to China A-share/ETF plus configurable public RSS sources.
3. Add watchlist assets to `config.json`. Prefer explicit `symbol`, `name`, `asset_type`, `market`, and `provider`.
4. Add user trading methods to `knowledge_base/input/trading_methods.csv`.
5. Run `scripts/update_knowledge_base.py`.
6. Review `knowledge_base/latest.md` and `knowledge_base/candidates.csv`.
7. If the user asks for recurring updates, create a Codex cron automation that runs this skill in the owning workspace after market close on trading weekdays.

## Data Sources

The updater intentionally uses configurable public sources:

- `news_sources`: RSS/Atom URLs, including search-RSS endpoints.
- `watchlist`: assets to snapshot through supported providers.
- `input/trading_methods.csv`: local user-supplied trading methods.

Supported market providers in the script:

- `eastmoney_stock`: exchange-traded China stocks, indices, ETFs, and LOF-like instruments through Eastmoney quote API. Use symbols like `sh510300`, `sz159915`, `600519`, or explicit `secid`.
- `fundgz`: China mutual-fund estimated NAV endpoint by fund code.
- `stooq`: US/global quote CSV endpoint using Stooq symbols such as `aapl.us`.

Read `references/scoring-and-configuration.md` before making major changes to source selection, scoring weights, or schema.

## Trading Methods

Keep user methods local unless the user supplies a compliant source. The CSV columns are:

```text
user_label,method_name,timeframe,asset_scope,tags,rule_text,risk_control,source
```

Use `tags` to connect methods to themes, for example `AI算力;ETF;趋势`. The scorer gives a small fit bonus when candidate themes match method tags, but never treats a method as a buy signal by itself.

## Ranking Rules

Rankings combine:

- sentiment and mention intensity from recent public items
- hot theme matches
- latest market momentum when available
- risk keywords and negative event penalties
- local trading-method tag fit

Always mention source failures and risk flags when summarizing results. A high score means "research first", not "buy".

## Recurring Updates

When scheduling inside Codex, prefer a cron automation in the project workspace. A sensible default for China-market research is weekdays after market close, for example 18:30 Asia/Shanghai:

```text
Use $finance-knowledge-updater in this workspace to update the finance research knowledge base. Run the bundled updater against the local knowledge_base/config.json, refresh public news sentiment, market snapshots, trading-method ingestion, SQLite data, latest report, and candidate CSV. Summarize hot themes, top candidates, source failures, and risk flags. Treat outputs as research notes, not investment advice.
```

Do not create duplicate automations if one already exists for the same workspace and prompt.
