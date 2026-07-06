"""
00_main.py —— 统一入口（一键执行完整分析流程）
================================================
按 v6.0 skill 的 Step 0-7 顺序调用各子脚本，输出完整分析报告。

用法：
    # 完整分析某只基金
    python 00_main.py <基金代码>
    python 00_main.py 001938

    # 仅宏观数据（不针对具体基金）
    python 00_main.py --macro

    # 跟踪已有持仓
    python 00_main.py --track <基金代码> <买入价> <买入日期> [金额]
"""

import sys
import importlib.util
import os
from datetime import datetime

from config import classify_market_switch_score, print_banner, save_result, get_logger

logger = get_logger(__name__)


def load_module(filename: str):
    """动态加载脚本模块（因为文件名以数字开头）"""
    module_name = filename.replace(".py", "").replace("-", "_")
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_holding_trend_consensus(top_holdings: list, tech_results: dict) -> dict:
    """v6.0：按持仓占比汇总前5大重仓股的上涨/震荡/下跌共振。"""
    if not top_holdings or not tech_results:
        return {
            "consensus_state": "unknown",
            "consensus_label": "⚪ 数据不足",
            "consensus_score": 0,
            "details": [],
            "note": "缺少重仓股或技术分析数据",
        }

    zone_score = {"uptrend": 100, "neutral": 50, "downtrend": 0, "unknown": 0}
    weighted_score = 0
    total_weight = 0
    state_weight = {"uptrend": 0, "neutral": 0, "downtrend": 0, "unknown": 0}
    details = []

    for holding in top_holdings[:5]:
        code = holding.get("code")
        ratio = holding.get("ratio") or 0
        tech = tech_results.get(code, {})
        trend = tech.get("trend_zone", {}) if isinstance(tech, dict) else {}
        state = trend.get("trend_state", "unknown")
        score = zone_score.get(state, 0)
        weighted_score += score * ratio
        total_weight += ratio
        state_weight[state] = state_weight.get(state, 0) + ratio
        details.append({
            "code": code,
            "name": holding.get("name"),
            "ratio": ratio,
            "trend_state": state,
            "trend_zone": trend.get("trend_zone", "⚪ 数据不足"),
            "trend_score": trend.get("score", 0),
            "action": trend.get("action", "无"),
        })

    consensus_score = round(weighted_score / total_weight, 1) if total_weight else 0
    up_ratio = state_weight.get("uptrend", 0)
    down_ratio = state_weight.get("downtrend", 0)

    if consensus_score >= 75 and up_ratio >= down_ratio * 2:
        consensus_state = "bullish"
        consensus_label = "🟢 重仓股共振向上"
    elif consensus_score >= 55 and down_ratio < up_ratio:
        consensus_state = "mixed_up"
        consensus_label = "🟡 偏多但有分歧"
    elif down_ratio >= up_ratio:
        consensus_state = "bearish"
        consensus_label = "🔴 重仓股共振向下"
    else:
        consensus_state = "mixed_down"
        consensus_label = "🟠 分歧偏弱"

    return {
        "consensus_state": consensus_state,
        "consensus_label": consensus_label,
        "consensus_score": consensus_score,
        "top5_total_ratio": round(total_weight, 2),
        "state_weight": {k: round(v, 2) for k, v in state_weight.items()},
        "details": details,
        "note": "基金净值与重仓股强相关；多数重仓股转弱时，新买入自动降级或否决",
    }


def direction_to_score(direction: str) -> int:
    """把五时钟方向压缩成大盘总开关分数。"""
    if direction == "2点钟":
        return 100
    if direction in ["1点钟", "3点钟"]:
        return 50
    if direction in ["4点钟", "5-6点钟"]:
        return 0
    return 50


