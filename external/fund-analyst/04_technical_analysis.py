"""
04_technical_analysis.py —— 量价技术分析（对应 v4.0 skill 1.4 + 3.2 + 6.1第三步）
================================================================================
获取字段：
  · MA5 / MA10 / MA20 / MA60 均线
  · MACD
  · RSI(14)
  · 布林带
  · 成交量趋势
  · 量价配合度

  ★ v4.0 核心：输出 "8项技术企稳信号" 检查清单（6.1第三步强化要点）

用法：
    python 04_technical_analysis.py <股票或指数代码>
    python 04_technical_analysis.py 600519       # A股
    python 04_technical_analysis.py 000300       # 指数（沪深300）
"""

import sys
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime

from config import (
    get_logger, with_retry, with_cache, save_result, print_banner,
    n_days_ago, today_str, date_to_akshare
)

logger = get_logger(__name__)


@with_retry()
@with_cache(cache_type="daily")
def get_stock_kline(code: str, days: int = 250) -> pd.DataFrame:
    """获取个股日K线"""
    try:
        start_date = date_to_akshare(n_days_ago(days + 20))
        end_date = date_to_akshare(today_str())
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"
        )
        return df
    except Exception as e:
        logger.warning(f"个股K线[{code}]获取失败，尝试指数: {e}")
        try:
            df = ak.stock_zh_index_daily(symbol=f"sh{code}" if code.startswith("6") else f"sz{code}")
            return df.tail(days + 20)
        except Exception as e2:
            logger.error(f"指数K线也失败: {e2}")
            return pd.DataFrame()


def normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    """统一K线列名"""
    col_map = {
        "date": "日期", "open": "开盘", "close": "收盘",
        "high": "最高", "low": "最低", "volume": "成交量"
    }
    # akshare stock_zh_a_hist 已经是中文列名
    if "日期" in df.columns:
        df = df.copy()
        df["日期"] = pd.to_datetime(df["日期"])
    else:
        # stock_zh_index_daily 是英文
        df = df.rename(columns=col_map)
        df["日期"] = pd.to_datetime(df["日期"])
    return df.sort_values("日期").reset_index(drop=True)


def calc_ma(df: pd.DataFrame, windows=(5, 10, 20, 60)) -> pd.DataFrame:
    """计算均线"""
    for w in windows:
        df[f"MA{w}"] = df["收盘"].rolling(window=w).mean()
    return df


def calc_macd(df: pd.DataFrame) -> pd.DataFrame:
    """MACD指标"""
    ema12 = df["收盘"].ewm(span=12, adjust=False).mean()
    ema26 = df["收盘"].ewm(span=26, adjust=False).mean()
    df["MACD_DIF"] = ema12 - ema26
    df["MACD_DEA"] = df["MACD_DIF"].ewm(span=9, adjust=False).mean()
    df["MACD_BAR"] = 2 * (df["MACD_DIF"] - df["MACD_DEA"])
    return df


def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """RSI指标"""
    delta = df["收盘"].diff()
    gain = (delta.clip(lower=0)).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss
    df[f"RSI{period}"] = 100 - (100 / (1 + rs))
    return df


def calc_bollinger(df: pd.DataFrame, window: int = 20, k: float = 2.0) -> pd.DataFrame:
    """布林带"""
    df["BOLL_MID"] = df["收盘"].rolling(window=window).mean()
    std = df["收盘"].rolling(window=window).std()
    df["BOLL_UP"] = df["BOLL_MID"] + k * std
    df["BOLL_DOWN"] = df["BOLL_MID"] - k * std
    return df


