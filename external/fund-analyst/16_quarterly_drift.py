"""
16_quarterly_drift.py -- 季报滞后与调仓偏差识别
================================================
基于用户提供的 fund_analyzer.py 思路，抽取可落地的数据层：
  · 获取基金盘中实时估算涨幅
  · 获取最新季报前10大重仓股
  · 获取重仓股实时涨跌幅
  · 计算“季报持仓理论加权涨幅”
  · 对比基金实时估算涨幅，识别季报滞后/基金经理可能已调仓

用法：
    python 16_quarterly_drift.py 001438
"""

from __future__ import annotations

import json
import inspect
import re
import sys
import time
from datetime import datetime

import requests
import pandas as pd

from config import get_logger, print_banner, save_result, with_cache, with_retry


logger = get_logger(__name__)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    )
}


def normalize_stock_code(code: str) -> str:
    digits = re.sub(r"\D", "", str(code))
    if not digits:
        return ""
    if digits.startswith(("6", "9")):
        return f"SH{digits[-6:]}"
    return f"SZ{digits[-6:]}"


def parse_float(value) -> float | None:
    if value in (None, "", "---", "--"):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


@with_retry()
@with_cache(cache_type="realtime")
def get_fund_realtime(fund_code: str) -> dict:
    """天天基金盘中实时估值。"""
    url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js?rt={int(time.time() * 1000)}"
    response = requests.get(url, headers=HTTP_HEADERS, timeout=10)
    response.raise_for_status()
    match = re.search(r"jsonpgz\((.*)\)", response.text)
    if not match:
        return {"fund_code": fund_code, "error": "实时估值接口未返回 jsonpgz"}
    data = json.loads(match.group(1))
    return {
        "fund_code": data.get("fundcode") or fund_code,
        "fund_name": data.get("name"),
        "est_nav": parse_float(data.get("gsz")),
        "est_change_pct": parse_float(data.get("gszzl")),
        "est_time": data.get("gztime"),
        "last_nav": parse_float(data.get("dwjz")),
        "last_date": data.get("jzrq"),
    }


