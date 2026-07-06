#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
19_macro_bear_signal.py -- A股宏观熊市/下行风险预警
====================================================

用途：
    python 19_macro_bear_signal.py
    python 19_macro_bear_signal.py --no-save

逻辑：
  1. 先评估宏观熊市风险，而不是把国家队/ETF资金流单独当买卖按钮。
  2. 使用 PMI、社融/信贷脉冲代理、沪深300估值分位、ROE周期四类因子。
  3. 明确标记数据新鲜度：过期或代理数据只能降权，不能支撑强动作。
  4. 将“国家队减仓=必然暴跌”“北向流出=看空”等已弱化/证伪信号排除出硬闸门。

说明：
  本脚本是 Step 2.5 / Step 6.0statefund 的宏观上层校准工具。
  输出用于仓位上限和风险降级，不构成投资建议。
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime
from typing import Any

import akshare as ak
import pandas as pd

from config import get_logger, print_banner, save_result, with_cache, with_retry


warnings.filterwarnings("ignore")
logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TODAY = pd.Timestamp(datetime.now().date())


def freshness_status(data_date: Any, max_age_days: int) -> dict[str, Any]:
    """Return data freshness metadata used by the risk gate."""
    if data_date in (None, "", "N/A"):
        return {"status": "unknown", "age_days": None, "is_fresh": False}
    date = pd.to_datetime(data_date, errors="coerce")
    if pd.isna(date):
        return {"status": "unknown", "age_days": None, "is_fresh": False}
    age_days = int((TODAY - pd.Timestamp(date.date())).days)
    if age_days <= max_age_days:
        status = "fresh"
    elif age_days <= max_age_days * 2:
        status = "stale"
    else:
        status = "very_stale"
    return {"status": status, "age_days": age_days, "is_fresh": status == "fresh"}


def stale_penalty(score: float, freshness: dict[str, Any]) -> float:
    """Penalize stale macro data without inventing a directional signal."""
    status = freshness.get("status")
    if status == "fresh":
        return score
    if status == "stale":
        return score * 0.75
    if status == "very_stale":
        return score * 0.50
    return score * 0.60


@with_retry()
@with_cache(cache_type="daily")
def fetch_pmi() -> pd.DataFrame:
    return ak.macro_china_pmi_yearly()


def get_pmi_signal() -> dict[str, Any]:
    """制造业PMI趋势评估。"""
    try:
        df = fetch_pmi()
        df = df[df["商品"] == "中国官方制造业PMI"].copy()
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df["今值"] = pd.to_numeric(df["今值"], errors="coerce")
        df = df.dropna(subset=["日期", "今值"]).sort_values("日期").tail(6)
        if df.empty:
            raise ValueError("PMI data is empty")
        values = df["今值"].tolist()
        latest = float(values[-1])
        trend = latest - float(values[-3]) if len(values) >= 3 else 0.0
        data_date = df["日期"].iloc[-1].date()
        freshness = freshness_status(data_date, 45)

        if latest >= 51:
            raw_score, status = 25, "扩张"
        elif latest >= 50:
            raw_score, status = 18, "临界扩张"
        elif latest >= 49:
            raw_score, status = 10, "轻度收缩"
        else:
            raw_score, status = 3, "明显收缩"

        score = stale_penalty(raw_score, freshness)
        return {
            "name": "制造业PMI",
            "score": round(score, 1),
            "raw_score": raw_score,
            "max_score": 25,
            "value": latest,
            "status": status,
            "note": f"3月变化{trend:+.1f}",
            "data_date": str(data_date),
            "freshness": freshness,
            "source": "ak.macro_china_pmi_yearly",
        }
    except Exception as exc:
        logger.warning(f"PMI数据获取失败: {exc}")
        return {
            "name": "制造业PMI",
            "score": 8,
            "raw_score": 8,
            "max_score": 25,
            "value": None,
            "status": "数据缺失",
            "note": str(exc),
            "data_date": None,
            "freshness": {"status": "unknown", "age_days": None, "is_fresh": False},
            "source": "ak.macro_china_pmi_yearly",
        }


