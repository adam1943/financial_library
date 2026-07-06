#!/usr/bin/env python3
"""Local dashboard for the finance research knowledge base."""

from __future__ import annotations

import csv
import datetime as dt
import json
import base64
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
KB_DIR = ROOT / "knowledge_base"
CONFIG_PATH = KB_DIR / "config.json"
DB_PATH = KB_DIR / "data" / "finance_kb.sqlite"
UPDATE_SCRIPT = ROOT / "finance-knowledge-updater" / "scripts" / "update_knowledge_base.py"
FUND_ANALYST_SCRIPT = ROOT / "finance-knowledge-updater" / "scripts" / "run_fund_analyst.py"
FUND_ANALYST_DIR = KB_DIR / "fund_analyst"
HOLDINGS_PATH = KB_DIR / "input" / "portfolio_holdings.csv"
TRADES_PATH = KB_DIR / "input" / "portfolio_trades.csv"
SCREENSHOT_DIR = KB_DIR / "input" / "screenshots"
RECENT_HISTORY_DAYS = 7
DEFAULT_AUTO_UPDATE_STALE_HOURS = 6
LIVE_QUOTE_TTL_SECONDS = 300
LIVE_QUOTE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

UPDATE_LOCK = threading.Lock()
UPDATE_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "mode": None,
    "stdout": "",
    "stderr": "",
}

FUND_ANALYST_LOCK = threading.Lock()
FUND_ANALYST_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "stdout": "",
    "stderr": "",
}


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(read_text(path, ""))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_request_json(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


SOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "akshare-stock-news-em",
        "name": "AKShare-东方财富财经新闻",
        "type": "akshare_stock_news_em",
        "url": "akshare://stock_news_em",
        "enabled": True,
        "max_items": 50,
    },
    {
        "id": "36kr-tech-finance",
        "name": "36Kr-科技财经快讯",
        "type": "rss",
        "url": "https://36kr.com/feed",
        "enabled": True,
    },
    {
        "id": "sina-finance-roll",
        "name": "新浪财经-滚动财经",
        "type": "json_sina_roll",
        "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=30&page=1",
        "enabled": True,
    },
    {
        "id": "sina-a-share-market",
        "name": "新浪财经-A股市场",
        "type": "json_sina_roll",
        "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&num=30&page=1",
        "enabled": True,
    },
    {
        "id": "eastmoney-fastnews",
        "name": "东方财富-7x24快讯",
        "type": "json_eastmoney_fastnews",
        "url": "https://eminfo.eastmoney.com/pc_news/FastNews/GetInfoList?code=100&pageNumber=1&pageSize=30",
        "enabled": True,
    },
]


PROVIDER_TEMPLATES: list[dict[str, str]] = [
    {"provider": "eastmoney_stock", "label": "东方财富行情", "hint": "A股股票/指数/ETF，如 sh600519、sz300750、sh510300"},
    {"provider": "tushare_daily", "label": "TuShare Pro 日线", "hint": "A股日线收盘价，如 600519.SH、sh600519；需设置 TUSHARE_TOKEN"},
    {"provider": "fundgz", "label": "天天基金估算净值", "hint": "场外基金代码，如 161725"},
    {"provider": "stooq", "label": "Stooq 全球行情", "hint": "美股/ETF，如 aapl.us、spy.us"},
]


SOURCE_CAPABILITIES: list[dict[str, str]] = [
    {"name": "东方财富 push2 实时行情", "category": "行情", "status": "已接入", "detail": "当前 eastmoney_stock provider 已用于 A股、指数、ETF 最新价、涨跌幅、成交额。"},
    {"name": "天天基金 fundgz 估算净值", "category": "行情", "status": "已接入", "detail": "当前 fundgz provider 已用于场外基金估算净值和估算涨跌幅。"},
    {"name": "AKShare stock_news_em", "category": "舆情", "status": "已接入", "detail": "当前本地 .venv 已安装 AKShare，并接入东方财富财经新闻封装；若依赖不可用会在来源状态里单独提示。"},
    {"name": "RSS/Atom/JSON 新闻源", "category": "舆情", "status": "已接入", "detail": "36Kr RSS、新浪财经 feed.mix JSON、东方财富快讯 JSON 和自定义 RSS/Atom 可进入新闻舆情解析。"},
    {"name": "东方财富 push2his 历史K线", "category": "历史行情", "status": "待适配", "detail": "入口可作为回测和持仓风控基础，但当前更新器尚未入库 K 线。"},
    {"name": "FRED 宏观 CSV", "category": "宏观", "status": "待适配", "detail": "可用于美债、通胀、美元等宏观序列；当前尚未参与评分。"},
    {"name": "SEC EDGAR", "category": "公告", "status": "可选", "detail": "适合美股披露，需要规范 User-Agent、限速和更长超时；不默认启用。"},
    {"name": "TuShare Pro HTTP", "category": "行情", "status": "已接入", "detail": "当前可通过 tushare_daily provider 获取 A 股日线收盘价和涨跌幅；无需安装 SDK，但需设置 TUSHARE_TOKEN。"},
    {"name": "AKShare 新浪/财联社扩展", "category": "舆情", "status": "待适配", "detail": "当前安装版本未暴露 stock_news_sina / stock_news_cls；先不默认启用，后续可按版本函数名或官方封装补充。"},
    {"name": "基金分析师模块", "category": "基金", "status": "已接入", "detail": "已隔离接入基金分析师 zip 的强势基金筛选、回撤画像和风险收益框架；缓存与历史输出未导入主库。"},
    {"name": "巨潮 / 交易所公告", "category": "公告", "status": "待适配", "detail": "适合公告、财报和风险事件，建议后续作为风险源单独接入。"},
    {"name": "微博 / 雪球 / 小红书直抓", "category": "社媒", "status": "谨慎", "detail": "账号、反爬和合规边界较强，建议只走授权、RSSHub 或人工导入。"},
]

HOLDING_FIELDS = [
    "symbol",
    "name",
    "asset_type",
    "market",
    "provider",
    "quantity",
    "cost_price",
    "cost_value",
    "current_price",
    "holding_amount",
    "notes",
    "updated_at",
]

TRADE_FIELDS = [
    "trade_date",
    "symbol",
    "name",
    "asset_type",
    "side",
    "quantity",
    "price",
    "fee",
    "amount",
    "pnl",
    "reason",
    "tags",
]


def read_candidates() -> list[dict[str, Any]]:
    path = KB_DIR / "candidates.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    try:
        snapshots = latest_snapshot_lookup()
    except Exception:
        snapshots = {}
    numeric_fields = [
        "price",
        "change_pct",
        "total_score",
        "sentiment_score",
        "momentum_score",
        "method_fit_score",
        "risk_penalty",
        "mention_count",
        "heat_score",
    ]
    for row in rows:
        for field in numeric_fields:
            value = row.get(field)
            if value in (None, ""):
                row[field] = None
                continue
            try:
                row[field] = float(value)
                if field == "mention_count":
                    row[field] = int(row[field])
            except ValueError:
                row[field] = None
        row["themes_list"] = [part for part in str(row.get("themes") or "").split(";") if part]
        row["news_sources_list"] = [part for part in str(row.get("news_sources") or "").split(";") if part]
        row["matched_news_list"] = [part.strip() for part in str(row.get("matched_news") or "").split("|") if part.strip()]
        snapshot = next(
            (snapshots[key] for key in symbol_keys(row.get("symbol"), str(row.get("provider") or ""), str(row.get("asset_type") or "")) if key in snapshots),
            None,
        )
        if snapshot and row.get("price") is None:
            row["price"] = snapshot.get("price")
            row["change_pct"] = snapshot.get("change_pct")
            row["provider"] = row.get("provider") or snapshot.get("provider")
            row["cached_snapshot_at"] = snapshot.get("snapshot_at")
            row["data_status"] = "quote_cached"
        status = row.get("data_status") or ""
        has_quote = status in {"quote_ok", "quote_cached"}
        row["confidence"] = "高" if has_quote and row["matched_news_list"] else "中" if has_quote or row["matched_news_list"] else "低"
        row["action"] = row.get("action") or infer_candidate_action(row)
        row["action_reason"] = row.get("action_reason") or ""
    rows.sort(
        key=lambda item: (
            item.get("heat_score") or 0,
            item.get("total_score") or 0,
            item.get("data_status") in {"quote_ok", "quote_cached"},
            item.get("mention_count") or 0,
        ),
        reverse=True,
    )
    return rows


