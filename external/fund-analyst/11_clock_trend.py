"""
11_clock_trend.py —— 五时钟趋势方向判定（★v5.0新增，对应 skill Step 6.1bis.1-6.1bis.2）
==========================================================================================
吸收 20 年实战股民「价格趋势五时钟方向理论」：
  · 1点钟方向（45-60°陡峭上涨）：极度看好/疯狂 → 高位追涨风险大
  · 2点钟方向（25-45°稳健上涨）：认可度高/乐观 → ★理想买入区
  · 3点钟方向（-10°到+10°）：横盘震荡/分歧 → 等待方向
  · 4点钟方向（-25°到-10°）：缓慢下跌/悲观 → 不接飞刀
  · 5-6点钟方向（-45°以下）：暴跌/极度悲观 → 严禁买入

核心原则："多数时候我们赚的都是趋势的钱"

用法：
    # 判定单只股票/指数/基金的时钟方向
    python 11_clock_trend.py <代码> [类型]
    
    示例：
    python 11_clock_trend.py 600519           # 默认A股
    python 11_clock_trend.py 000300 index     # 指数
    python 11_clock_trend.py 001938 fund      # 基金
"""

import sys
import math
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime

from config import (
    get_logger, with_retry, with_cache, save_result, print_banner,
    n_days_ago, today_str, date_to_akshare
)

logger = get_logger(__name__)


# ============ 数据获取 ============

@with_retry()
@with_cache(cache_type="daily")
def get_price_series(code: str, asset_type: str = "stock", days: int = 250) -> pd.DataFrame:
    """获取价格时间序列（适配股票/指数/基金）"""
    try:
        if asset_type == "fund":
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df.empty:
                return pd.DataFrame()
            df["净值日期"] = pd.to_datetime(df["净值日期"])
            df = df.sort_values("净值日期").reset_index(drop=True).tail(days + 20)
            return df.rename(columns={"净值日期": "日期", "单位净值": "收盘"})

        elif asset_type == "index":
            start_date = date_to_akshare(n_days_ago(days + 30))
            end_date = date_to_akshare(today_str())
            df = ak.index_zh_a_hist(symbol=code, period="daily",
                                     start_date=start_date, end_date=end_date)
            if df.empty:
                return pd.DataFrame()
            df["日期"] = pd.to_datetime(df["日期"])
            return df.sort_values("日期").reset_index(drop=True)

        else:  # stock
            start_date = date_to_akshare(n_days_ago(days + 30))
            end_date = date_to_akshare(today_str())
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                     start_date=start_date, end_date=end_date,
                                     adjust="qfq")
            if df.empty:
                return pd.DataFrame()
            df["日期"] = pd.to_datetime(df["日期"])
            return df.sort_values("日期").reset_index(drop=True)
    except Exception as e:
        logger.error(f"获取{code}价格序列失败: {e}")
        return pd.DataFrame()


# ============ 时钟方向判定核心算法 ============

def calc_trend_slope(prices: list, days: int) -> float:
    """
    计算近 N 日趋势线年化斜率
    
    使用线性回归（最小二乘法）拟合 log(price) vs day_index
    返回年化收益率（按 252 个交易日）
    """
    if len(prices) < days:
        return None

    recent = np.array(prices[-days:], dtype=float)
    if (recent <= 0).any():
        return None

    # 时间索引（标准化到 0~1）
    x = np.arange(len(recent))

    # 线性回归 y = a*x + b
    # 使用 log 空间以反映复合增长
    log_y = np.log(recent)
    a, b = np.polyfit(x, log_y, 1)

    # 日均对数增长率 → 年化
    daily_log_return = a
    annual_return = (math.exp(daily_log_return * 252) - 1) * 100  # 转为百分比

    return annual_return


