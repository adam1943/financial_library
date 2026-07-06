"""
01_fund_screening.py —— 基金筛选数据获取（对应 v4.0 skill 1.1 节）
================================================================
获取字段：
  · 近三年收益率同类排名百分位
  · 近三年最大回撤同类排名百分位
  · 近三年夏普比率同类排名百分位
  · 基金经理从业年限
  · 基金成立年限
  · 基金规模

用法：
    python 01_fund_screening.py <基金代码>
    python 01_fund_screening.py 001938
"""

import sys
import importlib.util
import os
import akshare as ak
import pandas as pd
from datetime import datetime

from config import get_logger, with_retry, with_cache, save_result, print_banner

logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_local_module(filename: str):
    module_name = filename.replace(".py", "").replace("-", "_")
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@with_retry()
@with_cache(cache_type="daily")
def get_fund_basic_info(fund_code: str) -> dict:
    """
    获取基金基础信息（规模、成立年限、基金经理等）
    数据源：akshare - 天天基金/东方财富
    """
    try:
        # 基金基本信息
        info_df = ak.fund_individual_basic_info_xq(symbol=fund_code)
        info_dict = dict(zip(info_df['item'], info_df['value']))

        # 基金规模 - 取最新净资产
        try:
            scale_df = ak.fund_individual_analysis_xq(symbol=fund_code)
            latest_scale = scale_df.iloc[0] if not scale_df.empty else {}
        except Exception:
            latest_scale = {}

        return {
            "fund_code": fund_code,
            "fund_name": info_dict.get("基金名称", "未知"),
            "fund_type": info_dict.get("基金类型", "未知"),
            "manager": info_dict.get("基金经理", "未知"),
            "company": info_dict.get("基金公司", "未知"),
            "establishment_date": info_dict.get("成立时间", ""),
            "raw_info": info_dict,
        }
    except Exception as e:
        logger.error(f"获取基金{fund_code}基础信息失败: {e}")
        return {"fund_code": fund_code, "error": str(e)}


@with_retry()
@with_cache(cache_type="daily")
def get_fund_performance_rank(fund_code: str) -> dict:
    """
    获取基金业绩排名（近1年/近3年同类排名百分位）
    """
    try:
        # 获取基金业绩表现
        perf_df = ak.fund_individual_achievement_xq(symbol=fund_code)

        result = {
            "fund_code": fund_code,
            "return_1y": None,
            "return_3y": None,
            "return_3y_rank_pct": None,
            "max_drawdown_3y_rank_pct": None,
            "sharpe_3y_rank_pct": None,
        }

        if not perf_df.empty:
            # 遍历不同周期
            for _, row in perf_df.iterrows():
                period = str(row.get("周期", ""))
                # 尝试提取同类排名百分位
                try:
                    rank_str = str(row.get("同类排名", ""))
                    if "/" in rank_str:
                        rank, total = rank_str.split("/")
                        pct = float(rank) / float(total) * 100
                        if "一年" in period or "1年" in period:
                            result["return_1y_rank_pct"] = round(pct, 2)
                        elif "三年" in period or "3年" in period:
                            result["return_3y_rank_pct"] = round(pct, 2)
                except Exception:
                    pass
        return result
    except Exception as e:
        logger.error(f"获取基金{fund_code}业绩排名失败: {e}")
        return {"fund_code": fund_code, "error": str(e)}


