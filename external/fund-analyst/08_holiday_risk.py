"""
08_holiday_risk.py —— 节假日风险评估（★v4.0新增，对应 skill 1.8 + 6.5）
========================================================================
核心功能：
  · 识别未来30天内的A股/港股/美股节假日
  · 计算距离下一节假日天数
  · 评估节前风险等级（6项信号）
  · 历史同期节前节后走势分析
  · 输出节假日调仓建议

用法：
    python 08_holiday_risk.py             # 评估当前节假日风险
    python 08_holiday_risk.py --history   # 附带历史数据分析
"""

import sys
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

from config import (
    get_logger, with_retry, with_cache, save_result, print_banner,
    MAJOR_HOLIDAYS, today_str, date_to_akshare
)

logger = get_logger(__name__)

try:
    import chinese_calendar as cc
    HAS_CN_CAL = True
except ImportError:
    HAS_CN_CAL = False
    logger.warning("chinese_calendar 未安装，节假日识别将使用内置规则")


# ============ 节假日识别 ============

def find_next_holiday(days_ahead: int = 60) -> dict:
    """找到未来N天内的下一个重大节假日"""
    today = datetime.now().date()
    
    if HAS_CN_CAL:
        # 使用chinese_calendar库精确识别
        next_holiday_date = None
        next_holiday_name = None
        holiday_start = None
        holiday_end = None

        for i in range(days_ahead):
            check_date = today + timedelta(days=i)
            if cc.is_holiday(check_date):
                try:
                    detail = cc.get_holiday_detail(check_date)
                    # detail格式: (True, "Holiday Name")
                    if detail[0] and detail[1]:
                        if next_holiday_date is None:
                            next_holiday_date = check_date
                            next_holiday_name = detail[1]
                            holiday_start = check_date
                        # 找假期结束日
                        if detail[1] == next_holiday_name:
                            holiday_end = check_date
                        else:
                            break
                except Exception:
                    continue

        if next_holiday_date:
            days_until = (next_holiday_date - today).days
            duration = (holiday_end - holiday_start).days + 1 if holiday_end else 1
            
            # 匹配风险等级
            risk_info = None
            for k, v in MAJOR_HOLIDAYS.items():
                if k in next_holiday_name or next_holiday_name in k:
                    risk_info = v
                    break
            if not risk_info:
                risk_info = {"duration": duration, "risk_level": "🟡 中"}

            return {
                "holiday_name": next_holiday_name,
                "start_date": str(holiday_start),
                "end_date": str(holiday_end),
                "days_until_start": days_until,
                "duration_days": duration,
                "risk_level": risk_info["risk_level"],
            }
    
    # Fallback：使用akshare的交易日历对比
    try:
        trade_cal = ak.tool_trade_date_hist_sina()
        trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"]).dt.date
        
        future_dates = []
        for i in range(1, days_ahead + 1):
            d = today + timedelta(days=i)
            # 工作日但非交易日 = 可能节假日
            if d.weekday() < 5 and d not in set(trade_cal["trade_date"]):
                future_dates.append(d)
        
        if future_dates:
            # 找连续的第一段
            groups = [[future_dates[0]]]
            for d in future_dates[1:]:
                if (d - groups[-1][-1]).days <= 3:
                    groups[-1].append(d)
                else:
                    break
            first_group = groups[0]
            return {
                "holiday_name": "未知节假日（规则推断）",
                "start_date": str(first_group[0]),
                "end_date": str(first_group[-1]),
                "days_until_start": (first_group[0] - today).days,
                "duration_days": len(first_group),
                "risk_level": "🟡 中",
            }
    except Exception as e:
        logger.error(f"交易日历查询失败: {e}")
    
    return {"error": f"未找到未来{days_ahead}天内的节假日", "days_until_start": None}


# ============ 节前6项风险信号 ============

