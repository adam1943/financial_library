"""
14_macro_geopolitical_risk.py —— v6.2 外围局势与中美贸易风险雷达
====================================================================
目标：
  · 主动扫描实时外围局势、贸易摩擦、出口管制、制裁、地缘风险
  · 用历史中美贸易/科技限制事件做事件窗口回测
  · 固定用 011892 易方达先锋成长混合C 做基准验证
  · 输出短期/中期风险等级和是否需要提前减仓/暂停买入

用法：
    python 14_macro_geopolitical_risk.py [基金代码]
    python 14_macro_geopolitical_risk.py 011892
"""

import json
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

import akshare as ak
import numpy as np
import pandas as pd
import requests

from config import (
    BASELINE_VALIDATION_FUND,
    date_to_akshare,
    get_logger,
    n_days_ago,
    print_banner,
    save_result,
    today_str,
    with_cache,
    with_retry,
)

logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


RISK_THEMES = {
    "trade_tariff": {
        "label": "关税/贸易摩擦",
        "keywords": ["tariff", "trade war", "trade talks", "trade truce", "duties", "retaliatory tariffs"],
        "weight": 18,
    },
    "export_control": {
        "label": "出口管制/实体清单",
        "keywords": ["export control", "entity list", "blacklist", "chip ban", "semiconductor restrictions"],
        "weight": 22,
    },
    "sanction": {
        "label": "制裁/投资限制",
        "keywords": ["sanction", "investment restriction", "outbound investment", "national security"],
        "weight": 20,
    },
    "critical_minerals": {
        "label": "稀土/关键矿产",
        "keywords": ["rare earth", "critical minerals", "gallium", "germanium", "graphite"],
        "weight": 18,
    },
    "taiwan_geopolitics": {
        "label": "台海/军事地缘",
        "keywords": ["Taiwan Strait", "military drill", "South China Sea", "PLA", "warship"],
        "weight": 25,
    },
    "macro_rates_fx": {
        "label": "美元/利率/汇率",
        "keywords": ["Federal Reserve", "Treasury yield", "US dollar", "yuan", "renminbi"],
        "weight": 12,
    },
}


HISTORICAL_EVENTS = [
    {"date": "2018-03-22", "name": "美国宣布301关税计划", "theme": "trade_tariff"},
    {"date": "2018-07-06", "name": "中美第一轮加征关税正式生效", "theme": "trade_tariff"},
    {"date": "2019-05-10", "name": "美国上调2000亿美元中国商品关税", "theme": "trade_tariff"},
    {"date": "2019-08-01", "name": "美国宣布新一轮对华关税", "theme": "trade_tariff"},
    {"date": "2020-01-15", "name": "中美第一阶段经贸协议签署", "theme": "trade_tariff"},
    {"date": "2022-10-07", "name": "美国BIS对华先进计算/半导体出口管制", "theme": "export_control"},
    {"date": "2023-08-09", "name": "美国发布对外投资限制行政令", "theme": "sanction"},
    {"date": "2024-05-14", "name": "美国宣布提高部分中国商品301关税", "theme": "trade_tariff"},
]


def safe_pct(a, b):
    if b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return (a / b - 1) * 100


@with_retry()
@with_cache(cache_type="daily")
def get_fund_nav(fund_code: str, years: int = 8) -> pd.DataFrame:
    """获取基金净值。"""
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df.empty:
            df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        if df.empty:
            return pd.DataFrame()
        nav_col = "单位净值" if "单位净值" in df.columns else "累计净值"
        df["净值日期"] = pd.to_datetime(df["净值日期"])
        df = df.rename(columns={"净值日期": "date", nav_col: "close"})
        cutoff = datetime.now() - pd.Timedelta(days=years * 365)
        return df[["date", "close"]][df["date"] >= cutoff].sort_values("date").reset_index(drop=True)
    except Exception as exc:
        logger.error(f"获取基金{fund_code}净值失败: {exc}")
        return pd.DataFrame()


@with_retry()
@with_cache(cache_type="daily")
def get_csi300_history(days: int = 3000) -> pd.DataFrame:
    """获取沪深300历史行情。"""
    try:
        df = ak.index_zh_a_hist(
            symbol="000300",
            period="daily",
            start_date=date_to_akshare(n_days_ago(days)),
            end_date=date_to_akshare(today_str()),
        )
        if df.empty:
            return pd.DataFrame()
        df["日期"] = pd.to_datetime(df["日期"])
        return df.rename(columns={"日期": "date", "收盘": "close"})[["date", "close"]].sort_values("date").reset_index(drop=True)
    except Exception as exc:
        logger.error(f"获取沪深300失败: {exc}")
        return pd.DataFrame()


def nearest_index_on_or_after(df: pd.DataFrame, target_date: pd.Timestamp):
    matches = df.index[df["date"] >= target_date].tolist()
    return matches[0] if matches else None