@with_retry()
@with_cache(cache_type="daily")
def get_fund_risk_metrics(fund_code: str) -> dict:
    """
    获取基金风险指标（最大回撤、夏普比率、年化波动率）
    """
    try:
        nav_df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")

        if nav_df.empty or len(nav_df) < 60:
            return {"fund_code": fund_code, "error": "净值数据不足"}

        nav_df['净值日期'] = pd.to_datetime(nav_df['净值日期'])
        nav_df = nav_df.sort_values('净值日期').reset_index(drop=True)

        # 近3年数据（约750个交易日）
        recent_3y = nav_df.tail(750) if len(nav_df) > 750 else nav_df
        recent_3y = recent_3y.copy()
        recent_3y['daily_return'] = recent_3y['单位净值'].pct_change()

        # 最大回撤
        cumulative = recent_3y['单位净值']
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        # 年化波动率 & 年化收益
        daily_returns = recent_3y['daily_return'].dropna()
        annualized_vol = daily_returns.std() * (252 ** 0.5)
        annualized_return = (recent_3y['单位净值'].iloc[-1] / recent_3y['单位净值'].iloc[0]) ** (252 / len(recent_3y)) - 1

        # 夏普比率（无风险利率按2%计算）
        risk_free_rate = 0.02
        sharpe = (annualized_return - risk_free_rate) / annualized_vol if annualized_vol > 0 else 0

        return {
            "fund_code": fund_code,
            "nav_points_count": len(recent_3y),
            "max_drawdown_3y": round(float(max_drawdown), 4),
            "max_drawdown_3y_pct": f"{max_drawdown*100:.2f}%",
            "annualized_return_3y": round(float(annualized_return), 4),
            "annualized_return_3y_pct": f"{annualized_return*100:.2f}%",
            "annualized_volatility_3y": round(float(annualized_vol), 4),
            "sharpe_ratio_3y": round(float(sharpe), 4),
            "latest_nav": float(recent_3y['单位净值'].iloc[-1]),
            "latest_nav_date": str(recent_3y['净值日期'].iloc[-1].date()),
        }
    except Exception as e:
        logger.error(f"获取基金{fund_code}风险指标失败: {e}")
        return {"fund_code": fund_code, "error": str(e)}


def check_screening_thresholds(fund_data: dict) -> dict:
    """
    执行 Step 4 量化门槛过滤（6项硬性条件）
    """
    thresholds = {
        "return_3y_rank_pct": {"limit": 20, "passed": None, "value": None, "desc": "近3年收益率排名前20%"},
        "max_drawdown_3y_rank_pct": {"limit": 20, "passed": None, "value": None, "desc": "近3年最大回撤排名前20%"},
        "sharpe_3y_rank_pct": {"limit": 20, "passed": None, "value": None, "desc": "近3年夏普比率排名前20%"},
        "manager_experience_years": {"limit": 3, "passed": None, "value": None, "desc": "基金经理从业≥3年"},
        "fund_age_years": {"limit": 3, "passed": None, "value": None, "desc": "基金成立≥3年"},
        "aum_billion_cny": {"limit": 10, "passed": None, "value": None, "desc": "基金规模≥10亿"},
    }

    # 填充已知值（实际接入时需要更完整的数据源）
    perf = fund_data.get("performance", {})
    risk = fund_data.get("risk_metrics", {})

    thresholds["return_3y_rank_pct"]["value"] = perf.get("return_3y_rank_pct")
    if thresholds["return_3y_rank_pct"]["value"] is not None:
        thresholds["return_3y_rank_pct"]["passed"] = thresholds["return_3y_rank_pct"]["value"] <= 20

    # 注意：基金经理从业年限/基金成立年限/基金规模需从 basic_info 另行解析

    passed_count = sum(1 for v in thresholds.values() if v["passed"] is True)
    failed_count = sum(1 for v in thresholds.values() if v["passed"] is False)
    unknown_count = sum(1 for v in thresholds.values() if v["passed"] is None)

    return {
        "thresholds": thresholds,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "unknown_count": unknown_count,
        "overall_pass": failed_count == 0 and unknown_count == 0,
    }


def get_recent_strength_reference(fund_code: str) -> dict:
    """近1/3/6月同赛道强势验证，来自 fund_drawdown_report.py。"""
    try:
        drawdown_mod = load_local_module("fund_drawdown_report.py")
        return drawdown_mod.build_recent_strength_reference(fund_code)
    except Exception as e:
        logger.error(f"获取基金{fund_code}近期强势/回撤数据失败: {e}")
        return {"fund_code": fund_code, "error": str(e)}


def get_risk_return_reference(fund_code: str) -> dict:
    """近1/3/6月夏普比率与波动率横向对比，来自 18_risk_return_screener.py。"""
    try:
        rr_mod = load_local_module("18_risk_return_screener.py")
        return rr_mod.build_risk_return_reference(fund_code, max_presort_count=120)
    except Exception as e:
        logger.error(f"获取基金{fund_code}夏普/波动率横向对比失败: {e}")
        return {
            "fund_code": fund_code,
            "error": str(e),
            "risk_return_guard": {
                "passed": False,
                "level": "unknown",
                "message": "夏普/波动率横向对比数据缺失",
                "risk_flags": [str(e)],
            },
        }