def infer_candidate_action(row: dict[str, Any]) -> str:
    risk = float(row.get("risk_penalty") or 0)
    total = float(row.get("total_score") or 0)
    mentions = int(row.get("mention_count") or 0)
    change = float(row.get("change_pct") or 0)
    has_quote = row.get("data_status") in {"quote_ok", "quote_cached"}
    if risk >= 16:
        return "谨慎回避"
    if not has_quote:
        return "仅作线索"
    if total >= 68 and mentions >= 2 and change >= 0 and risk <= 8:
        return "积极跟踪"
    if total >= 58 and risk <= 10:
        return "建仓观察"
    if total >= 45:
        return "持续关注"
    return "暂缓"


def db_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def parse_json_field(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today_text() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


def history_cutoff(days: int = RECENT_HISTORY_DAYS) -> str:
    return (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat(timespec="seconds")


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "").replace("%", "")
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100000000.0
        text = text[:-1]
    if not text or text in {"-", "--", "None", "null"}:
        return default
    try:
        return float(text) * multiplier
    except ValueError:
        return default


def split_tags(value: Any) -> list[str]:
    return [part for part in re.split(r"[;,，；\s]+", str(value or "")) if part]


def normalize_symbol(value: Any, provider: str = "", asset_type: str = "") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace(" ", "")
    provider_l = provider.lower()
    if provider_l == "tushare_daily":
        if re.fullmatch(r"\d{6}\.(sh|sz|bj)", raw):
            return raw
        if re.fullmatch(r"(sh|sz|bj)\d{6}", raw):
            return raw[2:] + f".{raw[:2]}"
        digits_for_ts = re.sub(r"\D", "", raw)
        if len(digits_for_ts) == 6:
            if digits_for_ts.startswith(("6", "5", "9")):
                return f"{digits_for_ts}.sh"
            if digits_for_ts.startswith(("4", "8")):
                return f"{digits_for_ts}.bj"
            return f"{digits_for_ts}.sz"
    if "." in raw and not re.fullmatch(r"\d+\.\d+", raw):
        return raw
    if raw.startswith(("sh", "sz")):
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6:
        if provider_l == "fundgz":
            return digits
        if provider_l == "eastmoney_stock":
            return f"sh{digits}" if digits.startswith(("5", "6", "9")) else f"sz{digits}"
        if asset_type == "fund" and digits.startswith(("16", "00", "11")):
            return digits
        return f"sh{digits}" if digits.startswith(("5", "6", "9")) else f"sz{digits}"
    return raw


def infer_provider(symbol: str, asset_type: str = "", provider: str = "") -> str:
    if provider:
        return provider
    raw = str(symbol or "").strip().lower()
    digits = re.sub(r"\D", "", raw)
    if re.fullmatch(r"\d{6}\.(sh|sz|bj)", raw):
        return "tushare_daily"
    if raw.endswith(".us"):
        return "stooq"
    if digits.startswith(("16", "11")) and not raw.startswith(("sh", "sz")):
        return "fundgz"
    if asset_type == "fund" and digits.startswith(("00",)) and not raw.startswith(("sh", "sz")):
        return "fundgz"
    return "eastmoney_stock"


def normalize_asset_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"基金", "etf", "lof", "fund", "基金/etf"}:
        return "fund"
    if text in {"指数", "index"}:
        return "index"
    if text in {"stock", "股票", "a股", "美股"}:
        return "stock"
    return text or "stock"


def infer_market(symbol: str, provider: str = "") -> str:
    raw = str(symbol or "").strip().lower()
    if provider == "stooq" or raw.endswith(".us"):
        return "US"
    if provider == "tushare_daily" or re.fullmatch(r"\d{6}\.(sh|sz|bj)", raw):
        return "CN"
    return "CN"


def classify_status_name(name: str) -> str:
    if re.search(r"(新闻|rss|atom|36kr|google|source)", name, re.I):
        return "news"
    if re.search(r"(^[a-z]{0,2}\d{6}|ETF|指数|时代|茅台|比亚迪|中际|富联|中芯|药明|格力)", name, re.I):
        return "market"
    return "news"


def summarize_news_item(item: dict[str, Any]) -> dict[str, Any]:
    summary = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "..."
        item["summary"] = summary
    text = summary or title
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s+|[。！？!?；;]", text) if part.strip()]
    key_points = parts[:2] if parts else [title] if title else []
    themes = item.get("themes") or []
    symbols = item.get("symbols") or []
    sentiment = float(item.get("sentiment_score") or 0)
    risk_count = int(item.get("risk_count") or 0)
    if themes:
        key_points.append("主题: " + " / ".join(str(theme) for theme in themes[:4]))
    if symbols:
        key_points.append("提及标的: " + " / ".join(str(symbol) for symbol in symbols[:5]))
    if risk_count > 0:
        key_points.append(f"含 {item.get('risk_count')} 个风险关键词，需核验公告和原文。")
    heat_score = min(100.0, max(0.0, 18.0 + len(themes) * 13.0 + len(symbols) * 8.0 + max(sentiment, 0.0) * 6.0 - risk_count * 8.0))
    item["heat_score"] = round(heat_score, 2)
    item["key_points"] = key_points[:4]
    return item


def enrich_source_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in rows:
        item["themes"] = parse_json_field(item.pop("themes_json", None), [])
        item["symbols"] = parse_json_field(item.pop("symbols_json", None), [])
        item["source_label"] = item.get("source") or "unknown"
        summarize_news_item(item)
    rows.sort(key=lambda item: (float(item.get("heat_score") or 0), str(item.get("published_at") or item.get("fetched_at") or "")), reverse=True)
    return rows


