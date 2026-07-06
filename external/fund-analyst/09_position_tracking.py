"""
09_position_tracking.py —— 持仓跟踪与动态决策（★v4.0核心新增，对应 skill 1.9 + 6.6/6.7/6.8）
=============================================================================================
核心功能：
  · 买入后浮盈浮亏跟踪
  · 止损/止盈目标计算
  · 移动止损位动态调整（对应 skill 6.8 第一层防御）
  · 分批止盈建议（+15%/+25%/+40%）
  · 被套深度诊断 + 解套策略推荐（对应 skill 6.7）
  · 盈利场景决策（对应 skill 6.6 情景A）

用法：
    # 交互式录入持仓
    python 09_position_tracking.py

    # 命令行快速跟踪
    python 09_position_tracking.py <基金代码> <买入价格> <买入日期YYYY-MM-DD> [仓位金额] [normal|neutral|downtrend]
    python 09_position_tracking.py 001938 2.5 2025-10-15 100000 downtrend
"""

import sys
import akshare as ak
import pandas as pd
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


@with_retry()
@with_cache(cache_type="realtime")
def get_current_nav(fund_code: str) -> dict:
    """获取基金最新净值"""
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df.empty:
            return {"error": "净值为空"}
        df["净值日期"] = pd.to_datetime(df["净值日期"])
        df = df.sort_values("净值日期").reset_index(drop=True)
        latest = df.iloc[-1]
        return {
            "fund_code": fund_code,
            "latest_nav": float(latest["单位净值"]),
            "latest_date": str(latest["净值日期"].date()),
            "prev_nav": float(df.iloc[-2]["单位净值"]) if len(df) >= 2 else None,
            "daily_change_pct": float(latest.get("日增长率", 0)) if pd.notna(latest.get("日增长率")) else None,
        }
    except Exception as e:
        logger.error(f"获取{fund_code}净值失败: {e}")
        return {"error": str(e)}


def calc_position_status(
    fund_code: str,
    buy_price: float,
    buy_date: str,
    position_amount: float = None,
    market_state: str = "normal",
) -> dict:
    """
    计算持仓当前状态（浮盈/浮亏/关键价位）
    """
    nav_info = get_current_nav(fund_code)
    if "error" in nav_info:
        return {"error": nav_info["error"]}

    current_nav = nav_info["latest_nav"]
    pnl_pct = (current_nav - buy_price) / buy_price * 100
    risk_profile = get_risk_profile(market_state)
    stop_prices = calc_stop_prices(buy_price, risk_profile["state"])

    # 持仓天数
    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
    holding_days = (datetime.now() - buy_dt).days

    # 关键价位
    price_levels = {
        f"初始止损{stop_prices['initial_stop_pct']}%": stop_prices["initial_stop_price"],
        f"硬止损{stop_prices['hard_stop_pct']}%": stop_prices["hard_stop_price"],
        "保本线": buy_price,                         # 0%
        "首次止盈+15%": round(buy_price * 1.15, 4),
        "二次止盈+25%": round(buy_price * 1.25, 4),
        "三次止盈+40%": round(buy_price * 1.40, 4),
    }

    # 移动止损位（v6.0：亏损更早处理，盈利后尽快保护本金）
    if pnl_pct < 5:
        moving_stop = stop_prices["initial_stop_price"]
        stop_desc = f"初始保护（买入价{stop_prices['initial_stop_pct']}%）"
    elif pnl_pct < 10:
        moving_stop = buy_price * 1.01
        stop_desc = "保本保护（成本价+1%）"
    elif pnl_pct < 15:
        moving_stop = buy_price * 1.03
        stop_desc = "小赚线（成本价+3%）"
    elif pnl_pct < 25:
        moving_stop = buy_price * 1.08
        stop_desc = "稳赚线（成本价+8%）"
    elif pnl_pct < 40:
        moving_stop = buy_price * 1.15
        stop_desc = "中赚线（成本价+15%）"
    else:
        moving_stop = round(current_nav * (1 - risk_profile["trailing_buffer_pct"] / 100), 4)
        stop_desc = f"趋势线（当前价-{risk_profile['trailing_buffer_pct']}%）"

    if risk_profile["state"] == "downtrend":
        downtrend_stop = round(current_nav * (1 - risk_profile["trailing_buffer_pct"] / 100), 4)
        moving_stop = max(moving_stop, downtrend_stop)
        stop_desc = f"{stop_desc}；大盘下行，移动止损同步收紧"

    result = {
        "fund_code": fund_code,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "risk_profile": risk_profile,
        "holding_days": holding_days,
        "current_nav": current_nav,
        "latest_date": nav_info["latest_date"],
        "pnl_pct": round(pnl_pct, 2),
        "price_levels": price_levels,
        "stop_policy": stop_prices,
        "stop_triggers": {
            "initial_stop_triggered": current_nav <= stop_prices["initial_stop_price"],
            "hard_stop_triggered": current_nav <= stop_prices["hard_stop_price"],
        },
        "moving_stop": {
            "price": round(moving_stop, 4),
            "description": stop_desc,
            "distance_pct": round((current_nav - moving_stop) / current_nav * 100, 2),
        },
    }

    if position_amount:
        result["position_amount"] = position_amount
        result["current_value"] = round(position_amount * (1 + pnl_pct / 100), 2)
        result["pnl_amount"] = round(position_amount * pnl_pct / 100, 2)

    return result


