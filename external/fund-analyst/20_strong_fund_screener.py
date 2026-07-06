#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
20_strong_fund_screener.py -- v6.24 强势基金趋势跟随筛选
================================================================

用途：
    python 20_strong_fund_screener.py
    python 20_strong_fund_screener.py --types mixed stock --top 20
    python 20_strong_fund_screener.py --format json --max-candidates 40

设计目标：
  1. 避免在 PowerShell 管道中传中文参数造成 "混合型/近1月" 变成 "???"。
  2. 用英文 CLI 参数，脚本内部统一使用 UTF-8 中文列名。
  3. 按 v6.24 筛出强势主动权益基金，并输出观察仓/确认仓/主仓买点。
  4. 补充申购状态、限额、近1/3/6月回撤、夏普、波动率和 MA10/20/60。

说明：
  本脚本生成候选池，不单独构成买入建议。宏观总开关关闭时，强势基金
  只能作为极小观察仓或等待买点，不能当作正常加仓。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from typing import Any

import akshare as ak
import numpy as np
import pandas as pd

import fund_drawdown_report as drawdown_mod
from config import print_banner, save_result


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


COL = {
    "seq": "序号",
    "code": "基金代码",
    "name": "基金简称",
    "date": "日期",
    "daily": "日增长率",
    "week1": "近1周",
    "month1": "近1月",
    "month3": "近3月",
    "month6": "近6月",
    "year1": "近1年",
    "year2": "近2年",
    "year3": "近3年",
    "ytd": "今年来",
}

TYPE_MAP = {
    "mixed": "混合型",
    "stock": "股票型",
    "all": "全部",
}

DEFAULT_TYPES = ("mixed", "stock")
TRADING_DAYS = 252
RISK_FREE_RATE = 0.015

EXCLUDE_KEYWORDS = (
    "一年持有",
    "两年持有",
    "三年持有",
    "持有期",
    "定开",
    "债券",
    "债",
    "货币",
    "养老",
    "FOF",
    "指数",
    "ETF",
    "联接",
    "增强",
)


def parse_float(value: Any) -> float | None:
    if value in (None, "", "---", "--", "-"):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        parsed = float(str(value).replace("%", "").replace(",", "").strip())
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def normalize_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(6) if digits else ""