def event_window_returns(df: pd.DataFrame, event_date: str, windows=(5, 20, 60)) -> dict:
    """计算事件后窗口收益和事件后20日最大回撤。"""
    if df.empty:
        return {"available": False}
    date = pd.Timestamp(event_date)
    idx = nearest_index_on_or_after(df, date)
    if idx is None or idx >= len(df):
        return {"available": False}

    start_price = float(df.loc[idx, "close"])
    output = {
        "available": True,
        "event_trade_date": str(df.loc[idx, "date"].date()),
        "start_price": round(start_price, 4),
    }
    for window in windows:
        end_idx = min(idx + window, len(df) - 1)
        ret = safe_pct(float(df.loc[end_idx, "close"]), start_price)
        output[f"return_{window}d_pct"] = round(ret, 2) if ret is not None else None

    end_idx = min(idx + 20, len(df) - 1)
    segment = df.loc[idx:end_idx, "close"].astype(float)
    running_peak = segment.cummax()
    dd = segment / running_peak - 1
    output["max_drawdown_20d_pct"] = round(float(dd.min()) * 100, 2)
    return output


def historical_event_study(fund_code: str) -> dict:
    """对中美贸易/科技限制事件做基金与沪深300事件研究。"""
    fund_df = get_fund_nav(fund_code)
    csi_df = get_csi300_history()
    rows = []
    for event in HISTORICAL_EVENTS:
        fund_ret = event_window_returns(fund_df, event["date"])
        csi_ret = event_window_returns(csi_df, event["date"])
        rows.append({
            **event,
            "fund": fund_ret,
            "csi300": csi_ret,
        })

    fund_available = [r for r in rows if r["fund"].get("available")]
    fund_20d = [r["fund"].get("return_20d_pct") for r in fund_available if r["fund"].get("return_20d_pct") is not None]
    fund_dd = [r["fund"].get("max_drawdown_20d_pct") for r in fund_available if r["fund"].get("max_drawdown_20d_pct") is not None]
    csi_20d = [r["csi300"].get("return_20d_pct") for r in rows if r["csi300"].get("return_20d_pct") is not None]

    return {
        "fund_code": fund_code,
        "event_count": len(rows),
        "fund_available_events": len(fund_available),
        "avg_fund_20d_return_pct": round(float(np.mean(fund_20d)), 2) if fund_20d else None,
        "worst_fund_20d_return_pct": round(float(np.min(fund_20d)), 2) if fund_20d else None,
        "worst_fund_20d_drawdown_pct": round(float(np.min(fund_dd)), 2) if fund_dd else None,
        "avg_csi300_20d_return_pct": round(float(np.mean(csi_20d)), 2) if csi_20d else None,
        "events": rows,
    }


@with_retry(max_retries=2, delay=1)
def fetch_gdelt_articles(days: int = 7, max_records: int = 50) -> list:
    """使用 GDELT 免费接口获取近几天中美贸易/地缘新闻。"""
    query = (
        '(China OR Chinese OR Beijing) '
        '(US OR "United States" OR Washington) '
        '("trade war" OR tariff OR sanction OR "export control" OR semiconductor OR '
        '"rare earth" OR Taiwan OR "South China Sea" OR "investment restriction")'
    )
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote(query)}&mode=artlist&format=json&maxrecords={max_records}"
        f"&timespan={days}d&sort=hybridrel"
    )
    response = requests.get(url, timeout=20, headers={"User-Agent": "FundRiskRadar/1.0"})
    response.raise_for_status()
    data = response.json()
    return data.get("articles", [])


def fetch_gdelt_articles_with_fallback() -> list:
    """GDELT 容易限流，按查询规模逐步降级。"""
    attempts = [(7, 50), (3, 25), (1, 15)]
    last_error = None
    for days, max_records in attempts:
        try:
            return fetch_gdelt_articles(days=days, max_records=max_records)
        except Exception as exc:
            last_error = exc
            logger.warning(f"GDELT查询降级: {days}d/{max_records}条失败: {exc}")
    raise last_error


def classify_articles(articles: list) -> dict:
    """按关键词主题统计实时风险。"""
    theme_hits = {
        key: {"label": cfg["label"], "count": 0, "score": 0, "examples": []}
        for key, cfg in RISK_THEMES.items()
    }

    for article in articles:
        text = " ".join([
            str(article.get("title", "")),
            str(article.get("seendate", "")),
            str(article.get("sourcecountry", "")),
            str(article.get("domain", "")),
        ]).lower()
        url = article.get("url")
        title = article.get("title")
        for key, cfg in RISK_THEMES.items():
            if any(keyword.lower() in text for keyword in cfg["keywords"]):
                theme_hits[key]["count"] += 1
                theme_hits[key]["score"] += cfg["weight"]
                if len(theme_hits[key]["examples"]) < 3:
                    theme_hits[key]["examples"].append({
                        "title": title,
                        "url": url,
                        "seen_date": article.get("seendate"),
                        "domain": article.get("domain"),
                    })

    raw_score = sum(v["score"] for v in theme_hits.values())
    diversity_bonus = sum(1 for v in theme_hits.values() if v["count"] > 0) * 5
    total_score = min(100, raw_score + diversity_bonus)

    if total_score >= 70:
        short_level = "🔴 高风险"
        action = "暂停新买入，高波动基金减仓或收紧至-3%~-5%止损"
    elif total_score >= 45:
        short_level = "🟠 较高风险"
        action = "暂停重仓，已有仓位减半观察，等待风险降温"
    elif total_score >= 25:
        short_level = "🟡 中等风险"
        action = "新买入仓位砍半，严格执行v6.0止损"
    else:
        short_level = "🟢 低风险"
        action = "外围风险未显著升温，按常规流程执行"

    active_themes = [
        {"theme": k, **v}
        for k, v in theme_hits.items()
        if v["count"] > 0
    ]
    active_themes = sorted(active_themes, key=lambda x: x["score"], reverse=True)

    return {
        "article_count": len(articles),
        "risk_score": total_score,
        "short_term_level": short_level,
        "active_themes": active_themes,
        "action": action,
    }


