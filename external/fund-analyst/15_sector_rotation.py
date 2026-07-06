#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
15_sector_rotation.py —— v6.4 行业/主题轮动软闸门

用途：
    python 15_sector_rotation.py <基金代码> [主题名]

说明：
    本脚本只使用可量化的净值/指数价格行为做轮动判断，不输出主观行业故事。
    结论是软闸门：不能覆盖 v6.0 止损、v6.2 外围风险、v6.3 量化验证硬规则。
"""

from __future__ import annotations

import sys
from datetime import datetime

import akshare as ak
import numpy as np
import pandas as pd

from config import get_logger, print_banner, save_result


logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


FUND_THEME_MAP = {
    "013309": {"theme": "恒生科技/港股科技", "proxy_type": "hk_index", "proxy": "HSTECH"},
    "160221": {"theme": "有色金属", "proxy_type": "a_board", "proxy": "有色金属"},
    "012643": {"theme": "中证红利/高股息", "proxy_type": "fund_only", "proxy": None},
    "011892": {"theme": "成长混合", "proxy_type": "fund_only", "proxy": None},
    "001688": {"theme": "全球互联网/QDII", "proxy_type": "fund_only", "proxy": None},
    "519771": {"theme": "主动权益/回报优选", "proxy_type": "fund_only", "proxy": None},
}


def _to_numeric_series(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def standardize_price_df(df: pd.DataFrame, value_candidates: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    date_col = _to_numeric_series(df, ["净值日期", "日期", "date", "Date"])
    value_col = _to_numeric_series(df, value_candidates)
    if not date_col or not value_col:
        return pd.DataFrame()

    out = df[[date_col, value_col]].copy()
    out.columns = ["date", "price"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out = out.dropna().sort_values("date").reset_index(drop=True)
    return out


def get_fund_price(fund_code: str) -> pd.DataFrame:
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df.empty:
            df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        return standardize_price_df(df, ["单位净值", "累计净值", "净值"])
    except Exception as exc:
        logger.warning(f"获取基金{fund_code}净值失败: {exc}")
        return pd.DataFrame()


def get_proxy_price(proxy_type: str, proxy: str | None) -> pd.DataFrame:
    if not proxy or proxy_type == "fund_only":
        return pd.DataFrame()
    try:
        if proxy_type == "hk_index":
            df = ak.stock_hk_index_daily_sina(symbol=proxy)
            return standardize_price_df(df, ["收盘", "close", "收盘价"])
        if proxy_type == "a_board":
            end_date = datetime.now().strftime("%Y%m%d")
            df = ak.stock_board_industry_hist_em(
                symbol=proxy,
                period="日k",
                start_date="20240101",
                end_date=end_date,
                adjust="",
            )
            return standardize_price_df(df, ["收盘", "close", "收盘价"])
    except Exception as exc:
        logger.warning(f"获取主题代理{proxy}失败: {exc}")
    return pd.DataFrame()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calc_rotation_metrics(price_df: pd.DataFrame) -> dict:
    if price_df.empty or len(price_df) < 65:
        return {"error": "价格序列不足，至少需要65个交易日"}

    df = price_df.copy().reset_index(drop=True)
    df["ret"] = df["price"].pct_change()
    df["ma20"] = df["price"].rolling(20).mean()
    df["ma60"] = df["price"].rolling(60).mean()

    current = float(df["price"].iloc[-1])
    ret20 = current / float(df["price"].iloc[-21]) - 1
    ret60 = current / float(df["price"].iloc[-61]) - 1
    ma20 = float(df["ma20"].iloc[-1])
    ma60 = float(df["ma60"].iloc[-1])
    ma60_prev = float(df["ma60"].iloc[-21])
    ma60_slope20 = ma60 / ma60_prev - 1 if ma60_prev else 0
    high60 = float(df["price"].tail(60).max())
    drawdown60 = current / high60 - 1 if high60 else 0
    vol20 = float(df["ret"].tail(20).std() * np.sqrt(252)) if df["ret"].tail(20).notna().any() else 0

    score = 50
    score += clamp(ret20 * 250, -25, 25)
    score += clamp(ret60 * 160, -30, 30)
    score += 10 if current > ma60 else -10
    score += 10 if ma60_slope20 > 0 else -10
    score += clamp(drawdown60 * 120, -15, 0)
    score = round(clamp(score, 0, 100), 1)

    if score >= 70 and ret20 > 0 and ret60 > 0 and current > ma60:
        label = "🟢 顺势向上"
        action = "可作为持有或轻仓买入的辅助加分，但仍需通过硬闸门"
    elif score >= 50:
        label = "🟡 分化观察"
        action = "不追涨不重仓，等待技术与量化硬闸门确认"
    else:
        label = "🔴 逆势走弱"
        action = "禁止加仓；已持仓按止损/减仓纪律处理"

    return {
        "latest_date": str(df["date"].iloc[-1].date()),
        "current_price": round(current, 4),
        "ret20_pct": round(ret20 * 100, 2),
        "ret60_pct": round(ret60 * 100, 2),
        "ma20": round(ma20, 4),
        "ma60": round(ma60, 4),
        "above_ma60": current > ma60,
        "ma60_slope20_pct": round(ma60_slope20 * 100, 2),
        "drawdown60_pct": round(drawdown60 * 100, 2),
        "vol20_annual_pct": round(vol20 * 100, 2),
        "rotation_score": score,
        "rotation_label": label,
        "action": action,
    }


def combine_fund_proxy(fund_metrics: dict, proxy_metrics: dict) -> dict:
    if fund_metrics.get("error"):
        return {
            "combined_score": 0,
            "combined_label": "🔴 数据不足",
            "combined_action": "无法使用行业轮动软闸门",
        }

    fund_score = float(fund_metrics.get("rotation_score", 0))
    if proxy_metrics and not proxy_metrics.get("error"):
        proxy_score = float(proxy_metrics.get("rotation_score", 0))
        combined = round(fund_score * 0.6 + proxy_score * 0.4, 1)
        source = "基金净值60% + 主题代理40%"
    else:
        combined = round(fund_score, 1)
        source = "仅基金净值（主题代理缺失或不适用）"

    if combined >= 70:
        label = "🟢 轮动顺势"
        action = "可保留观察；只有硬闸门也通过时才允许买入"
    elif combined >= 50:
        label = "🟡 轮动中性/分化"
        action = "不加仓；已有仓位设置移动止损"
    else:
        label = "🔴 轮动逆势"
        action = "禁止加仓；亏损仓优先减仓或止损"

    return {
        "combined_score": combined,
        "combined_label": label,
        "combined_action": action,
        "score_source": source,
    }


def analyze_sector_rotation(fund_code: str, theme: str | None = None) -> dict:
    meta = FUND_THEME_MAP.get(fund_code, {"theme": theme or "未知主题", "proxy_type": "fund_only", "proxy": None})
    if theme:
        meta = {**meta, "theme": theme}

    print_banner(f"v6.4 行业/主题轮动软闸门 | {fund_code}", char="═")
    fund_price = get_fund_price(fund_code)
    proxy_price = get_proxy_price(meta.get("proxy_type"), meta.get("proxy"))

    fund_metrics = calc_rotation_metrics(fund_price)
    proxy_metrics = calc_rotation_metrics(proxy_price) if not proxy_price.empty else {"note": "无主题代理数据"}
    combined = combine_fund_proxy(fund_metrics, proxy_metrics)

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme": meta.get("theme"),
        "proxy": meta.get("proxy"),
        "proxy_type": meta.get("proxy_type"),
        "fund_metrics": fund_metrics,
        "proxy_metrics": proxy_metrics,
        "combined_gate": combined,
        "method_note": "v6.4为行业/主题轮动软闸门，只能降级或提醒，不能覆盖止损、外围风险与v6.3量化硬闸门",
    }

    print(f"\n【主题】{result['theme']}  代理: {result['proxy'] or '无'}")
    if fund_metrics.get("error"):
        print(f"基金净值动量: 数据不足 - {fund_metrics['error']}")
    else:
        print(
            f"基金净值动量: 20日{fund_metrics['ret20_pct']}%  60日{fund_metrics['ret60_pct']}%  "
            f"距60日高点{fund_metrics['drawdown60_pct']}%  得分{fund_metrics['rotation_score']}/100  "
            f"{fund_metrics['rotation_label']}"
        )
    if proxy_metrics and not proxy_metrics.get("error") and not proxy_metrics.get("note"):
        print(
            f"主题代理动量: 20日{proxy_metrics['ret20_pct']}%  60日{proxy_metrics['ret60_pct']}%  "
            f"距60日高点{proxy_metrics['drawdown60_pct']}%  得分{proxy_metrics['rotation_score']}/100  "
            f"{proxy_metrics['rotation_label']}"
        )
    else:
        print(f"主题代理动量: {proxy_metrics.get('note') or proxy_metrics.get('error') or '未使用'}")

    print(f"\n【轮动结论】{combined['combined_label']}  得分 {combined['combined_score']}/100")
    print(f"  动作: {combined['combined_action']}")
    print(f"  说明: {combined['score_source']}")

    save_path = save_result(result, f"sector_rotation_{fund_code}", subdir="15_sector_rotation")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 15_sector_rotation.py <基金代码> [主题名]")
        print("示例: python 15_sector_rotation.py 013309")
        sys.exit(1)

    code = sys.argv[1]
    input_theme = sys.argv[2] if len(sys.argv) >= 3 else None
    analyze_sector_rotation(code, input_theme)