def dataframe_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.where(pd.notna(df), None).to_dict("records")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def fetch_rank_dataframe(fund_type: str) -> pd.DataFrame:
    df = ak.fund_open_fund_rank_em(symbol=fund_type).copy()
    df["rank_type"] = fund_type
    for col in (
        COL["daily"],
        COL["week1"],
        COL["month1"],
        COL["month3"],
        COL["month6"],
        COL["year1"],
        COL["year2"],
        COL["year3"],
        COL["ytd"],
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df[COL["code"]] = df[COL["code"]].map(normalize_code)
    return df


def fetch_purchase_map() -> dict[str, dict]:
    try:
        df = ak.fund_purchase_em().copy()
    except Exception as exc:
        return {"_error": {"error": str(exc)}}

    if df.empty or COL["code"] not in df.columns:
        return {}

    df[COL["code"]] = df[COL["code"]].map(normalize_code)
    output = {}
    interesting = (
        "申购",
        "购买",
        "状态",
        "限额",
        "起购",
        "日累计",
        "单日",
        "开放",
        "赎回",
    )
    for _, row in df.iterrows():
        code = row.get(COL["code"])
        if not code:
            continue
        payload = {}
        for col in df.columns:
            if any(key in str(col) for key in interesting):
                value = row.get(col)
                if pd.notna(value):
                    payload[str(col)] = value
        output[code] = payload
    return output


def build_rank_maps(rank_df: pd.DataFrame) -> dict[tuple[str, str], dict[str, tuple[int, float, int]]]:
    rank_maps: dict[tuple[str, str], dict[str, tuple[int, float, int]]] = {}
    for fund_type, group in rank_df.groupby("rank_type"):
        total = len(group)
        for col in (COL["month1"], COL["month3"], COL["month6"], COL["year1"]):
            if col not in group.columns:
                continue
            ranks = group[col].rank(method="min", ascending=False)
            mapping = {}
            for code, rank in zip(group[COL["code"]], ranks):
                if pd.isna(rank):
                    continue
                mapping[str(code)] = (int(rank), round(float(rank) / total * 100, 2), total)
            rank_maps[(fund_type, col)] = mapping
    return rank_maps


def calc_nav_metrics(fund_code: str) -> dict:
    points = drawdown_mod.fetch_all_nav_points(fund_code)
    points = sorted(points, key=lambda item: item[0])
    if not points:
        return {"error": "净值数据为空"}

    dates = pd.to_datetime([item[0] for item in points])
    nav = np.array([float(item[1]) for item in points], dtype=float)
    result = {
        "nav_points": int(len(nav)),
        "latest_date": str(dates[-1].date()),
        "latest_nav": round(float(nav[-1]), 4),
    }
    if len(nav) < 80:
        result["error"] = "净值点不足80个"
        return result

    for label, days in (("1m", 22), ("3m", 66), ("6m", 132), ("1y", 252)):
        window_nav = nav[-days:] if len(nav) >= days else nav
        if len(window_nav) < 10:
            continue
        returns = np.diff(window_nav) / window_nav[:-1]
        period_return = window_nav[-1] / window_nav[0] - 1
        annual_return = float(np.mean(returns) * TRADING_DAYS) if len(returns) else math.nan
        annual_vol = (
            float(np.std(returns, ddof=1) * math.sqrt(TRADING_DAYS))
            if len(returns) > 1
            else math.nan
        )
        sharpe = (
            (annual_return - RISK_FREE_RATE) / annual_vol
            if annual_vol and annual_vol > 0
            else math.nan
        )
        running_max = np.maximum.accumulate(window_nav)
        drawdown = (window_nav - running_max) / running_max
        result[f"{label}_return_pct"] = round(float(period_return * 100), 2)
        result[f"{label}_maxdd_pct"] = round(float(drawdown.min() * 100), 2)
        result[f"{label}_vol_pct"] = round(float(annual_vol * 100), 2) if np.isfinite(annual_vol) else None
        result[f"{label}_sharpe"] = round(float(sharpe), 2) if np.isfinite(sharpe) else None

    nav_series = pd.Series(nav)
    for ma in (10, 20, 60):
        ma_value = nav_series.rolling(ma).mean().iloc[-1]
        result[f"MA{ma}"] = round(float(ma_value), 4)
        result[f"above_MA{ma}"] = bool(nav[-1] >= ma_value)

    result["drawdown_from_60d_high_pct"] = round(float((nav[-1] / max(nav[-60:]) - 1) * 100), 2)
    result["drawdown_from_132d_high_pct"] = round(float((nav[-1] / max(nav[-132:]) - 1) * 100), 2)
    return result


def is_excluded_name(name: str) -> bool:
    return any(keyword in name for keyword in EXCLUDE_KEYWORDS)


def fund_root_name(name: str) -> str:
    root = str(name)
    for suffix in ("A", "C"):
        if root.endswith(suffix):
            root = root[:-1]
    return root


def build_buy_plan(latest_nav: float | None, macro_state: str) -> dict:
    if latest_nav is None:
        return {}
    macro_state = (macro_state or "closed").lower()
    if macro_state in {"open", "normal", "green"}:
        macro_action = "可按观察仓/确认仓/主仓三段执行"
        observation_pct = "10%-20%"
    elif macro_state in {"half", "neutral", "yellow"}:
        macro_action = "减半执行，只允许观察仓或确认仓减半"
        observation_pct = "≤10%"
    else:
        macro_action = "宏观关闭，仅允许极小观察仓或等待"
        observation_pct = "单基≤账户总资产1%，不得执行常规计划仓"

    return {
        "macro_action": macro_action,
        "observation_position": observation_pct,
        "strong_no_pullback": {
            "trigger": "横盘5-10个交易日不跌破MA10，或新高后回落不破前低",
            "position": observation_pct,
        },
        "small_pullback": {
            "trigger": "从近期高点回撤3%-6%，回踩MA10/MA20后重新上涨",
            "target_nav_range": [
                round(latest_nav * 0.94, 4),
                round(latest_nav * 0.97, 4),
            ],
            "position": "计划仓位20%-30%；宏观关闭时降级为极小观察",
        },
        "healthy_pullback": {
            "trigger": "回撤8%-12%，连续3日不创新低，且同类排名未明显恶化",
            "target_nav_range": [
                round(latest_nav * 0.88, 4),
                round(latest_nav * 0.92, 4),
            ],
            "position": "计划仓位30%-40%；宏观关闭时等待",
        },
        "stop_rules": [
            "买入后亏损达到-5%停止加仓",
            "跌破MA20后5个交易日不收回停止加仓",
            "近1月收益转负或同类排名跌出前30%降级",
        ],
        "take_profit_rules": [
            "收益+15%卖出30%",
            "收益+25%再卖出40%",
            "余仓用MA20/MA60或-8%移动止盈",
        ],
    }


def score_candidate(row: dict) -> float:
    score = 0.0
    score += (row.get("r1m") or 0) * 0.20
    score += (row.get("r3m") or 0) * 0.35
    score += (row.get("r6m") or 0) * 0.45
    for period in ("1m", "3m", "6m"):
        sharpe = row.get(f"{period}_sharpe")
        if sharpe is not None:
            score += min(sharpe, 8) * 2
        maxdd = row.get(f"{period}_maxdd_pct")
        if maxdd is not None:
            if maxdd >= -8:
                score += 5
            elif maxdd <= -15:
                score -= 8
    if row.get("above_MA10") and row.get("above_MA20") and row.get("above_MA60"):
        score += 12
    if row.get("申购状态") == "开放申购":
        score += 8
    elif row.get("申购状态") == "限大额":
        limit = parse_float(row.get("日累计限定金额"))
        if limit is not None and limit >= 1000:
            score += 4
        else:
            score -= 8
    return round(score, 2)


def screen_strong_funds(
    types: tuple[str, ...] = DEFAULT_TYPES,
    top: int = 12,
    max_candidates: int = 45,
    min_daily_limit: float = 1000,
    macro_state: str = "closed",
) -> dict:
    fund_types = [TYPE_MAP.get(item, item) for item in types]
    rank_frames = [fetch_rank_dataframe(fund_type) for fund_type in fund_types]
    rank_df = pd.concat(rank_frames, ignore_index=True)
    rank_maps = build_rank_maps(rank_df)
    purchase_map = fetch_purchase_map()

    candidates = rank_df[
        (rank_df[COL["month1"]] > 0)
        & (rank_df[COL["month3"]] > 0)
        & (rank_df[COL["month6"]] > 0)
    ].copy()
    candidates = candidates[
        ~candidates[COL["name"]].astype(str).map(is_excluded_name)
    ].copy()
    candidates["return_score"] = (
        candidates[COL["month1"]] * 0.20
        + candidates[COL["month3"]] * 0.35
        + candidates[COL["month6"]] * 0.45
    )
    candidates = candidates.sort_values("return_score", ascending=False).head(max_candidates)

    rows = []
    seen_roots = set()
    for _, item in candidates.iterrows():
        code = normalize_code(item.get(COL["code"]))
        name = str(item.get(COL["name"]) or "")
        root = fund_root_name(name)
        if name.endswith("C") and root in seen_roots:
            continue
        seen_roots.add(root)

        row = {
            "fund_code": code,
            "fund_name": name,
            "rank_type": item.get("rank_type"),
            "rank_date": str(item.get(COL["date"])),
            "rank_no": int(item.get(COL["seq"])),
            "r1m": parse_float(item.get(COL["month1"])),
            "r3m": parse_float(item.get(COL["month3"])),
            "r6m": parse_float(item.get(COL["month6"])),
            "r1y": parse_float(item.get(COL["year1"])),
            "return_score": round(float(item.get("return_score")), 2),
        }
        for col, key in (
            (COL["month1"], "1m_rank"),
            (COL["month3"], "3m_rank"),
            (COL["month6"], "6m_rank"),
            (COL["year1"], "1y_rank"),
        ):
            rank_info = rank_maps.get((item.get("rank_type"), col), {}).get(code)
            if rank_info:
                row[f"{key}_no"] = rank_info[0]
                row[f"{key}_pct"] = rank_info[1]
                row[f"{key}_total"] = rank_info[2]

        purchase = purchase_map.get(code, {})
        row.update(purchase)
        daily_limit = parse_float(purchase.get("日累计限定金额"))
        purchase_status = str(purchase.get("申购状态") or "")
        executable = purchase_status in {"开放申购", "限大额"} and (
            daily_limit is None or daily_limit >= min_daily_limit
        )
        row["purchase_executable"] = executable

        row.update(calc_nav_metrics(code))
        row["v624_buy_plan"] = build_buy_plan(row.get("latest_nav"), macro_state)
        row["strong_fund_score"] = score_candidate(row)
        rows.append(row)

    rows = sorted(rows, key=lambda item: item.get("strong_fund_score", 0), reverse=True)
    result = {
        "analysis_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "types": fund_types,
        "top": top,
        "max_candidates": max_candidates,
        "min_daily_limit": min_daily_limit,
        "macro_state": macro_state,
        "macro_note": (
            "宏观关闭时，候选基金只能用于极小观察仓或等待买点；"
            "不能把强势趋势跟随当作正常加仓。"
        ),
        "candidates": rows[:top],
        "source": "AkShare/Eastmoney fund_open_fund_rank_em + fund_purchase_em + Eastmoney NAV",
    }
    return json_safe(result)


def print_table(result: dict) -> None:
    print_banner("强势基金趋势跟随筛选 v6.24", char="═")
    print(
        f"分析时间: {result['analysis_time']} | 类型: {', '.join(result['types'])} | "
        f"宏观状态: {result['macro_state']}"
    )
    print(f"说明: {result['macro_note']}")
    rows = []
    for item in result.get("candidates", []):
        rows.append(
            {
                "代码": item.get("fund_code"),
                "名称": item.get("fund_name"),
                "类型": item.get("rank_type"),
                "近1月%": item.get("r1m"),
                "近3月%": item.get("r3m"),
                "近6月%": item.get("r6m"),
                "1月排名%": item.get("1m_rank_pct"),
                "3月排名%": item.get("3m_rank_pct"),
                "6月排名%": item.get("6m_rank_pct"),
                "1月回撤%": item.get("1m_maxdd_pct"),
                "3月回撤%": item.get("3m_maxdd_pct"),
                "6月回撤%": item.get("6m_maxdd_pct"),
                "1月夏普": item.get("1m_sharpe"),
                "3月夏普": item.get("3m_sharpe"),
                "6月夏普": item.get("6m_sharpe"),
                "MA10/20/60": (
                    f"{item.get('above_MA10')}/"
                    f"{item.get('above_MA20')}/"
                    f"{item.get('above_MA60')}"
                ),
                "申购": item.get("申购状态"),
                "日限额": item.get("日累计限定金额"),
                "评分": item.get("strong_fund_score"),
            }
        )
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    else:
        print("未筛出候选基金。")


def main() -> None:
    parser = argparse.ArgumentParser(description="v6.24 强势基金趋势跟随筛选")
    parser.add_argument(
        "--types",
        nargs="+",
        default=list(DEFAULT_TYPES),
        help="基金类型：mixed stock all；也可直接传 AkShare 中文类型",
    )
    parser.add_argument("--top", type=int, default=12, help="输出Top N候选")
    parser.add_argument("--max-candidates", type=int, default=45, help="收益预筛最多处理多少只")
    parser.add_argument("--min-daily-limit", type=float, default=1000, help="最低可接受日累计限额")
    parser.add_argument(
        "--macro-state",
        default="closed",
        choices=("open", "normal", "green", "half", "neutral", "yellow", "closed", "downtrend", "red"),
        help="宏观总开关状态；closed/red/downtrend 只允许极小观察仓",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--no-save", action="store_true", help="不保存JSON结果")
    args = parser.parse_args()

    result = screen_strong_funds(
        types=tuple(args.types),
        top=args.top,
        max_candidates=args.max_candidates,
        min_daily_limit=args.min_daily_limit,
        macro_state=args.macro_state,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_table(result)

    if not args.no_save:
        path = save_result(result, "strong_fund_screener", subdir="20_strong_fund")
        print(f"\n[OK] 结果已保存: {path}")


if __name__ == "__main__":
    main()