def score_technical(df: pd.DataFrame) -> dict:
    """
    对应 v4.0 skill 3.2 量价趋势验证评分（0-25分）
    """
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    score = 0
    details = {}

    # 1. MA20 vs MA60 (5分)
    if pd.notna(last.get("MA20")) and pd.notna(last.get("MA60")):
        if last["MA20"] > last["MA60"]:
            score += 5
            details["ma20_vs_ma60"] = "多头排列 +5"
        elif abs(last["MA20"] - last["MA60"]) / last["MA60"] < 0.01:
            score += 2
            details["ma20_vs_ma60"] = "纠缠 +2"
        else:
            details["ma20_vs_ma60"] = "空头 +0"

    # 2. MACD (5分)
    if pd.notna(last.get("MACD_BAR")) and pd.notna(prev.get("MACD_BAR")):
        if last["MACD_DIF"] > last["MACD_DEA"] and last["MACD_BAR"] > prev["MACD_BAR"]:
            score += 5
            details["macd"] = "金叉且放大 +5"
        elif abs(last["MACD_DIF"]) < 0.1:
            score += 2
            details["macd"] = "零轴附近 +2"
        else:
            details["macd"] = "空头或缩量 +0"

    # 3. RSI (5分)
    rsi_val = last.get("RSI14")
    if pd.notna(rsi_val):
        if 50 <= rsi_val <= 70:
            score += 5
            details["rsi"] = f"健康区 ({rsi_val:.1f}) +5"
        elif 30 <= rsi_val < 50 or 70 < rsi_val <= 80:
            score += 3
            details["rsi"] = f"中性 ({rsi_val:.1f}) +3"
        else:
            score += 1
            details["rsi"] = f"超买/超卖 ({rsi_val:.1f}) +1"

    # 4. 成交量趋势（5分）
    if "成交量" in df.columns:
        recent_30 = df["成交量"].tail(30).mean()
        recent_120 = df["成交量"].tail(120).mean() if len(df) >= 120 else recent_30
        ratio = recent_30 / recent_120 if recent_120 > 0 else 1
        if ratio > 1.1:
            score += 5
            details["volume_trend"] = f"放量 (比值{ratio:.2f}) +5"
        elif 0.9 <= ratio <= 1.1:
            score += 2
            details["volume_trend"] = f"持平 (比值{ratio:.2f}) +2"
        else:
            details["volume_trend"] = f"缩量 (比值{ratio:.2f}) +0"

    # 5. 量价配合（5分）
    if len(df) >= 5 and "成交量" in df.columns:
        recent_5 = df.tail(5)
        price_change = (recent_5["收盘"].iloc[-1] - recent_5["收盘"].iloc[0]) / recent_5["收盘"].iloc[0]
        volume_change = (recent_5["成交量"].mean() - df["成交量"].tail(10).head(5).mean()) / df["成交量"].tail(10).head(5).mean() if df["成交量"].tail(10).head(5).mean() > 0 else 0
        if price_change > 0 and volume_change > 0:
            score += 5
            details["price_volume"] = "价涨量增 +5"
        elif price_change < 0 and volume_change > 0.2:
            details["price_volume"] = "价跌量增（恐慌）+0"
        else:
            score += 2
            details["price_volume"] = "中性 +2"

    return {"total_score": score, "max_score": 25, "details": details}