def make_profit_decision(status: dict) -> dict:
    """
    浮盈场景决策（对应 skill 6.6.2 情景A）
    """
    pnl = status.get("pnl_pct", 0)

    if pnl < 0:
        return {"status": "非浮盈场景", "hint": "请用 make_loss_decision 处理"}

    # 根据浮盈幅度决策（对应 skill 6.6.2 情景A表格）
    if pnl < 5:
        decision = "观察维持"
        actions = ["维持原仓位，不操作", "避免浮盈焦虑，观察能否持续"]
        take_profit_pct = 0
    elif pnl < 10:
        decision = "检查买入逻辑"
        actions = [
            "不加仓（避免追高）",
            "不减仓",
            "检查原买入逻辑是否仍成立",
        ]
        take_profit_pct = 0
    elif pnl < 15:
        decision = "收紧止损"
        actions = [
            "移动止损上移至保本位（成本+1-2%）",
            "分析上涨原因（业绩/资金/情绪/跟风）",
            "不追加买入",
        ]
        take_profit_pct = 0
    elif pnl < 25:
        decision = "🟢 执行第一次止盈30%"
        actions = [
            f"卖出仓位30% @ 当前价 {status['current_nav']}",
            f"剩余70%仓位，止损上移至 {status['buy_price'] * 1.03:.4f}（成本+3%）",
            "开始关注估值分位是否进入高估区（PE>70%）",
        ]
        take_profit_pct = 30
    elif pnl < 40:
        decision = "🟢 执行第二次止盈40%（累计70%）"
        actions = [
            f"再卖出仓位40%（原始仓位的40%）",
            f"剩余30%仓位，止损上移至 {status['buy_price'] * 1.10:.4f}（成本+10%）",
            "进入负成本模式：本金基本已收回",
            "检查卖出三重验证：估值/情绪/技术",
        ]
        take_profit_pct = 40
    else:
        decision = "🟢 评估顶部信号 → 是否清仓剩余30%"
        actions = [
            "检查3个问题：估值是否高估？技术是否顶背离？行业是否从🔥转🌡️？",
            "若3项都'否' → 继续持有（触发移动止损再卖）",
            "若1-2项'是' → 再卖出50-70%",
            "若3项都'是' → 清仓剩余30%",
            "保留'利润仓位'作为趋势跟踪",
        ]
        take_profit_pct = 30

    # 浮盈焦虑检查（对应 skill 6.8.5 心态铁律第8条）
    reminders = [
        "❗ 赚到的才是钱，浮盈是数字",
        "❗ 绝不赚最后一个铜板，最后一段利润留给别人",
        "❗ 分批卖永远不会错，一次卖可能错过大行情但保住本金",
    ]

    return {
        "scenario": "浮盈",
        "pnl_pct": pnl,
        "decision": decision,
        "actions": actions,
        "take_profit_pct_this_time": take_profit_pct,
        "reminders": reminders,
    }


