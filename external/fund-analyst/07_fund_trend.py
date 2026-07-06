"""
07_fund_trend.py —— 基金走势与买卖时机（对应 v4.0 skill 1.7 + 6.1）
==================================================================
获取字段：
  · 基金近1年/近3年日净值序列
  · 近3年每次回撤幅度及修复天数
  · 基金跟踪指数PE/PB历史分位
  · 近5日/20日/60日涨跌幅
  · 买卖时机综合研判（★v4.0核心）

用法：
    python 07_fund_trend.py <基金代码>
    python 07_fund_trend.py 001938
"""

import sys
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime

from config import (
    calc_stop_prices,
    get_logger,
    get_risk_profile,
    with_retry,
    with_cache,
    save_result,
    print_banner,
)

logger = get_logger(__name__)


def is_actionable_buy(decision: str) -> bool:
    """是否属于真正可执行买入，而不是'不建议买入'这类否定表述。"""
    if not decision:
        return False
    negative_markers = ["不建议", "不买", "禁止", "等待", "观察"]
    if any(marker in decision for marker in negative_markers):
        return False
    return any(marker in decision for marker in ["买入", "小仓", "试探"])


@with_retry()
@with_cache(cache_type="daily")
def get_fund_nav(fund_code: str, years: int = 3) -> pd.DataFrame:
    """基金净值曲线"""
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        if df.empty:
            # 尝试单位净值走势
            df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        
        if df.empty:
            return pd.DataFrame()

        df["净值日期"] = pd.to_datetime(df["净值日期"])
        df = df.sort_values("净值日期").reset_index(drop=True)

        # 近N年数据
        cutoff_date = datetime.now() - pd.Timedelta(days=years * 365)
        df = df[df["净值日期"] >= cutoff_date]
        return df
    except Exception as e:
        logger.error(f"获取基金{fund_code}净值失败: {e}")
        return pd.DataFrame()


def analyze_drawdowns(nav_df: pd.DataFrame) -> dict:
    """分析历史回撤"""
    if nav_df.empty:
        return {"error": "净值数据为空"}

    # 使用单位净值
    nav_col = "单位净值" if "单位净值" in nav_df.columns else "累计净值"
    if nav_col not in nav_df.columns:
        return {"error": "无法识别净值字段"}

    nav_series = nav_df[nav_col].values
    dates = nav_df["净值日期"].values

    # 计算历次回撤
    drawdowns = []
    peak_idx = 0
    peak_val = nav_series[0]
    in_drawdown = False
    dd_start_idx = 0
    max_dd = 0

    for i, v in enumerate(nav_series):
        if v > peak_val:
            # 新高
            if in_drawdown:
                # 结束一次回撤
                recovery_days = (pd.Timestamp(dates[i]) - pd.Timestamp(dates[dd_start_idx])).days
                drawdowns.append({
                    "start_date": str(pd.Timestamp(dates[dd_start_idx]).date()),
                    "end_date": str(pd.Timestamp(dates[i]).date()),
                    "max_drawdown_pct": round(max_dd * 100, 2),
                    "recovery_days": recovery_days,
                })
                in_drawdown = False
                max_dd = 0
            peak_val = v
            peak_idx = i
        else:
            dd = (v - peak_val) / peak_val
            if dd < -0.05:  # 回撤>5%才记录
                if not in_drawdown:
                    in_drawdown = True
                    dd_start_idx = peak_idx
                    max_dd = dd
                else:
                    max_dd = min(max_dd, dd)

    # 当前是否处于回撤中
    current_drawdown = None
    if nav_series[-1] < peak_val:
        current_dd = (nav_series[-1] - peak_val) / peak_val
        current_drawdown = {
            "peak_value": float(peak_val),
            "current_value": float(nav_series[-1]),
            "drawdown_pct": round(current_dd * 100, 2),
            "days_since_peak": (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[peak_idx])).days,
        }

    # 统计
    if drawdowns:
        max_historic_dd = min(d["max_drawdown_pct"] for d in drawdowns)
        avg_recovery = sum(d["recovery_days"] for d in drawdowns) / len(drawdowns)
    else:
        max_historic_dd = 0
        avg_recovery = 0

    return {
        "total_drawdowns": len(drawdowns),
        "max_historic_drawdown_pct": max_historic_dd,
        "avg_recovery_days": round(avg_recovery, 1),
        "recent_5_drawdowns": drawdowns[-5:] if len(drawdowns) >= 5 else drawdowns,
        "current_drawdown": current_drawdown,
    }