@with_retry()
@with_cache(cache_type="daily")
def fetch_social_financing() -> pd.DataFrame:
    return ak.macro_china_shrzgm()


def get_social_financing_signal() -> dict[str, Any]:
    """社融增速评估，作为信贷脉冲代理。"""
    try:
        df = fetch_social_financing().copy()
        df["月份"] = pd.to_datetime(df["月份"].astype(str), errors="coerce")
        df["社会融资规模增量"] = pd.to_numeric(df["社会融资规模增量"], errors="coerce")
        df = df.dropna(subset=["月份", "社会融资规模增量"]).sort_values("月份").tail(24)
        if len(df) < 13:
            raise ValueError("社融样本不足13个月")
        latest_13 = df.tail(13)
        sf = latest_13["社会融资规模增量"].astype(float)
        recent_12 = sf.tail(12).sum()
        prev_12 = sf.head(12).sum()
        yoy = (recent_12 - prev_12) / abs(prev_12) if prev_12 != 0 else 0.0
        base_mean = sf.tail(12).mean()
        momentum = sf.tail(3).mean() / base_mean - 1 if base_mean != 0 else 0.0
        data_date = latest_13["月份"].iloc[-1].date()
        freshness = freshness_status(data_date, 75)

        if yoy > 0.10:
            raw_score, status = 22, "社融明显扩张"
        elif yoy > 0:
            raw_score, status = 16, "社融温和扩张"
        elif yoy > -0.10:
            raw_score, status = 8, "社融收缩"
        else:
            raw_score, status = 2, "社融大幅收缩"

        score = stale_penalty(raw_score, freshness)
        return {
            "name": "社融增速/信贷脉冲代理",
            "score": round(score, 1),
            "raw_score": raw_score,
            "max_score": 22,
            "value": round(yoy, 4),
            "status": status,
            "note": f"同比{yoy:+.1%} | 近3月动能{momentum:+.1%} | 2022后降权使用",
            "data_date": str(data_date),
            "freshness": freshness,
            "source": "ak.macro_china_shrzgm",
        }
    except Exception as exc:
        logger.warning(f"社融数据获取失败: {exc}")
        return {
            "name": "社融增速/信贷脉冲代理",
            "score": 7,
            "raw_score": 7,
            "max_score": 22,
            "value": None,
            "status": "数据缺失",
            "note": str(exc),
            "data_date": None,
            "freshness": {"status": "unknown", "age_days": None, "is_fresh": False},
            "source": "ak.macro_china_shrzgm",
        }


@with_retry()
@with_cache(cache_type="daily")
def fetch_csi300_valuation() -> pd.DataFrame:
    return ak.stock_zh_index_value_csindex(symbol="000300")


@with_retry()
@with_cache(cache_type="daily")
def fetch_csi300_price() -> pd.DataFrame:
    return ak.stock_zh_index_daily(symbol="sh000300")