@with_retry()
@with_cache(cache_type="quarterly")
def get_latest_holdings(fund_code: str) -> dict:
    """复用 02_fund_holdings.py 的 AkShare 持仓解析。"""
    import importlib.util
    import os

    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_fund_holdings.py")
    spec = importlib.util.spec_from_file_location("fund_holdings_mod", filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 02_fund_holdings.py caches its raw DataFrame via JSON and may reload it as
    # a string. Call the undecorated fetcher here; this function caches only the
    # parsed JSON-safe dict.
    holdings_fetcher = inspect.unwrap(module.get_fund_holdings)
    holdings_df = holdings_fetcher(fund_code)
    if not isinstance(holdings_df, pd.DataFrame):
        return {"fund_code": fund_code, "error": "季报持仓缓存格式异常，未获取到DataFrame"}
    if holdings_df.empty:
        return {"fund_code": fund_code, "error": "未获取到季报持仓"}
    parsed = module.parse_latest_two_quarters(holdings_df)
    parsed["fund_code"] = fund_code
    return parsed


@with_retry()
@with_cache(cache_type="realtime")
def get_stock_realtime(stock_codes: list[str]) -> dict[str, dict]:
    """腾讯行情 API，返回6位股票代码 -> 实时涨跌幅。"""
    normalized = [normalize_stock_code(code) for code in stock_codes]
    normalized = [code for code in normalized if code]
    if not normalized:
        return {}

    tencent_codes = [code.lower() for code in normalized]
    url = f"https://qt.gtimg.cn/q={','.join(tencent_codes)}"
    response = requests.get(url, headers=HTTP_HEADERS, timeout=10)
    response.raise_for_status()

    result: dict[str, dict] = {}
    for line in response.text.strip().splitlines():
        match = re.match(r'v_([a-z]{2}\d+)="(.*)"', line)
        if not match:
            continue
        fields = match.group(2).split("~")
        if len(fields) < 33:
            continue
        name = fields[1]
        code6 = fields[2]
        price = fields[3]
        yesterday = fields[4]
        # Tencent quote fields: 31 = change amount, 32 = change percent.
        change_amt = fields[31]
        change_pct = fields[32]
        result[code6] = {
            "name": name,
            "price": parse_float(price),
            "yesterday": parse_float(yesterday),
            "change_amt": parse_float(change_amt),
            "change_pct": parse_float(change_pct),
        }
    return result


def estimate_holdings_weighted_change(top_holdings: list[dict], stock_quotes: dict[str, dict]) -> dict:
    weighted_change = 0.0
    covered_weight = 0.0
    missing = []
    details = []

    for holding in top_holdings:
        code = re.sub(r"\D", "", str(holding.get("code", "")))[-6:]
        ratio = parse_float(holding.get("ratio"))
        quote = stock_quotes.get(code, {})
        change_pct = quote.get("change_pct")
        if ratio is None or change_pct is None:
            missing.append({"code": code, "name": holding.get("name"), "ratio": ratio})
            continue
        contribution = ratio / 100 * change_pct
        weighted_change += contribution
        covered_weight += ratio
        details.append(
            {
                "code": code,
                "name": holding.get("name") or quote.get("name"),
                "ratio": ratio,
                "stock_change_pct": change_pct,
                "contribution_pct": round(contribution, 4),
            }
        )

    if not details or covered_weight <= 0:
        weighted_change_pct = None
        normalized_weighted_change = None
    else:
        weighted_change_pct = round(weighted_change, 4)
        normalized_weighted_change = weighted_change / covered_weight * 100

    return {
        "weighted_change_pct": weighted_change_pct,
        "normalized_weighted_change_pct": round(normalized_weighted_change, 4) if normalized_weighted_change is not None else None,
        "covered_weight_pct": round(covered_weight, 2),
        "missing_count": len(missing),
        "missing": missing,
        "details": details,
    }


def classify_drift(est_change_pct: float | None, weighted_change_pct: float | None, covered_weight_pct: float) -> dict:
    if est_change_pct is None or weighted_change_pct is None:
        return {
            "level": "unknown",
            "bias_pct": None,
            "abs_bias_pct": None,
            "action": "实时估值或持仓理论涨幅缺失，不能判断季报滞后",
            "possible_rebalance": None,
        }

    bias = round(est_change_pct - weighted_change_pct, 4)
    abs_bias = abs(bias)

    if covered_weight_pct < 30:
        level = "data_weak"
        action = "前十大覆盖仓位过低，偏差只能作为弱参考"
        possible_rebalance = None
    elif abs_bias >= 2.0:
        level = "high_drift"
        direction = "实际估值明显强于季报持仓理论涨幅" if bias > 0 else "实际估值明显弱于季报持仓理论涨幅"
        action = f"{direction}，高度提示季报持仓可能滞后或基金经理已明显调仓"
        possible_rebalance = True
    elif abs_bias >= 1.0:
        level = "medium_drift"
        action = "估值偏差中等，提示季报持仓可能部分滞后，买卖决策需降低对季报持仓的依赖"
        possible_rebalance = True
    elif abs_bias >= 0.5:
        level = "low_drift"
        action = "估值偏差较小但可观察，季报持仓解释力略有不足"
        possible_rebalance = False
    else:
        level = "aligned"
        action = "实时估值与季报持仓理论涨幅基本一致，季报持仓仍具备较好解释力"
        possible_rebalance = False

    return {
        "level": level,
        "bias_pct": bias,
        "abs_bias_pct": round(abs_bias, 4),
        "action": action,
        "possible_rebalance": possible_rebalance,
    }


def analyze_quarterly_drift(fund_code: str) -> dict:
    print_banner(f"季报滞后与调仓偏差识别 | {fund_code}")

    realtime = get_fund_realtime(fund_code)
    holdings = get_latest_holdings(fund_code)
    top_holdings = holdings.get("top10_holdings", []) if isinstance(holdings, dict) else []
    stock_codes = [item.get("code", "") for item in top_holdings]
    stock_quotes = get_stock_realtime(stock_codes) if stock_codes else {}
    theoretical = estimate_holdings_weighted_change(top_holdings, stock_quotes)
    drift = classify_drift(
        realtime.get("est_change_pct"),
        theoretical.get("weighted_change_pct"),
        theoretical.get("covered_weight_pct") or 0,
    )

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "realtime": realtime,
        "latest_quarter": holdings.get("latest_quarter") if isinstance(holdings, dict) else None,
        "prev_quarter": holdings.get("prev_quarter") if isinstance(holdings, dict) else None,
        "top10_total_weight_pct": holdings.get("total_top10_ratio") if isinstance(holdings, dict) else None,
        "theoretical_from_quarterly_holdings": theoretical,
        "valuation_bias": drift,
        "holdings_changes": holdings.get("holdings_changes", []) if isinstance(holdings, dict) else [],
        "method_note": (
            "用最新季报前十大持仓的实时涨跌幅按持仓占比估算理论涨幅，"
            "再与天天基金实时估算涨幅比较。偏差越大，越说明季报持仓可能滞后，"
            "基金经理可能已调仓；该指标只能作为持仓解释力和数据质量闸门，不能单独做买卖信号。"
        ),
    }

    print(f"\n基金估算涨幅: {realtime.get('est_change_pct')}%  时间: {realtime.get('est_time')}")
    print(f"季报持仓理论涨幅: {theoretical.get('weighted_change_pct')}%")
    print(f"覆盖仓位: {theoretical.get('covered_weight_pct')}%")
    print(f"估值偏差: {drift.get('bias_pct')}%  等级: {drift.get('level')}")
    print(f"结论: {drift.get('action')}")

    save_path = save_result(result, f"quarterly_drift_{fund_code}", subdir="16_quarterly_drift")
    print(f"\n[OK] 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 16_quarterly_drift.py <基金代码>")
        sys.exit(1)
    analyze_quarterly_drift(sys.argv[1])