def diagnose_position(nav_df: pd.DataFrame) -> dict:
    """位置诊断（对应 skill 6.1 第一步）"""
    if nav_df.empty:
        return {"error": "数据为空"}

    nav_col = "单位净值" if "单位净值" in nav_df.columns else "累计净值"
    df_1y = nav_df.tail(250) if len(nav_df) >= 250 else nav_df

    current = float(df_1y[nav_col].iloc[-1])
    high_1y = float(df_1y[nav_col].max())
    low_1y = float(df_1y[nav_col].min())

    pct_from_high = (current - high_1y) / high_1y * 100
    pct_from_low = (current - low_1y) / low_1y * 100

    # 位置分类
    if pct_from_high >= -5:
        location = "🔴 高位区（距高点<5%）"
    elif pct_from_high >= -15:
        location = "🟡 中位区（距高点5-15%）"
    elif pct_from_high >= -25:
        location = "🟢 回调区（距高点15-25%）"
    else:
        location = "🟢 深跌区（距高点>25%）"

    # 近期涨跌幅
    returns = {}
    for days, label in [(5, "5d"), (20, "20d"), (60, "60d")]:
        if len(df_1y) > days:
            past = float(df_1y[nav_col].iloc[-days-1])
            returns[f"return_{label}_pct"] = round((current - past) / past * 100, 2)

    return {
        "current_nav": current,
        "high_1y": high_1y,
        "low_1y": low_1y,
        "pct_from_high": round(pct_from_high, 2),
        "pct_from_low": round(pct_from_low, 2),
        "location": location,
        **returns,
    }


def make_buy_decision(
    position: dict,
    stabilization: dict,
    valuation_pct: float = None,
    market_state: str = "normal",
    holding_trend_consensus: dict = None,
) -> dict:
    """
    ★ v6.0核心：综合买入决策（先大盘开关/重仓股趋势，再估值/技术）
    输入：位置诊断 + 企稳信号（来自04脚本） + 估值分位 + 大盘状态 + 重仓股共振
    """
    risk_profile = get_risk_profile(market_state)

    # 估值判定
    if valuation_pct is None:
        valuation_rating = "未知"
        valuation_ok = None
    elif valuation_pct < 30:
        valuation_rating = "🟢 低估"
        valuation_ok = True
    elif valuation_pct < 50:
        valuation_rating = "🟡 合理偏低"
        valuation_ok = True
    elif valuation_pct < 70:
        valuation_rating = "🟠 合理偏高"
        valuation_ok = False
    else:
        valuation_rating = "🔴 高估"
        valuation_ok = False

    # 技术面企稳
    tech_level = stabilization.get("confirmation_level", "🔴 禁止买入") if stabilization else "🔴 禁止买入"

    holding_trend_consensus = holding_trend_consensus or {}
    holding_state = holding_trend_consensus.get("consensus_state", "unknown")
    holding_score = holding_trend_consensus.get("consensus_score")
    holding_ok = holding_state in ["bullish", "mixed_up"] or holding_score is None

    gate_fail_reasons = []
    if not risk_profile["allow_new_buy"]:
        gate_fail_reasons.append("大盘/赛道总开关关闭，禁止新买入")
    if holding_state in ["bearish", "mixed_down"]:
        gate_fail_reasons.append("重仓股趋势共振偏弱，基金净值大概率承压")
    if valuation_ok is False:
        gate_fail_reasons.append("估值不提供安全边际")
    if "禁止" in tech_level or "观察" in tech_level:
        gate_fail_reasons.append("技术面尚未给出可买信号")

    high_confidence_pass = (
        risk_profile["state"] == "normal"
        and holding_ok
        and (holding_score is None or holding_score >= 75)
        and valuation_ok is True
        and "强企稳" in tech_level
    )

    if gate_fail_reasons:
        decision = "🔴 不建议买入"
        action = "；".join(gate_fail_reasons)
    elif high_confidence_pass:
        decision = "🟢 强买入（首笔30%）"
        action = "通过v6.0高置信门槛：大盘向上+重仓股共振+估值低+技术强企稳，可执行第一笔试探仓"
    elif valuation_ok and "弱企稳" in tech_level:
        decision = "🟡 可小仓位试探（≤10%）"
        action = "仅允许小仓观察，5-10个交易日后需重新验证大盘与重仓股趋势"
    elif valuation_ok and "观察" in tech_level:
        decision = "🟠 等待"
        action = "估值已满足，但技术面未企稳，继续等待企稳信号"
    elif valuation_ok and "禁止" in tech_level:
        decision = "🟠 观察（禁止接飞刀）"
        action = "虽然估值便宜，但仍在下跌通道，切勿接飞刀"
    elif not valuation_ok and "强企稳" in tech_level:
        decision = "🟡 谨慎小仓"
        action = "估值偏贵，即使技术强势也只能轻仓跟随，并设置更紧止损"
    else:
        decision = "🔴 不建议买入"
        action = "估值与技术面均不支持买入，等待更好时机"

    actionable_buy = is_actionable_buy(decision)

    if actionable_buy:
        adjusted_position_pct = round(30 * risk_profile["position_multiplier"], 1)
        if adjusted_position_pct <= 0:
            decision = "🔴 不建议买入"
            action = "大盘/赛道总开关关闭，禁止新买入"
            actionable_buy = False
        elif adjusted_position_pct < 30:
            action = f"{action}；受大盘开关影响，计划仓位上限降至常规的{risk_profile['position_multiplier']:.0%}"

    # 具体价格区间
    current = position.get("current_nav", 0)
    buy_range_low = round(current * 0.98, 4)
    buy_range_high = round(current * 1.02, 4)
    stop_prices = calc_stop_prices(current, risk_profile["state"])
    tp1 = round(current * 1.15, 4)  # +15%
    tp2 = round(current * 1.25, 4)  # +25%
    tp3 = round(current * 1.40, 4)  # +40%

    return {
        "valuation_rating": valuation_rating,
        "technical_level": tech_level,
        "market_gate": {
            "state": risk_profile["state"],
            "label": risk_profile["label"],
            "allow_new_buy": risk_profile["allow_new_buy"],
            "position_multiplier": risk_profile["position_multiplier"],
        },
        "holding_trend_consensus": holding_trend_consensus,
        "high_confidence_gate": {
            "passed": high_confidence_pass,
            "note": "目标是提高胜率和控制亏损，不承诺固定收益率或固定90%胜率",
            "fail_reasons": gate_fail_reasons,
        },
        "decision": decision,
        "action": action,
        "buy_range": {"low": buy_range_low, "high": buy_range_high} if actionable_buy else None,
        "stop_loss": stop_prices["initial_stop_price"],
        "hard_stop_loss": stop_prices["hard_stop_price"],
        "stop_policy": stop_prices,
        "take_profit": {
            "tp1_+15%": tp1,
            "tp2_+25%": tp2,
            "tp3_+40%": tp3,
        } if actionable_buy else None,
        "batch_plan": (
            "第一笔30% @ 当前价 | 第二笔40% @ 突破近期高点且重仓股共振继续向上 | 第三笔30% @ 趋势进一步确认；禁止因下跌补仓"
            if actionable_buy else None
        ),
    }