def get_csi300_valuation_signal() -> dict[str, Any]:
    """沪深300真实PE分位；失败时退回价格分位代理。"""
    try:
        df = fetch_csi300_valuation().copy()
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df["市盈率1"] = pd.to_numeric(df["市盈率1"], errors="coerce")
        df = df.dropna(subset=["日期", "市盈率1"]).sort_values("日期")
        cutoff = df["日期"].max() - pd.DateOffset(years=5)
        subset = df[df["日期"] >= cutoff]["市盈率1"]
        current = float(subset.iloc[-1])
        percentile = float((subset < current).sum() / len(subset))
        data_date = df["日期"].iloc[-1].date()
        freshness = freshness_status(data_date, 7)
        source = "ak.stock_zh_index_value_csindex(000300)"
        proxy = False
    except Exception as exc:
        logger.warning(f"沪深300PE数据失败，退回价格分位代理: {exc}")
        df = fetch_csi300_price().copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date")
        cutoff = df["date"].max() - pd.DateOffset(years=5)
        subset = df[df["date"] >= cutoff]["close"]
        current = float(subset.iloc[-1])
        percentile = float((subset < current).sum() / len(subset))
        data_date = df["date"].iloc[-1].date()
        freshness = freshness_status(data_date, 7)
        source = "ak.stock_zh_index_daily(sh000300), price percentile proxy"
        proxy = True

    if percentile < 0.20:
        raw_score, status = 25, "极度低估"
    elif percentile < 0.35:
        raw_score, status = 20, "低估"
    elif percentile < 0.55:
        raw_score, status = 13, "合理"
    elif percentile < 0.75:
        raw_score, status = 6, "偏贵"
    else:
        raw_score, status = 1, "高估"

    score = stale_penalty(raw_score, freshness)
    unit = "点位" if proxy else "PE"
    return {
        "name": "沪深300估值分位",
        "score": round(score, 1),
        "raw_score": raw_score,
        "max_score": 25,
        "value": round(current, 2),
        "percentile": round(percentile, 4),
        "status": status,
        "note": f"当前{unit}{current:.2f} | 5年分位{percentile:.1%}" + (" | 价格分位代理" if proxy else ""),
        "data_date": str(data_date),
        "freshness": freshness,
        "source": source,
        "proxy": proxy,
    }


def get_roe_signal() -> dict[str, Any]:
    """全市场ROE周期。当前作为人工复核锚点，不能视为实时数据。"""
    known_data = {
        "2020Q4": 9.5,
        "2021Q4": 10.2,
        "2022Q4": 8.8,
        "2023Q4": 8.2,
        "2024Q4": 7.92,
        "2026Q1": "回升(首次)",
    }
    return {
        "name": "全市场ROE周期",
        "score": 15,
        "raw_score": 15,
        "max_score": 20,
        "value": known_data,
        "status": "2026Q1首次回升，需2个季度以上确认",
        "note": "ROE底部确认是中期积极信号，但不是短线买入触发器",
        "data_date": "2026Q1",
        "freshness": {"status": "manual_anchor", "age_days": None, "is_fresh": False},
        "source": "manual historical ROE anchor; requires professional refresh",
    }


def compute_macro_score(signals: list[dict[str, Any]]) -> dict[str, Any]:
    weights = {
        "沪深300估值分位": 0.35,
        "制造业PMI": 0.25,
        "社融增速/信贷脉冲代理": 0.20,
        "全市场ROE周期": 0.20,
    }
    total = 0.0
    max_total = 0.0
    for item in signals:
        weight = weights[item["name"]]
        total += item["score"] * weight
        max_total += item["max_score"] * weight
    normalized = total / max_total * 100 if max_total else 0.0

    if normalized >= 70:
        level = "低风险"
        advice = "宏观环境支持持有/买入，但仍需通过大盘总开关和技术闸门"
        position_cap = "按常规上限"
    elif normalized >= 50:
        level = "中等风险"
        advice = "宏观有隐忧，权益仓位建议控制在60%以内"
        position_cap = "≤60%"
    elif normalized >= 35:
        level = "较高风险"
        advice = "多项指标恶化，权益仓位建议控制在40%以内，优先防守资产"
        position_cap = "≤40%"
    else:
        level = "高风险"
        advice = "宏观下行风险高，强买入/追涨应暂停，已有仓位优先降风险"
        position_cap = "≤20-30%"

    stale_items = [i["name"] for i in signals if i["freshness"]["status"] in {"stale", "very_stale", "unknown"}]
    data_quality = "完整" if not stale_items else "需复核"
    return {
        "score": round(normalized, 1),
        "level": level,
        "advice": advice,
        "position_cap": position_cap,
        "data_quality": data_quality,
        "stale_items": stale_items,
    }


