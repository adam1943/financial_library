#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
18_risk_return_screener.py -- 夏普比率/波动率同类横向筛选
================================================================

用途：
    python 18_risk_return_screener.py 混合型 all 2.0
    python 18_risk_return_screener.py --fund-code 011892
    python 18_risk_return_screener.py --compare 011892 519771 012920

逻辑：
  1. 先用东方财富同类基金近1月/3月/6月收益做预筛。
  2. 再拉取日净值，计算年化收益、年化波动率、夏普比率、最大回撤。
  3. 输出同类夏普排名前5%的基金，优先筛出夏普 >= 2 的候选。
  4. 对持有基金做横向对比，判断是否仍处在同类风险收益前列。

说明：
  夏普比率和波动率是风险收益维度，不是单独买入信号。强买入仍需通过
  回撤、四维严格闸门、热点匹配、大盘总开关、技术企稳与季报滞后检查。
"""

from __future__ import annotations

import argparse
import math
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import akshare as ak
import numpy as np
import pandas as pd

from config import get_logger, print_banner, save_result, with_cache, with_retry


warnings.filterwarnings("ignore")
logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


RISK_FREE_RATE = 0.015
TRADING_DAYS = 252
DEFAULT_TOP_PCT = 0.05
DEFAULT_MIN_SHARPE = 2.0
PRESORT_PCT = 0.40
MIN_PRESORT_COUNT = 60
DEFAULT_MAX_PRESORT_COUNT = 700
DEFAULT_REFERENCE_MAX_PRESORT_COUNT = 180
MAX_WORKERS = 8

PERIOD_CONFIG = {
    "1m": {"offset": {"months": 1}, "min_rows": 15, "rank_col": "近1月", "label": "近1月"},
    "3m": {"offset": {"months": 3}, "min_rows": 40, "rank_col": "近3月", "label": "近3月"},
    "6m": {"offset": {"months": 6}, "min_rows": 80, "rank_col": "近6月", "label": "近6月"},
    "1y": {"offset": {"years": 1}, "min_rows": 120, "rank_col": "近1年", "label": "近1年"},
}
DEFAULT_PERIODS = ("1m", "3m", "6m")
FUND_RANK_TYPES = ("股票型", "混合型", "债券型", "指数型", "QDII", "全部")


def parse_float(value: Any) -> float | None:
    if value in (None, "", "---", "--", "-"):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def dataframe_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.where(pd.notna(df), None).to_dict("records")


@with_retry()
@with_cache(cache_type="daily")
def fetch_fund_name_records() -> list[dict]:
    return dataframe_records(ak.fund_name_em())


def detect_rank_type(fund_code: str) -> dict:
    code = normalize_code(fund_code)
    for row in fetch_fund_name_records():
        if normalize_code(row.get("基金代码")) != code:
            continue
        raw_type = str(row.get("基金类型") or "")
        name = str(row.get("基金简称") or "")
        text = raw_type + name
        if "QDII" in text or "全球" in name or "海外" in name:
            rank_type = "QDII"
        elif "指数" in raw_type or "指数" in name or "ETF" in name.upper():
            rank_type = "指数型"
        elif "股票" in raw_type:
            rank_type = "股票型"
        elif "混合" in raw_type:
            rank_type = "混合型"
        elif "债" in raw_type:
            rank_type = "债券型"
        else:
            rank_type = "全部"
        return {
            "fund_code": code,
            "fund_name": name,
            "raw_fund_type": raw_type,
            "rank_type": rank_type,
        }
    return {
        "fund_code": code,
        "fund_name": "",
        "raw_fund_type": "",
        "rank_type": "全部",
    }


@with_retry()
@with_cache(cache_type="daily")
def fetch_rank_records(fund_type: str) -> list[dict]:
    df = ak.fund_open_fund_rank_em(symbol=fund_type)
    return dataframe_records(df)


def get_rank_dataframe(fund_type: str, presort_col: str) -> tuple[pd.DataFrame, str]:
    tried = []
    for symbol in [fund_type, "全部"]:
        if not symbol or symbol in tried:
            continue
        tried.append(symbol)
        try:
            rows = fetch_rank_records(symbol)
        except Exception as exc:
            logger.warning(f"获取{symbol}基金排行失败: {exc}")
            continue
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        if presort_col not in df.columns:
            fallback = "近1年" if "近1年" in df.columns else None
            if fallback is None:
                continue
            presort_col = fallback
        df["基金代码"] = df["基金代码"].map(normalize_code)
        df[presort_col] = pd.to_numeric(df[presort_col], errors="coerce")
        df = df[df[presort_col].notna()].copy()
        df = df.sort_values(presort_col, ascending=False).reset_index(drop=True)
        if not df.empty:
            return df, symbol
    return pd.DataFrame(), fund_type


@with_retry()
@with_cache(cache_type="daily")
def fetch_nav_records(fund_code: str) -> list[dict]:
    """Fetch NAV points via the existing Eastmoney direct route.

    Avoid ak.fund_open_fund_info_em here because some Python 3.13 environments
    crash inside py_mini_racer when AkShare evaluates Eastmoney JS payloads.
    """
    code = normalize_code(fund_code)
    import fund_drawdown_report as drawdown_mod

    points = drawdown_mod.fetch_all_nav_points(code)
    if not points:
        raise RuntimeError(f"净值数据获取失败: {code}")
    return [
        {
            "净值日期": nav_date.isoformat(),
            "单位净值": nav,
        }
        for nav_date, nav in points
    ]


def nav_records_to_frame(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["date", "nav"])
    df = pd.DataFrame(records)
    date_col = "净值日期" if "净值日期" in df.columns else "日期" if "日期" in df.columns else None
    nav_col = "单位净值" if "单位净值" in df.columns else "累计净值" if "累计净值" in df.columns else None
    if date_col is None or nav_col is None:
        return pd.DataFrame(columns=["date", "nav"])
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_col], errors="coerce"),
            "nav": pd.to_numeric(df[nav_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["date", "nav"])
    out = out[out["nav"] > 0].sort_values("date").drop_duplicates("date")
    return out.reset_index(drop=True)


def fetch_period_returns(fund_code: str, period: str) -> dict:
    config = PERIOD_CONFIG[period]
    df = nav_records_to_frame(fetch_nav_records(fund_code))
    if df.empty or len(df) < config["min_rows"]:
        return {"fund_code": normalize_code(fund_code), "error": "净值数据不足"}

    latest_date = df["date"].iloc[-1]
    cutoff = latest_date - pd.DateOffset(**config["offset"])
    window = df[df["date"] >= cutoff].copy()
    if len(window) < config["min_rows"]:
        return {
            "fund_code": normalize_code(fund_code),
            "error": f"{config['label']}净值点不足({len(window)}/{config['min_rows']})",
        }

    navs = window["nav"].astype(float).to_numpy()
    returns = np.diff(navs) / navs[:-1]
    returns = returns[np.isfinite(returns)]
    if len(returns) < max(5, config["min_rows"] - 1):
        return {"fund_code": normalize_code(fund_code), "error": "收益率序列不足"}

    return {
        "fund_code": normalize_code(fund_code),
        "period": period,
        "period_label": config["label"],
        "returns": returns,
        "nav_points": int(len(window)),
        "start_date": str(window["date"].iloc[0].date()),
        "end_date": str(latest_date.date()),
        "start_nav": float(navs[0]),
        "latest_nav": float(navs[-1]),
    }


def calc_risk_return_metrics(returns: np.ndarray, start_nav: float, latest_nav: float) -> dict:
    ann_return = float(np.mean(returns) * TRADING_DAYS)
    ann_vol = float(np.std(returns, ddof=1) * math.sqrt(TRADING_DAYS)) if len(returns) > 1 else 0.0
    sharpe = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else np.nan

    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    max_dd = float(((cumulative - running_max) / running_max).min()) if len(cumulative) else np.nan
    period_return = latest_nav / start_nav - 1 if start_nav else np.nan

    return {
        "period_return_pct": round(period_return * 100, 2) if np.isfinite(period_return) else None,
        "annualized_return_pct": round(ann_return * 100, 2),
        "annualized_volatility_pct": round(ann_vol * 100, 2),
        "sharpe_ratio": round(float(sharpe), 3) if np.isfinite(sharpe) else None,
        "max_drawdown_pct": round(max_dd * 100, 2) if np.isfinite(max_dd) else None,
    }


def calc_single_period_metrics(fund_code: str, period: str) -> dict:
    payload = fetch_period_returns(fund_code, period)
    if payload.get("error"):
        return payload
    metrics = calc_risk_return_metrics(payload["returns"], payload["start_nav"], payload["latest_nav"])
    return {
        "fund_code": payload["fund_code"],
        "period": period,
        "period_label": payload["period_label"],
        "nav_points": payload["nav_points"],
        "start_date": payload["start_date"],
        "end_date": payload["end_date"],
        "latest_nav": round(payload["latest_nav"], 4),
        **metrics,
    }


def screen_period(
    fund_type: str = "混合型",
    period: str = "1m",
    min_sharpe: float = DEFAULT_MIN_SHARPE,
    top_pct: float = DEFAULT_TOP_PCT,
    presort_pct: float = PRESORT_PCT,
    include_codes: list[str] | None = None,
    max_presort_count: int | None = DEFAULT_MAX_PRESORT_COUNT,
    max_workers: int = MAX_WORKERS,
    verbose: bool = True,
) -> dict:
    if period not in PERIOD_CONFIG:
        raise ValueError(f"不支持的周期 {period}，可选: {list(PERIOD_CONFIG)}")

    config = PERIOD_CONFIG[period]
    rank_df, actual_type = get_rank_dataframe(fund_type, config["rank_col"])
    if rank_df.empty:
        return {
            "fund_type": fund_type,
            "period": period,
            "error": "同类基金排行为空",
            "top_funds": [],
            "ranked_funds": [],
        }

    total = len(rank_df)
    peer_target_n = max(int(total * top_pct), 1)
    raw_presort_n = min(total, max(int(total * presort_pct), MIN_PRESORT_COUNT, peer_target_n))
    if max_presort_count and max_presort_count > 0:
        presort_n = min(raw_presort_n, max_presort_count, total)
    else:
        presort_n = raw_presort_n
    approximate_rank = presort_n < raw_presort_n
    candidates = rank_df.head(presort_n).copy()
    include_codes = [normalize_code(code) for code in (include_codes or []) if normalize_code(code)]
    for code in include_codes:
        if code in set(candidates["基金代码"]):
            continue
        rows = rank_df[rank_df["基金代码"] == code]
        if not rows.empty:
            candidates = pd.concat([candidates, rows.head(1)], ignore_index=True)
        else:
            candidates = pd.concat(
                [
                    candidates,
                    pd.DataFrame([{"基金代码": code, "基金简称": "", config["rank_col"]: None}]),
                ],
                ignore_index=True,
            )
    candidates = candidates.drop_duplicates("基金代码").reset_index(drop=True)

    if verbose:
        print_banner(f"风险收益筛选 | {actual_type} {config['label']}", char="═")
        print(
            f"同类基金{total}只，收益预筛{len(candidates)}只，样本内目标前{top_pct*100:.1f}%，"
            f"最低夏普{min_sharpe}"
        )

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(calc_single_period_metrics, row["基金代码"], period): row["基金代码"]
            for _, row in candidates.iterrows()
            if row.get("基金代码")
        }
        done = 0
        for future in as_completed(future_map):
            code = normalize_code(future_map[future])
            try:
                metrics = future.result()
                if not metrics.get("error"):
                    results[code] = metrics
            except Exception as exc:
                logger.debug(f"{code} {period} 风险收益计算失败: {exc}")
            done += 1
            if verbose and done % 20 == 0:
                print(f"  已完成 {done}/{len(future_map)}", end="\r")

    if not results:
        return {
            "fund_type": actual_type,
            "period": period,
            "error": "没有成功计算的基金",
            "top_funds": [],
            "ranked_funds": [],
        }

    metrics_df = pd.DataFrame(results.values())
    metrics_df = metrics_df.rename(columns={"fund_code": "基金代码"})
    merged = candidates.merge(metrics_df, on="基金代码", how="inner")
    merged = merged.rename(columns={"基金代码": "fund_code", "基金简称": "fund_name"})
    merged = merged.sort_values("sharpe_ratio", ascending=False, na_position="last").reset_index(drop=True)
    merged["risk_return_rank"] = merged.index + 1
    merged["peer_total"] = total
    merged["computed_peer_count"] = len(merged)
    merged["risk_return_rank_pct"] = (merged["risk_return_rank"] / total * 100).round(2)
    merged["computed_rank_pct"] = (merged["risk_return_rank"] / len(merged) * 100).round(2)
    merged["top5pct_pass"] = merged["computed_rank_pct"] <= top_pct * 100
    merged["sharpe_ge_min_pass"] = merged["sharpe_ratio"].fillna(-999) >= min_sharpe
    merged["positive_return_pass"] = merged["period_return_pct"].fillna(-999) > 0
    merged["source_presort_return_pct"] = pd.to_numeric(merged.get(config["rank_col"]), errors="coerce")
    merged["fund_type"] = actual_type
    merged["rank_scope"] = "收益预筛样本"
    merged["approximate_rank"] = approximate_rank

    ranked_records = dataframe_records(merged)
    top_df = merged[merged["top5pct_pass"]].copy()
    if min_sharpe > 0:
        top_df = top_df[top_df["sharpe_ge_min_pass"]].copy()
    top_records = dataframe_records(top_df)

    if verbose:
        print(f"  成功计算 {len(merged)}/{len(candidates)} 只")
        display_cols = [
            "risk_return_rank",
            "fund_code",
            "fund_name",
            "sharpe_ratio",
            "annualized_return_pct",
            "annualized_volatility_pct",
            "max_drawdown_pct",
            "period_return_pct",
            "risk_return_rank_pct",
        ]
        print("\n【同类夏普前5%且满足最低夏普】")
        if top_df.empty:
            print("  无")
        else:
            print(top_df[[c for c in display_cols if c in top_df.columns]].to_string(index=False))

    return {
        "fund_type": actual_type,
        "requested_fund_type": fund_type,
        "period": period,
        "period_label": config["label"],
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "risk_free_rate": RISK_FREE_RATE,
        "peer_total": total,
        "presort_count": len(candidates),
        "raw_presort_count": raw_presort_n,
        "max_presort_count": max_presort_count,
        "approximate_rank": approximate_rank,
        "computed_peer_count": len(merged),
        "top_pct": top_pct,
        "peer_target_top_count": peer_target_n,
        "computed_target_top_count": max(int(len(merged) * top_pct), 1),
        "min_sharpe": min_sharpe,
        "top_funds": top_records,
        "ranked_funds": ranked_records,
        "method_note": (
            "先按同类收益预筛，再用日净值计算夏普/波动率；top5%基于收益预筛样本。"
            "若使用max_presort_count导致样本小于原始40%预筛，排名为近似横向对比，"
            "强买入必须继续通过回撤、四维闸门和技术企稳。"
        ),
    }


def evaluate_risk_return_guard(metrics: list[dict], min_sharpe: float = DEFAULT_MIN_SHARPE) -> dict:
    valid = [item for item in metrics if not item.get("error")]
    if not valid:
        return {
            "passed": False,
            "level": "unknown",
            "passed_periods": 0,
            "total_periods": len(metrics),
            "message": "夏普/波动率横向对比数据缺失，不能作为持有或加仓依据",
            "risk_flags": ["风险收益数据缺失"],
            "support_flags": [],
        }

    leader_periods = [
        item
        for item in valid
        if item.get("top5pct_pass") and item.get("sharpe_ratio") is not None and item["sharpe_ratio"] >= min_sharpe
    ]
    positive_periods = [item for item in valid if item.get("period_return_pct") is not None and item["period_return_pct"] > 0]
    high_vol_periods = [
        item
        for item in valid
        if item.get("annualized_volatility_pct") is not None and item["annualized_volatility_pct"] >= 35
    ]
    weak_periods = [
        item
        for item in valid
        if item.get("sharpe_ratio") is not None and item["sharpe_ratio"] < 0.8
    ]

    avg_sharpe_values = [item["sharpe_ratio"] for item in valid if item.get("sharpe_ratio") is not None]
    avg_vol_values = [
        item["annualized_volatility_pct"]
        for item in valid
        if item.get("annualized_volatility_pct") is not None
    ]
    avg_rank_values = [item["computed_rank_pct"] for item in valid if item.get("computed_rank_pct") is not None]
    avg_sharpe = round(sum(avg_sharpe_values) / len(avg_sharpe_values), 3) if avg_sharpe_values else None
    avg_vol = round(sum(avg_vol_values) / len(avg_vol_values), 2) if avg_vol_values else None
    avg_rank_pct = round(sum(avg_rank_values) / len(avg_rank_values), 2) if avg_rank_values else None

    support_flags = []
    risk_flags = []
    if leader_periods:
        support_flags.append(
            "、".join(item["period_label"] for item in leader_periods)
            + f"进入同类收益预筛样本夏普前5%且夏普>={min_sharpe:g}"
        )
    if len(positive_periods) == len(valid):
        support_flags.append("近1/3/6月风险收益窗口均为正收益")
    if avg_sharpe is not None and avg_sharpe >= min_sharpe:
        support_flags.append(f"平均夏普{avg_sharpe}，风险收益效率较强")

    for item in high_vol_periods:
        risk_flags.append(f"{item['period_label']}年化波动率{item['annualized_volatility_pct']}%偏高")
    for item in weak_periods:
        risk_flags.append(f"{item['period_label']}夏普{item['sharpe_ratio']}低于0.8")
    for item in valid:
        if item.get("period_return_pct") is not None and item["period_return_pct"] <= 0:
            risk_flags.append(f"{item['period_label']}收益{item['period_return_pct']}%不是正值")
        rank_pct = item.get("computed_rank_pct")
        if rank_pct is not None and rank_pct > 20:
            risk_flags.append(f"{item['period_label']}风险收益样本排名仅前{rank_pct}%，未进入前20%")

    passed_count = len(leader_periods)
    if passed_count == len(valid) and len(valid) >= 3:
        level = "risk_return_leader"
        message = "近1/3/6月夏普和波动率在同类收益预筛样本中领先，可作为优先持有/候选正向证据"
    elif passed_count >= 2:
        level = "partial_leader"
        message = "多数窗口风险收益靠前，可继续持有或观察；加仓仍需等技术企稳和回撤可控"
    elif passed_count == 1:
        level = "watch"
        message = "仅单一窗口风险收益靠前，只能作为观察项，不支持强加仓"
    else:
        level = "lagging"
        message = "夏普/波动率横向排名未进入同类收益预筛样本前5%，若回撤或四维闸门也弱，应考虑减仓或换入更强同类"

    return {
        "passed": level in {"risk_return_leader", "partial_leader"},
        "level": level,
        "passed_periods": passed_count,
        "total_periods": len(valid),
        "message": message,
        "min_sharpe": min_sharpe,
        "avg_sharpe": avg_sharpe,
        "avg_annualized_volatility_pct": avg_vol,
        "avg_risk_return_rank_pct": avg_rank_pct,
        "support_flags": support_flags,
        "risk_flags": risk_flags[:8],
    }


def build_risk_return_reference(
    fund_code: str,
    fund_type: str | None = None,
    periods: tuple[str, ...] = DEFAULT_PERIODS,
    min_sharpe: float = DEFAULT_MIN_SHARPE,
    top_pct: float = DEFAULT_TOP_PCT,
    max_presort_count: int | None = DEFAULT_REFERENCE_MAX_PRESORT_COUNT,
    verbose: bool = False,
) -> dict:
    code = normalize_code(fund_code)
    detected = detect_rank_type(code)
    rank_type = fund_type or detected["rank_type"]
    period_metrics = []
    period_screens = {}

    for period in periods:
        try:
            screen = screen_period(
                rank_type,
                period,
                min_sharpe=min_sharpe,
                top_pct=top_pct,
                include_codes=[code],
                max_presort_count=max_presort_count,
                verbose=verbose,
            )
            period_screens[period] = {
                key: value
                for key, value in screen.items()
                if key not in {"ranked_funds"}
            }
            match = next((item for item in screen.get("ranked_funds", []) if item.get("fund_code") == code), None)
            if match:
                period_metrics.append(match)
            else:
                period_metrics.append({"fund_code": code, "period": period, "error": "未进入可计算样本"})
        except Exception as exc:
            period_metrics.append({"fund_code": code, "period": period, "error": str(exc)})

    guard = evaluate_risk_return_guard(period_metrics, min_sharpe=min_sharpe)
    return {
        "fund_code": code,
        "fund_name": detected.get("fund_name"),
        "raw_fund_type": detected.get("raw_fund_type"),
        "rank_type": rank_type,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "periods": list(periods),
        "risk_return_metrics": period_metrics,
        "risk_return_guard": guard,
        "period_screens": period_screens,
        "max_presort_count": max_presort_count,
        "source": "AkShare/Eastmoney fund_open_fund_rank_em + fund_open_fund_info_em",
    }


def compare_funds_risk_return(
    fund_codes: list[str],
    periods: tuple[str, ...] = DEFAULT_PERIODS,
    min_sharpe: float = DEFAULT_MIN_SHARPE,
    top_pct: float = DEFAULT_TOP_PCT,
    max_presort_count: int | None = DEFAULT_REFERENCE_MAX_PRESORT_COUNT,
    verbose: bool = False,
) -> dict:
    codes = []
    for code in fund_codes:
        code = normalize_code(code)
        if code and code not in codes:
            codes.append(code)
    detected = {code: detect_rank_type(code) for code in codes}
    groups: dict[str, list[str]] = {}
    for code, info in detected.items():
        groups.setdefault(info["rank_type"], []).append(code)

    references = {code: {"fund_code": code, "risk_return_metrics": []} for code in codes}
    for code, info in detected.items():
        references[code].update(
            {
                "fund_name": info.get("fund_name"),
                "raw_fund_type": info.get("raw_fund_type"),
                "rank_type": info.get("rank_type"),
                "periods": list(periods),
                "source": "AkShare/Eastmoney fund_open_fund_rank_em + fund_open_fund_info_em",
            }
        )

    for rank_type, group_codes in groups.items():
        for period in periods:
            try:
                screen = screen_period(
                    rank_type,
                    period,
                    min_sharpe=min_sharpe,
                    top_pct=top_pct,
                    include_codes=group_codes,
                    max_presort_count=max_presort_count,
                    verbose=verbose,
                )
                ranked = screen.get("ranked_funds", [])
                for code in group_codes:
                    match = next((item for item in ranked if item.get("fund_code") == code), None)
                    references[code]["risk_return_metrics"].append(
                        match if match else {"fund_code": code, "period": period, "error": "未进入可计算样本"}
                    )
            except Exception as exc:
                for code in group_codes:
                    references[code]["risk_return_metrics"].append(
                        {"fund_code": code, "period": period, "error": str(exc)}
                    )

    for code in codes:
        refs = references[code]
        refs["analysis_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        refs["risk_return_guard"] = evaluate_risk_return_guard(
            refs.get("risk_return_metrics", []),
            min_sharpe=min_sharpe,
        )

    summary_rows = []
    for code, ref in references.items():
        guard = ref.get("risk_return_guard", {})
        summary_rows.append(
            {
                "fund_code": code,
                "fund_name": ref.get("fund_name"),
                "rank_type": ref.get("rank_type"),
                "level": guard.get("level"),
                "passed_periods": guard.get("passed_periods"),
                "avg_sharpe": guard.get("avg_sharpe"),
                "avg_volatility_pct": guard.get("avg_annualized_volatility_pct"),
                "avg_rank_pct": guard.get("avg_risk_return_rank_pct"),
                "message": guard.get("message"),
            }
        )
    summary_rows = sorted(
        summary_rows,
        key=lambda item: (
            item.get("passed_periods") or 0,
            item.get("avg_sharpe") if item.get("avg_sharpe") is not None else -999,
        ),
        reverse=True,
    )

    return {
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fund_codes": codes,
        "periods": list(periods),
        "min_sharpe": min_sharpe,
        "top_pct": top_pct,
        "max_presort_count": max_presort_count,
        "references": references,
        "summary_rows": summary_rows,
    }


def print_reference(reference: dict) -> None:
    print_banner(f"夏普/波动率横向对比 | {reference.get('fund_code')}", char="═")
    print(
        f"{reference.get('fund_name') or ''}  类型:{reference.get('rank_type')}  "
        f"原始类型:{reference.get('raw_fund_type') or '未知'}"
    )
    rows = []
    for item in reference.get("risk_return_metrics", []):
        rows.append(
            {
                "周期": item.get("period_label") or item.get("period"),
                "夏普": item.get("sharpe_ratio"),
                "年化波动%": item.get("annualized_volatility_pct"),
                "阶段收益%": item.get("period_return_pct"),
                "最大回撤%": item.get("max_drawdown_pct"),
                "样本排名": (
                    f"{item.get('risk_return_rank')}/{item.get('computed_peer_count')}"
                    if item.get("risk_return_rank") is not None
                    else None
                ),
                "同类总数": item.get("peer_total"),
                "前5%": item.get("top5pct_pass"),
                "错误": item.get("error"),
            }
        )
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    guard = reference.get("risk_return_guard", {})
    print(f"\n结论: {guard.get('level')} | {guard.get('message')}")
    for flag in guard.get("support_flags", []):
        print(f"  + {flag}")
    for flag in guard.get("risk_flags", []):
        print(f"  · {flag}")


def main() -> None:
    parser = argparse.ArgumentParser(description="基金夏普比率/波动率同类横向筛选")
    parser.add_argument("fund_type", nargs="?", default="混合型", help="股票型/混合型/债券型/指数型/QDII/全部")
    parser.add_argument("period", nargs="?", default="all", help="1m/3m/6m/1y/all")
    parser.add_argument("min_sharpe", nargs="?", type=float, default=DEFAULT_MIN_SHARPE)
    parser.add_argument("--fund-code", help="分析单只基金在同类中的夏普/波动率排名")
    parser.add_argument("--compare", nargs="+", help="横向对比多只持有基金")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--top-pct", type=float, default=DEFAULT_TOP_PCT)
    parser.add_argument("--max-presort", type=int, default=DEFAULT_MAX_PRESORT_COUNT, help="收益预筛最多计算多少只；0表示不设上限")
    args = parser.parse_args()

    periods = DEFAULT_PERIODS if args.period == "all" else (args.period,)
    for period in periods:
        if period not in PERIOD_CONFIG:
            raise SystemExit(f"不支持的周期 {period}，可选: {list(PERIOD_CONFIG)} 或 all")

    if args.compare:
        result = compare_funds_risk_return(
            args.compare,
            periods=periods,
            min_sharpe=args.min_sharpe,
            top_pct=args.top_pct,
            max_presort_count=args.max_presort or None,
        )
        if args.format == "json":
            import json

            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print_banner("持有基金夏普/波动率横向对比", char="═")
            print(pd.DataFrame(result["summary_rows"]).to_string(index=False))
        save_path = save_result(result, "risk_return_compare", subdir="18_risk_return")
        print(f"\n[OK] 结果已保存: {save_path}")
        return

    if args.fund_code:
        result = build_risk_return_reference(
            args.fund_code,
            periods=periods,
            min_sharpe=args.min_sharpe,
            top_pct=args.top_pct,
            max_presort_count=args.max_presort or None,
            verbose=False,
        )
        if args.format == "json":
            import json

            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print_reference(result)
        save_path = save_result(result, f"risk_return_{normalize_code(args.fund_code)}", subdir="18_risk_return")
        print(f"\n[OK] 结果已保存: {save_path}")
        return

    screens = [
        screen_period(
            args.fund_type,
            period,
            min_sharpe=args.min_sharpe,
            top_pct=args.top_pct,
            max_presort_count=args.max_presort or None,
            verbose=args.format == "table",
        )
        for period in periods
    ]
    result = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fund_type": args.fund_type,
        "periods": list(periods),
        "min_sharpe": args.min_sharpe,
        "screens": screens,
    }
    if args.format == "json":
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    save_path = save_result(result, f"risk_return_screen_{args.fund_type}", subdir="18_risk_return")
    print(f"\n[OK] 结果已保存: {save_path}")


if __name__ == "__main__":
    main()