def analyze_fund_trend(
    fund_code: str,
    market_state: str = "normal",
    holding_trend_consensus: dict = None,
) -> dict:
    """主入口"""
    print_banner(f"基金走势 + 买卖时机 | {fund_code}")

    nav_df = get_fund_nav(fund_code, years=3)
    if nav_df.empty:
        print("⚠️  净值数据获取失败")
        return {"fund_code": fund_code, "error": "净值为空"}

    position = diagnose_position(nav_df)
    drawdowns = analyze_drawdowns(nav_df)

    # 注意：企稳信号建议直接调用 04_technical_analysis 对基金重仓股或跟踪指数进行判断
    # 此处仅用基金本身的净值做简化的企稳判断
    nav_col = "单位净值" if "单位净值" in nav_df.columns else "累计净值"
    last5_closes = nav_df[nav_col].tail(5).values
    simple_stab = check_simple_stabilization(nav_df, nav_col)

    decision = make_buy_decision(
        position,
        simple_stab,
        valuation_pct=None,
        market_state=market_state,
        holding_trend_consensus=holding_trend_consensus,
    )

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_points": len(nav_df),
        "position_diagnosis": position,
        "drawdown_analysis": drawdowns,
        "simple_stabilization": simple_stab,
        "buy_decision": decision,
    }

    # 打印
    p = result["position_diagnosis"]
    print(f"\n【位置诊断】")
    print(f"  当前净值: {p['current_nav']}")
    print(f"  近1年高点: {p['high_1y']}  距高点: {p['pct_from_high']}%")
    print(f"  近1年低点: {p['low_1y']}  距低点: +{p['pct_from_low']}%")
    print(f"  位置: {p['location']}")
    print(f"  涨跌: 5日{p.get('return_5d_pct')}%  20日{p.get('return_20d_pct')}%  60日{p.get('return_60d_pct')}%")

    dd = result["drawdown_analysis"]
    if "max_historic_drawdown_pct" in dd:
        print(f"\n【回撤分析】")
        print(f"  历史总回撤次数: {dd['total_drawdowns']}")
        print(f"  最大历史回撤: {dd['max_historic_drawdown_pct']}%")
        print(f"  平均修复天数: {dd['avg_recovery_days']}天")
        if dd.get("current_drawdown"):
            cd = dd["current_drawdown"]
            print(f"  ⚠️  当前回撤中: {cd['drawdown_pct']}%（距离高点{cd['days_since_peak']}天）")

    d = result["buy_decision"]
    print(f"\n【买卖决策】")
    print(f"  估值: {d['valuation_rating']}")
    print(f"  技术: {d['technical_level']}")
    print(f"  大盘开关: {d['market_gate']['label']}  新买入: {'允许' if d['market_gate']['allow_new_buy'] else '禁止'}")
    hc = d.get("holding_trend_consensus", {})
    if hc:
        print(f"  重仓股共振: {hc.get('consensus_label', 'N/A')}  得分: {hc.get('consensus_score', 'N/A')}/100")
    print(f"  高置信门槛: {'通过' if d['high_confidence_gate']['passed'] else '未通过'}")
    print(f"  决策: {d['decision']}")
    print(f"  操作: {d['action']}")
    if d.get("buy_range"):
        print(f"  买入区间: {d['buy_range']['low']} ~ {d['buy_range']['high']}")
        print(f"  初始止损: {d['stop_loss']} ({d['stop_policy']['initial_stop_pct']}%)")
        print(f"  硬止损: {d['hard_stop_loss']} ({d['stop_policy']['hard_stop_pct']}%)")
        print(f"  止盈: +15%@{d['take_profit']['tp1_+15%']} / +25%@{d['take_profit']['tp2_+25%']} / +40%@{d['take_profit']['tp3_+40%']}")
        print(f"  建仓: {d['batch_plan']}")

    save_path = save_result(result, f"trend_{fund_code}", subdir="07_trend")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