@with_retry()
@with_cache(cache_type="daily")
def check_pre_holiday_risks() -> dict:
    """
    对应 skill 6.5.3 节前6项风险信号检查：
    1. 两融余额连续3日下降
    2. 北向资金净流出>30亿连续3日
    3. 成交额萎缩>20%
    4. VIX>20
    5. 重大政策/财报窗口
    6. 地缘热点事件发酵
    """
    signals = {
        "两融余额连续3日下降":  {"triggered": None, "value": None, "desc": ""},
        "北向资金连续3日净流出>30亿": {"triggered": None, "value": None, "desc": ""},
        "成交额萎缩>20%":       {"triggered": None, "value": None, "desc": ""},
        "VIX>20":               {"triggered": None, "value": None, "desc": ""},
        "重大政策/财报窗口":     {"triggered": None, "value": None, "desc": "需人工判断"},
        "地缘热点事件发酵":      {"triggered": None, "value": None, "desc": "需人工判断"},
    }

    # 1. 两融余额
    try:
        margin_df = ak.stock_margin_sse()
        if not margin_df.empty:
            margin_df = margin_df.tail(5)
            balance_col = None
            for col in ['融资融券余额', '融资余额']:
                if col in margin_df.columns:
                    balance_col = col
                    break
            if balance_col:
                balances = margin_df[balance_col].values
                # 连续3日下降
                decline_3d = all(balances[i] > balances[i+1] for i in range(len(balances)-3, len(balances)-1))
                signals["两融余额连续3日下降"]["triggered"] = decline_3d
                signals["两融余额连续3日下降"]["value"] = f"近5日 {balances[0]/1e8:.0f}→{balances[-1]/1e8:.0f}亿"
    except Exception as e:
        signals["两融余额连续3日下降"]["desc"] = f"数据获取失败: {e}"

    # 2. 北向资金
    try:
        hgt_df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if not hgt_df.empty:
            hgt_df = hgt_df.tail(3)
            net_flow_col = "当日成交净买额" if "当日成交净买额" in hgt_df.columns else None
            if net_flow_col:
                flows = hgt_df[net_flow_col].values
                all_outflow_30 = all(f < -30 for f in flows)
                signals["北向资金连续3日净流出>30亿"]["triggered"] = all_outflow_30
                signals["北向资金连续3日净流出>30亿"]["value"] = f"近3日 {[f'{f:.1f}' for f in flows]}亿"
    except Exception as e:
        signals["北向资金连续3日净流出>30亿"]["desc"] = f"数据获取失败: {e}"

    # 3. 成交额萎缩
    try:
        # 使用沪深300判断大盘成交
        index_df = ak.stock_zh_index_daily(symbol="sh000300")
        if not index_df.empty and "volume" in index_df.columns:
            index_df = index_df.tail(30)
            recent_5_avg = index_df["volume"].tail(5).mean()
            prev_20_avg = index_df["volume"].head(20).mean()
            shrink_pct = (recent_5_avg - prev_20_avg) / prev_20_avg * 100
            signals["成交额萎缩>20%"]["triggered"] = shrink_pct < -20
            signals["成交额萎缩>20%"]["value"] = f"近5日均量vs前20日: {shrink_pct:.1f}%"
    except Exception as e:
        signals["成交额萎缩>20%"]["desc"] = f"数据获取失败: {e}"

    # 4. VIX
    try:
        vix_df = ak.index_us_stock_sina(symbol=".VIX")
        if not vix_df.empty:
            latest_vix = float(vix_df["close"].iloc[-1])
            signals["VIX>20"]["triggered"] = latest_vix > 20
            signals["VIX>20"]["value"] = f"当前VIX={latest_vix:.2f}"
    except Exception as e:
        signals["VIX>20"]["desc"] = f"数据获取失败: {e}"

    # 5-6. 政策/地缘 - 需要人工判断或AI判断
    signals["重大政策/财报窗口"]["triggered"] = False
    signals["地缘热点事件发酵"]["triggered"] = False

    # 统计
    triggered_count = sum(1 for s in signals.values() if s["triggered"] is True)
    known_count = sum(1 for s in signals.values() if s["triggered"] is not None)

    # 风险等级判定（对应 skill 6.5.3）
    if triggered_count >= 6:
        level = "🔴 风险极高"
        recommendation = "权益仓位降至50%以下"
    elif triggered_count >= 4:
        level = "🟠 风险较高"
        recommendation = "减仓20-30%，加配防御型资产"
    elif triggered_count >= 2:
        level = "🟡 风险中等"
        recommendation = "减仓10-20%"
    else:
        level = "🟢 风险较低"
        recommendation = "常规持仓，节前3日停止新开重仓"

    return {
        "signals": signals,
        "triggered_count": triggered_count,
        "known_count": known_count,
        "total_signals": 6,
        "risk_level": level,
        "recommendation": recommendation,
    }