def recent_source_items(limit: int = 80, days: int = RECENT_HISTORY_DAYS) -> list[dict[str, Any]]:
    rows = db_rows(
        """
        SELECT id, fetched_at, published_at, source, title, url, summary, sentiment_score, risk_count, themes_json, symbols_json
        FROM source_items
        WHERE fetched_at >= ?
        ORDER BY fetched_at DESC, published_at DESC
        LIMIT ?
        """,
        (history_cutoff(days), limit),
    )
    if not rows:
        rows = db_rows(
            """
            SELECT id, fetched_at, published_at, source, title, url, summary, sentiment_score, risk_count, themes_json, symbols_json
            FROM source_items
            WHERE fetched_at = (SELECT MAX(fetched_at) FROM source_items)
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    return enrich_source_items(rows)


def latest_attempt_meta() -> dict[str, Any]:
    latest = KB_DIR / "latest.md" if (KB_DIR / "latest.md").exists() else None
    attempt = KB_DIR / "latest_attempt.md" if (KB_DIR / "latest_attempt.md").exists() else None
    return {
        "latest_file": str(latest) if latest else None,
        "latest_mtime": dt.datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec="seconds") if latest else None,
        "attempt_file": str(attempt) if attempt else None,
        "attempt_mtime": dt.datetime.fromtimestamp(attempt.stat().st_mtime).isoformat(timespec="seconds") if attempt else None,
    }


def source_status_payload() -> dict[str, Any]:
    payload = read_json(KB_DIR / "source_status.json", {})
    statuses = payload.get("statuses") or []
    for status in statuses:
        status.setdefault("category", classify_status_name(str(status.get("name") or "")))
        if payload.get("run_at"):
            status.setdefault("run_at", payload.get("run_at"))
    if not statuses:
        rows = db_rows(
            """
            SELECT run_at, category, name, ok, detail, count
            FROM source_status
            ORDER BY run_at DESC
            LIMIT 80
            """
        )
        statuses = [
            {
                "name": row["name"],
                "ok": bool(row["ok"]),
                "detail": row["detail"],
                "count": row["count"],
                "category": row["category"],
                "run_at": row["run_at"],
            }
            for row in rows
        ]
        payload = {
            "run_at": rows[0]["run_at"] if rows else None,
            "ok_count": len([row for row in statuses if row.get("ok")]),
            "fail_count": len([row for row in statuses if not row.get("ok")]),
            "statuses": statuses,
        }
    history_rows = db_rows(
        """
        SELECT run_at, category, name, ok, detail, count
        FROM source_status
        WHERE run_at >= ?
        ORDER BY run_at DESC, name ASC
        LIMIT 240
        """,
        (history_cutoff(),),
    )
    history = [
        {
            "run_at": row["run_at"],
            "category": row["category"],
            "name": row["name"],
            "ok": bool(row["ok"]),
            "detail": row["detail"],
            "count": row["count"],
        }
        for row in history_rows
    ]
    payload["history"] = history
    payload["failed_history"] = [item for item in history if not item["ok"]]
    payload["recent_days"] = RECENT_HISTORY_DAYS
    payload["history_note"] = "历史失败用于判断来源稳定性，不代表当前更新失败。当前失败以 fail_count 为准。"
    return payload


def heat_keywords(source_items: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_by_symbol: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for key in symbol_keys(candidate.get("symbol"), str(candidate.get("provider") or ""), str(candidate.get("asset_type") or "")):
            candidate_by_symbol[key] = candidate
    buckets: dict[str, dict[str, Any]] = {}
    for item in source_items:
        sentiment = float(item.get("sentiment_score") or 0)
        risk = int(item.get("risk_count") or 0)
        source = str(item.get("source_label") or item.get("source") or "")
        terms: list[tuple[str, str]] = []
        terms.extend(("theme", str(theme)) for theme in item.get("themes") or [])
        for symbol in item.get("symbols") or []:
            candidate = next((candidate_by_symbol[key] for key in symbol_keys(symbol) if key in candidate_by_symbol), None)
            label = str(candidate.get("name") or symbol) if candidate else str(symbol)
            terms.append(("symbol", label))
        for term_type, label in terms:
            if not label:
                continue
            bucket = buckets.setdefault(label, {"name": label, "type": term_type, "count": 0, "sources": set(), "symbols": set(), "sentiment": 0.0, "risk": 0, "titles": []})
            bucket["count"] += 1
            bucket["sources"].add(source)
            bucket["sentiment"] += sentiment
            bucket["risk"] += risk
            if term_type == "symbol":
                bucket["symbols"].add(label)
            elif item.get("symbols"):
                bucket["symbols"].update(str(symbol) for symbol in item.get("symbols") or [])
            if item.get("title"):
                bucket["titles"].append(str(item["title"]))
    result: list[dict[str, Any]] = []
    for bucket in buckets.values():
        score = min(100.0, bucket["count"] * 6.0 + len(bucket["sources"]) * 7.0 + max(bucket["sentiment"], 0.0) * 0.9 - bucket["risk"] * 5.0)
        result.append({
            "name": bucket["name"],
            "type": bucket["type"],
            "heat_score": round(score, 2),
            "count": bucket["count"],
            "source_count": len(bucket["sources"]),
            "sentiment": round(bucket["sentiment"], 2),
            "risk": bucket["risk"],
            "symbols": sorted(bucket["symbols"])[:6],
            "titles": bucket["titles"][:3],
        })
    result.sort(key=lambda item: (item["heat_score"], item["count"], item["source_count"]), reverse=True)
    return result[:16]


def market_pulse(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = ["sh000001", "sz399001", "sz399006", "sh510300", "sh510500", "sh588000", "sh512760", "sz159915"]
    lookup: dict[str, dict[str, Any]] = {}
    for row in snapshots:
        for key in symbol_keys(row.get("symbol"), str(row.get("provider") or ""), str(row.get("asset_type") or "")):
            lookup[key] = row
    pulse = []
    for symbol in preferred:
        row = next((lookup[key] for key in symbol_keys(symbol) if key in lookup), None)
        if row:
            pulse.append(row)
    return pulse[:8]


def summary_payload() -> dict[str, Any]:
    candidates = read_candidates()
    config = read_json(CONFIG_PATH, {})
    source_items = recent_source_items(120)
    snapshots = db_rows(
        """
        SELECT ms.snapshot_at, ms.symbol, ms.name, ms.asset_type, ms.market, ms.provider, ms.price, ms.change_pct, ms.volume, ms.amount
        FROM market_snapshots ms
        JOIN (
            SELECT symbol, MAX(snapshot_at) AS latest_snapshot
            FROM market_snapshots
            GROUP BY symbol
        ) latest
        ON latest.symbol = ms.symbol AND latest.latest_snapshot = ms.snapshot_at
        ORDER BY ms.snapshot_at DESC, ms.symbol ASC
        LIMIT 120
        """
    )
    methods = db_rows(
        """
        SELECT user_label, method_name, timeframe, asset_scope, tags_json, rule_text, risk_control, source
        FROM trading_methods
        ORDER BY ingested_at DESC
        LIMIT 30
        """
    )
    for method in methods:
        method["tags"] = parse_json_field(method.pop("tags_json", None), [])

    theme_counts: dict[str, dict[str, float]] = {}
    for row in candidates:
        for theme in row.get("themes_list", []):
            bucket = theme_counts.setdefault(theme, {"count": 0, "score": 0.0})
            bucket["count"] += 1
            bucket["score"] += float(row.get("total_score") or 0)
    themes = [
        {"name": name, "count": int(value["count"]), "avg_score": round(value["score"] / max(value["count"], 1), 2)}
        for name, value in theme_counts.items()
    ]
    themes.sort(key=lambda item: (item["count"], item["avg_score"]), reverse=True)

    risk_items = [item for item in source_items if int(item.get("risk_count") or 0) > 0]
    top_score = candidates[0]["total_score"] if candidates else None
    avg_score = round(sum(float(item.get("total_score") or 0) for item in candidates) / len(candidates), 2) if candidates else None
    positive = len([item for item in candidates if (item.get("change_pct") or 0) > 0])
    source_status = source_status_payload()
    supported = [item for item in candidates if item.get("data_status") in {"quote_ok", "quote_cached"} or item.get("mention_count")]
    priority_candidates = [
        item
        for item in candidates
        if item.get("data_status") in {"quote_ok", "quote_cached"}
        and (item.get("action") or infer_candidate_action(item)) not in {"谨慎回避", "暂缓"}
    ]
    priority_candidates.sort(key=lambda item: (item.get("heat_score") or 0, item.get("total_score") or 0), reverse=True)
    stocks = [item for item in candidates if item.get("asset_type") == "stock"]
    funds = [item for item in candidates if item.get("asset_type") == "fund"]
    status_counts: dict[str, int] = {}
    for item in candidates:
        status_counts[item.get("data_status") or "unknown"] = status_counts.get(item.get("data_status") or "unknown", 0) + 1
    dashboard_config = config.get("dashboard") or {}
    stale_hours = safe_float(dashboard_config.get("auto_update_stale_hours"), DEFAULT_AUTO_UPDATE_STALE_HOURS) or DEFAULT_AUTO_UPDATE_STALE_HOURS
    latest_path = KB_DIR / "latest_attempt.md" if (KB_DIR / "latest_attempt.md").exists() else KB_DIR / "latest.md"
    latest_mtime = latest_path.stat().st_mtime if latest_path.exists() else 0
    age_hours = round((dt.datetime.now().timestamp() - latest_mtime) / 3600, 2) if latest_mtime else None
    auto_update_enabled = bool(dashboard_config.get("auto_update_on_open", True))

    return {
        "meta": latest_attempt_meta(),
        "counts": {
            "candidates": len(candidates),
            "watchlist": len(config.get("watchlist", [])),
            "news_sources": len(config.get("news_sources", [])),
            "methods": len(methods),
            "source_items": len(source_items),
            "snapshots": len(snapshots),
            "stocks": len(stocks),
            "funds": len(funds),
            "supported": len(supported),
            "source_failures": int(source_status.get("fail_count") or 0),
        },
        "metrics": {
            "top_score": top_score,
            "avg_score": avg_score,
            "positive_momentum": positive,
            "risk_items": len(risk_items),
            "source_ok": int(source_status.get("ok_count") or 0),
            "source_fail": int(source_status.get("fail_count") or 0),
        },
        "top_candidates": candidates[:8],
        "priority_candidates": priority_candidates[:8],
        "top_stocks": stocks[:8],
        "top_funds": funds[:8],
        "themes": themes[:8],
        "heat_keywords": heat_keywords(source_items, candidates),
        "risk_items": risk_items[:8],
        "news_items": source_items[:8],
        "recent_news_items": source_items[:120],
        "snapshots": snapshots[:8],
        "market_pulse": market_pulse(snapshots),
        "methods": methods,
        "source_status": source_status,
        "status_counts": status_counts,
        "auto_update": {
            "enabled": auto_update_enabled,
            "stale_hours": stale_hours,
            "age_hours": age_hours,
            "should_update": bool(auto_update_enabled and (age_hours is None or age_hours >= stale_hours)),
        },
        "update_state": dict(UPDATE_STATE),
    }


def ensure_csv_file(path: Path, fields: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fields).writeheader()


def read_csv_records(path: Path, fields: list[str]) -> list[dict[str, Any]]:
    ensure_csv_file(path, fields)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [{field: row.get(field, "") for field in fields} for row in rows]


def write_csv_records(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def append_csv_record(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    ensure_csv_file(path, fields)
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writerow({field: row.get(field, "") for field in fields})


def symbol_keys(symbol: Any, provider: str = "", asset_type: str = "") -> set[str]:
    raw = str(symbol or "").strip().lower()
    norm = normalize_symbol(raw, provider, asset_type)
    digits = re.sub(r"\D", "", raw)
    keys = {raw, norm}
    if digits:
        keys.add(digits)
        keys.add(f"sh{digits}")
        keys.add(f"sz{digits}")
        keys.add(f"{digits}.sh")
        keys.add(f"{digits}.sz")
        keys.add(f"{digits}.bj")
    return {key for key in keys if key}


def latest_snapshot_lookup() -> dict[str, dict[str, Any]]:
    rows = db_rows(
        """
        SELECT ms.snapshot_at, ms.symbol, ms.name, ms.asset_type, ms.market, ms.provider, ms.price, ms.change_pct, ms.volume, ms.amount
        FROM market_snapshots ms
        JOIN (
            SELECT symbol, MAX(snapshot_at) AS latest_snapshot
            FROM market_snapshots
            GROUP BY symbol
        ) latest
        ON latest.symbol = ms.symbol AND latest.latest_snapshot = ms.snapshot_at
        """
    )
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in symbol_keys(row.get("symbol"), str(row.get("provider") or ""), str(row.get("asset_type") or "")):
            lookup[key] = row
    return lookup


def candidate_lookup() -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in read_candidates():
        for key in symbol_keys(row.get("symbol"), str(row.get("provider") or ""), str(row.get("asset_type") or "")):
            lookup[key] = row
    return lookup


def cached_quote_for_symbol(symbol: str, provider: str, asset_type: str) -> dict[str, Any]:
    keys = symbol_keys(symbol, provider, asset_type)
    try:
        snapshots = latest_snapshot_lookup()
        snapshot = next((snapshots[key] for key in keys if key in snapshots), None)
        if snapshot:
            return {**snapshot, "_source": "latest_quote"}
    except Exception:
        pass
    try:
        candidates = candidate_lookup()
        candidate = next((candidates[key] for key in keys if key in candidates), None)
        if candidate and candidate.get("price") is not None:
            return {**candidate, "_source": "candidate_price"}
    except Exception:
        pass
    return {}


def fetch_text(url: str, timeout: int = 8, data: bytes | None = None, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 finance-dashboard/1.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Connection": "close",
            **(headers or {}),
        },
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def eastmoney_secid(symbol: str) -> str:
    raw = str(symbol or "").strip().lower()
    if raw.startswith("sh"):
        return "1." + raw[2:]
    if raw.startswith("sz"):
        return "0." + raw[2:]
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError("missing code")
    return ("1." if digits.startswith(("5", "6", "9")) else "0.") + digits


def fetch_eastmoney_live_quote(symbol: str, asset_type: str) -> dict[str, Any]:
    secid = eastmoney_secid(symbol)
    fields = "f43,f57,f58,f60,f170,f47,f48"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={urllib.parse.quote(secid)}&fields={fields}"
    raw = fetch_text(url, timeout=6)
    data = json.loads(raw).get("data") or {}
    if not data:
        raise ValueError("empty eastmoney quote")
    price_scale = 1000.0 if asset_type == "fund" else 100.0
    price = safe_float(data.get("f43"), None)
    if price is not None:
        price /= price_scale
    change_pct = safe_float(data.get("f170"), None)
    if change_pct is not None:
        change_pct /= 100.0
    return {
        "symbol": normalize_symbol(symbol, "eastmoney_stock", asset_type),
        "name": data.get("f58"),
        "provider": "eastmoney_stock",
        "price": price,
        "change_pct": change_pct,
        "amount": safe_float(data.get("f48"), None),
        "_source": "live_eastmoney",
    }


def tushare_code(symbol: str) -> str:
    raw = str(symbol or "").strip().lower().replace(" ", "")
    if re.fullmatch(r"\d{6}\.(sh|sz|bj)", raw):
        return raw.upper()
    if re.fullmatch(r"(sh|sz|bj)\d{6}", raw):
        return f"{raw[2:]}.{raw[:2].upper()}"
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 6:
        raise ValueError("cannot infer tushare code")
    if digits.startswith(("6", "5", "9")):
        return f"{digits}.SH"
    if digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    return f"{digits}.SZ"


def read_tushare_token() -> str:
    for env_name in ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN"):
        token = os.environ.get(env_name)
        if token:
            return token.strip()
    token_file = KB_DIR / "input" / "tushare_token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    return ""


def fetch_tushare_daily_quote(symbol: str) -> dict[str, Any]:
    token = read_tushare_token()
    if not token:
        raise ValueError("missing tushare token")
    today = dt.date.today()
    payload = {
        "api_name": "daily",
        "token": token,
        "params": {"ts_code": tushare_code(symbol), "start_date": (today - dt.timedelta(days=14)).strftime("%Y%m%d"), "end_date": today.strftime("%Y%m%d")},
        "fields": "ts_code,trade_date,close,pct_chg,vol,amount",
    }
    raw = fetch_text(
        "https://api.tushare.pro",
        timeout=8,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    data = json.loads(raw)
    if data.get("code") not in (0, "0", None):
        raise ValueError(str(data.get("msg") or "tushare error"))
    result = data.get("data") or {}
    fields = result.get("fields") or []
    rows = [dict(zip(fields, item)) for item in result.get("items") or []]
    if not rows:
        raise ValueError("empty tushare quote")
    row = max(rows, key=lambda item: str(item.get("trade_date") or ""))
    return {
        "symbol": normalize_symbol(symbol, "tushare_daily", "stock"),
        "provider": "tushare_daily",
        "price": safe_float(row.get("close"), None),
        "change_pct": safe_float(row.get("pct_chg"), None),
        "amount": safe_float(row.get("amount"), None),
        "_source": "live_tushare",
    }


def fetch_fundgz_live_quote(symbol: str) -> dict[str, Any]:
    code = re.sub(r"\D", "", str(symbol or ""))
    raw = fetch_text(f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}", timeout=6)
    match = re.search(r"\((\{.*\})\)", raw)
    if not match:
        raise ValueError("empty fundgz quote")
    data = json.loads(match.group(1))
    return {
        "symbol": code,
        "name": data.get("name"),
        "provider": "fundgz",
        "price": safe_float(data.get("gsz") or data.get("dwjz"), None),
        "change_pct": safe_float(data.get("gszzl"), None),
        "_source": "live_fundgz",
    }


def live_quote_for_holding(symbol: str, provider: str, asset_type: str) -> dict[str, Any]:
    cache_key = f"{provider}:{asset_type}:{symbol}".lower()
    cached = LIVE_QUOTE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < LIVE_QUOTE_TTL_SECONDS:
        return cached[1]
    quote: dict[str, Any] = {}
    try:
        if provider == "fundgz" or (asset_type == "fund" and not str(symbol).lower().startswith(("sh", "sz"))):
            quote = fetch_fundgz_live_quote(symbol)
        else:
            quote = fetch_eastmoney_live_quote(symbol, asset_type)
    except Exception:
        digits = re.sub(r"\D", "", str(symbol or ""))
        if asset_type == "stock" and len(digits) == 6 and digits.startswith(("0", "3", "6")):
            try:
                quote = fetch_tushare_daily_quote(symbol)
            except Exception:
                quote = {}
    LIVE_QUOTE_CACHE[cache_key] = (time.time(), quote)
    return quote


def normalize_holding(payload: dict[str, Any]) -> dict[str, Any]:
    asset_type = normalize_asset_type(payload.get("asset_type") or "stock")
    raw_symbol = str(payload.get("symbol") or "").strip().lower()
    if asset_type == "stock" and re.fullmatch(r"(16|11)\d{4}", raw_symbol):
        asset_type = "fund"
    provider = infer_provider(str(payload.get("symbol") or ""), asset_type, str(payload.get("provider") or ""))
    symbol = normalize_symbol(payload.get("symbol"), provider, asset_type)
    if not symbol:
        raise ValueError("symbol is required")
    quantity = safe_float(payload.get("quantity"), 0.0) or 0.0
    cost_price = safe_float(payload.get("cost_price"), 0.0) or 0.0
    cost_value = safe_float(payload.get("cost_value"), None)
    holding_amount = safe_float(payload.get("holding_amount"), None)
    current_price = safe_float(payload.get("current_price"), None)
    if current_price is None:
        cached_quote = cached_quote_for_symbol(symbol, provider, asset_type)
        current_price = safe_float(cached_quote.get("price"), None)
    if current_price is None:
        live_quote = live_quote_for_holding(symbol, provider, asset_type)
        current_price = safe_float(live_quote.get("price"), None)
    if cost_value is None and quantity and cost_price:
        cost_value = quantity * cost_price
    if holding_amount is None and quantity and current_price is not None:
        holding_amount = quantity * current_price
    return {
        "symbol": symbol,
        "name": str(payload.get("name") or symbol).strip(),
        "asset_type": asset_type,
        "market": str(payload.get("market") or infer_market(symbol, provider)).strip(),
        "provider": provider,
        "quantity": quantity,
        "cost_price": cost_price,
        "cost_value": "" if cost_value is None else round(cost_value, 4),
        "current_price": "" if current_price is None else current_price,
        "holding_amount": "" if holding_amount is None else round(holding_amount, 4),
        "notes": str(payload.get("notes") or "").strip(),
        "updated_at": now_iso(),
    }


def upsert_holding(payload: dict[str, Any]) -> dict[str, Any]:
    holding = normalize_holding(payload)
    rows = read_csv_records(HOLDINGS_PATH, HOLDING_FIELDS)
    key = (holding["symbol"].lower(), holding["provider"].lower())
    replaced = False
    for index, row in enumerate(rows):
        row_key = (normalize_symbol(row.get("symbol"), row.get("provider", ""), row.get("asset_type", "")).lower(), str(row.get("provider") or "").lower())
        if row_key == key:
            rows[index] = holding
            replaced = True
            break
    if not replaced:
        rows.append(holding)
    write_csv_records(HOLDINGS_PATH, HOLDING_FIELDS, rows)
    return holding


def remove_holding(payload: dict[str, Any]) -> int:
    symbol = normalize_symbol(payload.get("symbol"), str(payload.get("provider") or ""), str(payload.get("asset_type") or ""))
    provider = str(payload.get("provider") or "").lower()
    rows = read_csv_records(HOLDINGS_PATH, HOLDING_FIELDS)
    kept = []
    removed = 0
    for row in rows:
        same_symbol = normalize_symbol(row.get("symbol"), row.get("provider", ""), row.get("asset_type", "")).lower() == symbol.lower()
        same_provider = not provider or str(row.get("provider") or "").lower() == provider
        if same_symbol and same_provider:
            removed += 1
        else:
            kept.append(row)
    write_csv_records(HOLDINGS_PATH, HOLDING_FIELDS, kept)
    return removed


def normalize_trade(payload: dict[str, Any]) -> dict[str, Any]:
    asset_type = normalize_asset_type(payload.get("asset_type") or "stock")
    symbol = normalize_symbol(payload.get("symbol"), "", asset_type)
    if not symbol:
        raise ValueError("symbol is required")
    quantity = safe_float(payload.get("quantity"), 0.0) or 0.0
    price = safe_float(payload.get("price"), 0.0) or 0.0
    fee = safe_float(payload.get("fee"), 0.0) or 0.0
    amount = safe_float(payload.get("amount"), None)
    side = str(payload.get("side") or "buy").strip().lower()
    if side not in {"buy", "sell", "dividend", "transfer", "adjust"}:
        side = "buy"
    if amount is None:
        amount = quantity * price + fee if side == "buy" else quantity * price - fee
    return {
        "trade_date": str(payload.get("trade_date") or today_text()).strip(),
        "symbol": symbol,
        "name": str(payload.get("name") or symbol).strip(),
        "asset_type": asset_type,
        "side": side,
        "quantity": quantity,
        "price": price,
        "fee": fee,
        "amount": round(float(amount), 4),
        "pnl": "" if payload.get("pnl") in (None, "") else safe_float(payload.get("pnl"), 0.0),
        "reason": str(payload.get("reason") or "").strip(),
        "tags": str(payload.get("tags") or "").strip(),
    }


def add_trade(payload: dict[str, Any]) -> dict[str, Any]:
    trade = normalize_trade(payload)
    append_csv_record(TRADES_PATH, TRADE_FIELDS, trade)
    return trade


def source_matches_holding(item: dict[str, Any], holding: dict[str, Any]) -> bool:
    keys = symbol_keys(holding.get("symbol"), holding.get("provider", ""), holding.get("asset_type", ""))
    symbols = {str(symbol).lower() for symbol in item.get("symbols") or []}
    if keys & symbols:
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    name = str(holding.get("name") or "").lower()
    return any(key and key in text for key in keys) or bool(name and name in text)


def analyze_portfolio() -> dict[str, Any]:
    raw_holdings = read_csv_records(HOLDINGS_PATH, HOLDING_FIELDS)
    trades = read_csv_records(TRADES_PATH, TRADE_FIELDS)
    snapshots = latest_snapshot_lookup()
    candidates = candidate_lookup()
    news_items = recent_source_items(80)

    holdings: list[dict[str, Any]] = []
    totals = {
        "cost_value": 0.0,
        "market_value": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "total_pnl": 0.0,
        "total_fee": 0.0,
        "traded_value": 0.0,
    }
    allocation_by_type: dict[str, float] = {}
    suggestions: list[dict[str, str]] = []

    for row in raw_holdings:
        provider = infer_provider(str(row.get("symbol") or ""), str(row.get("asset_type") or ""), str(row.get("provider") or ""))
        symbol = normalize_symbol(row.get("symbol"), provider, str(row.get("asset_type") or ""))
        keys = symbol_keys(symbol, provider, str(row.get("asset_type") or ""))
        snapshot = next((snapshots[key] for key in keys if key in snapshots), {})
        candidate = next((candidates[key] for key in keys if key in candidates), {})
        quantity = safe_float(row.get("quantity"), 0.0) or 0.0
        cost_price = safe_float(row.get("cost_price"), 0.0) or 0.0
        cost_value_input = safe_float(row.get("cost_value"), None)
        holding_amount = safe_float(row.get("holding_amount"), None)
        manual_current = safe_float(row.get("current_price"), None)
        snapshot_price = safe_float(snapshot.get("price"), None)
        candidate_price = safe_float(candidate.get("price"), None)
        live_quote: dict[str, Any] = {}
        if snapshot_price is None and candidate_price is None and manual_current is None:
            live_quote = live_quote_for_holding(symbol, provider, str(row.get("asset_type") or ""))
        live_price = safe_float(live_quote.get("price"), None)
        current_price = snapshot_price if snapshot_price is not None else candidate_price if candidate_price is not None else manual_current if manual_current is not None else live_price
        change_pct = safe_float(snapshot.get("change_pct"), None)
        if change_pct is None:
            change_pct = safe_float(candidate.get("change_pct"), None)
        if change_pct is None:
            change_pct = safe_float(live_quote.get("change_pct"), None)
        cost_value = quantity * cost_price if quantity and cost_price else cost_value_input
        price_market_value = quantity * current_price if quantity and current_price is not None else None
        market_value = price_market_value if price_market_value is not None else holding_amount
        if cost_value is None and market_value is not None:
            cost_value = market_value
        cost_value = cost_value or 0.0
        market_value = market_value or 0.0
        unrealized_pnl = market_value - cost_value
        unrealized_pct = (unrealized_pnl / cost_value * 100.0) if cost_value else None
        news_hits = [item for item in news_items if source_matches_holding(item, {**row, "symbol": symbol, "provider": provider})][:5]
        asset_type = normalize_asset_type(row.get("asset_type") or candidate.get("asset_type") or "stock")
        if snapshot:
            data_status = "latest_quote"
        elif candidate_price is not None:
            data_status = "candidate_price"
        elif manual_current is not None:
            data_status = "manual_price"
        elif live_price is not None:
            data_status = live_quote.get("_source", "live_quote")
        elif holding_amount is not None:
            data_status = "manual_amount"
        else:
            data_status = "missing_price"
        holding = {
            **row,
            "symbol": symbol,
            "provider": provider,
            "asset_type": asset_type,
            "market": row.get("market") or candidate.get("market") or infer_market(symbol, provider),
            "name": row.get("name") or snapshot.get("name") or candidate.get("name") or symbol,
            "quantity": quantity,
            "cost_price": cost_price,
            "cost_value": round(cost_value, 4),
            "current_price": current_price,
            "holding_amount": "" if holding_amount is None else round(holding_amount, 4),
            "change_pct": change_pct,
            "market_value": round(market_value, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "unrealized_pct": None if unrealized_pct is None else round(unrealized_pct, 2),
            "latest_snapshot_at": snapshot.get("snapshot_at"),
            "data_status": data_status,
            "research_score": candidate.get("total_score"),
            "research_themes": candidate.get("themes_list") or [],
            "risk_penalty": candidate.get("risk_penalty"),
            "news_hits": news_hits,
        }
        holdings.append(holding)
        totals["cost_value"] += cost_value
        totals["market_value"] += market_value
        totals["unrealized_pnl"] += unrealized_pnl
        allocation_by_type[asset_type] = allocation_by_type.get(asset_type, 0.0) + market_value

    for trade in trades:
        trade["quantity"] = safe_float(trade.get("quantity"), 0.0) or 0.0
        trade["price"] = safe_float(trade.get("price"), 0.0) or 0.0
        trade["fee"] = safe_float(trade.get("fee"), 0.0) or 0.0
        trade["amount"] = safe_float(trade.get("amount"), 0.0) or 0.0
        trade["pnl"] = safe_float(trade.get("pnl"), None)
        totals["total_fee"] += trade["fee"]
        totals["traded_value"] += abs(trade["amount"])
        if trade["pnl"] is not None:
            totals["realized_pnl"] += trade["pnl"]

    totals["total_pnl"] = totals["realized_pnl"] + totals["unrealized_pnl"]
    pnl_pct = totals["total_pnl"] / totals["cost_value"] * 100.0 if totals["cost_value"] else None
    for key, value in list(totals.items()):
        totals[key] = round(value, 4)
    totals["total_pnl_pct"] = None if pnl_pct is None else round(pnl_pct, 2)

    for holding in holdings:
        allocation = holding["market_value"] / totals["market_value"] * 100.0 if totals["market_value"] else 0.0
        holding["allocation_pct"] = round(allocation, 2)
    holdings.sort(key=lambda item: item.get("market_value") or 0, reverse=True)

    allocation = [
        {"name": name or "未分类", "value": round(value, 4), "pct": round(value / totals["market_value"] * 100.0, 2) if totals["market_value"] else 0.0}
        for name, value in sorted(allocation_by_type.items(), key=lambda item: item[1], reverse=True)
    ]

    realized = [trade["pnl"] for trade in trades if trade.get("pnl") is not None]
    wins = [value for value in realized if value > 0]
    losses = [value for value in realized if value < 0]
    tag_counts: dict[str, int] = {}
    for trade in trades:
        for tag in split_tags(trade.get("tags")):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    style = {
        "trade_count": len(trades),
        "buy_count": len([trade for trade in trades if trade.get("side") == "buy"]),
        "sell_count": len([trade for trade in trades if trade.get("side") == "sell"]),
        "win_rate": round(len(wins) / len(realized) * 100.0, 2) if realized else None,
        "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else None,
        "payoff_ratio": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 2) if wins and losses else None,
        "turnover_proxy": round(totals["traded_value"] / totals["market_value"], 2) if totals["market_value"] else None,
        "top_tags": [{"name": key, "count": value} for key, value in sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:6]],
    }

    if not holdings:
        suggestions.append({"level": "info", "title": "先录入持仓", "detail": "录入股票、基金、数量和成本后，系统会结合行情、舆情和交易记录生成分析。"})
    else:
        if holdings[0].get("allocation_pct", 0) >= 45:
            suggestions.append({"level": "warn", "title": "单一持仓偏集中", "detail": f"{holdings[0]['name']} 占组合约 {holdings[0]['allocation_pct']}%，建议复核仓位上限和回撤承受能力。"})
        weak = [item for item in holdings if item.get("unrealized_pct") is not None and item["unrealized_pct"] <= -8]
        if weak:
            names = "、".join(item["name"] for item in weak[:3])
            suggestions.append({"level": "risk", "title": "浮亏标的需要复盘", "detail": f"{names} 浮亏超过 8%，建议核对买入逻辑是否仍成立，并设置退出/减仓条件。"})
        strong = [item for item in holdings if item.get("unrealized_pct") is not None and item["unrealized_pct"] >= 15]
        if strong:
            names = "、".join(item["name"] for item in strong[:3])
            suggestions.append({"level": "good", "title": "盈利仓位可做保护", "detail": f"{names} 已有较高浮盈，可考虑分批止盈、移动止损或降低主题暴露。"})
        missing = [item for item in holdings if item.get("data_status") == "missing_price"]
        if missing:
            suggestions.append({"level": "warn", "title": "部分标的缺少行情", "detail": "缺少现价的持仓无法准确计算盈亏，请手动填现价或将代码加入观察池。"})
        amount_only = [item for item in holdings if item.get("data_status") == "manual_amount" and not safe_float(item.get("current_price"), None)]
        if amount_only:
            suggestions.append({"level": "info", "title": "金额型基金已纳入市值", "detail": "只填持仓金额的基金可计算组合市值；若要计算真实盈亏，请补充成本金额或交易记录。"})
        hot_news = [item for item in holdings if item.get("news_hits")]
        if hot_news:
            suggestions.append({"level": "info", "title": "持仓已有舆情命中", "detail": "持仓新闻卡片中有最近 7 天命中的热点/风险信息，适合优先点开复核原文。"})
    if realized:
        if style["win_rate"] is not None and style["win_rate"] < 40:
            suggestions.append({"level": "warn", "title": "胜率偏低", "detail": "已记录交易的胜率低于 40%，建议减少随手交易，要求买入前写清触发条件。"})
        if style["payoff_ratio"] is not None and style["payoff_ratio"] < 1:
            suggestions.append({"level": "warn", "title": "盈亏比偏弱", "detail": "平均盈利小于平均亏损，建议把止损和止盈条件前置到交易计划。"})
    if style["turnover_proxy"] is not None and style["turnover_proxy"] > 3:
        suggestions.append({"level": "warn", "title": "换手偏高", "detail": "交易额已超过当前组合市值 3 倍，建议复盘是否存在过度交易。"})

    return {
        "paths": {"holdings": str(HOLDINGS_PATH), "trades": str(TRADES_PATH), "screenshots": str(SCREENSHOT_DIR)},
        "metrics": {"holding_count": len(holdings), **totals},
        "holdings": holdings,
        "trades": sorted(trades, key=lambda row: str(row.get("trade_date") or ""), reverse=True)[:120],
        "allocation": allocation,
        "style": style,
        "suggestions": suggestions,
        "ocr": {"available": False, "message": "当前环境未检测到本地 OCR；截图会保存到本地，可先使用文本/CSV 导入，后续可接入 tesseract 或云 OCR。"},
    }


HEADER_ALIASES = {
    "symbol": {"symbol", "code", "代码", "证券代码", "基金代码"},
    "name": {"name", "名称", "证券名称", "基金名称"},
    "asset_type": {"asset_type", "类型", "资产类型", "品种"},
    "quantity": {"quantity", "qty", "持仓", "持仓数量", "数量", "份额", "可用余额"},
    "cost_price": {"cost_price", "成本价", "成本", "持仓成本", "买入均价"},
    "cost_value": {"cost_value", "成本金额", "持仓成本金额", "投入本金", "本金", "总成本", "累计投入"},
    "current_price": {"current_price", "最新价", "当前价", "市价", "现价", "估值"},
    "holding_amount": {"holding_amount", "持仓金额", "持有金额", "当前金额", "市值", "参考市值", "持仓市值", "资产市值", "最新市值", "总市值"},
    "provider": {"provider", "数据源", "行情源"},
    "market": {"market", "市场"},
    "notes": {"notes", "备注", "说明"},
}


def normalize_import_header(name: str) -> str:
    text = str(name or "").strip().lstrip("\ufeff").lower()
    for field, aliases in HEADER_ALIASES.items():
        if text in {alias.lower() for alias in aliases}:
            return field
    return text


def split_import_line(line: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\s,，;\t]+", line.strip()) if part.strip()]


def parse_holding_text(text: str) -> list[dict[str, Any]]:
    lines = [line.strip().strip("|") for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:8])
    delimiter = "\t" if "\t" in sample else "," if "," in sample else "，" if "，" in sample else None
    imported: list[dict[str, Any]] = []
    if delimiter and len(lines) >= 2:
        reader = csv.DictReader(lines, delimiter=delimiter)
        for row in reader:
            normalized = {normalize_import_header(key): value for key, value in row.items() if key is not None}
            if normalized.get("symbol"):
                imported.append(normalized)
    else:
        header_tokens = split_import_line(lines[0])
        has_header = any(normalize_import_header(token) in HEADER_ALIASES for token in header_tokens)
        if has_header and len(lines) >= 2:
            headers = [normalize_import_header(token) for token in header_tokens]
            for line in lines[1:]:
                values = split_import_line(line)
                row = {headers[index]: values[index] for index in range(min(len(headers), len(values)))}
                if row.get("symbol"):
                    imported.append(row)
        else:
            for line in lines:
                code_match = re.search(r"(?<!\d)([036159]\d{5})(?!\d)", line)
                if not code_match:
                    continue
                numbers = [safe_float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", line)]
                numbers = [item for item in numbers if item is not None]
                before_after = re.split(code_match.group(1), line, maxsplit=1)
                tail = before_after[1] if len(before_after) > 1 else ""
                name = ""
                name_match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9\-\*]{2,18})", tail.strip())
                if name_match:
                    name = name_match.group(1)
                numeric_tail = numbers[1:] if numbers and int(numbers[0]) == int(code_match.group(1)) else numbers
                line_asset_type = "fund" if re.search(r"(基金|ETF|LOF)", line, re.I) else "stock"
                row = {
                    "symbol": code_match.group(1),
                    "name": name,
                    "asset_type": line_asset_type,
                    "notes": "文本导入",
                }
                amount_like = bool(re.search(r"(金额|市值|本金)", line)) or (line_asset_type == "fund" and len(numeric_tail) <= 2 and (numeric_tail[0] if numeric_tail else 0) >= 1000)
                if amount_like:
                    row["holding_amount"] = numeric_tail[0] if len(numeric_tail) > 0 else ""
                    row["cost_value"] = numeric_tail[1] if len(numeric_tail) > 1 else ""
                else:
                    row["quantity"] = numeric_tail[0] if len(numeric_tail) > 0 else ""
                    row["cost_price"] = numeric_tail[1] if len(numeric_tail) > 1 else ""
                    row["current_price"] = numeric_tail[2] if len(numeric_tail) > 2 else ""
                imported.append(row)
    return imported


def import_holdings_text(payload: dict[str, Any]) -> dict[str, Any]:
    rows = parse_holding_text(str(payload.get("text") or ""))
    if not rows:
        raise ValueError("没有解析到持仓，请检查是否包含代码列，或使用格式：代码,名称,类型,数量,成本价,现价；基金也可用：代码,名称,类型,持仓金额,成本金额")
    saved = []
    for row in rows:
        if not row.get("asset_type"):
            row["asset_type"] = payload.get("asset_type") or "stock"
        saved.append(upsert_holding(row))
    return {"imported": len(saved), "holdings": saved, "portfolio": analyze_portfolio()}


def save_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    raw_data = str(payload.get("data") or payload.get("data_url") or "")
    filename = str(payload.get("filename") or "portfolio-screenshot.png")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    if "," in raw_data and raw_data.strip().lower().startswith("data:"):
        raw_data = raw_data.split(",", 1)[1]
    if not raw_data:
        raise ValueError("image data is required")
    try:
        data = base64.b64decode(raw_data, validate=True)
    except Exception as exc:
        raise ValueError(f"invalid base64 image data: {exc}") from exc
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}"
    path.write_bytes(data)
    return {
        "saved": True,
        "path": str(path),
        "bytes": len(data),
        "ocr": {"available": False, "message": "截图已保存；当前先使用文本/CSV 导入，后续可接入 OCR 自动识别。"},
    }


def reports_payload() -> dict[str, Any]:
    cleanup_report_history()
    reports_dir = KB_DIR / "reports"
    reports = []
    if reports_dir.exists():
        for path in sorted(reports_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
            reports.append({
                "name": path.name,
                "path": str(path),
                "mtime": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                "failed": "failed" in path.name,
            })
    return {
        "latest": read_text(KB_DIR / "latest.md"),
        "latest_attempt": read_text(KB_DIR / "latest_attempt.md"),
        "reports": reports,
    }


def cleanup_report_history(keep: int = 1) -> list[str]:
    reports_dir = KB_DIR / "reports"
    if not reports_dir.exists():
        return []
    paths = sorted(reports_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for path in paths[keep:]:
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            pass
    return removed


def config_payload() -> dict[str, Any]:
    return {
        "config": read_json(CONFIG_PATH, {}),
        "path": str(CONFIG_PATH),
        "source_templates": SOURCE_TEMPLATES,
        "provider_templates": PROVIDER_TEMPLATES,
        "source_capabilities": SOURCE_CAPABILITIES,
    }


def fund_analyst_payload() -> dict[str, Any]:
    payload = read_json(FUND_ANALYST_DIR / "latest.json", {})
    config = read_json(CONFIG_PATH, {})
    fund_cfg = config.get("fund_analyst") or {}
    if not payload:
        payload = {
            "ok": False,
            "enabled": bool(fund_cfg.get("enabled", True)),
            "run_at": None,
            "strong_funds": {"candidates": []},
            "portfolio_fund_risks": [],
            "errors": [],
            "method_note": "尚未运行基金分析师模块。",
        }
    payload["state"] = dict(FUND_ANALYST_STATE)
    payload["config"] = fund_cfg
    payload["path"] = str(FUND_ANALYST_DIR / "latest.json")
    return payload


def mutate_config(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = read_json(CONFIG_PATH, {})
    config.setdefault("news_sources", [])
    config.setdefault("watchlist", [])

    if action == "toggle_source":
        index = int(payload["index"])
        if index < 0 or index >= len(config["news_sources"]):
            raise ValueError("source index out of range")
        config["news_sources"][index]["enabled"] = bool(payload.get("enabled"))
    elif action == "remove_source":
        index = int(payload["index"])
        if index < 0 or index >= len(config["news_sources"]):
            raise ValueError("source index out of range")
        config["news_sources"].pop(index)
    elif action == "add_source":
        item = {
            "name": str(payload.get("name") or "自定义RSS"),
            "type": str(payload.get("type") or "rss"),
            "url": str(payload.get("url") or ""),
            "enabled": bool(payload.get("enabled", True)),
        }
        if not item["url"]:
            raise ValueError("url is required")
        existing = {source.get("url") for source in config["news_sources"]}
        if item["url"] in existing:
            raise ValueError("source url already exists")
        config["news_sources"].append(item)
    elif action == "add_source_template":
        template_id = str(payload.get("template_id") or "")
        template = next((item for item in SOURCE_TEMPLATES if item["id"] == template_id), None)
        if not template:
            raise ValueError("unknown template_id")
        item = {key: value for key, value in template.items() if key != "id"}
        existing = {source.get("url") for source in config["news_sources"]}
        if item["url"] not in existing:
            config["news_sources"].append(item)
    elif action == "toggle_watch":
        index = int(payload["index"])
        if index < 0 or index >= len(config["watchlist"]):
            raise ValueError("watch index out of range")
        config["watchlist"][index]["enabled"] = bool(payload.get("enabled"))
    elif action == "remove_watch":
        index = int(payload["index"])
        if index < 0 or index >= len(config["watchlist"]):
            raise ValueError("watch index out of range")
        config["watchlist"].pop(index)
    elif action == "add_watch":
        item = {
            "symbol": str(payload.get("symbol") or "").strip(),
            "name": str(payload.get("name") or "").strip(),
            "asset_type": str(payload.get("asset_type") or "stock"),
            "market": str(payload.get("market") or "CN"),
            "provider": str(payload.get("provider") or "eastmoney_stock"),
            "enabled": bool(payload.get("enabled", True)),
        }
        if not item["symbol"]:
            raise ValueError("symbol is required")
        if not item["name"]:
            item["name"] = item["symbol"]
        existing = {
            (str(asset.get("symbol") or "").lower(), str(asset.get("provider") or "").lower())
            for asset in config["watchlist"]
        }
        key = (item["symbol"].lower(), item["provider"].lower())
        if key in existing:
            raise ValueError("watch asset already exists")
        config["watchlist"].append(item)
    else:
        raise ValueError("unknown config action")

    write_json(CONFIG_PATH, config)
    return config_payload()


def run_fund_analyst_task() -> None:
    global FUND_ANALYST_STATE
    config = read_json(CONFIG_PATH, {})
    external_dir = str((config.get("fund_analyst") or {}).get("external_dir") or "external/fund-analyst")
    command = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(FUND_ANALYST_SCRIPT),
        "--config",
        str(CONFIG_PATH),
        "--output",
        str(FUND_ANALYST_DIR),
        "--external-dir",
        str(ROOT / external_dir),
    ]
    if not Path(command[0]).exists():
        command[0] = sys.executable
    with FUND_ANALYST_LOCK:
        FUND_ANALYST_STATE = {
            "running": True,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }
    try:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
        stdout = completed.stdout[-6000:]
        stderr = completed.stderr[-6000:]
        returncode = completed.returncode
    except Exception as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}"
        returncode = 1
    with FUND_ANALYST_LOCK:
        FUND_ANALYST_STATE.update({
            "running": False,
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        })


def start_fund_analyst() -> dict[str, Any]:
    with FUND_ANALYST_LOCK:
        if FUND_ANALYST_STATE.get("running"):
            return {"started": False, "state": dict(FUND_ANALYST_STATE)}
        FUND_ANALYST_STATE["running"] = True
    thread = threading.Thread(target=run_fund_analyst_task, daemon=True)
    thread.start()
    return {"started": True, "state": dict(FUND_ANALYST_STATE)}


def run_update(mode: str) -> None:
    global UPDATE_STATE
    command = [
        sys.executable,
        str(UPDATE_SCRIPT),
        "--config",
        str(CONFIG_PATH),
        "--output",
        str(KB_DIR),
    ]
    if mode == "offline":
        command.append("--offline-sample")

    with UPDATE_LOCK:
        UPDATE_STATE = {
            "running": True,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "returncode": None,
            "mode": mode,
            "stdout": "",
            "stderr": "",
        }
    try:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
        stdout = completed.stdout[-6000:]
        stderr = completed.stderr[-6000:]
        returncode = completed.returncode
    except Exception as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}"
        returncode = 1

    with UPDATE_LOCK:
        UPDATE_STATE.update({
            "running": False,
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        })
    cleanup_report_history()


def start_update(mode: str) -> dict[str, Any]:
    with UPDATE_LOCK:
        if UPDATE_STATE.get("running"):
            return {"started": False, "state": dict(UPDATE_STATE)}
        UPDATE_STATE["running"] = True
    thread = threading.Thread(target=run_update, args=(mode,), daemon=True)
    thread.start()
    return {"started": True, "state": dict(UPDATE_STATE)}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" and parsed.query:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if parsed.path == "/api/summary":
            self.send_json(summary_payload())
            return
        if parsed.path == "/api/candidates":
            self.send_json({"candidates": read_candidates()})
            return
        if parsed.path == "/api/report":
            self.send_json(reports_payload())
            return
        if parsed.path == "/api/config":
            self.send_json(config_payload())
            return
        if parsed.path == "/api/update-state":
            self.send_json({"state": dict(UPDATE_STATE)})
            return
        if parsed.path == "/api/portfolio":
            self.send_json(analyze_portfolio())
            return
        if parsed.path == "/api/fund-analyst":
            self.send_json(fund_analyst_payload())
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/update":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["online"])[0]
            if mode not in {"online", "offline"}:
                self.send_json({"error": "mode must be online or offline"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(start_update(mode), HTTPStatus.ACCEPTED)
            return
        if parsed.path == "/api/fund-analyst/update":
            self.send_json(start_fund_analyst(), HTTPStatus.ACCEPTED)
            return
        if parsed.path.startswith("/api/config/"):
            action = parsed.path.rsplit("/", 1)[-1].replace("-", "_")
            try:
                payload = read_request_json(self)
                self.send_json(mutate_config(action, payload))
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path.startswith("/api/portfolio/"):
            action = parsed.path.rsplit("/", 1)[-1].replace("-", "_")
            try:
                payload = read_request_json(self)
                if action == "add_holding":
                    self.send_json({"holding": upsert_holding(payload), "portfolio": analyze_portfolio()})
                elif action == "remove_holding":
                    self.send_json({"removed": remove_holding(payload), "portfolio": analyze_portfolio()})
                elif action == "add_trade":
                    self.send_json({"trade": add_trade(payload), "portfolio": analyze_portfolio()})
                elif action == "import_text":
                    self.send_json(import_holdings_text(payload))
                elif action == "upload_screenshot":
                    self.send_json(save_screenshot(payload))
                else:
                    self.send_json({"error": "unknown portfolio action"}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run finance knowledge-base dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Finance dashboard running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
