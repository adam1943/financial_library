#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
17_hot_sector_fund_recommendation.py -- 热点板块基金推荐
========================================================

用途：
    python 17_hot_sector_fund_recommendation.py [基金代码]

逻辑：
  1. 扫描当前行业板块 + 概念板块涨幅排名，识别热点赛道。
  2. 用热点关键词匹配开放基金/场内基金排行，形成候选基金池。
  3. 对候选基金叠加近1/3/6月涨跌幅、同赛道排名、回撤画像。
  4. 若传入当前持有基金代码，评估其季报持仓行业暴露是否贴合热点。

说明：
  该脚本用于“推荐候选基金”，不是单独买入信号。强推荐仍需通过
  四维严格闸门、回撤风控、大盘总开关、量化验证和季报滞后检查。
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests

from config import OUTPUT_DIR, date_to_akshare, get_logger, n_days_ago, print_banner, save_result, today_str, with_cache, with_retry


logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


THEME_ALIAS_RULES = {
    "光伏": ["光伏", "太阳能", "HJT", "TOPCon", "钙钛矿", "新能源"],
    "芯片": ["芯片", "半导体", "集成电路", "存储", "先进封装", "光刻机"],
    "半导体": ["半导体", "芯片", "集成电路", "存储", "先进封装", "光刻机"],
    "新能源": ["新能源", "电池", "锂电", "储能", "固态电池", "新能源车", "光伏"],
    "电池": ["电池", "锂电", "储能", "固态电池", "新能源车", "新能源"],
    "储能": ["储能", "电池", "锂电", "新能源", "电力设备"],
    "人工智能": ["人工智能", "AI", "算力", "云计算", "数据中心", "软件", "传媒"],
    "算力": ["算力", "人工智能", "AI", "数据中心", "CPO", "光通信", "通信"],
    "通信": ["通信", "5G", "CPO", "光通信", "数据中心", "算力"],
    "机器人": ["机器人", "人形机器人", "工业母机", "自动化", "智能制造"],
    "军工": ["军工", "国防", "航空", "航天", "北斗"],
    "有色": ["有色", "金属", "稀土", "小金属", "黄金", "铜", "铝"],
    "证券": ["证券", "券商", "金融"],
    "医药": ["医药", "医疗", "创新药", "生物", "中药", "CXO"],
    "港股": ["港股", "恒生", "中概", "互联网", "科技"],
}

EXCLUDED_FUND_KEYWORDS = ["债券", "货币", "短债", "纯债", "现金", "同业存单", "理财", "养老"]
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/boardlist.html",
}