def evaluate_selection_reference(fund_data: dict) -> dict:
    """把近期强势规则作为入选/持有参考标准之一。"""
    recent = fund_data.get("recent_strength_reference", {})
    gate = recent.get("recent_strength") if isinstance(recent, dict) else None
    if not gate:
        return {
            "passed": False,
            "level": "unknown",
            "message": "近1/3/6月同赛道强势数据缺失，不能作为入选/持有正向依据",
            "supporting_standards": [],
            "blocking_reasons": [recent.get("error", "近期强势数据缺失") if isinstance(recent, dict) else "近期强势数据缺失"],
        }

    metrics = gate.get("metrics", [])
    passed_periods = gate.get("passed_periods", 0)
    blocking_reasons = []
    for item in metrics:
        if not item.get("passed"):
            reason = "；".join(item.get("reasons") or ["未满足"])
            blocking_reasons.append(f"{item.get('label')}: {reason}")

    if gate.get("passed"):
        level = "positive_reference"
        message = gate.get("decision_reference") or "可作为基金入选/继续持有的正向参考标准之一"
    elif passed_periods:
        level = "partial_reference"
        message = gate.get("decision_reference") or "仅部分周期满足，只能作为观察项"
    else:
        level = "not_reference"
        message = gate.get("decision_reference") or "不作为入选/持有正向依据"

    return {
        "passed": bool(gate.get("passed")),
        "level": level,
        "message": message,
        "supporting_standards": [
            "近1月/近3月/近6月同赛道排名进入前5%-10%",
            "三个窗口涨跌幅均为正值",
            "三个窗口涨幅均高于基准赛道/同类平均，避免把跌幅更小误判为强势",
        ],
        "blocking_reasons": blocking_reasons,
        "gate": gate,
    }


def evaluate_four_dimension_reference(fund_data: dict) -> dict:
    """四维严格闸门：回撤、涨跌幅、同赛道平均、同赛道排名前5。"""
    recent = fund_data.get("recent_strength_reference", {})
    gate = recent.get("four_dimension_gate") if isinstance(recent, dict) else None
    if not gate:
        return {
            "passed": False,
            "level": "unknown",
            "message": "四维严格闸门数据缺失，不支持强买入/强加仓",
            "blocking_reasons": [recent.get("error", "四维数据缺失") if isinstance(recent, dict) else "四维数据缺失"],
        }

    blocking_reasons = []
    for item in gate.get("metrics", []):
        if not item.get("passed"):
            reason = "；".join(item.get("reasons") or ["未满足"])
            blocking_reasons.append(f"{item.get('label')}: {reason}")

    if gate.get("passed"):
        level = "strict_pass"
        message = gate.get("decision_reference") or "四维严格闸门通过"
    elif gate.get("passed_periods", 0):
        level = "partial_pass"
        message = gate.get("decision_reference") or "四维严格闸门仅部分周期通过"
    else:
        level = "strict_fail"
        message = gate.get("decision_reference") or "四维严格闸门不通过"

    return {
        "passed": bool(gate.get("passed")),
        "level": level,
        "message": message,
        "blocking_reasons": blocking_reasons,
        "gate": gate,
    }


def _profile_window(profile: dict, label: str) -> dict:
    for item in profile.get("windows", []) or []:
        if item.get("label") == label:
            return item
    return {}