def build_market_switch(broad_market: dict, sector_data: dict, global_data: dict) -> dict:
    """v6.0：大盘/资金/全球三层方向总开关。"""
    broad_direction = broad_market.get("long_term_120d", {}).get("direction") if broad_market else None
    broad_score = direction_to_score(broad_direction)

    sector_score = 50
    sector_reasons = []
    north = (sector_data or {}).get("northbound_flow", {})
    margin = (sector_data or {}).get("margin_trend", {})
    if north.get("score_out_of_25") is not None:
        sector_score += (north["score_out_of_25"] - 12) * 2
        sector_reasons.append(f"北向资金评分{north['score_out_of_25']}/25")
    if margin.get("trend") == "上升":
        sector_score += 15
        sector_reasons.append("两融余额上升")
    elif margin.get("trend") == "下降":
        sector_score -= 15
        sector_reasons.append("两融余额下降")
    sector_score = max(0, min(100, sector_score))

    global_score = 50
    global_reasons = []
    vix = (global_data or {}).get("vix", {})
    vix_val = vix.get("vix")
    if vix_val is not None:
        if vix_val > 30:
            global_score = 0
        elif vix_val > 20:
            global_score = 35
        elif vix_val > 15:
            global_score = 60
        else:
            global_score = 80
        global_reasons.append(f"VIX={vix_val}")

    weighted_score = round(broad_score * 0.4 + sector_score * 0.4 + global_score * 0.2, 1)
    switch = classify_market_switch_score(weighted_score)

    return {
        "weighted_score": weighted_score,
        "state": switch["state"],
        "label": switch["label"],
        "switch": switch["switch"],
        "broad_market": {
            "object": "沪深300",
            "direction": broad_direction or "未知",
            "score": broad_score,
        },
        "sector_fund_flow": {
            "score": round(sector_score, 1),
            "reasons": sector_reasons,
        },
        "global_linkage": {
            "score": round(global_score, 1),
            "reasons": global_reasons,
        },
        "decision": (
            "允许正常执行买入流程" if switch["state"] == "normal"
            else "仓位砍半，止损收紧" if switch["state"] == "neutral"
            else "禁止新买入，已持仓减仓并收紧止损"
        ),
    }