def check_stabilization_signals(df: pd.DataFrame) -> dict:
    """
    ★ v4.0 核心功能：8项技术企稳信号检查（对应 6.1 第三步）
    权重合计100%，用于"低估≠立即买入"精确判定
    """
    signals = {
        "止跌信号":      {"weight": 15, "triggered": False, "desc": ""},
        "缩量企稳":      {"weight": 10, "triggered": False, "desc": ""},
        "MA5上穿MA10":   {"weight": 15, "triggered": False, "desc": ""},
        "MA20拐头":      {"weight": 15, "triggered": False, "desc": ""},
        "MACD底背离/金叉": {"weight": 15, "triggered": False, "desc": ""},
        "RSI脱离超卖":   {"weight": 10, "triggered": False, "desc": ""},
        "K线企稳形态":   {"weight": 10, "triggered": False, "desc": ""},
        "量价配合":      {"weight": 10, "triggered": False, "desc": ""},
    }

    if len(df) < 30:
        return {"error": "K线数据不足30日", "signals": signals}

    last3 = df.tail(3)
    last5 = df.tail(5)
    last10 = df.tail(10)

    # 1. 止跌信号：连续3日不创新低 或 单日跌幅<=1%
    recent_5d_lows = df["最低"].tail(5).values if "最低" in df.columns else df["收盘"].tail(5).values
    if len(recent_5d_lows) >= 3:
        # 连续3日不创新低
        no_new_low = all(recent_5d_lows[-i-1] <= recent_5d_lows[-i-2] for i in range(2)) == False
        # 最新一日跌幅
        last_change = (df["收盘"].iloc[-1] - df["收盘"].iloc[-2]) / df["收盘"].iloc[-2]
        if no_new_low or abs(last_change) <= 0.01:
            signals["止跌信号"]["triggered"] = True
            signals["止跌信号"]["desc"] = f"最新跌幅{last_change*100:.2f}%"

    # 2. 缩量企稳：成交量较近5日均量萎缩20%以上
    if "成交量" in df.columns:
        vol_5d_avg = df["成交量"].tail(5).mean()
        vol_today = df["成交量"].iloc[-1]
        if vol_today < vol_5d_avg * 0.8:
            signals["缩量企稳"]["triggered"] = True
            signals["缩量企稳"]["desc"] = f"缩量至5日均量的{vol_today/vol_5d_avg*100:.0f}%"

    # 3. MA5上穿MA10
    if pd.notna(df["MA5"].iloc[-1]) and pd.notna(df["MA10"].iloc[-1]):
        if df["MA5"].iloc[-1] > df["MA10"].iloc[-1] and df["MA5"].iloc[-3] <= df["MA10"].iloc[-3]:
            signals["MA5上穿MA10"]["triggered"] = True
            signals["MA5上穿MA10"]["desc"] = "MA5金叉MA10"

    # 4. MA20拐头（从向下到走平或向上）
    if pd.notna(df["MA20"].iloc[-1]) and pd.notna(df["MA20"].iloc[-5]):
        ma20_now = df["MA20"].iloc[-1]
        ma20_3d_ago = df["MA20"].iloc[-3]
        ma20_5d_ago = df["MA20"].iloc[-5]
        # 5日前→3日前下降，3日前→现在走平或上升
        if ma20_5d_ago > ma20_3d_ago and ma20_now >= ma20_3d_ago:
            signals["MA20拐头"]["triggered"] = True
            signals["MA20拐头"]["desc"] = "MA20由下转平/上"

    # 5. MACD底背离或金叉
    if pd.notna(df["MACD_DIF"].iloc[-1]):
        # 零轴下方金叉
        if df["MACD_DIF"].iloc[-1] > df["MACD_DEA"].iloc[-1] and df["MACD_DIF"].iloc[-3] <= df["MACD_DEA"].iloc[-3]:
            signals["MACD底背离/金叉"]["triggered"] = True
            signals["MACD底背离/金叉"]["desc"] = "MACD金叉"
        # 底背离：价格创新低但MACD未创新低（简化版）
        elif len(df) >= 20:
            price_low = df["收盘"].tail(20).min()
            macd_low = df["MACD_DIF"].tail(20).min()
            if df["收盘"].iloc[-1] <= price_low * 1.02 and df["MACD_DIF"].iloc[-1] > macd_low * 1.1:
                signals["MACD底背离/金叉"]["triggered"] = True
                signals["MACD底背离/金叉"]["desc"] = "MACD底背离"

    # 6. RSI脱离超卖
    rsi = df["RSI14"].iloc[-1]
    rsi_3d_ago = df["RSI14"].iloc[-4] if len(df) >= 4 else rsi
    if pd.notna(rsi) and pd.notna(rsi_3d_ago):
        if rsi_3d_ago < 30 and rsi > 40:
            signals["RSI脱离超卖"]["triggered"] = True
            signals["RSI脱离超卖"]["desc"] = f"RSI从{rsi_3d_ago:.1f}回升至{rsi:.1f}"

    # 7. K线企稳形态（简化：锤头线）
    if "最高" in df.columns and "最低" in df.columns and "开盘" in df.columns:
        last_row = df.iloc[-1]
        body = abs(last_row["收盘"] - last_row["开盘"])
        lower_shadow = min(last_row["开盘"], last_row["收盘"]) - last_row["最低"]
        upper_shadow = last_row["最高"] - max(last_row["开盘"], last_row["收盘"])
        # 锤头线：下影线>=2倍实体 且 上影线<=实体
        if body > 0 and lower_shadow >= 2 * body and upper_shadow <= body:
            signals["K线企稳形态"]["triggered"] = True
            signals["K线企稳形态"]["desc"] = "锤头线"

    # 8. 量价配合（止跌后首次放量）
    if "成交量" in df.columns and len(df) >= 10:
        vol_avg_5 = df["成交量"].tail(6).head(5).mean()
        vol_today = df["成交量"].iloc[-1]
        price_change_today = (df["收盘"].iloc[-1] - df["收盘"].iloc[-2]) / df["收盘"].iloc[-2]
        if vol_today >= vol_avg_5 * 1.2 and price_change_today > 0:
            signals["量价配合"]["triggered"] = True
            signals["量价配合"]["desc"] = f"放量上涨，量比{vol_today/vol_avg_5:.2f}"

    # 汇总
    triggered_count = sum(1 for s in signals.values() if s["triggered"])
    weighted_pct = sum(s["weight"] for s in signals.values() if s["triggered"])

    # 确认等级（对应 skill 6.1 第三步表格）
    if triggered_count >= 6 or weighted_pct >= 70:
        level = "🟢 强企稳"
        recommendation = "可执行第一笔试探仓（30%）"
    elif triggered_count >= 4 or weighted_pct >= 50:
        level = "🟡 弱企稳"
        recommendation = "可小仓位试探（≤10%）"
    elif triggered_count >= 2 or weighted_pct >= 30:
        level = "🟠 观察等待"
        recommendation = "继续观察，不出手"
    else:
        level = "🔴 禁止买入"
        recommendation = "仍在下跌通道，切勿接飞刀"

    return {
        "signals": signals,
        "triggered_count": triggered_count,
        "total_signals": 8,
        "weighted_pct": weighted_pct,
        "confirmation_level": level,
        "recommendation": recommendation,
    }