# ============ 历史节前后走势分析 ============

@with_retry()
@with_cache(cache_type="static")
def get_holiday_history(years: int = 10) -> dict:
    """
    获取过去N年主要指数节前10日/节后10日涨跌幅分布
    基于沪深300近N年数据
    """
    try:
        # 获取沪深300长周期数据
        end_date = date_to_akshare(today_str())
        start_year = datetime.now().year - years
        start_date = f"{start_year}0101"
        
        df = ak.index_zh_a_hist(symbol="000300", period="daily", 
                                 start_date=start_date, end_date=end_date)
        if df.empty:
            return {"error": "历史数据获取失败"}

        df["日期"] = pd.to_datetime(df["日期"])
        df = df.sort_values("日期").reset_index(drop=True)

        # 简化版：仅基于交易日间隙识别长假
        df["prev_date"] = df["日期"].shift(1)
        df["gap_days"] = (df["日期"] - df["prev_date"]).dt.days

        # 间隙>=4天 = 长假
        long_holidays = df[df["gap_days"] >= 4].copy()
        
        results = []
        for _, row in long_holidays.iterrows():
            gap_date = row["日期"]
            # 找节前10日/节后10日
            try:
                pre_df = df[df["日期"] < gap_date].tail(10)
                post_df = df[df["日期"] >= gap_date].head(10)

                if len(pre_df) >= 2 and len(post_df) >= 2:
                    pre_ret = (pre_df["收盘"].iloc[-1] - pre_df["收盘"].iloc[0]) / pre_df["收盘"].iloc[0] * 100
                    post_ret = (post_df["收盘"].iloc[-1] - post_df["收盘"].iloc[0]) / post_df["收盘"].iloc[0] * 100
                    results.append({
                        "resume_date": str(gap_date.date()),
                        "gap_days": int(row["gap_days"]),
                        "pre_10d_return_pct": round(float(pre_ret), 2),
                        "post_10d_return_pct": round(float(post_ret), 2),
                    })
            except Exception:
                continue

        if results:
            pre_returns = [r["pre_10d_return_pct"] for r in results]
            post_returns = [r["post_10d_return_pct"] for r in results]
            return {
                "sample_count": len(results),
                "pre_holiday_stats": {
                    "avg_return_pct": round(sum(pre_returns) / len(pre_returns), 2),
                    "max_return_pct": round(max(pre_returns), 2),
                    "min_return_pct": round(min(pre_returns), 2),
                    "up_count": sum(1 for r in pre_returns if r > 0),
                    "down_count": sum(1 for r in pre_returns if r < 0),
                },
                "post_holiday_stats": {
                    "avg_return_pct": round(sum(post_returns) / len(post_returns), 2),
                    "max_return_pct": round(max(post_returns), 2),
                    "min_return_pct": round(min(post_returns), 2),
                    "up_count": sum(1 for r in post_returns if r > 0),
                    "down_count": sum(1 for r in post_returns if r < 0),
                },
                "history_samples": results[-10:],  # 最近10次
            }
    except Exception as e:
        logger.error(f"历史节假日走势分析失败: {e}")
        return {"error": str(e)}

    return {"error": "无样本数据"}


