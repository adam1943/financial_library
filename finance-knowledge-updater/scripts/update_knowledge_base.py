#!/usr/bin/env python3
"""Update a local finance research knowledge base.

The script uses only Python standard-library modules so it can run in a fresh
Codex workspace. It is a research assistant, not an investment adviser.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import html.parser
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.8+ normally has zoneinfo.
    ZoneInfo = None  # type: ignore


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "knowledge_base_dir": "knowledge_base",
    "timezone": "Asia/Shanghai",
    "lookback_days": 7,
    "top_n": 20,
    "request_timeout_seconds": 8,
    "request_attempts": 2,
    "tushare_token_env": "TUSHARE_TOKEN",
    "akshare_python": ".venv/bin/python",
    "stock_name_cache_days": 14,
    "news_sources": [
        {
            "name": "AKShare-东方财富财经新闻",
            "type": "akshare_stock_news_em",
            "url": "akshare://stock_news_em",
            "enabled": True,
            "max_items": 50,
        },
        {
            "name": "新浪财经-滚动财经",
            "type": "json_sina_roll",
            "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=30&page=1",
            "enabled": True,
        },
        {
            "name": "新浪财经-A股市场",
            "type": "json_sina_roll",
            "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&num=30&page=1",
            "enabled": True,
        },
        {
            "name": "东方财富-7x24快讯",
            "type": "json_eastmoney_fastnews",
            "url": "https://eminfo.eastmoney.com/pc_news/FastNews/GetInfoList?code=100&pageNumber=1&pageSize=30",
            "enabled": True,
        },
        {
            "name": "36Kr-科技财经快讯",
            "type": "rss",
            "url": "https://36kr.com/feed",
            "enabled": True,
        },
    ],
    "watchlist": [
        {"symbol": "sh000001", "name": "上证指数", "asset_type": "index", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz399001", "name": "深证成指", "asset_type": "index", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz399006", "name": "创业板指", "asset_type": "index", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh510300", "name": "沪深300ETF", "asset_type": "fund", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh510500", "name": "中证500ETF", "asset_type": "fund", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh588000", "name": "科创50ETF", "asset_type": "fund", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh512760", "name": "芯片ETF", "asset_type": "fund", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz159915", "name": "创业板ETF", "asset_type": "fund", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh600519", "name": "贵州茅台", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz300750", "name": "宁德时代", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz002594", "name": "比亚迪", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh601138", "name": "工业富联", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz300308", "name": "中际旭创", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh688981", "name": "中芯国际", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sh603259", "name": "药明康德", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
        {"symbol": "sz000651", "name": "格力电器", "asset_type": "stock", "market": "CN", "provider": "eastmoney_stock"},
    ],
    "method_input_csv": "input/trading_methods.csv",
    "keyword_sets": {
        "positive": ["政策支持", "业绩预增", "盈利改善", "订单增长", "资金流入", "突破", "创新高", "景气度", "分红", "回购", "估值修复"],
        "risk": ["退市", "亏损", "业绩下滑", "减持", "立案", "调查", "处罚", "违约", "暴雷", "诉讼", "监管", "地缘", "制裁", "流动性风险"],
        "themes": {
            "AI算力": ["人工智能", "AI", "算力", "大模型", "数据中心", "服务器"],
            "半导体": ["半导体", "芯片", "先进封装", "存储", "晶圆", "光刻"],
            "新能源": ["新能源", "光伏", "储能", "锂电", "风电", "氢能"],
            "创新药": ["创新药", "医药", "CXO", "临床", "生物科技"],
            "消费复苏": ["消费", "白酒", "旅游", "免税", "餐饮", "复苏"],
            "低空经济": ["低空经济", "无人机", "eVTOL", "通航"],
            "红利低波": ["红利", "高股息", "低波", "央企", "分红"],
            "并购重组": ["并购", "重组", "资产注入", "国企改革"],
            "出海": ["出海", "海外订单", "出口", "全球化"],
        },
    },
}


OFFLINE_ITEMS: list[dict[str, Any]] = [
    {
        "source": "offline-sample",
        "title": "政策支持人工智能和算力基础设施，工业富联与中际旭创关注度升温",
        "url": "offline://ai-compute-chip-etf",
        "summary": "多地发布人工智能产业政策，市场讨论算力、半导体、数据中心、工业富联、中际旭创和芯片ETF景气度。",
        "published_at": "2026-06-25T15:30:00+08:00",
    },
    {
        "source": "offline-sample",
        "title": "高股息红利资产继续获得资金流入，沪深300ETF与贵州茅台成交活跃",
        "url": "offline://dividend-hs300-etf",
        "summary": "机构称分红、央企和低波策略仍适合作为组合底仓，消费复苏方向关注贵州茅台，但需关注估值修复后的波动。",
        "published_at": "2026-06-25T14:30:00+08:00",
    },
    {
        "source": "offline-sample",
        "title": "新能源链出现订单改善信号，宁德时代与比亚迪被资金重新关注",
        "url": "offline://new-energy-orders",
        "summary": "储能、锂电和出海订单增长带动新能源主题修复，宁德时代、比亚迪和创业板ETF出现资金流入讨论。",
        "published_at": "2026-06-25T13:40:00+08:00",
    },
    {
        "source": "offline-sample",
        "title": "半导体国产替代热度延续，中芯国际与科创50ETF同受关注",
        "url": "offline://semiconductor-localization",
        "summary": "先进封装、存储和晶圆制造方向继续发酵，市场讨论中芯国际、科创50ETF和芯片ETF的弹性。",
        "published_at": "2026-06-25T13:20:00+08:00",
    },
    {
        "source": "offline-sample",
        "title": "部分公司公告减持和业绩下滑，医药与小盘题材短线情绪分化",
        "url": "offline://risk-events",
        "summary": "市场对减持、业绩下滑和监管事件保持谨慎，创新药和CXO方向需要继续核验公告和业绩。",
        "published_at": "2026-06-25T13:00:00+08:00",
    },
]

OFFLINE_QUOTES: list[dict[str, Any]] = [
    {"symbol": "sh510300", "name": "沪深300ETF", "asset_type": "fund", "market": "CN", "provider": "offline", "price": 3.92, "change_pct": 0.82, "volume": None, "amount": None},
    {"symbol": "sh512760", "name": "芯片ETF", "asset_type": "fund", "market": "CN", "provider": "offline", "price": 1.18, "change_pct": 2.35, "volume": None, "amount": None},
    {"symbol": "sh588000", "name": "科创50ETF", "asset_type": "fund", "market": "CN", "provider": "offline", "price": 0.93, "change_pct": 1.42, "volume": None, "amount": None},
    {"symbol": "sh000001", "name": "上证指数", "asset_type": "index", "market": "CN", "provider": "offline", "price": 3038.0, "change_pct": 0.38, "volume": None, "amount": None},
    {"symbol": "sh600519", "name": "贵州茅台", "asset_type": "stock", "market": "CN", "provider": "offline", "price": 1412.50, "change_pct": 0.56, "volume": None, "amount": None},
    {"symbol": "sz300750", "name": "宁德时代", "asset_type": "stock", "market": "CN", "provider": "offline", "price": 196.80, "change_pct": 1.88, "volume": None, "amount": None},
    {"symbol": "sz002594", "name": "比亚迪", "asset_type": "stock", "market": "CN", "provider": "offline", "price": 258.30, "change_pct": 1.21, "volume": None, "amount": None},
    {"symbol": "sh601138", "name": "工业富联", "asset_type": "stock", "market": "CN", "provider": "offline", "price": 27.64, "change_pct": 3.76, "volume": None, "amount": None},
    {"symbol": "sz300308", "name": "中际旭创", "asset_type": "stock", "market": "CN", "provider": "offline", "price": 138.20, "change_pct": 4.18, "volume": None, "amount": None},
    {"symbol": "sh688981", "name": "中芯国际", "asset_type": "stock", "market": "CN", "provider": "offline", "price": 55.72, "change_pct": 2.62, "volume": None, "amount": None},
]


@dataclass
class Status:
    name: str
    ok: bool
    detail: str
    count: int = 0


def now_in_timezone(tz_name: str) -> dt.datetime:
    if ZoneInfo is not None:
        try:
            return dt.datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return dt.datetime.now().astimezone()


def iso_now(tz_name: str) -> str:
    return now_in_timezone(tz_name).isoformat(timespec="seconds")


def ensure_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    merged = DEFAULT_CONFIG.copy()
    merged.update(config)
    if "keyword_sets" not in merged:
        merged["keyword_sets"] = DEFAULT_CONFIG["keyword_sets"]
    return merged


def output_dir_for(config: dict[str, Any], config_path: Path, output_arg: str | None) -> Path:
    if output_arg:
        return Path(output_arg).expanduser().resolve()
    configured = Path(str(config.get("knowledge_base_dir", "knowledge_base"))).expanduser()
    if configured.is_absolute():
        return configured
    return (config_path.parent / configured).resolve() if config_path.parent.name != "knowledge_base" else config_path.parent.resolve()


def setup_dirs(output_dir: Path, run_date: str) -> dict[str, Path]:
    paths = {
        "root": output_dir,
        "data": output_dir / "data",
        "input": output_dir / "input",
        "raw": output_dir / "raw" / run_date,
        "reports": output_dir / "reports",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def ensure_method_template(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["user_label", "method_name", "timeframe", "asset_scope", "tags", "rule_text", "risk_control", "source"])
        writer.writerow([
            "示例用户",
            "趋势+回撤控制",
            "中短期",
            "ETF;行业基金",
            "ETF;趋势;红利低波",
            "只在行业景气度和价格趋势共振时纳入观察，分批跟踪，不追单。",
            "单一主题仓位不超过组合上限；跌破预设均线或出现重大风险公告时复盘。",
            "local-template",
        ])


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_items (
            id TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            published_at TEXT,
            source TEXT,
            title TEXT NOT NULL,
            url TEXT,
            summary TEXT,
            sentiment_score REAL,
            risk_count INTEGER,
            symbols_json TEXT,
            themes_json TEXT,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id TEXT PRIMARY KEY,
            snapshot_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            asset_type TEXT,
            market TEXT,
            provider TEXT,
            price REAL,
            change_pct REAL,
            volume REAL,
            amount REAL,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trading_methods (
            id TEXT PRIMARY KEY,
            ingested_at TEXT NOT NULL,
            user_label TEXT,
            method_name TEXT,
            timeframe TEXT,
            asset_scope TEXT,
            tags_json TEXT,
            rule_text TEXT,
            risk_control TEXT,
            source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_scores (
            id TEXT PRIMARY KEY,
            scored_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            asset_type TEXT,
            market TEXT,
            provider TEXT,
            data_status TEXT,
            price REAL,
            change_pct REAL,
            total_score REAL,
            sentiment_score REAL,
            momentum_score REAL,
            method_fit_score REAL,
            risk_penalty REAL,
            mention_count INTEGER,
            themes_json TEXT,
            rationale_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_status (
            id TEXT PRIMARY KEY,
            run_at TEXT NOT NULL,
            category TEXT,
            name TEXT,
            ok INTEGER,
            detail TEXT,
            count INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_names (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            provider TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    for statement in [
        "ALTER TABLE candidate_scores ADD COLUMN provider TEXT",
        "ALTER TABLE candidate_scores ADD COLUMN data_status TEXT",
        "ALTER TABLE candidate_scores ADD COLUMN price REAL",
        "ALTER TABLE candidate_scores ADD COLUMN change_pct REAL",
    ]:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def stable_id(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


def fetch_text(url: str, timeout: int, attempts: int = 1) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 finance-knowledge-updater/1.0",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Connection": "close",
        },
    )
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.8 * attempt)
    assert last_exc is not None
    raise last_exc


def fetch_json_post(url: str, payload: dict[str, Any], timeout: int, attempts: int = 1) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "finance-knowledge-updater/1.0"},
        method="POST",
    )
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.8 * attempt)
    assert last_exc is not None
    raise last_exc


class MLStripper(html.parser.HTMLParser):  # type: ignore[attr-defined]
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(part.strip() for part in self.parts if part.strip())


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    try:
        parser = MLStripper()
        parser.feed(value)
        return html.unescape(parser.text())
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", value))


def parse_rss_or_atom(xml_text: str, source_name: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = strip_html(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        summary = strip_html(item.findtext("description"))
        published = (item.findtext("pubDate") or item.findtext("published") or item.findtext("date") or "").strip()
        if title:
            items.append({"source": source_name, "title": title, "url": link, "summary": summary, "published_at": published})
    if items:
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = strip_html(entry.findtext("atom:title", default="", namespaces=ns))
        summary = strip_html(entry.findtext("atom:summary", default="", namespaces=ns) or entry.findtext("atom:content", default="", namespaces=ns))
        published = (entry.findtext("atom:published", default="", namespaces=ns) or entry.findtext("atom:updated", default="", namespaces=ns)).strip()
        link = ""
        for link_el in entry.findall("atom:link", ns):
            href = link_el.attrib.get("href")
            if href:
                link = href
                break
        if title:
            items.append({"source": source_name, "title": title, "url": link, "summary": summary, "published_at": published})
    return items


def parse_sina_roll(json_text: str, source_name: str) -> list[dict[str, Any]]:
    payload = json.loads(json_text)
    rows = (payload.get("result") or {}).get("data") or []
    items: list[dict[str, Any]] = []
    for row in rows:
        title = strip_html(str(row.get("title") or row.get("stitle") or ""))
        url = str(row.get("url") or row.get("wapurl") or "").strip()
        summary = strip_html(str(row.get("intro") or row.get("summary") or row.get("keywords") or ""))
        published = str(row.get("ctime") or row.get("time") or row.get("date") or "").strip()
        if title:
            items.append({"source": source_name, "title": title, "url": url, "summary": summary, "published_at": published})
    return items


def parse_eastmoney_fastnews(json_text: str, source_name: str) -> list[dict[str, Any]]:
    payload = json.loads(json_text)
    rows = payload.get("items") or payload.get("data") or []
    items: list[dict[str, Any]] = []
    for row in rows:
        title = strip_html(str(row.get("title") or row.get("infoTitle") or row.get("Title") or ""))
        summary = strip_html(str(row.get("digest") or row.get("summary") or row.get("content") or row.get("infoContent") or ""))
        url = str(row.get("url") or row.get("infoUrl") or row.get("Url") or "").strip()
        info_code = row.get("infoCode") or row.get("code")
        if not url and info_code:
            url = f"https://kuaixun.eastmoney.com/a/{info_code}.html"
        published = str(row.get("showTime") or row.get("publishTime") or row.get("time") or row.get("date") or "").strip()
        if title:
            items.append({"source": source_name, "title": title, "url": url, "summary": summary, "published_at": published})
    return items


def resolve_tool_python(config: dict[str, Any], key: str, default: str) -> Path:
    configured = Path(str(config.get(key) or default)).expanduser()
    if configured.is_absolute():
        return configured
    # Keep venv symlinks intact. Resolving `.venv/bin/python` can jump to the
    # base interpreter and lose the virtualenv site-packages.
    return Path.cwd() / configured


def parse_akshare_em_rows(rows: list[dict[str, Any]], source_name: str, max_items: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows[:max_items]:
        title = strip_html(str(row.get("新闻标题") or row.get("title") or ""))
        summary = strip_html(str(row.get("新闻内容") or row.get("content") or row.get("摘要") or ""))
        url = str(row.get("新闻链接") or row.get("url") or "").strip()
        published = str(row.get("发布时间") or row.get("time") or row.get("date") or "").strip()
        source = str(row.get("文章来源") or source_name).strip() or source_name
        keyword = str(row.get("关键词") or "").strip()
        if keyword and keyword not in title and keyword not in summary:
            summary = f"{summary} 关键词: {keyword}".strip()
        if title:
            items.append({"source": source_name if source == source_name else f"{source_name}/{source}", "title": title, "url": url, "summary": summary, "published_at": published})
    return items


def fetch_akshare_stock_news_em(source: dict[str, Any], config: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    source_name = str(source.get("name") or "AKShare-东方财富财经新闻")
    max_items = int(source.get("max_items") or 50)
    python_path = resolve_tool_python(config, "akshare_python", ".venv/bin/python")
    if not python_path.exists():
        raise FileNotFoundError(f"AKShare Python not found: {python_path}")
    code = (
        "import akshare as ak, json\n"
        "df = ak.stock_news_em()\n"
        "rows = df.head(200).astype(str).to_dict('records')\n"
        "print(json.dumps(rows, ensure_ascii=False))\n"
    )
    completed = subprocess.run(
        [str(python_path), "-c", code],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=max(timeout + 12, 20),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[:500] or f"AKShare exited with {completed.returncode}")
    rows = json.loads(completed.stdout or "[]")
    if not isinstance(rows, list):
        raise ValueError("Unexpected AKShare payload")
    return parse_akshare_em_rows(rows, source_name, max_items)


def parse_news_payload(text: str, source_type: str, source_name: str) -> list[dict[str, Any]]:
    if source_type in {"rss", "atom"}:
        return parse_rss_or_atom(text, source_name)
    if source_type == "json_sina_roll":
        return parse_sina_roll(text, source_name)
    if source_type == "json_eastmoney_fastnews":
        return parse_eastmoney_fastnews(text, source_name)
    raise ValueError(f"unsupported source type: {source_type}")


def parse_float(value: Any, scale: float = 1.0) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return None
        return float(value) / scale
    text = str(value).strip()
    if not text or text in {"-", "--", "None", "null"}:
        return None
    text = text.replace("%", "").replace(",", "")
    try:
        return float(text) / scale
    except ValueError:
        return None


def infer_eastmoney_secid(symbol: str) -> str:
    raw = symbol.strip().lower()
    if raw.startswith("sh"):
        return "1." + raw[2:]
    if raw.startswith("sz"):
        return "0." + raw[2:]
    if "." in raw and raw.split(".", 1)[0] in {"0", "1"}:
        return raw
    code = re.sub(r"\D", "", raw)
    if not code:
        raise ValueError(f"Cannot infer Eastmoney secid from symbol: {symbol}")
    if code.startswith(("5", "6", "9")):
        return "1." + code
    return "0." + code


def normalized_symbol(symbol: str) -> str:
    return symbol.strip().lower()


def symbol_aliases(symbol: str) -> set[str]:
    raw = normalized_symbol(symbol)
    digits = re.sub(r"\D", "", raw)
    aliases = {raw}
    if len(digits) == 6:
        aliases.update({digits, f"sh{digits}", f"sz{digits}", f"bj{digits}", f"{digits}.sh", f"{digits}.sz", f"{digits}.bj"})
    return {item for item in aliases if item}


def canonical_symbol(symbol: str, assets: dict[str, dict[str, Any]] | None = None) -> str:
    raw = normalized_symbol(symbol)
    if assets:
        for alias in symbol_aliases(raw):
            if alias in assets:
                return alias
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6 and re.fullmatch(r"(sh|sz|bj)\d{6}", raw):
        if raw.startswith("sh"):
            return f"{digits}.sh"
        if raw.startswith("sz"):
            return f"{digits}.sz"
        return f"{digits}.bj"
    return raw


def normalized_tushare_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().lower().replace(" ", "")
    if re.fullmatch(r"\d{6}\.(sh|sz|bj)", raw):
        return raw
    if re.fullmatch(r"(sh|sz|bj)\d{6}", raw):
        return f"{raw[2:]}.{raw[:2]}"
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6:
        if digits.startswith(("6", "5", "9")):
            return f"{digits}.sh"
        if digits.startswith(("4", "8")):
            return f"{digits}.bj"
        return f"{digits}.sz"
    return raw


def fetch_eastmoney_quote(asset: dict[str, Any], timeout: int, attempts: int = 1) -> dict[str, Any]:
    secid = asset.get("secid") or infer_eastmoney_secid(str(asset.get("symbol", "")))
    fields = "f43,f57,f58,f60,f170,f168,f47,f48"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={urllib.parse.quote(str(secid))}&fields={fields}"
    try:
        raw = fetch_text(url, timeout, attempts)
        data = json.loads(raw).get("data") or {}
    except Exception:
        list_fields = "f12,f14,f2,f3,f5,f6"
        fallback_url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?secids={urllib.parse.quote(str(secid))}&fields={list_fields}"
        raw = fetch_text(fallback_url, timeout, attempts)
        rows = ((json.loads(raw).get("data") or {}).get("diff") or [])
        if not rows:
            raise
        data = rows[0]
    symbol = normalized_symbol(str(asset.get("symbol") or data.get("f57") or secid))
    price_scale = 1000.0 if str(asset.get("asset_type", "")).lower() == "fund" else 100.0
    price = parse_float(data.get("f43"), price_scale)
    if price is None:
        price = parse_float(data.get("f2"), price_scale)
    prev_close = parse_float(data.get("f60"), price_scale)
    change_pct = parse_float(data.get("f170"), 100.0)
    if change_pct is None:
        change_pct = parse_float(data.get("f3"), 100.0)
    if change_pct is None and price is not None and prev_close:
        change_pct = (price / prev_close - 1.0) * 100.0
    return {
        "symbol": symbol,
        "name": asset.get("name") or data.get("f58") or data.get("f14"),
        "asset_type": asset.get("asset_type", "stock"),
        "market": asset.get("market", "CN"),
        "provider": "eastmoney_stock",
        "price": price,
        "change_pct": change_pct,
        "volume": parse_float(data.get("f47") if data.get("f47") is not None else data.get("f5")),
        "amount": parse_float(data.get("f48") if data.get("f48") is not None else data.get("f6")),
        "raw": data,
    }


def fetch_fundgz_quote(asset: dict[str, Any], timeout: int, attempts: int = 1) -> dict[str, Any]:
    code = re.sub(r"\D", "", str(asset.get("symbol", "")))
    if not code:
        raise ValueError(f"Missing fund code for {asset}")
    url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}"
    raw = fetch_text(url, timeout, attempts)
    match = re.search(r"\((\{.*\})\)", raw)
    if not match:
        raise ValueError(f"Unexpected fundgz payload for {code}")
    data = json.loads(match.group(1))
    return {
        "symbol": code,
        "name": asset.get("name") or data.get("name"),
        "asset_type": asset.get("asset_type", "fund"),
        "market": asset.get("market", "CN"),
        "provider": "fundgz",
        "price": parse_float(data.get("gsz") or data.get("dwjz")),
        "change_pct": parse_float(data.get("gszzl")),
        "volume": None,
        "amount": None,
        "raw": data,
    }


def fetch_stooq_quote(asset: dict[str, Any], timeout: int, attempts: int = 1) -> dict[str, Any]:
    symbol = str(asset.get("symbol", "")).strip().lower()
    url = f"https://stooq.com/q/l/?s={urllib.parse.quote(symbol)}&f=sd2t2ohlcv&h&e=csv"
    raw = fetch_text(url, timeout, attempts)
    rows = list(csv.DictReader(raw.splitlines()))
    if not rows:
        raise ValueError(f"No Stooq rows for {symbol}")
    row = rows[0]
    close = parse_float(row.get("Close"))
    open_price = parse_float(row.get("Open"))
    change_pct = None
    if close is not None and open_price:
        change_pct = (close / open_price - 1.0) * 100.0
    return {
        "symbol": symbol,
        "name": asset.get("name") or symbol.upper(),
        "asset_type": asset.get("asset_type", "stock"),
        "market": asset.get("market", "GLOBAL"),
        "provider": "stooq",
        "price": close,
        "change_pct": change_pct,
        "volume": parse_float(row.get("Volume")),
        "amount": None,
        "raw": row,
    }


def tushare_code(symbol: str) -> str:
    raw = str(symbol or "").strip().lower().replace(" ", "")
    if re.fullmatch(r"\d{6}\.(sh|sz|bj)", raw):
        return raw.upper()
    if re.fullmatch(r"(sh|sz|bj)\d{6}", raw):
        return f"{raw[2:]}.{raw[:2].upper()}"
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 6:
        raise ValueError(f"Cannot infer TuShare ts_code from symbol: {symbol}")
    if digits.startswith(("6", "5", "9")):
        return f"{digits}.SH"
    if digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    return f"{digits}.SZ"


def fetch_tushare_daily_quote(asset: dict[str, Any], timeout: int, attempts: int = 1) -> dict[str, Any]:
    token_env = str(asset.get("token_env") or os.environ.get("TUSHARE_TOKEN_ENV") or "TUSHARE_TOKEN")
    token = str(asset.get("token") or os.environ.get(token_env) or os.environ.get("TUSHARE_PRO_TOKEN") or "").strip()
    token_file = Path(str(asset.get("token_file") or os.environ.get("TUSHARE_TOKEN_FILE") or "knowledge_base/input/tushare_token.txt")).expanduser()
    if not token and token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Missing TuShare token. Set {token_env}, TUSHARE_PRO_TOKEN, or {token_file}.")

    ts_code = tushare_code(str(asset.get("symbol") or ""))
    today = dt.date.today()
    start_date = str(asset.get("start_date") or (today - dt.timedelta(days=14)).strftime("%Y%m%d"))
    end_date = str(asset.get("end_date") or today.strftime("%Y%m%d"))
    payload = {
        "api_name": "daily",
        "token": token,
        "params": {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        "fields": "ts_code,trade_date,open,high,low,close,pct_chg,vol,amount",
    }
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            data = fetch_json_post("https://api.tushare.pro", payload, timeout, 1)
            if data.get("code") not in (0, "0", None):
                raise ValueError(str(data.get("msg") or f"TuShare returned code {data.get('code')}"))
            payload_data = data.get("data") or {}
            fields = payload_data.get("fields") or []
            items = payload_data.get("items") or []
            if not items:
                raise ValueError(f"No TuShare daily rows for {ts_code}")
            rows = [dict(zip(fields, item)) for item in items]
            row = max(rows, key=lambda item: str(item.get("trade_date") or ""))
            return {
                "symbol": normalized_tushare_symbol(str(asset.get("symbol") or ts_code)),
                "name": asset.get("name") or ts_code,
                "asset_type": asset.get("asset_type", "stock"),
                "market": asset.get("market", "CN"),
                "provider": "tushare_daily",
                "price": parse_float(row.get("close")),
                "change_pct": parse_float(row.get("pct_chg")),
                "volume": parse_float(row.get("vol")),
                "amount": parse_float(row.get("amount")),
                "raw": row,
            }
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.8 * attempt)
    assert last_exc is not None
    raise last_exc


def read_tushare_token(config: dict[str, Any]) -> str:
    token_env = str(config.get("tushare_token_env") or os.environ.get("TUSHARE_TOKEN_ENV") or "TUSHARE_TOKEN")
    token = str(config.get("tushare_token") or os.environ.get(token_env) or os.environ.get("TUSHARE_PRO_TOKEN") or "").strip()
    token_file = Path(str(config.get("tushare_token_file") or os.environ.get("TUSHARE_TOKEN_FILE") or "knowledge_base/input/tushare_token.txt")).expanduser()
    if not token and token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    return token


def load_stock_name_cache(output_dir: Path, config: dict[str, Any], timeout: int, attempts: int) -> dict[str, str]:
    cache_path = output_dir / "data" / "stock_names.json"
    max_age_days = int(config.get("stock_name_cache_days", 14))
    if cache_path.exists():
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400.0
        if age_days <= max_age_days:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return {str(key).lower(): str(value) for key, value in payload.items()}
            except Exception:
                pass

    token = read_tushare_token(config)
    if not token:
        return {}
    request_payload = {
        "api_name": "stock_basic",
        "token": token,
        "params": {"list_status": "L"},
        "fields": "ts_code,symbol,name,area,industry,market,list_date",
    }
    try:
        data = fetch_json_post("https://api.tushare.pro", request_payload, timeout, attempts)
        if data.get("code") not in (0, "0", None):
            return {}
        fields = (data.get("data") or {}).get("fields") or []
        rows = (data.get("data") or {}).get("items") or []
        mapping: dict[str, str] = {}
        for item in rows:
            row = dict(zip(fields, item))
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            for value in (row.get("ts_code"), row.get("symbol")):
                for alias in symbol_aliases(str(value or "")):
                    mapping[alias] = name
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return mapping
    except Exception:
        return {}


def fetch_quote(asset: dict[str, Any], timeout: int, attempts: int = 1) -> dict[str, Any]:
    provider = str(asset.get("provider", "eastmoney_stock")).lower()
    if provider == "eastmoney_stock":
        try:
            return fetch_eastmoney_quote(asset, timeout, attempts)
        except Exception:
            digits = re.sub(r"\D", "", str(asset.get("symbol") or ""))
            asset_type = str(asset.get("asset_type") or "").lower()
            if asset_type == "stock" and len(digits) == 6 and digits.startswith(("0", "3", "6")):
                fallback_asset = dict(asset)
                fallback_asset["provider"] = "tushare_daily"
                quote = fetch_tushare_daily_quote(fallback_asset, timeout, attempts)
                quote["raw"] = {"fallback_from": "eastmoney_stock", "tushare": quote.get("raw")}
                return quote
            raise
    if provider == "fundgz":
        return fetch_fundgz_quote(asset, timeout, attempts)
    if provider == "stooq":
        return fetch_stooq_quote(asset, timeout, attempts)
    if provider == "tushare_daily":
        return fetch_tushare_daily_quote(asset, timeout, attempts)
    raise ValueError(f"Unsupported provider: {provider}")


def collect_news(config: dict[str, Any], timeout: int, attempts: int, offline_sample: bool) -> tuple[list[dict[str, Any]], list[Status]]:
    if offline_sample:
        return [dict(item) for item in OFFLINE_ITEMS], [Status("offline-sample-news", True, "loaded bundled sample items", len(OFFLINE_ITEMS))]

    items: list[dict[str, Any]] = []
    statuses: list[Status] = []
    for source in config.get("news_sources", []):
        if source.get("enabled", True) is False:
            continue
        source_type = str(source.get("type", "rss")).lower()
        name = str(source.get("name") or source.get("url") or "unnamed-source")
        if source_type == "akshare_stock_news_em":
            try:
                parsed = fetch_akshare_stock_news_em(source, config, timeout)
                items.extend(parsed)
                statuses.append(Status(name, True, "fetched via AKShare stock_news_em", len(parsed)))
            except Exception as exc:
                statuses.append(Status(name, False, f"{type(exc).__name__}: {exc}", 0))
            continue
        if source_type not in {"rss", "atom", "json_sina_roll", "json_eastmoney_fastnews"}:
            statuses.append(Status(name, False, f"unsupported source type: {source_type}", 0))
            continue
        text = ""
        try:
            text = fetch_text(str(source["url"]), timeout, attempts)
            parsed = parse_news_payload(text, source_type, name)
            items.extend(parsed)
            statuses.append(Status(name, True, "fetched", len(parsed)))
        except Exception as exc:
            snippet = strip_html(text[:180]).replace("\n", " ").strip() if text else ""
            detail = f"{type(exc).__name__}: {exc}"
            if snippet:
                detail = f"{detail}; response starts with: {snippet}"
            statuses.append(Status(name, False, detail, 0))
    return dedupe_items(items), statuses


def collect_quotes(
    config: dict[str, Any],
    timeout: int,
    attempts: int,
    offline_sample: bool,
    conn: sqlite3.Connection | None = None,
) -> tuple[list[dict[str, Any]], list[Status]]:
    if offline_sample:
        return [dict(item) for item in OFFLINE_QUOTES], [Status("offline-sample-quotes", True, "loaded bundled sample quotes", len(OFFLINE_QUOTES))]

    quotes: list[dict[str, Any]] = []
    statuses: list[Status] = []
    cached = cached_quote_lookup(conn) if conn is not None else {}
    for asset in config.get("watchlist", []):
        if asset.get("enabled", True) is False:
            continue
        label = f"{asset.get('symbol', '')} {asset.get('name', '')}".strip()
        try:
            asset_payload = dict(asset)
            if str(asset_payload.get("provider", "")).lower() == "tushare_daily":
                if config.get("tushare_token_env"):
                    asset_payload.setdefault("token_env", config.get("tushare_token_env"))
                if config.get("tushare_token_file"):
                    asset_payload.setdefault("token_file", config.get("tushare_token_file"))
            quote = fetch_quote(asset_payload, timeout, attempts)
            quotes.append(quote)
            statuses.append(Status(label, True, "fetched", 1))
        except Exception as exc:
            symbol = str(asset.get("symbol") or "")
            cached_quote = next((cached[alias] for alias in symbol_aliases(symbol) if alias in cached), None)
            if cached_quote:
                fallback_quote = dict(cached_quote)
                fallback_quote.pop("raw_json", None)
                fallback_quote.pop("cached", None)
                fallback_quote.update({
                    "symbol": fallback_quote.get("symbol") or normalized_symbol(symbol),
                    "name": fallback_quote.get("name") or asset.get("name"),
                    "asset_type": fallback_quote.get("asset_type") or asset.get("asset_type"),
                    "market": fallback_quote.get("market") or asset.get("market"),
                    "provider": fallback_quote.get("provider") or asset.get("provider"),
                    "raw": {"cached_fallback": True, "source_error": f"{type(exc).__name__}: {exc}", "snapshot_at": fallback_quote.get("snapshot_at")},
                })
                quotes.append(fallback_quote)
                statuses.append(Status(label, True, f"cached fallback after {type(exc).__name__}: {exc}", 1))
            else:
                statuses.append(Status(label, False, f"{type(exc).__name__}: {exc}", 0))
    return quotes, statuses


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = item.get("url") or item.get("title") or json.dumps(item, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def text_for_item(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}"


def score_text(text: str, keyword_sets: dict[str, Any]) -> tuple[float, int, list[str]]:
    lower_text = text.lower()
    score = 0.0
    risk_count = 0
    themes: list[str] = []

    for keyword in keyword_sets.get("positive", []):
        if str(keyword).lower() in lower_text:
            score += 1.2
    for keyword in keyword_sets.get("risk", []):
        if str(keyword).lower() in lower_text:
            risk_count += 1
            score -= 1.8
    for theme, keywords in keyword_sets.get("themes", {}).items():
        hits = [kw for kw in keywords if str(kw).lower() in lower_text]
        if hits:
            themes.append(str(theme))
            score += min(2.5, 0.8 * len(hits))
    return round(score, 3), risk_count, sorted(set(themes))


def match_symbols(text: str, watchlist: list[dict[str, Any]]) -> list[str]:
    lower_text = text.lower()
    matches: set[str] = set()
    for asset in watchlist:
        symbol = normalized_symbol(str(asset.get("symbol", "")))
        name = str(asset.get("name", "")).strip()
        code = re.sub(r"\D", "", symbol)
        aliases = symbol_aliases(symbol)
        if any(alias and alias in lower_text for alias in aliases):
            matches.add(symbol)
        if code and len(code) >= 5 and code in lower_text:
            matches.add(symbol or code)
        if name and name.lower() in lower_text:
            matches.add(symbol or name)

    for code in re.findall(r"(?<!\d)([036]\d{5})(?!\d)", text):
        matches.add(code)
    return sorted(matches)


def ingest_news(conn: sqlite3.Connection, items: list[dict[str, Any]], config: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    keyword_sets = config.get("keyword_sets", {})
    watchlist = config.get("watchlist", [])
    for item in items:
        text = text_for_item(item)
        sentiment, risk_count, themes = score_text(text, keyword_sets)
        symbols = match_symbols(text, watchlist)
        row_id = stable_id(str(item.get("url") or ""), str(item.get("title") or ""))
        enriched_item = dict(item)
        enriched_item.update({"id": row_id, "sentiment_score": sentiment, "risk_count": risk_count, "themes": themes, "symbols": symbols})
        enriched.append(enriched_item)
        conn.execute(
            """
            INSERT OR REPLACE INTO source_items
            (id, fetched_at, published_at, source, title, url, summary, sentiment_score, risk_count, symbols_json, themes_json, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                fetched_at,
                item.get("published_at"),
                item.get("source"),
                item.get("title"),
                item.get("url"),
                item.get("summary"),
                sentiment,
                risk_count,
                json.dumps(symbols, ensure_ascii=False),
                json.dumps(themes, ensure_ascii=False),
                json.dumps(item, ensure_ascii=False),
            ),
        )
    conn.commit()
    return enriched