def make_loss_decision(status: dict) -> dict:
    """
    被套场景决策 - 解套策略（对应 skill 6.7）
    """
    pnl = status.get("pnl_pct", 0)

    if pnl >= 0:
        return {"status": "非浮亏场景", "hint": "请用 make_profit_decision 处理"}

    loss_pct = abs(pnl)
    risk_profile = status.get("risk_profile", get_risk_profile("normal"))
    stop_policy = status.get("stop_policy", {})
    stop_triggers = status.get("stop_triggers", {})

    if stop_triggers.get("hard_stop_triggered"):
        level = "🔴 硬止损已触发"
        strategy = "立即止损"
        actions = [
            f"当前亏损{pnl:.2f}%已触及硬止损{stop_policy.get('hard_stop_pct')}%",
            "执行卖出或至少大幅降仓，不等待反弹",
            "停止补仓，重新进入观察池，等待大盘/赛道/重仓股三重修复",
        ]
    elif stop_triggers.get("initial_stop_triggered"):
        level = "🟠 初始止损已触发"
        strategy = "先减仓再复核"
        actions = [
            f"当前亏损{pnl:.2f}%已触及初始止损{stop_policy.get('initial_stop_pct')}%",
            "先减仓50%-100%，再复核下跌原因",
            "若大盘或重仓股趋势为下跌区间，直接按硬止损处理",
        ]

    # 被套深度分级（对应 skill 6.7.1）
    elif loss_pct < 3:
        level = "🟢 正常波动"
        strategy = "持有观察"
        actions = ["正常波动范围，但继续盯住初始止损线"]
    elif loss_pct < 5:
        level = "🟡 预警区"
        strategy = "收紧观察"
        actions = [
            "接近v6.0初始止损区，禁止补仓",
            "复核大盘总开关、赛道资金、前5大重仓股趋势",
            "若任一核心逻辑转弱，减仓50%",
        ]
    elif loss_pct < 8:
        level = "🟠 止损执行区"
        strategy = "减仓/止损优先"
        actions = [
            "已进入5-8%亏损控制带，优先把亏损锁在小范围",
            "若大盘不是🟢向上，立即止损或至少减仓70%",
            "仅当大盘🟢、重仓股共振🟢、技术重新强企稳时，才允许保留小仓观察",
        ]
    elif loss_pct < 15:
        level = "🔴 硬止损后风险区"
        strategy = "止损换仓"
        actions = [
            "亏损已超过v6.0硬止损上限，原则上不再解套、不摊薄",
            "卖出或保留极小观察仓，资金转入通过Step0-6的新标的或现金/债券",
            "复盘为何未在-5%~-8%执行，修正交易纪律",
        ]
    else:
        level = "⚫ 纪律失效区"
        strategy = "强制降风险"
        actions = [
            "亏损已显著超过新版硬止损，先救流动性，不再等待解套叙事",
            "分批卖出仅用于降低冲击，不以'等回本'为目标",
            "套回资金至少50%进入现金/债券/低波资产，剩余资金重新按v6.0高置信门槛筛选",
        ]

    # 被套诊断四问（对应 skill 6.7.2）
    diagnosis_questions = {
        "问题1": "下跌是系统性/行业性/个股性？（参考03脚本行业数据、大盘指数对比）",
        "问题2": "买入逻辑5项是否被证伪？（估值/基本面/政策/技术/行业）",
        "问题3": "前5大重仓股是否多数进入下跌区间？（参考04脚本v6.0趋势区间）",
        "问题4": "是否已触及初始/硬止损？若触及，先执行纪律再讨论后续机会",
    }

    # 禁忌
    taboos = [
        "❌ 躺平装死'反正不看就没亏'",
        "❌ 借钱/杠杆补仓",
        "❌ 连续补仓（越跌越买）",
        "❌ 卖其他盈利基金补仓被套基金",
        "❌ 幻想一次大涨解套（V型解套概率<20%）",
        "❌ 不分析原因就'坚信会涨回来'",
        "❌ 跟风加仓网友推荐的'抄底'",
        "❌ 被套越多仓位越重",
    ]

    return {
        "scenario": "浮亏",
        "pnl_pct": pnl,
        "loss_pct": round(loss_pct, 2),
        "depth_level": level,
        "strategy_name": strategy,
        "actions": actions,
        "risk_profile": risk_profile,
        "diagnosis_four_questions": diagnosis_questions,
        "taboos": taboos,
        "trigger_stop_loss_check": status["current_nav"] <= status["moving_stop"]["price"],
    }