def evaluate_drawdown_guard(fund_data: dict) -> dict:
    """持有回撤与同赛道排名闸门。

    主动基金不能只用历史最大回撤机械判卖出。历史回撤用于判断
    波动档案和仓位上限；真实动作要结合当前回撤、近期同赛道排名、
    修复速度和基金经理近期调仓是否可能有效。
    """
    recent = fund_data.get("recent_strength_reference", {})
    if not isinstance(recent, dict):
        return {
            "passed": False,
            "level": "unknown",
            "action": "回撤/同赛道排名数据缺失，不能支持继续持有或加仓",
            "risk_flags": ["回撤数据缺失"],
        }

    profile = recent.get("drawdown_profile") or {}
    strength = recent.get("recent_strength") or {}
    current = profile.get("current_drawdown") or {}
    current_dd = current.get("drawdown_pct")
    if current_dd is None and profile.get("latest_nav") is not None:
        current_dd = 0.0

    one_year = _profile_window(profile, "近1年")
    three_year = _profile_window(profile, "近3年")
    since_inception = _profile_window(profile, "成立以来")
    max_dd_1y = one_year.get("max_drawdown_pct")
    max_dd_3y = three_year.get("max_drawdown_pct")
    max_dd_since = since_inception.get("max_drawdown_pct")
    avg_recovery_days = profile.get("avg_recovery_days")

    metrics = strength.get("metrics", []) or []
    passed_periods = sum(1 for item in metrics if item.get("passed"))
    rank_pcts = [item.get("peer_rank_pct") for item in metrics if item.get("peer_rank_pct") is not None]
    avg_rank_pct = round(sum(rank_pcts) / len(rank_pcts), 2) if rank_pcts else None
    all_recent_strength = bool(metrics) and passed_periods == len(metrics)
    current_near_high = current_dd is not None and current_dd >= -3
    weak_rank_periods = []
    severe_rank_periods = []
    for item in metrics:
        rank_pct = item.get("peer_rank_pct")
        if rank_pct is None:
            continue
        if rank_pct > 50:
            weak_rank_periods.append(f"{item.get('label')}同赛道排名后{round(100 - rank_pct, 2)}%")
        if rank_pct > 70:
            severe_rank_periods.append(f"{item.get('label')}同赛道排名后30%")

    recent_events = profile.get("recent_5_drawdowns") or []
    recovered_recent_events = [event for event in recent_events if event.get("recovered")]
    recent_recovery_days = [
        event.get("recovery_days")
        for event in recovered_recent_events
        if event.get("recovery_days") is not None
    ]
    recent_avg_recovery_days = (
        round(sum(recent_recovery_days) / len(recent_recovery_days), 1)
        if recent_recovery_days
        else None
    )

    support_flags = []
    if current_near_high:
        support_flags.append("当前净值接近/创出阶段高点，说明近期回撤已修复")
    if all_recent_strength:
        support_flags.append("近1/3/6月同赛道强势均通过，近期相对排名支持继续观察持有")
    if avg_rank_pct is not None and avg_rank_pct <= 10:
        support_flags.append(f"近1/3/6月平均同赛道排名前{avg_rank_pct}%，说明近期基金经理操作有效")
    if recent_events and len(recovered_recent_events) == len(recent_events):
        support_flags.append("最近5次>5%回撤均已修复，短期修复能力尚可")
    if recent_avg_recovery_days is not None and recent_avg_recovery_days <= 30:
        support_flags.append(f"最近回撤平均{recent_avg_recovery_days}天修复，近期风险控制有效")

    manager_adjustment_score = len(support_flags)
    manager_adjustment_effective = manager_adjustment_score >= 3

    risk_flags = []
    hard_flags = []
    volatility_flags = []

    if current_dd is not None:
        if current_dd <= -15:
            hard_flags.append(f"当前回撤{current_dd}%超过-15%深回撤线")
        elif current_dd <= -10:
            if severe_rank_periods or len(weak_rank_periods) >= 2:
                hard_flags.append(f"当前回撤{current_dd}%且同赛道排名转弱，触发减仓复核")
            else:
                risk_flags.append(f"当前回撤{current_dd}%超过-10%警戒线")
        elif current_dd <= -8:
            risk_flags.append(f"当前回撤{current_dd}%超过-8%硬止损观察线")

    if max_dd_1y is not None:
        if max_dd_1y <= -25:
            volatility_flags.append(f"近1年最大回撤{max_dd_1y}%过深，仓位上限需下调")
        elif max_dd_1y <= -15:
            volatility_flags.append(f"近1年最大回撤{max_dd_1y}%偏大，不能按低风险基金持有")

    if max_dd_3y is not None:
        if max_dd_3y <= -35:
            volatility_flags.append(f"近3年最大回撤{max_dd_3y}%过深，属于高波动基金档案")
        elif max_dd_3y <= -25:
            volatility_flags.append(f"近3年最大回撤{max_dd_3y}%偏大，继续持有必须配止损/止盈计划")

    if max_dd_since is not None and max_dd_since <= -40:
        volatility_flags.append(f"成立以来最大回撤{max_dd_since}%接近/超过-40%")

    if avg_recovery_days is not None:
        if avg_recovery_days >= 180:
            risk_flags.append(f"平均修复天数{avg_recovery_days}天过长")
        elif avg_recovery_days >= 90:
            volatility_flags.append(f"平均修复天数{avg_recovery_days}天偏长，持有体验有压力")

    if severe_rank_periods:
        if current_dd is not None and current_dd <= -5:
            hard_flags.extend(severe_rank_periods)
        else:
            risk_flags.extend(severe_rank_periods)
    elif len(weak_rank_periods) >= 2:
        risk_flags.append("近1/3/6月至少两个窗口排名落入同赛道后50%")
    elif weak_rank_periods:
        risk_flags.extend(weak_rank_periods)

    if hard_flags:
        level = "reduce_or_exit"
        action = "当前回撤或同赛道排名已恶化，优先减仓、止损或换入更强同类基金"
    elif risk_flags:
        level = "degraded_hold"
        action = "风险正在恶化，仅允许降级持有或小仓观察；新增资金暂停，等待回撤修复和排名改善"
    elif volatility_flags and manager_adjustment_effective:
        level = "hold_with_position_cap"
        action = "历史回撤提示高波动，但近期排名、净值修复和相对表现支持继续持有；按高波动基金控制仓位，不追涨加仓"
    elif volatility_flags:
        level = "degraded_hold"
        action = "历史回撤和修复天数提示持有压力，缺少足够近期强势证据时只宜降级持有"
    else:
        level = "hold_ok"
        action = "回撤、修复和同赛道排名未触发风控，可作为继续持有的风险前提之一"

    return {
        "passed": level in {"hold_ok", "hold_with_position_cap"},
        "level": level,
        "action": action,
        "risk_flags": hard_flags + risk_flags + volatility_flags,
        "hard_flags": hard_flags,
        "watch_flags": risk_flags,
        "volatility_flags": volatility_flags,
        "support_flags": support_flags,
        "manager_adjustment_effective": manager_adjustment_effective,
        "metrics": {
            "current_drawdown_pct": current_dd,
            "max_drawdown_1y_pct": max_dd_1y,
            "max_drawdown_3y_pct": max_dd_3y,
            "max_drawdown_since_inception_pct": max_dd_since,
            "avg_recovery_days": avg_recovery_days,
            "recent_avg_recovery_days": recent_avg_recovery_days,
            "passed_recent_strength_periods": passed_periods,
            "avg_peer_rank_pct": avg_rank_pct,
            "manager_adjustment_score": manager_adjustment_score,
            "weak_rank_periods": weak_rank_periods,
            "severe_rank_periods": severe_rank_periods,
        },
    }