def generate_holiday_advice(next_holiday: dict, risks: dict, history: dict = None) -> dict:
    """综合生成节假日调仓建议"""
    advice = {
        "下一个节假日": next_holiday.get("holiday_name", "未知"),
        "距今天数": next_holiday.get("days_until_start"),
        "休市天数": next_holiday.get("duration_days"),
        "节假日风险等级": next_holiday.get("risk_level", "🟡 中"),
        "节前风险信号": risks["risk_level"],
        "触发信号数": f"{risks['triggered_count']}/{risks['known_count']}",
    }

    # 对应 skill 6.5.4 节假日调仓硬性规则
    days_until = next_holiday.get("days_until_start", 999) or 999
    actions = []

    if days_until <= 3:
        actions.append("🔴 节前3日内：禁止新开重仓，单笔新买入≤计划仓位20%")
    if days_until <= 7:
        actions.append("🟠 节前7日内：评估高位高估值基金，考虑止盈30%+")
        actions.append("🟠 QDII必须特别评估：检查VIX/美股位置")
    if days_until <= 10:
        actions.append("🟡 节前10日内：开始减仓预案，保留≥15%现金子弹")

    if "极高" in next_holiday.get("risk_level", ""):
        actions.append("🔴 【春节/国庆】权益仓位建议降至60-70%以下")

    if risks["triggered_count"] >= 4:
        actions.append("🔴 节前风险信号密集：减仓20-30%，加配黄金/红利")

    advice["操作建议"] = actions
    advice["节后建议"] = [
        "节后首日：观察不操作，等待1完整交易日",
        "节后第2-3日：视情况补仓或减仓",
        "若节中海外有黑天鹅：节前预设的卖出计划执行，不在情绪中决策",
    ]

    if history and "pre_holiday_stats" in history:
        pre = history["pre_holiday_stats"]
        post = history["post_holiday_stats"]
        advice["历史参考"] = {
            "样本数": history["sample_count"],
            "节前10日平均涨跌": f"{pre['avg_return_pct']}%（最大{pre['max_return_pct']}%/最小{pre['min_return_pct']}%）",
            "节前下跌概率": f"{pre['down_count']}/{history['sample_count']}",
            "节后10日平均涨跌": f"{post['avg_return_pct']}%（最大{post['max_return_pct']}%/最小{post['min_return_pct']}%）",
            "节后下跌概率": f"{post['down_count']}/{history['sample_count']}",
        }

    return advice


def analyze_holiday_risk(with_history: bool = False) -> dict:
    """主入口"""
    print_banner("节假日风险评估")

    next_holiday = find_next_holiday(days_ahead=90)
    risks = check_pre_holiday_risks()
    history = get_holiday_history(years=10) if with_history else None

    result = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "next_holiday": next_holiday,
        "pre_holiday_risks": risks,
        "historical_performance": history,
        "advice": generate_holiday_advice(next_holiday, risks, history),
    }

    # 打印
    nh = result["next_holiday"]
    print(f"\n【下一个节假日】")
    if "error" in nh:
        print(f"  {nh['error']}")
    else:
        print(f"  名称: {nh.get('holiday_name')}")
        print(f"  开始: {nh.get('start_date')}  结束: {nh.get('end_date')}")
        print(f"  距今: {nh.get('days_until_start')}天  休市: {nh.get('duration_days')}天")
        print(f"  风险等级: {nh.get('risk_level')}")

    print(f"\n【节前6项风险信号】")
    print(f"  触发: {risks['triggered_count']}/{risks['known_count']} (满分6)")
    print(f"  评级: {risks['risk_level']}")
    for name, s in risks["signals"].items():
        if s["triggered"] is True:
            mark = "🔴"
        elif s["triggered"] is False:
            mark = "🟢"
        else:
            mark = "⬜"
        print(f"  {mark} {name}: {s.get('value') or s.get('desc')}")
    print(f"\n  建议: {risks['recommendation']}")

    if history and "pre_holiday_stats" in history:
        h = history
        print(f"\n【历史节假日表现（近{h['sample_count']}次长假）】")
        print(f"  节前10日: 平均{h['pre_holiday_stats']['avg_return_pct']}%  下跌{h['pre_holiday_stats']['down_count']}次")
        print(f"  节后10日: 平均{h['post_holiday_stats']['avg_return_pct']}%  下跌{h['post_holiday_stats']['down_count']}次")

    print(f"\n【综合操作建议】")
    for action in result["advice"].get("操作建议", []):
        print(f"  {action}")

    save_path = save_result(result, "holiday_risk", subdir="08_holiday")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    with_hist = "--history" in sys.argv
    analyze_holiday_risk(with_history=with_hist)