def ingest_quotes(conn: sqlite3.Connection, quotes: list[dict[str, Any]], snapshot_at: str) -> None:
    for quote in quotes:
        symbol = normalized_symbol(str(quote.get("symbol", "")))
        row_id = stable_id(snapshot_at, symbol, str(quote.get("provider", "")))
        conn.execute(
            """
            INSERT OR REPLACE INTO market_snapshots
            (id, snapshot_at, symbol, name, asset_type, market, provider, price, change_pct, volume, amount, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                snapshot_at,
                symbol,
                quote.get("name"),
                quote.get("asset_type"),
                quote.get("market"),
                quote.get("provider"),
                quote.get("price"),
                quote.get("change_pct"),
                quote.get("volume"),
                quote.get("amount"),
                json.dumps(quote.get("raw", quote), ensure_ascii=False),
            ),
        )
    conn.commit()


def ingest_statuses(conn: sqlite3.Connection, statuses: list[Status], run_at: str) -> None:
    for status in statuses:
        category = "market" if re.search(r"(^[a-z]{0,2}\d{6}|ETF|指数|时代|茅台|比亚迪|中际|富联|中芯|药明|格力)", status.name, re.I) else "news"
        row_id = stable_id(run_at, category, status.name)
        conn.execute(
            """
            INSERT OR REPLACE INTO source_status
            (id, run_at, category, name, ok, detail, count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (row_id, run_at, category, status.name, 1 if status.ok else 0, status.detail, status.count),
        )
    conn.commit()


def ingest_symbol_names(conn: sqlite3.Connection, mapping: dict[str, str], provider: str, updated_at: str) -> None:
    for symbol, name in mapping.items():
        clean_name = str(name or "").strip()
        if not symbol or not clean_name:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO symbol_names
            (symbol, name, provider, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(symbol).lower(), clean_name, provider, updated_at),
        )
    conn.commit()