def screen_fund(fund_code: str) -> dict:
    """主入口：完整筛选单只基金"""
    print_banner(f"基金筛选分析 | {fund_code}")

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": get_fund_basic_info(fund_code),
        "performance": get_fund_performance_rank(fund_code),
        "risk_metrics": get_fund_risk_metrics(fund_code),
        "recent_strength_reference": get_recent_strength_reference(fund_code),
        "risk_return_reference": get_risk_return_reference(fund_code),
    }
    result["screening"] = check_screening_thresholds(result)
    result["selection_reference"] = evaluate_selection_reference(result)
    result["four_dimension_reference"] = evaluate_four_dimension_reference(result)
    result["drawdown_guard"] = evaluate_drawdown_guard(result)

    # 打印关键信息
    basic = result["basic_info"]
    risk = result["risk_metrics"]
    print(f"\n基金名称: {basic.get('fund_name', 'N/A')}")
    print(f"基金类型: {basic.get('fund_type', 'N/A')}")
    print(f"基金经理: {basic.get('manager', 'N/A')}")
    print(f"成立时间: {basic.get('establishment_date', 'N/A')}")
    if "latest_nav" in risk:
        print(f"\n最新净值: {risk['latest_nav']} ({risk.get('latest_nav_date')})")
        print(f"近3年年化收益: {risk.get('annualized_return_3y_pct', 'N/A')}")
        print(f"近3年最大回撤: {risk.get('max_drawdown_3y_pct', 'N/A')}")
        print(f"近3年夏普比率: {risk.get('sharpe_ratio_3y', 'N/A')}")
    print(f"\n门槛通过数: {result['screening']['passed_count']}/6")
    print(f"门槛未通过数: {result['screening']['failed_count']}/6")
    print(f"数据缺失数: {result['screening']['unknown_count']}/6")

    recent_gate = result["selection_reference"].get("gate", {})
    if recent_gate:
        print(f"\n【近1/3/6月同赛道强势参考】")
        for item in recent_gate.get("metrics", []):
            rank_pct = item.get("peer_rank_pct")
            rank_text = f"前{rank_pct}%" if rank_pct is not None else "排名缺失"
            status = "[PASS]" if item.get("passed") else "[FAIL]"
            print(
                f"  {status} {item.get('label')}: 收益{item.get('return_pct')}% "
                f"vs 基准赛道{item.get('peer_avg_pct')}%，同赛道{rank_text}，"
                f"最大回撤{item.get('max_drawdown_pct')}%"
            )
            if item.get("reasons"):
                print(f"     未满足: {'；'.join(item['reasons'])}")
        print(f"  结论: {result['selection_reference']['message']}")
    else:
        print(f"\n【近1/3/6月同赛道强势参考】{result['selection_reference']['message']}")

    four_gate = result["four_dimension_reference"].get("gate", {})
    if four_gate:
        print(f"\n【四维严格闸门：回撤/涨跌幅/同赛道平均/排名前5】")
        for item in four_gate.get("metrics", []):
            status = "[PASS]" if item.get("passed") else "[FAIL]"
            print(
                f"  {status} {item.get('label')}: 回撤{item.get('drawdown_pct')}%，"
                f"涨跌幅{item.get('return_pct')}%，同赛道平均{item.get('peer_avg_pct')}%，"
                f"排名{item.get('peer_rank')}/{item.get('peer_total')}"
            )
            if item.get("reasons"):
                print(f"     未满足: {'；'.join(item['reasons'])}")
        print(f"  结论: {result['four_dimension_reference']['message']}")
    else:
        print(f"\n【四维严格闸门】{result['four_dimension_reference']['message']}")

    drawdown_guard = result.get("drawdown_guard", {})
    guard_metrics = drawdown_guard.get("metrics", {})
    print(f"\n【持有回撤与同赛道排名闸门】{drawdown_guard.get('level')}")
    print(f"  当前回撤: {guard_metrics.get('current_drawdown_pct')}%")
    print(f"  近1年最大回撤: {guard_metrics.get('max_drawdown_1y_pct')}%")
    print(f"  近3年最大回撤: {guard_metrics.get('max_drawdown_3y_pct')}%")
    print(f"  成立以来最大回撤: {guard_metrics.get('max_drawdown_since_inception_pct')}%")
    print(f"  平均修复天数: {guard_metrics.get('avg_recovery_days')}天")
    print(f"  近5次回撤平均修复: {guard_metrics.get('recent_avg_recovery_days')}天")
    print(f"  近1/3/6月平均同赛道排名: 前{guard_metrics.get('avg_peer_rank_pct')}%")
    print(f"  基金经理近期调仓有效性: {drawdown_guard.get('manager_adjustment_effective')}")
    print(f"  结论: {drawdown_guard.get('action')}")
    for flag in drawdown_guard.get("support_flags", []):
        print(f"  + {flag}")
    for flag in drawdown_guard.get("risk_flags", []):
        print(f"  · {flag}")

    risk_return_reference = result.get("risk_return_reference", {})
    rr_guard = risk_return_reference.get("risk_return_guard", {})
    print(f"\n【夏普比率/波动率横向闸门】{rr_guard.get('level')}")
    for item in risk_return_reference.get("risk_return_metrics", []):
        if item.get("error"):
            print(f"  [DATA] {item.get('period')}: {item.get('error')}")
            continue
        rank_text = (
            f"{item.get('risk_return_rank')}/{item.get('computed_peer_count')}"
            if item.get("risk_return_rank") is not None
            else "排名缺失"
        )
        status = "[PASS]" if item.get("top5pct_pass") and item.get("sharpe_ge_min_pass") else "[FAIL]"
        print(
            f"  {status} {item.get('period_label')}: 夏普{item.get('sharpe_ratio')}，"
            f"年化波动{item.get('annualized_volatility_pct')}%，"
            f"阶段收益{item.get('period_return_pct')}%，"
            f"样本排名{rank_text}，样本前{item.get('computed_rank_pct')}%"
        )
    print(f"  结论: {rr_guard.get('message')}")
    for flag in rr_guard.get("support_flags", []):
        print(f"  + {flag}")
    for flag in rr_guard.get("risk_flags", []):
        print(f"  · {flag}")

    save_path = save_result(result, f"fund_screening_{fund_code}", subdir="01_screening")
    print(f"\n[OK] 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 01_fund_screening.py <基金代码>")
        print("示例: python 01_fund_screening.py 001938")
        sys.exit(1)

    fund_code = sys.argv[1]
    screen_fund(fund_code)