def build_medium_term_view(realtime: dict, event_study: dict) -> dict:
    """结合实时风险和历史冲击评估中期风险。"""
    score = realtime.get("risk_score", 0)
    worst_dd = event_study.get("worst_fund_20d_drawdown_pct")
    worst_20d = event_study.get("worst_fund_20d_return_pct")
    if worst_dd is not None and worst_dd <= -10:
        score += 15
    if worst_20d is not None and worst_20d <= -8:
        score += 10
    score = min(100, score)

    if score >= 75:
        level = "🔴 中期高波动风险"
        horizon = "1-3个月"
        action = "成长风格基金以防守为先，等待风险主题降温和指数重新站上MA60"
    elif score >= 50:
        level = "🟠 中期偏高风险"
        horizon = "2-8周"
        action = "仓位上限降低，优先保留现金/债券/低波资产"
    elif score >= 30:
        level = "🟡 中期观察"
        horizon = "2-4周"
        action = "保持观察，若重仓股共振转弱立即降级"
    else:
        level = "🟢 中期风险可控"
        horizon = "1-4周"
        action = "按常规v6.1闸门执行"

    return {
        "medium_term_score": score,
        "medium_term_level": level,
        "horizon": horizon,
        "action": action,
    }


def analyze_macro_geopolitical_risk(fund_code: str = None) -> dict:
    """主入口。"""
    fund_code = fund_code or BASELINE_VALIDATION_FUND["code"]
    print_banner(f"v6.2 外围局势风险雷达 | {fund_code}", char="═")

    try:
        articles = fetch_gdelt_articles_with_fallback()
        realtime = classify_articles(articles)
    except Exception as exc:
        logger.error(f"GDELT实时新闻获取失败: {exc}")
        realtime = {
            "article_count": 0,
            "risk_score": 50,
            "short_term_level": "🟡 中等风险",
            "active_themes": [],
            "action": "实时新闻源失败，按中等风险处理并要求人工复核官方消息",
            "data_quality": "degraded",
            "error": str(exc),
        }

    event_study = historical_event_study(fund_code)
    medium = build_medium_term_view(realtime, event_study)

    result = {
        "fund_code": fund_code,
        "fund_name": BASELINE_VALIDATION_FUND["name"] if fund_code == BASELINE_VALIDATION_FUND["code"] else None,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "realtime_risk": realtime,
        "historical_event_study": event_study,
        "medium_term_view": medium,
        "decision_rule": "短期🔴或中期🔴时，禁止新买入；短期🟠时仓位至少减半；历史20日最大回撤<-10%时收紧止损",
    }

    print("\n【实时外围风险】")
    print(f"  新闻样本: {realtime['article_count']}  风险分: {realtime['risk_score']}/100  等级: {realtime['short_term_level']}")
    print(f"  动作: {realtime['action']}")
    for theme in realtime.get("active_themes", [])[:5]:
        print(f"  · {theme['label']}: {theme['count']}条  主题分: {theme['score']}")
        for ex in theme.get("examples", [])[:2]:
            print(f"    - {ex.get('title')} ({ex.get('domain')})")

    print("\n【历史事件回测】")
    print(f"  可用于基金回测事件数: {event_study['fund_available_events']}/{event_study['event_count']}")
    print(f"  基金事件后20日平均收益: {event_study['avg_fund_20d_return_pct']}%")
    print(f"  基金事件后20日最差收益: {event_study['worst_fund_20d_return_pct']}%")
    print(f"  基金事件后20日最差回撤: {event_study['worst_fund_20d_drawdown_pct']}%")
    csi_avg = event_study["avg_csi300_20d_return_pct"]
    print(f"  沪深300事件后20日平均收益: {csi_avg}%" if csi_avg is not None else "  沪深300事件后20日平均收益: 数据缺失")

    print("\n【中期研判】")
    print(f"  等级: {medium['medium_term_level']}  分数: {medium['medium_term_score']}/100  窗口: {medium['horizon']}")
    print(f"  动作: {medium['action']}")

    save_path = save_result(result, f"macro_geopolitical_{fund_code}", subdir="14_macro_geopolitical")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) >= 2 else BASELINE_VALIDATION_FUND["code"]
    analyze_macro_geopolitical_risk(code)