def analyze_trend_zone(df: pd.DataFrame) -> dict:
    """
    v6.0：判断股票/指数当前处于上涨、震荡还是下跌区间。

    基金净值与重仓股强相关；买入基金前必须知道重仓股是在顺风区还是逆风区。
    """
    if len(df) < 60:
        return {
            "trend_zone": "⚪ 数据不足",
            "trend_state": "unknown",
            "score": 0,
            "action": "数据不足，不能作为买入依据",
        }

    close = df["收盘"].astype(float)
    current = float(close.iloc[-1])
    ma20 = float(df["MA20"].iloc[-1]) if pd.notna(df["MA20"].iloc[-1]) else None
    ma60 = float(df["MA60"].iloc[-1]) if pd.notna(df["MA60"].iloc[-1]) else None
    ma20_10d_ago = float(df["MA20"].iloc[-11]) if len(df) >= 61 and pd.notna(df["MA20"].iloc[-11]) else ma20
    ma60_20d_ago = float(df["MA60"].iloc[-21]) if len(df) >= 81 and pd.notna(df["MA60"].iloc[-21]) else ma60

    ret_20d = (current / float(close.iloc[-21]) - 1) * 100 if len(df) >= 21 else None
    ret_60d = (current / float(close.iloc[-61]) - 1) * 100 if len(df) >= 61 else None
    high_60d = float(close.tail(60).max())
    drawdown_60d = (current / high_60d - 1) * 100 if high_60d > 0 else None

    ma20_slope = (ma20 / ma20_10d_ago - 1) * 100 if ma20 and ma20_10d_ago else None
    ma60_slope = (ma60 / ma60_20d_ago - 1) * 100 if ma60 and ma60_20d_ago else None

    score = 0
    flags = {
        "price_above_ma60": bool(ma60 and current > ma60),
        "ma20_above_ma60": bool(ma20 and ma60 and ma20 > ma60),
        "ma60_up": bool(ma60_slope is not None and ma60_slope > 0),
        "ret_20d_positive": bool(ret_20d is not None and ret_20d > 0),
        "ret_60d_positive": bool(ret_60d is not None and ret_60d > 0),
        "drawdown_controlled": bool(drawdown_60d is not None and drawdown_60d > -8),
    }

    score += 25 if flags["price_above_ma60"] else 0
    score += 20 if flags["ma20_above_ma60"] else 0
    score += 20 if flags["ma60_up"] else 0
    score += 15 if flags["ret_20d_positive"] else 0
    score += 10 if flags["ret_60d_positive"] else 0
    score += 10 if flags["drawdown_controlled"] else 0

    down_flags = {
        "price_below_ma60": bool(ma60 and current < ma60),
        "ma20_below_ma60": bool(ma20 and ma60 and ma20 < ma60),
        "ma60_down": bool(ma60_slope is not None and ma60_slope < -1),
        "ret_20d_weak": bool(ret_20d is not None and ret_20d < -5),
        "ret_60d_weak": bool(ret_60d is not None and ret_60d < -8),
    }
    down_count = sum(down_flags.values())

    if score >= 70 and down_count <= 1:
        trend_zone = "🟢 上涨区间"
        trend_state = "uptrend"
        action = "可作为基金买入的正向共振信号"
    elif score >= 45 and down_count <= 3:
        trend_zone = "🟡 震荡区间"
        trend_state = "neutral"
        action = "只能小仓或等待突破，不能重仓"
    else:
        trend_zone = "🔴 下跌区间"
        trend_state = "downtrend"
        action = "基金买入前置否决；已持仓应收紧止损或减仓"

    return {
        "trend_zone": trend_zone,
        "trend_state": trend_state,
        "score": score,
        "current_price": round(current, 4),
        "ma20": round(ma20, 4) if ma20 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "ma20_slope_10d_pct": round(ma20_slope, 2) if ma20_slope is not None else None,
        "ma60_slope_20d_pct": round(ma60_slope, 2) if ma60_slope is not None else None,
        "return_20d_pct": round(ret_20d, 2) if ret_20d is not None else None,
        "return_60d_pct": round(ret_60d, 2) if ret_60d is not None else None,
        "drawdown_60d_pct": round(drawdown_60d, 2) if drawdown_60d is not None else None,
        "positive_flags": flags,
        "down_flags": down_flags,
        "down_flag_count": down_count,
        "action": action,
    }