def active_avoidance_rules() -> list[str]:
    return [
        "出现类似资管新规级别的监管政策突发",
        "PMI连续3个月<49且社融同比负增长",
        "全球VIX>30且沪深300单周跌幅>5%",
        "沪深300PE/估值分位>80%且价格跌破MA60",
    ]


def invalidated_rules() -> list[str]:
    return [
        "国家队减仓 = 必然暴跌：需要区分急停护盘、渐进式减仓、ETF套利缓冲和接盘资金。",
        "北向资金流出 = 单独看空：2021年后预测力下降，只能作辅助背景。",
        "ETF份额下降 = 立刻清仓：必须结合价格、成交额、持有人结构、估值和宏观风险。",
    ]


def historical_bear_reference() -> list[dict[str, str]]:
    return [
        {"period": "2007-10→2008-10", "drop": "-70.9%", "cause": "全球金融危机+国内紧缩"},
        {"period": "2009-08→2012-12", "drop": "-43.1%", "cause": "刺激退出+通胀过热+盈利收缩"},
        {"period": "2015-06→2016-01", "drop": "-48.6%", "cause": "融资盘去杠杆+熔断政策失误+人民币贬值"},
        {"period": "2018-01→2019-01", "drop": "-29.3%", "cause": "资管新规去杠杆+贸易战+股权质押踩踏"},
        {"period": "2021-02→2024-02", "drop": "-26.1%", "cause": "监管整顿+ROE下行+疫情封控"},
    ]


def print_signal(signal: dict[str, Any]) -> None:
    freshness = signal["freshness"]
    freshness_text = freshness["status"]
    if freshness.get("age_days") is not None:
        freshness_text += f"/{freshness['age_days']}天"
    print(f"\n【{signal['name']}】")
    print(f"  状态: {signal['status']} | 得分: {signal['score']}/{signal['max_score']}")
    print(f"  口径: {signal['note']}")
    print(f"  数据: {signal['source']} | 日期: {signal['data_date']} | 新鲜度: {freshness_text}")


def run(save: bool = True) -> dict[str, Any]:
    signals = [
        get_pmi_signal(),
        get_social_financing_signal(),
        get_csi300_valuation_signal(),
        get_roe_signal(),
    ]
    summary = compute_macro_score(signals)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "signals": signals,
        "active_avoidance_rules": active_avoidance_rules(),
        "invalidated_rules": invalidated_rules(),
        "historical_bear_reference": historical_bear_reference(),
    }
    if save:
        result["output_file"] = save_result(result, "macro_bear_signal", subdir="macro_bear_signal")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="A股宏观熊市/下行风险预警")
    parser.add_argument("--no-save", action="store_true", help="不保存JSON结果")
    args = parser.parse_args()

    print_banner("A股宏观下行风险评估 v6.23")
    result = run(save=not args.no_save)
    for signal in result["signals"]:
        print_signal(signal)

    summary = result["summary"]
    print("\n" + "-" * 65)
    print(f"综合宏观风险得分: {summary['score']}/100")
    print(f"风险等级: {summary['level']}")
    print(f"仓位上限: {summary['position_cap']}")
    print(f"操作建议: {summary['advice']}")
    print(f"数据质量: {summary['data_quality']}")
    if summary["stale_items"]:
        print("需复核数据: " + "、".join(summary["stale_items"]))
    print("-" * 65)

    print("\n【主动规避信号】")
    for item in result["active_avoidance_rules"]:
        print(f"  □ {item}")

    print("\n【已降级或证伪的单因子】")
    for item in result["invalidated_rules"]:
        print(f"  - {item}")

    print("\n【历史熊市归因参考】")
    print(f"{'区间':<18} {'跌幅':<8} 主因")
    for item in result["historical_bear_reference"]:
        print(f"{item['period']:<18} {item['drop']:<8} {item['cause']}")

    if result.get("output_file"):
        print(f"\n结果已保存: {result['output_file']}")
    print("\n提示：本评估只用于风险降级与仓位上限，不构成投资建议。")


if __name__ == "__main__":
    main()