def parse_float(value: Any) -> float | None:
    if value in (None, "", "---", "--", "-"):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def load_local_module(filename: str):
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dataframe_records(df: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    if df is None or df.empty:
        return []
    source = df[columns].copy() if columns else df.copy()
    return source.where(pd.notna(source), None).to_dict("records")


@with_retry()
@with_cache(cache_type="realtime")
def fetch_board_spot(board_type: str) -> list[dict]:
    """Return JSON-safe board spot records for industry or concept boards."""
    try:
        df = fetch_board_spot_direct(board_type)
    except Exception as direct_exc:
        logger.warning(f"东方财富webguest板块实时接口失败，尝试AkShare备用接口: {board_type} {direct_exc}")
        try:
            if board_type == "industry":
                df = ak.stock_board_industry_name_em()
            elif board_type == "concept":
                df = ak.stock_board_concept_name_em()
            else:
                return []
        except Exception as ak_exc:
            raise RuntimeError(f"webguest与AkShare板块实时接口均失败: webguest={direct_exc}; akshare={ak_exc}") from ak_exc

    if df.empty:
        try:
            logger.warning(f"东方财富webguest板块实时接口返回空表，尝试AkShare备用接口: {board_type}")
            if board_type == "industry":
                df = ak.stock_board_industry_name_em()
            elif board_type == "concept":
                df = ak.stock_board_concept_name_em()
            else:
                return []
        except Exception as ak_exc:
            raise RuntimeError(f"webguest返回空表且AkShare板块备用接口失败: {ak_exc}") from ak_exc

    if df.empty:
        return []

    name_col = "板块名称" if "板块名称" in df.columns else "名称"
    pct_col = "涨跌幅" if "涨跌幅" in df.columns else "涨幅"
    records = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        change_pct = parse_float(row.get(pct_col))
        if not name or change_pct is None:
            continue
        records.append(
            {
                "board_type": board_type,
                "sector": name,
                "change_pct": round(change_pct, 2),
                "source": row.get("数据源")
                or ("东方财富-行业板块" if board_type == "industry" else "东方财富-概念板块"),
            }
        )
    return records


def fetch_board_spot_akshare_first(board_type: str) -> list[dict]:
    """Deprecated compatibility route: keep AkShare-first behavior for manual diagnosis."""
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_name_em()
        elif board_type == "concept":
            df = ak.stock_board_concept_name_em()
        else:
            return []
    except Exception as exc:
        logger.warning(f"AkShare板块接口失败，尝试东方财富webguest实时接口: {board_type} {exc}")
        df = fetch_board_spot_direct(board_type)

    if df.empty:
        return []

    name_col = "板块名称" if "板块名称" in df.columns else "名称"
    pct_col = "涨跌幅" if "涨跌幅" in df.columns else "涨幅"
    records = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        change_pct = parse_float(row.get(pct_col))
        if not name or change_pct is None:
            continue
        records.append(
            {
                "board_type": board_type,
                "sector": name,
                "change_pct": round(change_pct, 2),
                "source": row.get("数据源")
                or ("东方财富-行业板块" if board_type == "industry" else "东方财富-概念板块"),
            }
        )
    return records


def parse_eastmoney_json_or_jsonp(text: str) -> dict:
    """Parse Eastmoney JSON/JSONP payload returned by clist endpoints."""
    body = (text or "").strip()
    match = re.match(r"^[\w$]+\((.*)\)\s*;?$", body, flags=re.S)
    if match:
        body = match.group(1)
    return json.loads(body)


def fetch_board_spot_direct(board_type: str) -> pd.DataFrame:
    if board_type == "industry":
        fs = "m:90 t:2 f:!50"
        params = {
            "pn": "1",
            "pz": "120",
            "po": "1",
            "np": "1",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": "f3,f12,f14",
            "dect": "1",
            "timil": "1",
            "cb": "jQuery1123000000000000000_1",
        }
        legacy_url = "https://17.push2.eastmoney.com/api/qt/clist/get"
    elif board_type == "concept":
        fs = "m:90 t:3 f:!50"
        params = {
            "pn": "1",
            "pz": "120",
            "po": "1",
            "np": "1",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": "f3,f12,f14",
            "dect": "1",
            "timil": "1",
            "cb": "jQuery1123000000000000000_2",
        }
        legacy_url = "https://79.push2.eastmoney.com/api/qt/clist/get"
    else:
        return pd.DataFrame()

    url = "https://push2.eastmoney.com/webguest/api/qt/clist/get"
    try:
        response = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        data = parse_eastmoney_json_or_jsonp(response.text)
    except Exception as exc:
        logger.warning(f"东方财富webguest实时接口失败，尝试旧版直连接口: {board_type} {exc}")
        legacy_params = {
            key: value
            for key, value in params.items()
            if key not in {"cb", "timil", "dect"}
        }
        legacy_params["ut"] = "bd1d9ddb04089700cf9c27f6f7426281"
        response = requests.get(legacy_url, params=legacy_params, headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()

    if data.get("rc") not in (0, None):
        raise RuntimeError(f"东方财富板块接口返回异常: rc={data.get('rc')} data={data.get('data')}")

    rows = data.get("data", {}).get("diff", []) or []
    records = []
    for idx, item in enumerate(rows, 1):
        records.append(
            {
                "排名": idx,
                "板块名称": item.get("f14"),
                "板块代码": item.get("f12"),
                "涨跌幅": item.get("f3"),
                "数据源": "东方财富webguest实时接口",
            }
        )
    return pd.DataFrame(records)


@with_retry()
@with_cache(cache_type="daily")
def fetch_board_history_metrics(board_type: str, sector_name: str) -> dict:
    end_date = date_to_akshare(today_str())
    start_date = date_to_akshare(n_days_ago(45))
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_hist_em(
                symbol=sector_name,
                start_date=start_date,
                end_date=end_date,
                period="日k",
                adjust="",
            )
        else:
            df = ak.stock_board_concept_hist_em(
                symbol=sector_name,
                start_date=start_date,
                end_date=end_date,
                period="daily",
                adjust="",
            )
    except Exception as exc:
        return {"error": str(exc)}

    if df.empty or "收盘" not in df.columns:
        return {"error": "历史行情为空"}

    df = df.sort_values("日期").reset_index(drop=True)
    close = pd.to_numeric(df["收盘"], errors="coerce").dropna().reset_index(drop=True)
    if len(close) < 6:
        return {"error": "历史行情不足"}

    latest = float(close.iloc[-1])
    metrics = {"latest_close": round(latest, 4)}
    if len(close) >= 6:
        metrics["return_5d_pct"] = round((latest / float(close.iloc[-6]) - 1) * 100, 2)
    if len(close) >= 21:
        metrics["return_20d_pct"] = round((latest / float(close.iloc[-21]) - 1) * 100, 2)
    return metrics


def build_theme_keywords(sector_name: str) -> list[str]:
    keywords = {sector_name}
    for trigger, aliases in THEME_ALIAS_RULES.items():
        if trigger in sector_name or any(alias in sector_name for alias in aliases):
            keywords.update(aliases)
            keywords.add(trigger)
    cleaned = []
    for keyword in keywords:
        keyword = str(keyword).strip()
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)
    return cleaned


def identify_hot_sectors(top_n: int = 8, enrich_n: int = 12) -> dict:
    boards: list[dict] = []
    errors = []
    for board_type in ["industry", "concept"]:
        try:
            boards.extend(fetch_board_spot(board_type))
        except Exception as exc:
            errors.append(f"{board_type}: {exc}")

    if not boards:
        return {"error": "未获取到行业/概念板块实时涨幅", "errors": errors}

    avg_change = round(sum(item["change_pct"] for item in boards) / len(boards), 2)
    ranked = sorted(boards, key=lambda item: item["change_pct"], reverse=True)
    enriched = []
    for idx, item in enumerate(ranked[:enrich_n], 1):
        hist = fetch_board_history_metrics(item["board_type"], item["sector"])
        return_5d = hist.get("return_5d_pct") if isinstance(hist, dict) else None
        return_20d = hist.get("return_20d_pct") if isinstance(hist, dict) else None
        heat_score = (
            item["change_pct"] * 10
            + (return_5d or 0) * 2
            + (return_20d or 0)
            + max(0, 12 - idx)
        )
        enriched.append(
            {
                **item,
                "rank": idx,
                "market_avg_change_pct": avg_change,
                "return_5d_pct": return_5d,
                "return_20d_pct": return_20d,
                "heat_score": round(heat_score, 2),
                "keywords": build_theme_keywords(item["sector"]),
            }
        )

    hot = [
        item
        for item in enriched
        if item["change_pct"] > max(0, avg_change) and item["change_pct"] > 0
    ][:top_n]
    if not hot:
        hot = enriched[:top_n]

    return {
        "analysis_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_avg_change_pct": avg_change,
        "total_boards": len(boards),
        "hot_sectors": hot,
        "errors": errors,
    }


def load_last_good_hot_sector_scan() -> dict | None:
    output_dir = Path(OUTPUT_DIR) / "17_hot_sector_funds"
    if not output_dir.exists():
        return None
    files = sorted(output_dir.glob("hot_sector_funds_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            import json

            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            scan = payload.get("hot_sector_scan", {})
            if scan.get("hot_sectors"):
                scan = {
                    **scan,
                    "data_status": "last_known_good",
                    "last_good_file": str(path),
                    "warning": "实时板块接口失败，使用最近一次成功热点扫描；只可降级参考，不能作为强买入依据",
                }
                return scan
        except Exception:
            continue
    return None


@with_retry()
@with_cache(cache_type="daily")
def fetch_fund_name_types() -> dict[str, dict]:
    df = ak.fund_name_em()
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("基金代码", "")).zfill(6)
        if not code:
            continue
        result[code] = {
            "fund_type": row.get("基金类型"),
            "short_name": row.get("基金简称"),
        }
    return result


@with_retry()
@with_cache(cache_type="daily")
def fetch_open_fund_rank() -> list[dict]:
    df = ak.fund_open_fund_rank_em(symbol="全部")
    return dataframe_records(df)


@with_retry()
@with_cache(cache_type="daily")
def fetch_exchange_fund_rank() -> list[dict]:
    df = ak.fund_exchange_rank_em()
    return dataframe_records(df)


def standardize_fund_record(row: dict, source: str, type_map: dict[str, dict]) -> dict:
    code = str(row.get("基金代码", "")).zfill(6)
    name = str(row.get("基金简称") or row.get("基金名称") or "").strip()
    fund_type = row.get("类型") or type_map.get(code, {}).get("fund_type")
    return {
        "fund_code": code,
        "fund_name": name,
        "fund_type": fund_type,
        "source": source,
        "date": row.get("日期"),
        "daily_pct": parse_float(row.get("日增长率") if "日增长率" in row else row.get("增长率")),
        "week1_pct": parse_float(row.get("近1周")),
        "month1_pct": parse_float(row.get("近1月")),
        "month3_pct": parse_float(row.get("近3月")),
        "month6_pct": parse_float(row.get("近6月")),
        "year1_pct": parse_float(row.get("近1年")),
        "year3_pct": parse_float(row.get("近3年")),
    }


def build_fund_universe() -> list[dict]:
    type_map = fetch_fund_name_types()
    universe = {}
    sources = [
        ("open_fund_rank", fetch_open_fund_rank()),
        ("exchange_fund_rank", fetch_exchange_fund_rank()),
    ]
    for source, rows in sources:
        for row in rows:
            fund = standardize_fund_record(row, source, type_map)
            code = fund["fund_code"]
            name = fund["fund_name"]
            if not code or not name:
                continue
            if any(keyword in name for keyword in EXCLUDED_FUND_KEYWORDS):
                continue
            existing = universe.get(code)
            if existing is None or source == "exchange_fund_rank":
                universe[code] = fund
    return list(universe.values())


def match_fund_to_hot_sector(fund: dict, sector: dict) -> list[str]:
    name = fund.get("fund_name", "")
    matched = []
    for keyword in sector.get("keywords", []):
        if keyword and keyword in name:
            matched.append(keyword)
    return matched


def base_candidate_score(fund: dict, sector: dict) -> float:
    daily = fund.get("daily_pct") or 0
    week1 = fund.get("week1_pct") or 0
    month1 = fund.get("month1_pct") or 0
    month3 = fund.get("month3_pct") or 0
    month6 = fund.get("month6_pct") or 0
    heat = sector.get("heat_score") or sector.get("change_pct", 0) * 10

    score = 50
    score += clamp(heat / 4, 0, 25)
    score += clamp(daily * 3, -10, 15)
    score += clamp(week1 * 1.5, -10, 18)
    score += clamp(month1 * 0.9, -15, 30)
    score += clamp(month3 * 0.35, -15, 25)
    score += clamp(month6 * 0.15, -10, 15)
    if fund.get("source") == "exchange_fund_rank" or "ETF" in fund.get("fund_name", "").upper():
        score += 3
    return round(clamp(score, 0, 120), 2)


def build_raw_candidates(hot_sectors: list[dict], max_per_sector: int = 8) -> list[dict]:
    universe = build_fund_universe()
    candidates = []
    seen = set()
    for sector in hot_sectors:
        matched_for_sector = []
        for fund in universe:
            matched_keywords = match_fund_to_hot_sector(fund, sector)
            if not matched_keywords:
                continue
            if (fund.get("month1_pct") is not None and fund["month1_pct"] <= 0) or (
                fund.get("daily_pct") is not None and fund["daily_pct"] < -1
            ):
                continue
            candidate = {
                **fund,
                "matched_sector": sector["sector"],
                "matched_board_type": sector["board_type"],
                "matched_keywords": matched_keywords,
                "sector_change_pct": sector.get("change_pct"),
                "sector_return_5d_pct": sector.get("return_5d_pct"),
                "sector_return_20d_pct": sector.get("return_20d_pct"),
                "sector_heat_score": sector.get("heat_score"),
                "base_score": base_candidate_score(fund, sector),
            }
            matched_for_sector.append(candidate)
        matched_for_sector = sorted(matched_for_sector, key=lambda item: item["base_score"], reverse=True)
        for candidate in matched_for_sector[:max_per_sector]:
            key = candidate["fund_code"]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return sorted(candidates, key=lambda item: item["base_score"], reverse=True)


def calc_avg_peer_rank_pct(metrics: list[dict]) -> float | None:
    ranks = [item.get("peer_rank_pct") for item in metrics if item.get("peer_rank_pct") is not None]
    if not ranks:
        return None
    return round(sum(ranks) / len(ranks), 2)


def calc_avg_peer_rank_pct_from_objects(metrics: list) -> float | None:
    ranks = []
    for item in metrics:
        value = getattr(item, "peer_rank_pct", None)
        if value is not None:
            ranks.append(value)
    if not ranks:
        return None
    return round(sum(ranks) / len(ranks), 2)


def enrich_candidate_with_drawdown(candidate: dict) -> dict:
    try:
        drawdown_mod = load_local_module("fund_drawdown_report.py")
        report = drawdown_mod.build_report(candidate["fund_code"])
        rows = drawdown_mod.flatten_report(report)
    except Exception as exc:
        return {**candidate, "risk_error": str(exc), "final_score": candidate["base_score"] - 12, "recommendation_level": "data_weak"}

    recent = report.recent_strength
    four = report.four_dimension_gate
    first_row = rows[0] if rows else {}
    max_dd_1y = first_row.get("max_drawdown_1y_pct")
    max_dd_3y = first_row.get("max_drawdown_3y_pct")
    current_dd = first_row.get("current_drawdown_pct")
    avg_rank_pct = calc_avg_peer_rank_pct_from_objects(recent.metrics if recent else [])
    four_passed_periods = four.passed_periods if four else 0
    recent_passed = bool(recent.passed) if recent else False
    four_passed = bool(four.passed) if four else False

    score = candidate["base_score"]
    score += 18 if recent_passed else -8
    score += 25 if four_passed else four_passed_periods * 5 - 8
    if avg_rank_pct is not None:
        score += clamp(12 - avg_rank_pct, -8, 12)
    if max_dd_1y is not None:
        if max_dd_1y <= -25:
            score -= 12
        elif max_dd_1y <= -15:
            score -= 5
        elif max_dd_1y >= -8:
            score += 4
    if current_dd is not None and current_dd <= -8:
        score -= 10

    if four_passed and recent_passed:
        level = "strong_recommend"
        action = "热点、涨幅、同赛道排名和回撤闸门均较强，可列入优先候选；仍需看大盘总开关与仓位上限"
    elif recent_passed:
        level = "watch_candidate"
        action = "热点与近期相对排名支持观察，但四维严格闸门未完全通过，不支持追高重仓"
    else:
        level = "only_watch"
        action = "仅因热点匹配入池，回撤/同赛道排名不足以支持强买入"

    return {
        **candidate,
        "recent_strength_pass": recent_passed,
        "four_dimension_pass": four_passed,
        "four_dimension_passed_periods": four_passed_periods,
        "avg_peer_rank_pct": avg_rank_pct,
        "current_drawdown_pct": current_dd,
        "max_drawdown_1y_pct": max_dd_1y,
        "max_drawdown_3y_pct": max_dd_3y,
        "risk_decision": (
            getattr(four, "decision_reference", None)
            or getattr(recent, "decision_reference", None)
        ),
        "recommendation_level": level,
        "recommendation_action": action,
        "final_score": round(clamp(score, 0, 160), 2),
    }


def attach_risk_return_to_candidates(candidates: list[dict], max_presort_count: int = 80) -> list[dict]:
    """批量叠加夏普比率/波动率横向对比，避免逐只基金重复扫描同类池。"""
    if not candidates:
        return []
    try:
        rr_mod = load_local_module("18_risk_return_screener.py")
        codes = [item["fund_code"] for item in candidates if item.get("fund_code")]
        comparison = rr_mod.compare_funds_risk_return(
            codes,
            min_sharpe=2.0,
            max_presort_count=max_presort_count,
            verbose=False,
        )
        references = comparison.get("references", {})
    except Exception as exc:
        logger.warning(f"热点候选夏普/波动率横向对比失败: {exc}")
        return [
            {
                **item,
                "risk_return_error": str(exc),
                "risk_return_level": "unknown",
                "risk_return_pass": False,
                "final_score": round(clamp(item.get("final_score", item.get("base_score", 0)) - 6, 0, 160), 2),
            }
            for item in candidates
        ]

    enriched = []
    for item in candidates:
        ref = references.get(item.get("fund_code"), {})
        guard = ref.get("risk_return_guard", {})
        level = guard.get("level", "unknown")
        score = item.get("final_score", item.get("base_score", 0))
        if level == "risk_return_leader":
            score += 16
        elif level == "partial_leader":
            score += 9
        elif level == "watch":
            score -= 2
        elif level == "lagging":
            score -= 10
        else:
            score -= 6
        if guard.get("avg_sharpe") is not None and guard["avg_sharpe"] >= 2:
            score += 5
        if guard.get("avg_annualized_volatility_pct") is not None and guard["avg_annualized_volatility_pct"] >= 35:
            score -= 5

        recommendation_level = item.get("recommendation_level")
        recommendation_action = item.get("recommendation_action", "")
        if level in ["lagging", "unknown"]:
            if recommendation_level == "strong_recommend":
                recommendation_level = "watch_candidate"
            elif recommendation_level == "watch_candidate":
                recommendation_level = "only_watch"
            recommendation_action += "；夏普/波动率横向排名不足，不能作为强买入，只能等待回撤和技术企稳"
        elif level == "watch" and recommendation_level == "strong_recommend":
            recommendation_level = "watch_candidate"
            recommendation_action += "；风险收益仅单窗口领先，强推荐降级为观察候选"
        elif level in ["risk_return_leader", "partial_leader"]:
            recommendation_action += "；夏普/波动率横向对比提供正向支持"

        enriched.append(
            {
                **item,
                "risk_return_level": level,
                "risk_return_pass": bool(guard.get("passed")),
                "avg_sharpe": guard.get("avg_sharpe"),
                "avg_annualized_volatility_pct": guard.get("avg_annualized_volatility_pct"),
                "avg_risk_return_rank_pct": guard.get("avg_risk_return_rank_pct"),
                "risk_return_guard": guard,
                "risk_return_metrics": ref.get("risk_return_metrics", []),
                "recommendation_level": recommendation_level,
                "recommendation_action": recommendation_action,
                "final_score": round(clamp(score, 0, 160), 2),
            }
        )
    return enriched


def evaluate_current_fund_alignment(fund_code: str, hot_sectors: list[dict]) -> dict:
    if not fund_code:
        return {}
    try:
        holdings_mod = load_local_module("02_fund_holdings.py")
        fetcher = inspect.unwrap(holdings_mod.get_fund_holdings)
        holdings_df = fetcher(fund_code)
        if not isinstance(holdings_df, pd.DataFrame) or holdings_df.empty:
            return {"fund_code": fund_code, "error": "未获取到当前基金持仓"}
        parsed = holdings_mod.parse_latest_two_quarters(holdings_df)
    except Exception as exc:
        return {"fund_code": fund_code, "error": str(exc)}

    hot_keywords = set()
    for sector in hot_sectors:
        hot_keywords.update(sector.get("keywords", []))
        hot_keywords.add(sector.get("sector", ""))

    matched_exposure = []
    total_hot_weight = 0.0
    for item in (parsed.get("industry_exposure") or {}).get("distribution", []):
        industry = str(item.get("industry", ""))
        ratio = parse_float(item.get("ratio")) or 0
        matched = [keyword for keyword in hot_keywords if keyword and keyword in industry]
        if matched:
            total_hot_weight += ratio
            matched_exposure.append({"industry": industry, "ratio": ratio, "matched_keywords": matched[:5]})

    total_top10 = parse_float(parsed.get("total_top10_ratio")) or 0
    if total_hot_weight >= 40:
        level = "high_alignment"
        action = "当前基金持仓与热点板块贴合度高，重点用回撤、排名和估值偏差控制仓位，不必频繁换基"
    elif total_hot_weight >= 15:
        level = "partial_alignment"
        action = "当前基金部分贴合热点，可继续观察；若同赛道排名走弱，再考虑切换到推荐基金"
    else:
        level = "low_alignment"
        action = "当前基金持仓与热点板块贴合度低，可把推荐基金作为调仓候选，但不建议一次性追高切换"

    return {
        "fund_code": fund_code,
        "latest_quarter": parsed.get("latest_quarter"),
        "top10_total_weight_pct": total_top10,
        "hot_matched_weight_pct": round(total_hot_weight, 2),
        "alignment_level": level,
        "action": action,
        "matched_exposure": matched_exposure,
    }


def analyze_hot_sector_funds(
    fund_code: str | None = None,
    top_sectors: int = 8,
    top_funds: int = 8,
    enrich_limit: int = 8,
) -> dict:
    print_banner("热点板块与基金推荐 | v6.18", char="═")

    sector_result = identify_hot_sectors(top_n=top_sectors)
    if sector_result.get("error"):
        fallback = load_last_good_hot_sector_scan()
        if fallback:
            print(f"[WARN] {sector_result.get('error')}，使用最近一次成功热点扫描降级分析")
            sector_result = fallback
        else:
            result = {
                "fund_code": fund_code,
                "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": sector_result.get("error"),
                "hot_sector_scan": sector_result,
            }
            save_path = save_result(result, f"hot_sector_funds_{fund_code or 'market'}", subdir="17_hot_sector_funds")
            print(f"[WARN] {result['error']}")
            print(f"[OK] 结果已保存: {save_path}")
            return result

    hot = sector_result.get("hot_sectors", [])
    print("\n【当前热点板块】")
    for item in hot[:top_sectors]:
        print(
            f"  {item['rank']:2d}. {item['sector']}({item['board_type']}) "
            f"今日{item['change_pct']}%  5日{item.get('return_5d_pct')}%  20日{item.get('return_20d_pct')}%"
        )

    raw_candidates = build_raw_candidates(hot)
    enriched = []
    for candidate in raw_candidates[:enrich_limit]:
        enriched.append(enrich_candidate_with_drawdown(candidate))
    enriched = attach_risk_return_to_candidates(enriched)
    enriched = sorted(enriched, key=lambda item: item.get("final_score", item.get("base_score", 0)), reverse=True)
    recommendations = enriched[:top_funds]
    alignment = evaluate_current_fund_alignment(fund_code, hot) if fund_code else {}

    print("\n【推荐基金候选】")
    if not recommendations:
        print("  未找到与当前热点板块直接匹配且近期涨幅为正的基金候选")
    for idx, fund in enumerate(recommendations, 1):
        print(
            f"  {idx:2d}. {fund['fund_code']} {fund['fund_name']} | {fund['matched_sector']} "
            f"日{fund.get('daily_pct')}%  1月{fund.get('month1_pct')}%  3月{fund.get('month3_pct')}% "
            f"夏普均值{fund.get('avg_sharpe')} 波动{fund.get('avg_annualized_volatility_pct')}% "
            f"得分{fund.get('final_score', fund.get('base_score'))}  {fund.get('recommendation_level')}"
        )
        print(f"      {fund.get('recommendation_action')}")

    if alignment:
        print("\n【当前基金热点贴合度】")
        if alignment.get("error"):
            print(f"  {alignment['error']}")
        else:
            print(
                f"  前十大持仓{alignment.get('top10_total_weight_pct')}%，"
                f"热点匹配{alignment.get('hot_matched_weight_pct')}%，"
                f"{alignment.get('alignment_level')}"
            )
            print(f"  {alignment.get('action')}")

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hot_sector_scan": sector_result,
        "current_fund_hot_alignment": alignment,
        "raw_candidate_count": len(raw_candidates),
        "recommendations": recommendations,
        "method_note": (
            "先用行业/概念板块涨幅识别热点，再匹配主题基金，并叠加近1/3/6月涨跌幅、"
            "同赛道排名和回撤画像。推荐结果是候选池，不单独构成买入建议。"
        ),
    }
    save_path = save_result(result, f"hot_sector_funds_{fund_code or 'market'}", subdir="17_hot_sector_funds")
    print(f"\n[OK] 结果已保存: {save_path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="热点板块基金推荐")
    parser.add_argument("fund_code", nargs="?", help="可选：当前持有基金代码，用于评估热点贴合度")
    parser.add_argument("--top-sectors", type=int, default=8)
    parser.add_argument("--top-funds", type=int, default=8)
    parser.add_argument("--enrich-limit", type=int, default=8)
    args = parser.parse_args()
    analyze_hot_sector_funds(args.fund_code, args.top_sectors, args.top_funds, args.enrich_limit)


if __name__ == "__main__":
    main()