def determine_clock_direction(annualized_slope: float) -> dict:
    """
    根据年化斜率判定时钟方向
    """
    if annualized_slope is None:
        return {
            "direction": "未知",
            "name": "数据不足",
            "color": "⚪",
            "description": "数据不足无法判定",
            "operation": "等待数据完整",
            "score": 0,
        }

    if annualized_slope >= 60:
        return {
            "direction": "1点钟",
            "name": "极陡峭上涨（疯狂）",
            "color": "🟠",
            "description": "市场极度看好,接近疯狂",
            "operation": "高位追涨风险大,已持仓者准备分批止盈",
            "score": 60,  # 不是首选,因为过热
        }
    elif annualized_slope >= 30:
        return {
            "direction": "2点钟",
            "name": "稳健上涨（理想）",
            "color": "🟢",
            "description": "市场认可度高,投资者乐观",
            "operation": "★ 理想买入区:基本面与股价同步上涨",
            "score": 100,  # 最理想
        }
    elif annualized_slope > -10:
        return {
            "direction": "3点钟",
            "name": "横盘震荡（犹豫）",
            "color": "🟡",
            "description": "市场犹豫/怀疑/不信任",
            "operation": "等待方向选择,不出手",
            "score": 40,
        }
    elif annualized_slope > -30:
        return {
            "direction": "4点钟",
            "name": "缓慢下跌（悲观）",
            "color": "🔴",
            "description": "市场不认可,投资者悲观",
            "operation": "不接飞刀,等待真正企稳",
            "score": 10,
        }
    else:
        return {
            "direction": "5-6点钟",
            "name": "急跌/暴跌（极度悲观）",
            "color": "🔴",
            "description": "市场极度不相信企业商业前景",
            "operation": "严禁买入,等待真正企稳信号",
            "score": 0,
        }


def detect_trend_transition(long_term: dict, short_term: dict) -> dict:
    """
    检测趋势转变期
    """
    long_dir = long_term["direction"]
    short_dir = short_term["direction"]

    transition_type = None
    importance = "stable"
    note = ""

    if long_dir == short_dir:
        transition_type = "稳定型"
        note = f"长期短期方向一致（{long_dir}）,趋势确立"
    elif long_dir == "4点钟" and short_dir == "3点钟":
        transition_type = "下跌→横盘 过渡"
        importance = "watch"
        note = "🟡 待企稳信号,关注是否进一步转好"
    elif long_dir == "3点钟" and short_dir == "2点钟":
        transition_type = "横盘→上涨 过渡"
        importance = "key"
        note = "🟢 ★ 关键时刻 ★ 横盘正在转为上涨,重点关注"
    elif long_dir == "2点钟" and short_dir == "1点钟":
        transition_type = "健康→过热 预警"
        importance = "warning"
        note = "🟠 涨势加速,警惕短期过热"
    elif long_dir == "2点钟" and short_dir == "3点钟":
        transition_type = "健康→停滞"
        importance = "watch"
        note = "🟡 上涨动能减弱,需关注是否破位"
    elif long_dir == "5-6点钟" and short_dir in ["4点钟", "3点钟"]:
        transition_type = "暴跌→缓和 过渡"
        importance = "watch"
        note = "🟡 跌势放缓,但仍需观察是否真正企稳"
    elif long_dir in ["1点钟", "2点钟"] and short_dir in ["4点钟", "5-6点钟"]:
        transition_type = "上涨→下跌 反转"
        importance = "danger"
        note = "🔴 趋势反转！考虑止盈或止损"
    else:
        transition_type = f"{long_dir} → {short_dir}"
        importance = "watch"
        note = "趋势变化,需密切关注"

    return {
        "transition_type": transition_type,
        "importance": importance,
        "note": note,
    }


# ============ 主分析函数 ============