def run_full_analysis(fund_code: str) -> dict:
    """完整执行 Step 1-7 分析流程"""
    print_banner(f"★ 基金智能筛选与行业轮动分析 v6.19 ★  目标：{fund_code}", char="═", width=70)
    print(f"分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    final_report = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "skill_version": "v6.19",
    }

    # ===== Step 1.1 基金筛选 =====
    print_banner("Step 1.1 | 基金筛选数据", char="─")
    try:
        mod = load_module("01_fund_screening.py")
        final_report["step_1_1_screening"] = mod.screen_fund(fund_code)
        final_report["step_1_1bis_risk_return"] = final_report["step_1_1_screening"].get("risk_return_reference", {})
    except Exception as e:
        logger.error(f"Step 1.1 失败: {e}")
        final_report["step_1_1_screening"] = {"error": str(e)}
        final_report["step_1_1bis_risk_return"] = {"error": str(e)}

    # ===== Step 1.2 持仓数据 =====
    print_banner("Step 1.2 | 持仓数据", char="─")
    try:
        mod = load_module("02_fund_holdings.py")
        final_report["step_1_2_holdings"] = mod.analyze_holdings(fund_code)
    except Exception as e:
        logger.error(f"Step 1.2 失败: {e}")
        final_report["step_1_2_holdings"] = {"error": str(e)}

    # ===== Step 1.3 行业板块数据 =====
    print_banner("Step 1.3 | 行业板块数据", char="─")
    try:
        mod = load_module("03_sector_data.py")
        final_report["step_1_3_sectors"] = mod.analyze_sectors()
    except Exception as e:
        logger.error(f"Step 1.3 失败: {e}")
        final_report["step_1_3_sectors"] = {"error": str(e)}

    # ===== Step 1.3bis 热点板块基金推荐（v6.18新增） =====
    print_banner("Step 1.3bis | 热点板块与推荐基金", char="─")
    try:
        mod = load_module("17_hot_sector_fund_recommendation.py")
        final_report["step_1_3bis_hot_sector_funds"] = mod.analyze_hot_sector_funds(fund_code)
    except Exception as e:
        logger.error(f"Step 1.3bis 热点板块基金推荐失败: {e}")
        final_report["step_1_3bis_hot_sector_funds"] = {"error": str(e)}

    # ===== Step 1.4 量价技术（对跟踪指数或重仓股） =====
    print_banner("Step 1.4 | 量价技术分析（重仓股）", char="─")
    try:
        holdings = final_report.get("step_1_2_holdings", {})
        top_holdings = holdings.get("top10_holdings", [])
        if top_holdings:
            mod = load_module("04_technical_analysis.py")
            # v6.0：对前5大重仓股进行技术分析，形成基金-股票趋势共振
            tech_results = {}
            for h in top_holdings[:5]:
                code = h.get("code")
                if code:
                    try:
                        tech_results[code] = mod.analyze_technical(code)
                    except Exception as e:
                        tech_results[code] = {"error": str(e)}
            final_report["step_1_4_technical"] = tech_results
            final_report["step_1_4_holding_trend_consensus"] = build_holding_trend_consensus(top_holdings, tech_results)
        else:
            final_report["step_1_4_technical"] = {"note": "无法获取重仓股，跳过"}
            final_report["step_1_4_holding_trend_consensus"] = build_holding_trend_consensus([], {})
    except Exception as e:
        logger.error(f"Step 1.4 失败: {e}")
        final_report["step_1_4_technical"] = {"error": str(e)}
        final_report["step_1_4_holding_trend_consensus"] = build_holding_trend_consensus([], {})

    # ===== Step 1.5 全球市场 =====
    print_banner("Step 1.5 | 全球市场数据", char="─")
    try:
        mod = load_module("05_global_market.py")
        final_report["step_1_5_global"] = mod.analyze_global_market()
    except Exception as e:
        logger.error(f"Step 1.5 失败: {e}")
        final_report["step_1_5_global"] = {"error": str(e)}

    # ===== Step 1.6 基本面（重仓股） =====
    print_banner("Step 1.6 | 基本面分析（重仓股）", char="─")
    try:
        holdings = final_report.get("step_1_2_holdings", {})
        top_holdings = holdings.get("top10_holdings", [])
        if top_holdings:
            mod = load_module("06_fundamental_data.py")
            fund_results = {}
            for h in top_holdings[:5]:
                code = h.get("code")
                if code:
                    try:
                        fund_results[code] = mod.analyze_fundamental(code)
                    except Exception as e:
                        fund_results[code] = {"error": str(e)}
            final_report["step_1_6_fundamental"] = fund_results
        else:
            final_report["step_1_6_fundamental"] = {"note": "跳过"}
    except Exception as e:
        logger.error(f"Step 1.6 失败: {e}")
        final_report["step_1_6_fundamental"] = {"error": str(e)}

    # ===== Step 1.6bis 季报滞后与调仓偏差识别（v6.17新增） =====
    print_banner("Step 1.6bis | 季报滞后与调仓偏差识别", char="─")
    try:
        mod = load_module("16_quarterly_drift.py")
        final_report["step_1_6bis_quarterly_drift"] = mod.analyze_quarterly_drift(fund_code)
    except Exception as e:
        logger.error(f"Step 1.6bis 季报滞后偏差识别失败: {e}")
        final_report["step_1_6bis_quarterly_drift"] = {"error": str(e)}

    # ===== Step 2.5 大盘方向总开关（v6.0新增） =====
    print_banner("Step 2.5 | v6.0 大盘方向总开关", char="─")
    try:
        mod = load_module("11_clock_trend.py")
        broad_market = mod.analyze_clock_trend("000300", "index")
    except Exception as e:
        logger.error(f"Step 2.5 沪深300趋势失败: {e}")
        broad_market = {"error": str(e)}
    final_report["step_2_5_market_switch"] = build_market_switch(
        broad_market,
        final_report.get("step_1_3_sectors", {}),
        final_report.get("step_1_5_global", {}),
    )
    ms = final_report["step_2_5_market_switch"]
    print(f"\n【大盘方向总开关】{ms['switch']}  得分 {ms['weighted_score']}/100")
    print(f"  沪深300方向: {ms['broad_market']['direction']}  分数: {ms['broad_market']['score']}")
    print(f"  资金层分数: {ms['sector_fund_flow']['score']}  全球层分数: {ms['global_linkage']['score']}")
    print(f"  决策: {ms['decision']}")

    # ===== Step 2.6 外围局势风险雷达（v6.2新增） =====
    print_banner("Step 2.6 | v6.2 外围局势风险雷达", char="─")
    try:
        mod = load_module("14_macro_geopolitical_risk.py")
        final_report["step_2_6_macro_geopolitical"] = mod.analyze_macro_geopolitical_risk(fund_code)
    except Exception as e:
        logger.error(f"Step 2.6 外围局势风险雷达失败: {e}")
        final_report["step_2_6_macro_geopolitical"] = {"error": str(e)}

    # ===== Step 6.0 量化验证闸门（v6.1新增） =====
    print_banner("Step 6.0 | v6.1 量化验证闸门", char="─")
    try:
        mod = load_module("13_quant_validation.py")
        final_report["step_6_0_quant_validation"] = mod.analyze_quant_validation(
            fund_code,
            market_state=final_report.get("step_2_5_market_switch", {}).get("state", "neutral"),
        )
    except Exception as e:
        logger.error(f"Step 6.0 量化验证失败: {e}")
        final_report["step_6_0_quant_validation"] = {"error": str(e)}

    # ===== Step 1.7 基金走势 + 买卖时机 =====
    print_banner("Step 1.7 | 基金走势与买卖时机", char="─")
    try:
        mod = load_module("07_fund_trend.py")
        final_report["step_1_7_trend"] = mod.analyze_fund_trend(
            fund_code,
            market_state=final_report.get("step_2_5_market_switch", {}).get("state", "neutral"),
            holding_trend_consensus=final_report.get("step_1_4_holding_trend_consensus", {}),
        )
    except Exception as e:
        logger.error(f"Step 1.7 失败: {e}")
        final_report["step_1_7_trend"] = {"error": str(e)}

    # ===== Step 1.8 节假日风险（v4.0新增） =====
    print_banner("Step 1.8 | 节假日风险评估", char="─")
    try:
        mod = load_module("08_holiday_risk.py")
        final_report["step_1_8_holiday"] = mod.analyze_holiday_risk(with_history=False)
    except Exception as e:
        logger.error(f"Step 1.8 失败: {e}")
        final_report["step_1_8_holiday"] = {"error": str(e)}

    # ===== 最终总结 =====
    print_banner("最终分析汇总", char="═", width=70)
    summary = generate_summary(final_report)
    final_report["summary"] = summary
    
    # 保存完整报告
    save_path = save_result(final_report, f"full_analysis_{fund_code}", subdir="00_full_report")
    print(f"\n📊 完整分析报告已保存: {save_path}")

    return final_report