def check_simple_stabilization(nav_df: pd.DataFrame, nav_col: str) -> dict:
    """基金净值的简化企稳判断（若需精确请用 04 脚本对跟踪指数分析）"""
    if len(nav_df) < 30:
        return {"confirmation_level": "🔴 数据不足", "triggered_count": 0, "total_signals": 5}

    signals = {
        "连续3日不创新低": False,
        "MA5上穿MA10": False,
        "MA20拐头": False,
        "近5日波动收窄": False,
        "近20日未深跌": False,
    }

    navs = nav_df[nav_col].tail(30).values
    ma5 = pd.Series(navs).rolling(5).mean().values
    ma10 = pd.Series(navs).rolling(10).mean().values
    ma20 = pd.Series(navs).rolling(20).mean().values

    # 1. 连续3日不创新低
    last5_lows = navs[-5:]
    if last5_lows[-1] > min(last5_lows[:-3]) if len(last5_lows) >= 3 else False:
        signals["连续3日不创新低"] = True

    # 2. MA5 > MA10
    if not np.isnan(ma5[-1]) and not np.isnan(ma10[-1]):
        if ma5[-1] > ma10[-1] and ma5[-3] <= ma10[-3]:
            signals["MA5上穿MA10"] = True

    # 3. MA20拐头
    if not np.isnan(ma20[-1]) and not np.isnan(ma20[-5]):
        if ma20[-5] > ma20[-3] and ma20[-1] >= ma20[-3]:
            signals["MA20拐头"] = True

    # 4. 近5日波动收窄
    vol_5 = np.std(navs[-5:])
    vol_10 = np.std(navs[-10:-5])
    if vol_5 < vol_10 * 0.7:
        signals["近5日波动收窄"] = True

    # 5. 近20日未深跌
    drop_20d = (navs[-1] - navs[-20]) / navs[-20]
    if drop_20d > -0.03:
        signals["近20日未深跌"] = True

    triggered = sum(signals.values())
    if triggered >= 4:
        level = "🟢 强企稳"
    elif triggered >= 3:
        level = "🟡 弱企稳"
    elif triggered >= 2:
        level = "🟠 观察"
    else:
        level = "🔴 禁止"

    return {
        "signals": signals,
        "triggered_count": triggered,
        "total_signals": 5,
        "confirmation_level": level,
        "note": "此为基金净值简化判断，精确分析请用04脚本对跟踪指数/重仓股进行"
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 07_fund_trend.py <基金代码>")
        sys.exit(1)
    analyze_fund_trend(sys.argv[1])