def position_in_range(df: pd.DataFrame) -> dict:
    """基金/股票净值位置诊断（对应 skill 6.1 第一步）"""
    if len(df) < 250:
        df = df.copy()
    else:
        df = df.tail(250).copy()

    current = float(df["收盘"].iloc[-1])
    high_1y = float(df["收盘"].max())
    low_1y = float(df["收盘"].min())

    pct_from_high = (current - high_1y) / high_1y * 100
    pct_from_low = (current - low_1y) / low_1y * 100

    # 位置分类
    if pct_from_high >= -5:
        location = "高位区（距高点<5%）"
    elif pct_from_high >= -15:
        location = "中位区（距高点5-15%）"
    elif pct_from_high >= -25:
        location = "回调区（距高点15-25%）"
    else:
        location = "深跌区（距高点>25%）"

    # 近期涨跌幅
    ret_5d = (current - float(df["收盘"].iloc[-6])) / float(df["收盘"].iloc[-6]) * 100 if len(df) >= 6 else None
    ret_20d = (current - float(df["收盘"].iloc[-21])) / float(df["收盘"].iloc[-21]) * 100 if len(df) >= 21 else None
    ret_60d = (current - float(df["收盘"].iloc[-61])) / float(df["收盘"].iloc[-61]) * 100 if len(df) >= 61 else None

    return {
        "current_price": current,
        "high_1y": high_1y,
        "low_1y": low_1y,
        "pct_from_high": round(pct_from_high, 2),
        "pct_from_low": round(pct_from_low, 2),
        "location": location,
        "return_5d_pct": round(ret_5d, 2) if ret_5d is not None else None,
        "return_20d_pct": round(ret_20d, 2) if ret_20d is not None else None,
        "return_60d_pct": round(ret_60d, 2) if ret_60d is not None else None,
    }


def analyze_technical(code: str) -> dict:
    """主入口"""
    print_banner(f"量价技术分析 | {code}")

    df = get_stock_kline(code, days=250)
    if df.empty:
        print("⚠️  未获取到K线数据")
        return {"code": code, "error": "K线获取失败"}

    df = normalize_kline(df)
    df = calc_ma(df)
    df = calc_macd(df)
    df = calc_rsi(df)
    df = calc_bollinger(df)

    result = {
        "code": code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_points": len(df),
        "position_diagnosis": position_in_range(df),
        "trend_zone": analyze_trend_zone(df),
        "technical_score": score_technical(df),
        "stabilization_signals": check_stabilization_signals(df),
    }

    # 打印
    pos = result["position_diagnosis"]
    print(f"\n【位置诊断】当前价: {pos['current_price']}")
    print(f"  近1年最高: {pos['high_1y']}  距高点: {pos['pct_from_high']}%")
    print(f"  近1年最低: {pos['low_1y']}  距低点: +{pos['pct_from_low']}%")
    print(f"  位置判定: {pos['location']}")
    print(f"  近5日: {pos['return_5d_pct']}%  近20日: {pos['return_20d_pct']}%  近60日: {pos['return_60d_pct']}%")

    tech = result["technical_score"]
    print(f"\n【技术评分】{tech['total_score']}/25")
    for k, v in tech["details"].items():
        print(f"  · {k}: {v}")

    trend = result["trend_zone"]
    print(f"\n【v6.0 趋势区间】{trend['trend_zone']}  得分 {trend['score']}/100")
    print(f"  MA20: {trend.get('ma20')}  MA60: {trend.get('ma60')}  MA60斜率: {trend.get('ma60_slope_20d_pct')}%")
    print(f"  20日: {trend.get('return_20d_pct')}%  60日: {trend.get('return_60d_pct')}%  60日回撤: {trend.get('drawdown_60d_pct')}%")
    print(f"  动作: {trend['action']}")

    stab = result["stabilization_signals"]
    if "error" not in stab:
        print(f"\n【v4.0 八项企稳信号】触发 {stab['triggered_count']}/8  加权 {stab['weighted_pct']}%")
        print(f"  确认等级: {stab['confirmation_level']}")
        print(f"  建议: {stab['recommendation']}")
        for name, s in stab["signals"].items():
            mark = "✅" if s["triggered"] else "⬜"
            print(f"  {mark} {name}: {s['desc']}")

    save_path = save_result(result, f"technical_{code}", subdir="04_technical")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 04_technical_analysis.py <股票/指数代码>")
        print("示例: python 04_technical_analysis.py 600519")
        sys.exit(1)

    analyze_technical(sys.argv[1])