def track_position(
    fund_code: str,
    buy_price: float,
    buy_date: str,
    position_amount: float = None,
    market_state: str = "normal",
) -> dict:
    """主入口：完整持仓跟踪"""
    print_banner(f"持仓跟踪分析 | {fund_code}")

    status = calc_position_status(fund_code, buy_price, buy_date, position_amount, market_state)
    if "error" in status:
        print(f"⚠️  {status['error']}")
        return status

    # 根据盈亏决策
    if status["pnl_pct"] >= 0:
        decision = make_profit_decision(status)
    else:
        decision = make_loss_decision(status)

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "position_status": status,
        "decision": decision,
    }

    # 打印
    print(f"\n【持仓基本信息】")
    print(f"  基金代码: {fund_code}")
    print(f"  买入日期: {buy_date}  持仓天数: {status['holding_days']}")
    print(f"  买入价: {buy_price}")
    print(f"  当前净值: {status['current_nav']}（{status['latest_date']}）")
    print(f"  累计收益: {status['pnl_pct']:+.2f}%")
    print(f"  v6.0风控: {status['risk_profile']['label']}")

    if position_amount:
        print(f"  持仓金额: {position_amount:,.0f} → 当前市值: {status['current_value']:,.0f}")
        print(f"  盈亏金额: {status['pnl_amount']:+,.0f}")

    print(f"\n【关键价位】")
    for label, price in status["price_levels"].items():
        distance = (price - status["current_nav"]) / status["current_nav"] * 100
        print(f"  {label}: {price}（距当前 {distance:+.2f}%）")

    ms = status["moving_stop"]
    print(f"\n【移动止损】")
    print(f"  当前止损位: {ms['price']}  描述: {ms['description']}")
    print(f"  距当前价: {ms['distance_pct']}%")

    d = result["decision"]
    print(f"\n【决策】 {d.get('decision', d.get('strategy_name', 'N/A'))}")
    if "depth_level" in d:
        print(f"  套牢深度: {d['depth_level']}")
    print(f"  场景: {d['scenario']}")
    print(f"  操作建议:")
    for a in d["actions"]:
        print(f"    · {a}")

    if "diagnosis_four_questions" in d:
        print(f"\n【被套诊断四问】")
        for k, v in d["diagnosis_four_questions"].items():
            print(f"  {k}: {v}")

    if "reminders" in d:
        print(f"\n【心态提醒】")
        for r in d["reminders"]:
            print(f"  {r}")

    if "taboos" in d:
        print(f"\n【解套禁忌】")
        for t in d["taboos"]:
            print(f"  {t}")

    save_path = save_result(result, f"tracking_{fund_code}", subdir="09_tracking")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


def interactive_mode():
    """交互式录入持仓"""
    print_banner("交互式持仓跟踪")
    fund_code = input("请输入基金代码: ").strip()
    buy_price = float(input("请输入买入价格: ").strip())
    buy_date = input("请输入买入日期（YYYY-MM-DD）: ").strip()
    amount_str = input("请输入买入金额（可留空）: ").strip()
    amount = float(amount_str) if amount_str else None
    market_state = input("请输入大盘状态（normal/neutral/downtrend，可留空）: ").strip() or "normal"
    return track_position(fund_code, buy_price, buy_date, amount, market_state)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        interactive_mode()
    elif len(sys.argv) >= 4:
        fund_code = sys.argv[1]
        buy_price = float(sys.argv[2])
        buy_date = sys.argv[3]
        amount = float(sys.argv[4]) if len(sys.argv) >= 5 else None
        market_state = sys.argv[5] if len(sys.argv) >= 6 else "normal"
        track_position(fund_code, buy_price, buy_date, amount, market_state)
    else:
        print("用法：")
        print("  python 09_position_tracking.py  # 交互式录入")
        print("  python 09_position_tracking.py <基金代码> <买入价> <买入日期> [金额] [normal|neutral|downtrend]")
        print("示例：python 09_position_tracking.py 001938 2.5 2025-10-15 100000 downtrend")