def generate_summary(report: dict) -> dict:
    """生成最终分析汇总"""
    summary = {
        "fund_code": report["fund_code"],
        "key_findings": [],
        "recommendations": [],
        "risks": [],
    }

    # 量化门槛检查
    screening = report.get("step_1_1_screening", {}).get("screening", {})
    if screening:
        if screening.get("overall_pass"):
            summary["key_findings"].append("✅ 量化6项门槛：通过")
        else:
            summary["key_findings"].append(f"❌ 量化门槛：通过{screening.get('passed_count',0)}/6")

    selection_reference = report.get("step_1_1_screening", {}).get("selection_reference", {})
    if selection_reference:
        gate = selection_reference.get("gate", {})
        if selection_reference.get("passed"):
            summary["key_findings"].append("✅ 近1/3/6月同赛道强势：可作为入选/持有正向参考")
            summary["recommendations"].append(selection_reference.get("message", "近期同赛道强势通过，可作为入选/持有参考标准之一"))
        elif gate:
            summary["key_findings"].append(
                f"⚠️ 近1/3/6月同赛道强势：通过{gate.get('passed_periods', 0)}/{len(gate.get('metrics', []))}"
            )
            summary["risks"].append(selection_reference.get("message", "近期同赛道强势未完全通过，不能作为入选/持有正向依据"))
        else:
            summary["risks"].append(selection_reference.get("message", "近1/3/6月同赛道强势数据缺失"))

    four_dimension = report.get("step_1_1_screening", {}).get("four_dimension_reference", {})
    if four_dimension:
        gate = four_dimension.get("gate", {})
        if four_dimension.get("passed"):
            summary["key_findings"].append("✅ 四维严格闸门通过：回撤已记录，涨跌幅/同赛道平均为正，且排名前5")
        elif gate:
            summary["key_findings"].append(
                f"⚠️ 四维严格闸门：通过{gate.get('passed_periods', 0)}/{len(gate.get('metrics', []))}"
            )
            summary["risks"].append(four_dimension.get("message", "四维严格闸门未完全通过"))
            for reason in four_dimension.get("blocking_reasons", [])[:3]:
                summary["risks"].append(f"四维闸门未满足：{reason}")
        else:
            summary["risks"].append(four_dimension.get("message", "四维严格闸门数据缺失"))

    drawdown_guard = report.get("step_1_1_screening", {}).get("drawdown_guard", {})
    if drawdown_guard:
        metrics = drawdown_guard.get("metrics", {})
        summary["key_findings"].append(
            "持有回撤闸门："
            f"{drawdown_guard.get('level')}；当前回撤{metrics.get('current_drawdown_pct')}%，"
            f"近1年最大回撤{metrics.get('max_drawdown_1y_pct')}%，"
            f"近3年最大回撤{metrics.get('max_drawdown_3y_pct')}%"
        )
        if drawdown_guard.get("level") == "reduce_or_exit":
            summary["risks"].append("🔴 持有回撤/同赛道排名触发硬风控：禁止加仓，优先减仓或止损")
        elif drawdown_guard.get("level") == "degraded_hold":
            summary["risks"].append("🟠 持有回撤/同赛道排名触发降级：只允许降级持有或观察，暂停新增资金")
        elif drawdown_guard.get("level") == "hold_with_position_cap":
            summary["recommendations"].append("历史回撤提示高波动，但近期相对排名和修复有效：可继续持有，按高波动基金控制仓位")
        for flag in drawdown_guard.get("risk_flags", [])[:3]:
            summary["risks"].append(f"回撤/排名风险：{flag}")
        for flag in drawdown_guard.get("support_flags", [])[:2]:
            summary["key_findings"].append(f"持有支持证据：{flag}")

    risk_return = report.get("step_1_1bis_risk_return") or report.get("step_1_1_screening", {}).get("risk_return_reference", {})
    rr_guard = risk_return.get("risk_return_guard", {}) if isinstance(risk_return, dict) else {}
    if rr_guard:
        summary["key_findings"].append(
            "夏普/波动率横向闸门："
            f"{rr_guard.get('level')}；平均夏普{rr_guard.get('avg_sharpe')}，"
            f"平均年化波动{rr_guard.get('avg_annualized_volatility_pct')}%，"
            f"通过{rr_guard.get('passed_periods')}/{rr_guard.get('total_periods')}个窗口"
        )
        if rr_guard.get("level") in ["lagging", "unknown"]:
            summary["risks"].append("⚠️ 夏普/波动率横向排名不足：不能支持强加仓，需与同类更强基金比较")
        elif rr_guard.get("level") == "watch":
            summary["risks"].append("夏普/波动率仅单窗口领先：只作为观察项，不支持追高重仓")
        elif rr_guard.get("level") in ["risk_return_leader", "partial_leader"]:
            summary["recommendations"].append(rr_guard.get("message", "风险收益横向表现较好，可作为继续持有参考"))
        for flag in rr_guard.get("risk_flags", [])[:3]:
            summary["risks"].append(f"风险收益风险：{flag}")
        for flag in rr_guard.get("support_flags", [])[:2]:
            summary["key_findings"].append(f"风险收益支持证据：{flag}")

    quarterly_drift = report.get("step_1_6bis_quarterly_drift", {})
    valuation_bias = quarterly_drift.get("valuation_bias", {}) if isinstance(quarterly_drift, dict) else {}
    if valuation_bias:
        summary["key_findings"].append(
            f"季报持仓估值偏差：{valuation_bias.get('level')}，偏差{valuation_bias.get('bias_pct')}%"
        )
        if valuation_bias.get("level") in ["high_drift", "medium_drift"]:
            summary["risks"].append("⚠️ 季报持仓与实时估值偏差明显：基金经理可能已调仓，降低对季报持仓推演的依赖")
        elif valuation_bias.get("level") in ["data_weak", "unknown"]:
            summary["risks"].append("季报持仓估值偏差数据质量不足：不能用前十大持仓推演替代净值趋势判断")

    hot_sector_funds = report.get("step_1_3bis_hot_sector_funds", {})
    if hot_sector_funds and not hot_sector_funds.get("error"):
        hot = hot_sector_funds.get("hot_sector_scan", {}).get("hot_sectors", [])
        if hot:
            top_hot = "、".join(f"{item.get('sector')}({item.get('change_pct')}%)" for item in hot[:3])
            summary["key_findings"].append(f"当前热点板块：{top_hot}")
        alignment = hot_sector_funds.get("current_fund_hot_alignment", {})
        if alignment and not alignment.get("error"):
            summary["key_findings"].append(
                "当前基金热点贴合度："
                f"{alignment.get('alignment_level')}，热点匹配{alignment.get('hot_matched_weight_pct')}%"
            )
            if alignment.get("alignment_level") == "low_alignment":
                summary["recommendations"].append("当前基金与热点板块贴合较低，可把热点推荐基金纳入调仓候选，但不一次性追高切换")
        recs = hot_sector_funds.get("recommendations", [])
        if recs:
            best = recs[0]
            summary["recommendations"].append(
                "热点基金候选："
                f"{best.get('fund_code')} {best.get('fund_name')}，匹配{best.get('matched_sector')}，"
                f"1月{best.get('month1_pct')}%，3月{best.get('month3_pct')}%，"
                f"等级{best.get('recommendation_level')}"
            )
            for fund in recs[:3]:
                if fund.get("recommendation_level") not in ["strong_recommend", "watch_candidate"]:
                    continue
                summary["key_findings"].append(
                    f"推荐候选：{fund.get('fund_code')} {fund.get('fund_name')} "
                    f"({fund.get('matched_sector')}, 得分{fund.get('final_score')})"
                )

    # 买卖决策
    market_switch = report.get("step_2_5_market_switch", {})
    if market_switch:
        summary["key_findings"].append(f"大盘总开关：{market_switch.get('switch')}（{market_switch.get('weighted_score')}/100）")
        if market_switch.get("state") == "downtrend":
            summary["risks"].append("🔴 大盘/资金/全球总开关关闭：禁止新买入，持仓收紧止损")

    macro_geo = report.get("step_2_6_macro_geopolitical", {})
    realtime = macro_geo.get("realtime_risk", {})
    medium = macro_geo.get("medium_term_view", {})
    if realtime:
        summary["key_findings"].append(f"外围局势短期风险：{realtime.get('short_term_level')}（{realtime.get('risk_score')}/100）")
        if "🔴" in str(realtime.get("short_term_level")):
            summary["risks"].append("🔴 外围/贸易/地缘短期风险高：禁止新买入，持仓收紧止损")
        elif "🟠" in str(realtime.get("short_term_level")):
            summary["risks"].append("🟠 外围/贸易/地缘风险偏高：仓位至少减半")
    if medium:
        summary["key_findings"].append(f"外围局势中期风险：{medium.get('medium_term_level')}（{medium.get('medium_term_score')}/100）")

    consensus = report.get("step_1_4_holding_trend_consensus", {})
    if consensus:
        summary["key_findings"].append(f"重仓股趋势共振：{consensus.get('consensus_label')}（{consensus.get('consensus_score')}/100）")
        if consensus.get("consensus_state") in ["bearish", "mixed_down"]:
            summary["risks"].append("🔴 前5大重仓股趋势偏弱：基金净值存在跟跌风险")

    quant_gate = report.get("step_6_0_quant_validation", {}).get("validation_gate", {})
    if quant_gate:
        summary["key_findings"].append(f"量化验证闸门：{quant_gate.get('decision')}（{quant_gate.get('score')}/100）")
        if "不通过" in str(quant_gate.get("decision")):
            summary["risks"].append("🔴 回测/滚动稳定性/回撤控制未通过：禁止新买入")
        elif "半通过" in str(quant_gate.get("decision")):
            summary["risks"].append("🟡 量化验证半通过：只能轻仓或继续观察")

    decision = report.get("step_1_7_trend", {}).get("buy_decision", {})
    if decision:
        summary["key_findings"].append(f"买卖决策：{decision.get('decision', 'N/A')}")
        summary["recommendations"].append(decision.get("action", ""))

    # 节假日风险
    holiday = report.get("step_1_8_holiday", {})
    if "next_holiday" in holiday:
        nh = holiday["next_holiday"]
        if nh.get("days_until_start") is not None and nh["days_until_start"] <= 30:
            summary["risks"].append(f"⚠️ 近期节假日：{nh.get('holiday_name')} 距今{nh['days_until_start']}天，风险{nh.get('risk_level')}")

    # 位置风险
    position = report.get("step_1_7_trend", {}).get("position_diagnosis", {})
    if "高位区" in str(position.get("location", "")):
        summary["risks"].append("⚠️ 基金净值处于高位区（距高点<5%），买入请谨慎")

    # 打印汇总
    print(f"\n🎯 基金代码: {summary['fund_code']}")
    print(f"\n📌 关键发现:")
    for f in summary["key_findings"]:
        print(f"  · {f}")

    if summary["recommendations"]:
        print(f"\n💡 核心建议:")
        for r in summary["recommendations"]:
            print(f"  · {r}")

    if summary["risks"]:
        print(f"\n⚠️  主要风险:")
        for r in summary["risks"]:
            print(f"  · {r}")

    return summary