def symbol_name_lookup(conn: sqlite3.Connection, extra_mapping: dict[str, str] | None = None) -> dict[str, str]:
    lookup = {str(key).lower(): str(value) for key, value in (extra_mapping or {}).items() if value}
    rows = conn.execute("SELECT symbol, name FROM symbol_names").fetchall()
    for sqlite_row in rows:
        row = dict(sqlite_row)
        for alias in symbol_aliases(str(row.get("symbol") or "")):
            lookup.setdefault(alias, str(row.get("name") or ""))
    return lookup


def cached_quote_lookup(conn: sqlite3.Connection, days: int = 7) -> dict[str, dict[str, Any]]:
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT ms.snapshot_at, ms.symbol, ms.name, ms.asset_type, ms.market, ms.provider, ms.price, ms.change_pct, ms.volume, ms.amount, ms.raw_json
        FROM market_snapshots ms
        JOIN (
            SELECT symbol, MAX(snapshot_at) AS latest_snapshot
            FROM market_snapshots
            WHERE snapshot_at >= ?
            GROUP BY symbol
        ) latest
        ON latest.symbol = ms.symbol AND latest.latest_snapshot = ms.snapshot_at
        """,
        (cutoff,),
    ).fetchall()
    lookup: dict[str, dict[str, Any]] = {}
    for sqlite_row in rows:
        row = dict(sqlite_row)
        row["cached"] = True
        for alias in symbol_aliases(str(row.get("symbol") or "")):
            lookup[alias] = row
    return lookup


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;,，；\s]+", value)
    return sorted({part.strip() for part in parts if part.strip()})


def ingest_methods(conn: sqlite3.Connection, csv_path: Path, ingested_at: str) -> list[dict[str, Any]]:
    ensure_method_template(csv_path)
    methods: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            tags = split_tags(row.get("tags"))
            method = {
                "user_label": row.get("user_label", ""),
                "method_name": row.get("method_name", ""),
                "timeframe": row.get("timeframe", ""),
                "asset_scope": row.get("asset_scope", ""),
                "tags": tags,
                "rule_text": row.get("rule_text", ""),
                "risk_control": row.get("risk_control", ""),
                "source": row.get("source", "local-csv"),
            }
            row_id = stable_id(method["user_label"], method["method_name"], method["rule_text"], method["risk_control"])
            method["id"] = row_id
            methods.append(method)
            conn.execute(
                """
                INSERT OR REPLACE INTO trading_methods
                (id, ingested_at, user_label, method_name, timeframe, asset_scope, tags_json, rule_text, risk_control, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    ingested_at,
                    method["user_label"],
                    method["method_name"],
                    method["timeframe"],
                    method["asset_scope"],
                    json.dumps(tags, ensure_ascii=False),
                    method["rule_text"],
                    method["risk_control"],
                    method["source"],
                ),
            )
    conn.commit()
    return methods