def analyze_clock_trend(code: str, asset_type: str = "stock") -> dict:
    """主入口：完整时钟趋势分析"""
    print_banner(f"五时钟趋势分析 | {code} ({asset_type})", char="═")

    df = get_price_series(code, asset_type, days=250)
    if df.empty or len(df) < 30:
        print(f"⚠️  数据不足（仅 {len(df)} 条）")
        return {"code": code, "error": "数据不足"}

    closes = df["收盘"].astype(float).tolist()

    # 计算多周期趋势
    long_term_slope = calc_trend_slope(closes, days=120) if len(closes) >= 120 else None
    short_term_slope = calc_trend_slope(closes, days=30)
    very_short_slope = calc_trend_slope(closes, days=10) if len(closes) >= 10 else None

    long_term = determine_clock_direction(long_term_slope)
    short_term = determine_clock_direction(short_term_slope)
    very_short = determine_clock_direction(very_short_slope)

    transition = detect_trend_transition(long_term, short_term)

    # 汇总操作建议
    if "key" in transition["importance"] or long_term["direction"] == "2点钟":
        overall_recommendation = "🟢 推荐:符合理想买入条件,可结合 6.1 8项企稳和 6.1bis 筹码峰双重确认"
    elif "danger" in transition["importance"]:
        overall_recommendation = "🔴 警告:趋势反转,持仓者立即评估止盈/止损"
    elif long_term["direction"] in ["4点钟", "5-6点钟"]:
        overall_recommendation = "🔴 不推荐:仍在下跌通道,严禁接飞刀"
    elif long_term["direction"] == "1点钟":
        overall_recommendation = "🟠 谨慎:虽然上涨但已过热,考虑止盈而非加仓"
    else:
        overall_recommendation = "🟡 等待:趋势不明朗,继续观察"

    result = {
        "code": code,
        "asset_type": asset_type,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_points": len(df),
        "current_price": closes[-1],
        "long_term_120d": {
            "annualized_slope_pct": round(long_term_slope, 2) if long_term_slope is not None else None,
            **long_term,
        },
        "short_term_30d": {
            "annualized_slope_pct": round(short_term_slope, 2) if short_term_slope is not None else None,
            **short_term,
        },
        "very_short_10d": {
            "annualized_slope_pct": round(very_short_slope, 2) if very_short_slope is not None else None,
            **very_short,
        },
        "trend_transition": transition,
        "overall_recommendation": overall_recommendation,
    }

    # 打印
    print(f"\n当前价: {closes[-1]:.4f}  数据点: {len(df)}")

    print(f"\n【近120日 长期趋势】")
    print(f"  年化斜率: {long_term_slope:.2f}%" if long_term_slope is not None else "  年化斜率: 数据不足")
    print(f"  方向: {long_term['color']} {long_term['direction']} - {long_term['name']}")
    print(f"  解读: {long_term['description']}")
    print(f"  操作: {long_term['operation']}")

    print(f"\n【近30日 短期趋势】")
    print(f"  年化斜率: {short_term_slope:.2f}%" if short_term_slope is not None else "  年化斜率: 数据不足")
    print(f"  方向: {short_term['color']} {short_term['direction']} - {short_term['name']}")

    print(f"\n【近10日 超短期】")
    print(f"  方向: {very_short['color']} {very_short['direction']}")

    print(f"\n【趋势转变检测】")
    print(f"  类型: {transition['transition_type']}")
    print(f"  重要性: {transition['importance']}")
    print(f"  评估: {transition['note']}")

    print(f"\n{'═' * 60}")
    print(f"  综合建议: {overall_recommendation}")
    print(f"{'═' * 60}")
    print(f"\n💡 与 6.1 8项企稳的配合使用：")
    print(f"   · 时钟2点钟 + 8项企稳🟢 = 全力买入30%")
    print(f"   · 时钟3点钟 + 8项企稳🟢 = 等突破再买入")
    print(f"   · 时钟2点钟 + 8项企稳🟡 = 轻仓试探≤10%")
    print(f"   · 时钟4-5点钟 + 任何企稳 = 严禁买入")

    save_path = save_result(result, f"clock_trend_{code}", subdir="11_clock")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


def batch_analyze(codes: list, asset_type: str = "stock"):
    """批量分析多个标的"""
    print_banner(f"批量时钟趋势分析（{len(codes)} 个）", char="═")
    
    results = {}
    for code in codes:
        try:
            results[code] = analyze_clock_trend(code, asset_type)
        except Exception as e:
            results[code] = {"error": str(e)}
    
    # 汇总
    print_banner("批量分析汇总", char="═")
    print(f"\n{'代码':10s}  {'方向':12s}  {'操作建议'}")
    print("-" * 80)
    for code, r in results.items():
        if "error" not in r:
            lt = r.get("long_term_120d", {})
            print(f"{code:10s}  {lt.get('color')} {lt.get('direction', '?'):8s}  {lt.get('operation', '')}")
        else:
            print(f"{code:10s}  ❌ {r['error']}")
    
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：")
        print("  python 11_clock_trend.py <代码> [类型]")
        print("\n类型可选: stock(默认) / index / fund")
        print("\n示例：")
        print("  python 11_clock_trend.py 600519              # A股")
        print("  python 11_clock_trend.py 000300 index        # 沪深300指数")
        print("  python 11_clock_trend.py 001938 fund         # 基金")
        sys.exit(1)
    
    code = sys.argv[1]
    asset_type = sys.argv[2] if len(sys.argv) >= 3 else "stock"
    analyze_clock_trend(code, asset_type)