def run_macro_only():
    """只运行宏观数据"""
    print_banner("★ 宏观数据扫描 ★", char="═", width=70)
    
    macro_report = {"analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    try:
        mod = load_module("03_sector_data.py")
        macro_report["sectors"] = mod.analyze_sectors()
    except Exception as e:
        macro_report["sectors"] = {"error": str(e)}

    try:
        mod = load_module("05_global_market.py")
        macro_report["global"] = mod.analyze_global_market()
    except Exception as e:
        macro_report["global"] = {"error": str(e)}

    try:
        mod = load_module("08_holiday_risk.py")
        macro_report["holiday"] = mod.analyze_holiday_risk(with_history=True)
    except Exception as e:
        macro_report["holiday"] = {"error": str(e)}

    save_path = save_result(macro_report, "macro_only", subdir="00_full_report")
    print(f"\n📊 宏观扫描已保存: {save_path}")
    return macro_report


def run_tracking(
    fund_code: str,
    buy_price: float,
    buy_date: str,
    amount: float = None,
    market_state: str = "normal",
):
    """持仓跟踪"""
    mod = load_module("09_position_tracking.py")
    return mod.track_position(fund_code, buy_price, buy_date, amount, market_state)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("=" * 60)
        print("基金智能筛选与行业轮动分析师 v6.19 - 统一入口")
        print("=" * 60)
        print("\n用法：")
        print("  python 00_main.py <基金代码>                # 完整分析")
        print("  python 00_main.py --macro                  # 仅宏观数据扫描")
        print("  python 00_main.py --track <基金代码> <买入价> <买入日期> [金额] [normal|neutral|downtrend]")
        print("\n示例：")
        print("  python 00_main.py 001938")
        print("  python 00_main.py --macro")
        print("  python 00_main.py --track 001938 2.5 2025-10-15 100000")
        sys.exit(0)

    if sys.argv[1] == "--macro":
        run_macro_only()
    elif sys.argv[1] == "--track":
        if len(sys.argv) < 5:
            print("用法: python 00_main.py --track <基金代码> <买入价> <买入日期> [金额] [normal|neutral|downtrend]")
            sys.exit(1)
        amount = None
        market_state = "normal"
        if len(sys.argv) >= 6:
            if sys.argv[5] in ["normal", "neutral", "downtrend"]:
                market_state = sys.argv[5]
            else:
                amount = float(sys.argv[5])
        if len(sys.argv) >= 7:
            market_state = sys.argv[6]
        run_tracking(sys.argv[2], float(sys.argv[3]), sys.argv[4], amount, market_state)
    else:
        run_full_analysis(sys.argv[1])