def latest_quotes_by_symbol(quotes: list[dict[str, Any]], watchlist: list[dict[str, Any]], conn: sqlite3.Connection | None = None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    alias_to_symbol: dict[str, str] = {}
    cached = cached_quote_lookup(conn) if conn is not None else {}
    for asset in watchlist:
        symbol = canonical_symbol(str(asset.get("symbol", "")))
        if symbol:
            merged = dict(asset)
            cached_quote = next((cached[alias] for alias in symbol_aliases(symbol) | symbol_aliases(str(asset.get("symbol", ""))) if alias in cached), None)
            if cached_quote:
                merged.update(cached_quote)
                merged["data_status"] = "quote_cached"
            result.setdefault(symbol, merged)
            for alias in symbol_aliases(symbol) | symbol_aliases(str(asset.get("symbol", ""))):
                alias_to_symbol[alias] = symbol
    for quote in quotes:
        quote_aliases = symbol_aliases(str(quote.get("symbol", "")))
        symbol = next((alias_to_symbol[alias] for alias in quote_aliases if alias in alias_to_symbol), canonical_symbol(str(quote.get("symbol", ""))))
        if symbol:
            merged = dict(result.get(symbol, {}))
            merged.update(quote)
            merged["data_status"] = "quote_ok"
            result[symbol] = merged
            for alias in symbol_aliases(symbol):
                alias_to_symbol[alias] = symbol
    return result


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def method_fit_score(themes: set[str], methods: list[dict[str, Any]], asset_type: str) -> tuple[float, list[str]]:
    matched: list[str] = []
    score = 0.0
    for method in methods:
        tags = {str(tag).lower() for tag in method.get("tags", [])}
        theme_hits = {theme for theme in themes if theme.lower() in tags}
        type_hit = asset_type and asset_type.lower() in tags
        if theme_hits or type_hit:
            label = method.get("method_name") or method.get("user_label") or "method"
            matched.append(str(label))
            score += 1.5 + 0.7 * len(theme_hits)
    return clamp(score, 0.0, 8.0), sorted(set(matched))


def research_action(total: float, mention_count: int, change_pct: float | None, risk_penalty: float, has_quote: bool) -> tuple[str, str]:
    if risk_penalty >= 16:
        return "谨慎回避", "风险事件或负面关键词较多，先核验公告和基本面。"
    if not has_quote:
        return "仅作线索", "只有舆情命中，缺少行情支撑，先补数据再判断。"
    if total >= 68 and mention_count >= 2 and (change_pct or 0) >= 0 and risk_penalty <= 8:
        return "积极跟踪", "舆情热度、行情和风险状态同时较好，适合进入重点研究清单。"
    if total >= 58 and risk_penalty <= 10:
        return "建仓观察", "综合分较高但仍需等待买点、仓位计划和回撤条件确认。"
    if total >= 45:
        return "持续关注", "具备一定支撑，但信号不够集中，适合观察而非追高。"
    return "暂缓", "分数或动量偏弱，优先级低于当前高热标的。"


def score_candidates(
    conn: sqlite3.Connection,
    enriched_items: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    config: dict[str, Any],
    scored_at: str,
    name_lookup: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    watchlist = config.get("watchlist", [])
    assets = latest_quotes_by_symbol(quotes, watchlist, conn)
    names = symbol_name_lookup(conn, name_lookup)
    alias_to_symbol: dict[str, str] = {}
    for symbol in assets:
        for alias in symbol_aliases(symbol):
            alias_to_symbol[alias] = symbol
    candidates: dict[str, dict[str, Any]] = {}

    for symbol, asset in assets.items():
        candidates[symbol] = {
            "symbol": symbol,
            "name": asset.get("name", symbol),
            "asset_type": asset.get("asset_type", "stock"),
            "market": asset.get("market", ""),
            "provider": asset.get("provider", ""),
            "price": asset.get("price"),
            "change_pct": asset.get("change_pct"),
            "data_status": asset.get("data_status") or ("quote_ok" if asset.get("price") is not None or asset.get("change_pct") is not None else "watchlist_only"),
            "mention_count": 0,
            "sentiment_sum": 0.0,
            "risk_count": 0,
            "themes": set(),
            "matched_titles": [],
            "matched_sources": set(),
        }

    for item in enriched_items:
        symbols = item.get("symbols") or []
        if not symbols:
            # Promote theme-only items to broad ETF/index candidates through theme names.
            text = text_for_item(item)
            for asset in watchlist:
                name = str(asset.get("name", ""))
                if name and any(theme in name for theme in item.get("themes", [])):
                    symbols.append(normalized_symbol(str(asset.get("symbol", ""))))
        for symbol in symbols:
            aliases = symbol_aliases(str(symbol))
            symbol_norm = next((alias_to_symbol[alias] for alias in aliases if alias in alias_to_symbol), canonical_symbol(str(symbol)))
            if symbol_norm not in candidates:
                display_name = next((names[alias] for alias in aliases if alias in names and names[alias]), symbol_norm)
                candidates[symbol_norm] = {
                    "symbol": symbol_norm,
                    "name": display_name,
                    "asset_type": "stock",
                    "market": "",
                    "provider": "",
                    "price": None,
                    "change_pct": None,
                    "data_status": "news_only",
                    "mention_count": 0,
                    "sentiment_sum": 0.0,
                    "risk_count": 0,
                    "themes": set(),
                    "matched_titles": [],
                    "matched_sources": set(),
                }
            candidate = candidates[symbol_norm]
            candidate["mention_count"] += 1
            candidate["sentiment_sum"] += float(item.get("sentiment_score") or 0.0)
            candidate["risk_count"] += int(item.get("risk_count") or 0)
            candidate["themes"].update(item.get("themes") or [])
            if item.get("title"):
                candidate["matched_titles"].append(str(item["title"]))
            if item.get("source"):
                candidate["matched_sources"].add(str(item["source"]))

    scored: list[dict[str, Any]] = []
    for symbol, candidate in candidates.items():
        themes = set(candidate["themes"])
        sentiment_component = clamp(float(candidate["sentiment_sum"]) + candidate["mention_count"] * 0.7, -12.0, 12.0) * 3.0
        change_pct = candidate.get("change_pct")
        momentum_component = clamp(float(change_pct or 0.0), -8.0, 8.0) * 2.0
        fit_score, matched_methods = method_fit_score(themes, methods, str(candidate.get("asset_type", "")))
        risk_penalty = min(float(candidate["risk_count"]) * 4.0, 24.0)
        if change_pct is not None and float(change_pct) <= -3.0:
            risk_penalty += 4.0
        total = clamp(50.0 + sentiment_component + momentum_component + fit_score - risk_penalty, 0.0, 100.0)
        has_quote = candidate.get("data_status") in {"quote_ok", "quote_cached"} or candidate.get("price") is not None
        heat_score = clamp(
            candidate["mention_count"] * 9.0
            + len(candidate.get("matched_sources") or []) * 7.0
            + len(themes) * 3.0
            + max(sentiment_component, 0.0) * 0.45
            + max(momentum_component, 0.0) * 0.3
            - risk_penalty * 0.8,
            0.0,
            100.0,
        )
        action, action_reason = research_action(total, int(candidate["mention_count"]), change_pct, risk_penalty, bool(has_quote))
        candidate_name = candidate.get("name") or next((names[alias] for alias in symbol_aliases(symbol) if alias in names and names[alias]), symbol)
        rationale = {
            "matched_titles": candidate["matched_titles"][:5],
            "matched_methods": matched_methods,
            "themes": sorted(themes),
            "action": action,
            "action_reason": action_reason,
            "heat_score": round(heat_score, 2),
            "formula": "50 + sentiment + momentum + method_fit - risk_penalty",
        }
        row = {
            "symbol": symbol,
            "name": candidate_name,
            "asset_type": candidate.get("asset_type") or "",
            "market": candidate.get("market") or "",
            "provider": candidate.get("provider") or "",
            "data_status": candidate.get("data_status") or "",
            "price": candidate.get("price"),
            "change_pct": change_pct,
            "total_score": round(total, 2),
            "sentiment_score": round(sentiment_component, 2),
            "momentum_score": round(momentum_component, 2),
            "method_fit_score": round(fit_score, 2),
            "risk_penalty": round(risk_penalty, 2),
            "mention_count": int(candidate["mention_count"]),
            "heat_score": round(heat_score, 2),
            "action": action,
            "action_reason": action_reason,
            "themes": sorted(themes),
            "news_sources": sorted(candidate.get("matched_sources") or []),
            "rationale": rationale,
        }
        scored.append(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO candidate_scores
            (id, scored_at, symbol, name, asset_type, market, provider, data_status, price, change_pct, total_score, sentiment_score, momentum_score, method_fit_score, risk_penalty, mention_count, themes_json, rationale_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id(scored_at, symbol),
                scored_at,
                symbol,
                row["name"],
                row["asset_type"],
                row["market"],
                row["provider"],
                row["data_status"],
                row["price"],
                row["change_pct"],
                row["total_score"],
                row["sentiment_score"],
                row["momentum_score"],
                row["method_fit_score"],
                row["risk_penalty"],
                row["mention_count"],
                json.dumps(row["themes"], ensure_ascii=False),
                json.dumps(rationale, ensure_ascii=False),
            ),
        )
    conn.commit()
    scored.sort(key=lambda row: (row["heat_score"], row["total_score"], row["mention_count"]), reverse=True)
    return scored


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_source_status(path: Path, statuses: list[Status], run_at: str) -> None:
    payload = {
        "run_at": run_at,
        "ok_count": sum(1 for status in statuses if status.ok),
        "fail_count": sum(1 for status in statuses if not status.ok),
        "statuses": [status.__dict__ for status in statuses],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_candidates_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    fields = [
        "symbol",
        "name",
        "asset_type",
        "market",
        "provider",
        "data_status",
        "price",
        "change_pct",
        "total_score",
        "sentiment_score",
        "momentum_score",
        "method_fit_score",
        "risk_penalty",
        "mention_count",
        "heat_score",
        "action",
        "action_reason",
        "themes",
        "news_sources",
        "matched_news",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in candidates:
            out = {field: row.get(field) for field in fields}
            out["themes"] = ";".join(row.get("themes") or [])
            out["news_sources"] = ";".join(row.get("news_sources") or [])
            out["matched_news"] = " | ".join((row.get("rationale") or {}).get("matched_titles") or [])
            writer.writerow(out)


def cleanup_report_history(reports_dir: Path, keep: int = 1) -> None:
    if not reports_dir.exists():
        return
    reports = sorted(reports_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in reports[keep:]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def hot_themes(enriched_items: list[dict[str, Any]]) -> list[tuple[str, int, float]]:
    data: dict[str, dict[str, float]] = {}
    for item in enriched_items:
        for theme in item.get("themes") or []:
            entry = data.setdefault(theme, {"count": 0.0, "score": 0.0})
            entry["count"] += 1.0
            entry["score"] += float(item.get("sentiment_score") or 0.0)
    result = [(theme, int(values["count"]), round(values["score"], 2)) for theme, values in data.items()]
    result.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return result


def render_report(
    run_at: str,
    output_dir: Path,
    statuses: list[Status],
    enriched_items: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    top_n: int,
    offline_sample: bool,
) -> str:
    ok_count = sum(1 for status in statuses if status.ok)
    fail_count = sum(1 for status in statuses if not status.ok)
    lines: list[str] = []
    lines.append(f"# 金融交易知识库更新报告")
    lines.append("")
    lines.append(f"- 更新时间: {run_at}")
    lines.append(f"- 输出目录: `{output_dir}`")
    lines.append(f"- 模式: {'离线样例' if offline_sample else '联网更新'}")
    lines.append(f"- 来源状态: {ok_count} 成功 / {fail_count} 失败")
    lines.append(f"- 新闻/舆情条目: {len(enriched_items)}")
    lines.append(f"- 行情快照: {len(quotes)}")
    lines.append(f"- 本地交易方法: {len(methods)}")
    lines.append("")
    lines.append("> 说明: 本报告用于投研线索筛选，不构成投资建议或交易指令。")
    lines.append("")

    lines.append("## 热点主题")
    themes = hot_themes(enriched_items)
    if not themes:
        lines.append("")
        lines.append("暂无可识别主题。")
    else:
        lines.append("")
        lines.append("| 主题 | 命中数 | 情绪分 |")
        lines.append("| --- | ---: | ---: |")
        for theme, count, score in themes[:10]:
            lines.append(f"| {theme} | {count} | {score} |")
    lines.append("")

    lines.append("## 候选股票/基金")
    top_candidates = candidates[:top_n]
    if not top_candidates:
        lines.append("")
        lines.append("暂无候选项。请检查 watchlist、新闻源或交易方法输入。")
    else:
        lines.append("")
        lines.append("| 排名 | 标的 | 建议 | 类型 | 价格 | 涨跌幅% | 总分 | 热度 | 提及 | 主题 | 风险扣分 |")
        lines.append("| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |")
        for idx, row in enumerate(top_candidates, 1):
            price = "" if row.get("price") is None else f"{float(row['price']):.3f}"
            change = "" if row.get("change_pct") is None else f"{float(row['change_pct']):.2f}"
            themes_text = ";".join(row.get("themes") or [])
            label = f"{row.get('name') or row['symbol']} ({row['symbol']})"
            lines.append(f"| {idx} | {label} | {row.get('action', '')} | {row.get('asset_type', '')} | {price} | {change} | {row['total_score']:.2f} | {row.get('heat_score', 0):.2f} | {row['mention_count']} | {themes_text} | {row['risk_penalty']:.2f} |")
    lines.append("")

    risk_items = [item for item in enriched_items if int(item.get("risk_count") or 0) > 0]
    lines.append("## 风险提示")
    if not risk_items:
        lines.append("")
        lines.append("本次采集未识别到明显风险关键词。仍需人工复核公告、财报、估值和流动性。")
    else:
        lines.append("")
        for item in risk_items[:10]:
            lines.append(f"- {item.get('title', '')} [{item.get('source', '')}]")
    lines.append("")

    failed = [status for status in statuses if not status.ok]
    lines.append("## 数据源状态")
    lines.append("")
    if failed:
        lines.append("失败来源:")
        for status in failed:
            lines.append(f"- {status.name}: {status.detail}")
        lines.append("")
    lines.append("全部来源:")
    for status in statuses:
        marker = "OK" if status.ok else "FAIL"
        lines.append(f"- {marker} {status.name}: {status.detail} ({status.count})")
    lines.append("")

    lines.append("## 下一步")
    lines.append("")
    lines.append("- 在 `knowledge_base/config.json` 增删 watchlist 标的。")
    lines.append("- 在 `knowledge_base/input/trading_methods.csv` 录入真实交易方法和风控条件。")
    lines.append("- 对高分候选继续核验财报、公告、估值、成交额、回撤和持仓相关性。")
    return "\n".join(lines) + "\n"


def method_csv_path(output_dir: Path, config: dict[str, Any]) -> Path:
    configured = Path(str(config.get("method_input_csv", "input/trading_methods.csv"))).expanduser()
    return configured if configured.is_absolute() else output_dir / configured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update a stock/fund research knowledge base.")
    parser.add_argument("--config", default="knowledge_base/config.json", help="Path to JSON config. Created when missing.")
    parser.add_argument("--output", default=None, help="Knowledge base output directory. Overrides config.")
    parser.add_argument("--offline-sample", action="store_true", help="Use bundled sample data instead of network sources.")
    args = parser.parse_args(argv)

    config_path = Path(args.config).expanduser().resolve()
    config = ensure_config(config_path)
    output_dir = output_dir_for(config, config_path, args.output)
    tz_name = str(config.get("timezone", "Asia/Shanghai"))
    run_at = iso_now(tz_name)
    run_date = run_at[:10]
    paths = setup_dirs(output_dir, run_date)
    timeout = int(config.get("request_timeout_seconds", 8))
    attempts = int(config.get("request_attempts", 1))

    db_path = paths["data"] / "finance_kb.sqlite"
    conn = init_db(db_path)
    try:
        news_items, news_statuses = collect_news(config, timeout, attempts, args.offline_sample)
        quotes, quote_statuses = collect_quotes(config, timeout, attempts, args.offline_sample, conn)
        method_path = method_csv_path(output_dir, config)
        methods = ingest_methods(conn, method_path, run_at)
        name_cache = load_stock_name_cache(output_dir, config, timeout, attempts)
        watch_names: dict[str, str] = {}
        for asset in config.get("watchlist", []):
            for alias in symbol_aliases(str(asset.get("symbol") or "")):
                if asset.get("name"):
                    watch_names[alias] = str(asset.get("name"))
        quote_names: dict[str, str] = {}
        for quote in quotes:
            for alias in symbol_aliases(str(quote.get("symbol") or "")):
                if quote.get("name"):
                    quote_names[alias] = str(quote.get("name"))
        ingest_symbol_names(conn, {**name_cache, **watch_names, **quote_names}, "auto", run_at)
        enriched_items = ingest_news(conn, news_items, config, run_at)
        ingest_quotes(conn, quotes, run_at)
        candidates = score_candidates(conn, enriched_items, quotes, methods, config, run_at, name_cache)

        statuses = news_statuses + quote_statuses
        ingest_statuses(conn, statuses, run_at)
        write_jsonl(paths["raw"] / "news_items.jsonl", enriched_items)
        write_jsonl(paths["raw"] / "market_snapshots.jsonl", quotes)
        write_source_status(paths["raw"] / "source_status.json", statuses, run_at)
        write_source_status(output_dir / "source_status.json", statuses, run_at)

        report = render_report(
            run_at=run_at,
            output_dir=output_dir,
            statuses=statuses,
            enriched_items=enriched_items,
            quotes=quotes,
            methods=methods,
            candidates=candidates,
            top_n=int(config.get("top_n", 20)),
            offline_sample=args.offline_sample,
        )
        fresh_data_available = bool(enriched_items or quotes)
        time_slug = run_at[11:19].replace(":", "")
        if fresh_data_available or args.offline_sample:
            report_path = paths["reports"] / f"{run_date}.md"
            write_candidates_csv(output_dir / "candidates.csv", candidates)
            report_path.write_text(report, encoding="utf-8")
            (output_dir / "latest.md").write_text(report, encoding="utf-8")
            preserved_previous_outputs = False
        else:
            report_path = paths["reports"] / f"{run_date}-failed-{time_slug}.md"
            report_path.write_text(report, encoding="utf-8")
            preserved_previous_outputs = True
        (output_dir / "latest_attempt.md").write_text(report, encoding="utf-8")
        cleanup_report_history(paths["reports"])

        print(json.dumps({
            "ok": True,
            "run_at": run_at,
            "output_dir": str(output_dir),
            "database": str(db_path),
            "latest_report": str(output_dir / "latest.md"),
            "attempt_report": str(report_path),
            "candidates_csv": str(output_dir / "candidates.csv"),
            "news_items": len(enriched_items),
            "quotes": len(quotes),
            "methods": len(methods),
            "preserved_previous_outputs": preserved_previous_outputs,
            "source_failures": [status.__dict__ for status in statuses if not status.ok],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
