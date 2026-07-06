"""
12_chip_distribution.py —— 筹码峰分布与公允价分析（★v5.0新增，对应 skill Step 6.1bis.3-6.1bis.5）
==================================================================================================
吸收 20 年实战股民「筹码峰理论」：
  · 筹码峰 = 市场公允价格（有大量市场共识的位置）
  · 上方筹码峰 → 阻力位
  · 下方筹码峰 → 支撑位
  · "等股价回撤到重要筹码峰附近,公允价值附近买入"

核心算法：
  基于近 250 个交易日的 OHLCV 数据,假设单日成交量在最高价-最低价区间内均匀分布,
  累积每个价格 bin 的成交量,排序得 TOP3 筹码峰。

并整合 6.1bis.4 双重辅助验证决策矩阵（结合时钟方向 + 筹码峰）

用法：
    python 12_chip_distribution.py <代码> [类型]
    python 12_chip_distribution.py 600519
    python 12_chip_distribution.py 000300 index
    python 12_chip_distribution.py 001938 fund

    # 完整双重验证（结合时钟方向）
    python 12_chip_distribution.py <代码> --combined
"""

import sys
import os
import importlib.util
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
def get_ohlcv(code: str, asset_type: str = "stock", days: int = 250) -> pd.DataFrame:
    """获取OHLCV数据（基金没有日内最高/最低,无法做筹码分布）"""
    try:
        if asset_type == "fund":
            # 基金只有净值,做近似筹码分析（基于净值范围估算）
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df.empty:
                return pd.DataFrame()
            df["净值日期"] = pd.to_datetime(df["净值日期"])
            df = df.sort_values("净值日期").reset_index(drop=True).tail(days)
            # 模拟 OHLC：用前后净值做近似 high/low
            df["开盘"] = df["单位净值"].shift(1).fillna(df["单位净值"])
            df["收盘"] = df["单位净值"]
            df["最高"] = df[["开盘", "收盘"]].max(axis=1)
            df["最低"] = df[["开盘", "收盘"]].min(axis=1)
            # 基金没有成交量,用申购赎回净值变化幅度做替代权重
            df["成交量"] = abs(df["收盘"] - df["开盘"]) * 1000 + 1
            return df.rename(columns={"净值日期": "日期"})

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
        logger.error(f"获取{code}OHLCV失败: {e}")
        return pd.DataFrame()


# ============ 筹码分布核心算法 ============

def build_chip_distribution(df: pd.DataFrame, num_bins: int = 100) -> dict:
    """
    构建筹码分布
    
    算法：
      For each 交易日:
          假设当日成交量在最高价-最低价区间内均匀分布
          累积到对应价格 bin
    
    Args:
        df: 包含 最高/最低/成交量 的 DataFrame
        num_bins: 价格分桶数（默认100）
    
    Returns:
        {
            "price_bins": [...],          # 各价格bin的中点
            "volume_distribution": [...], # 各bin的累积成交量
            "max_price": 最高价,
            "min_price": 最低价,
        }
    """
    if df.empty or "最高" not in df.columns:
        return {"error": "数据缺失"}

    # 整体价格区间
    overall_max = df["最高"].max()
    overall_min = df["最低"].min()

    if overall_max <= overall_min:
        return {"error": "价格区间无效"}

    # 创建价格 bins
    bin_edges = np.linspace(overall_min, overall_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    volume_distribution = np.zeros(num_bins)

    # 逐日累积
    for _, row in df.iterrows():
        day_high = float(row["最高"])
        day_low = float(row["最低"])
        day_volume = float(row.get("成交量", 0))

        if day_high <= day_low or day_volume <= 0:
            continue

        # 找出当日价格区间所覆盖的 bin
        low_idx = np.searchsorted(bin_edges, day_low, side='right') - 1
        high_idx = np.searchsorted(bin_edges, day_high, side='right') - 1
        low_idx = max(0, low_idx)
        high_idx = min(num_bins - 1, high_idx)

        # 均匀分布到这些 bin
        num_covered = high_idx - low_idx + 1
        volume_per_bin = day_volume / num_covered

        for idx in range(low_idx, high_idx + 1):
            volume_distribution[idx] += volume_per_bin

    return {
        "price_bins": bin_centers.tolist(),
        "volume_distribution": volume_distribution.tolist(),
        "max_price": float(overall_max),
        "min_price": float(overall_min),
        "total_volume": float(volume_distribution.sum()),
    }


def find_top_chip_peaks(distribution: dict, top_n: int = 3, min_distance_pct: float = 5) -> list:
    """
    找出 TOP N 筹码峰
    
    Args:
        distribution: build_chip_distribution 输出
        top_n: 取 TOP N 个峰
        min_distance_pct: 两个峰之间至少相隔的价格百分比距离（避免相邻bin都被识别为峰）
    
    Returns:
        [{rank, price, volume, volume_pct}, ...]
    """
    if "error" in distribution:
        return []

    bins = distribution["price_bins"]
    volumes = np.array(distribution["volume_distribution"])
    total_volume = distribution["total_volume"]

    if total_volume == 0:
        return []

    # 排序得到 TOP N（带最小距离约束）
    sorted_indices = np.argsort(volumes)[::-1]  # 降序

    selected_peaks = []
    for idx in sorted_indices:
        if len(selected_peaks) >= top_n:
            break
        peak_price = bins[idx]
        # 检查与已选峰的最小距离
        too_close = False
        for sel in selected_peaks:
            if abs(peak_price - sel["price"]) / sel["price"] * 100 < min_distance_pct:
                too_close = True
                break
        if not too_close:
            selected_peaks.append({
                "price": peak_price,
                "volume": float(volumes[idx]),
                "volume_pct": float(volumes[idx] / total_volume * 100),
            })

    # 添加 rank
    for i, peak in enumerate(selected_peaks, 1):
        peak["rank"] = i

    return selected_peaks


# ============ 当前价位置判定 ============

def assess_current_price_position(current_price: float, peaks: list) -> dict:
    """
    根据当前价与筹码峰的相对位置,判定支撑/阻力情况
    """
    if not peaks:
        return {"error": "无筹码峰数据"}

    # 计算各峰相对距离（百分比）
    peak_analysis = []
    for peak in peaks:
        distance_pct = (peak["price"] - current_price) / current_price * 100
        peak_analysis.append({
            "rank": peak["rank"],
            "price": round(peak["price"], 4),
            "volume_pct": round(peak["volume_pct"], 2),
            "distance_pct": round(distance_pct, 2),
            "position": "上方阻力" if distance_pct > 0 else "下方支撑",
            "abs_distance_pct": abs(distance_pct),
        })

    # 找出最近的筹码峰
    nearest = min(peak_analysis, key=lambda x: x["abs_distance_pct"])
    
    # 判定位置类型
    if nearest["abs_distance_pct"] < 3:
        position_type = "🟡 接近某筹码峰（在公允区,关键决策点）"
        operation = "等待方向选择,观察是否突破/跌破"
    elif any(p["position"] == "上方阻力" and p["abs_distance_pct"] < 5 for p in peak_analysis):
        position_type = "⚠️ 接近上方阻力（距上方筹码峰<5%）"
        operation = "谨慎买入,可能在此横盘/回撤"
    elif any(p["position"] == "下方支撑" and p["abs_distance_pct"] < 5 for p in peak_analysis):
        position_type = "🟢 接近下方支撑（距下方筹码峰<5%）"
        operation = "★ 黄金机会:回撤至公允价附近,可加仓"
    elif all(p["abs_distance_pct"] > 10 for p in peak_analysis):
        position_type = "🟠 远离所有筹码峰（偏离公允价）"
        operation = "等待回归公允价后再决策"
    else:
        position_type = "🟡 中性区域"
        operation = "结合其他指标综合判断"

    return {
        "current_price": current_price,
        "peaks_with_distance": peak_analysis,
        "nearest_peak": nearest,
        "position_type": position_type,
        "operation": operation,
    }


# ============ 6.1bis.4 双重辅助验证决策矩阵 ============

def double_validation_matrix(clock_direction: str, chip_position: str) -> dict:
    """
    将时钟方向 + 筹码峰位置交叉,得到买卖建议
    """
    # 简化逻辑（对应 skill 6.1bis.4）
    matrix = {
        ("2点钟", "🟢 接近下方支撑"): ("🟢 强买入信号", "可执行第一笔仓位 30%"),
        ("2点钟", "🟡 接近某筹码峰"): ("🟢 买入信号", "可执行 25% 仓位"),
        ("2点钟", "⚠️ 接近上方阻力"): ("🟡 等回调", "等回调至下方筹码峰再买"),
        ("2点钟", "🟠 远离所有筹码峰"): ("🟡 试探", "仓位减半至 15%"),
        ("3点钟", "🟢 接近下方支撑"): ("🟡 等突破", "等待突破筹码峰确认上行"),
        ("3点钟", "🟡 接近某筹码峰"): ("🟡 等待方向", "横盘 + 公允价,需突破"),
        ("3点钟", "⚠️ 接近上方阻力"): ("🟠 不出手", "横盘+阻力位"),
        ("3点钟", "🟠 远离所有筹码峰"): ("🟠 不出手", "无明确信号"),
        ("4点钟", "🟢 接近下方支撑"): ("🟠 严密观察", "下跌途中遇支撑,但仍危险"),
        ("4点钟", "🟡 接近某筹码峰"): ("🔴 严禁买入", "下跌且未止跌"),
        ("4点钟", "⚠️ 接近上方阻力"): ("🔴 严禁买入", "已持仓者考虑止盈"),
        ("4点钟", "🟠 远离所有筹码峰"): ("🔴 严禁买入", "下跌中,不接飞刀"),
        ("1点钟", "🟢 接近下方支撑"): ("🟠 谨慎", "已过热,考虑止盈剩余仓位"),
        ("1点钟", "🟡 接近某筹码峰"): ("🟠 已持仓者止盈", "高位筹码堆积警惕"),
        ("1点钟", "⚠️ 接近上方阻力"): ("🟠 已持仓者止盈", "极度高位"),
        ("1点钟", "🟠 远离所有筹码峰"): ("🟠 已持仓者止盈", "脱离公允价"),
        ("5-6点钟", "*"): ("🔴 严禁买入", "暴跌中,等待真正企稳"),
    }

    # 匹配
    for (clock, chip), (signal, action) in matrix.items():
        if clock == clock_direction:
            if chip == "*" or chip == chip_position:
                return {
                    "clock_direction": clock_direction,
                    "chip_position": chip_position,
                    "signal": signal,
                    "action": action,
                }

    return {
        "clock_direction": clock_direction,
        "chip_position": chip_position,
        "signal": "🟡 待评估",
        "action": "组合不在标准矩阵,建议人工判断",
    }


# ============ 主分析函数 ============

def analyze_chip_distribution(code: str, asset_type: str = "stock") -> dict:
    """主入口：完整筹码峰分析"""
    print_banner(f"筹码峰分析 | {code} ({asset_type})", char="═")

    df = get_ohlcv(code, asset_type, days=250)
    if df.empty or len(df) < 30:
        print(f"⚠️  数据不足（仅 {len(df)} 条）")
        return {"code": code, "error": "数据不足"}

    if asset_type == "fund":
        print("\n⚠️  注意: 基金没有日内最高/最低数据,采用净值波动近似估算,精度有限")

    # 1. 构建筹码分布
    print("\n📊 正在构建筹码分布...")
    distribution = build_chip_distribution(df, num_bins=100)
    if "error" in distribution:
        return {"code": code, **distribution}

    # 2. 找 TOP 3 筹码峰
    peaks = find_top_chip_peaks(distribution, top_n=3, min_distance_pct=5)

    # 3. 当前价位置评估
    current_price = float(df["收盘"].iloc[-1])
    position = assess_current_price_position(current_price, peaks)

    result = {
        "code": code,
        "asset_type": asset_type,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_period": f"{df['日期'].iloc[0].strftime('%Y-%m-%d')} ~ {df['日期'].iloc[-1].strftime('%Y-%m-%d')}",
        "data_points": len(df),
        "current_price": current_price,
        "price_range": {
            "min": distribution["min_price"],
            "max": distribution["max_price"],
        },
        "top3_chip_peaks": peaks,
        "current_position_analysis": position,
    }

    # === 打印 ===
    print(f"\n当前价: {current_price:.4f}")
    print(f"分析周期: {result['data_period']}（{len(df)} 个交易日）")
    print(f"价格区间: {distribution['min_price']:.4f} ~ {distribution['max_price']:.4f}")

    print(f"\n【TOP 3 筹码峰（公允价格）】")
    for peak in peaks:
        distance = (peak["price"] - current_price) / current_price * 100
        position_str = "↑ 上方（阻力）" if distance > 0 else "↓ 下方（支撑）"
        print(f"  TOP{peak['rank']}: {peak['price']:.4f}  占总成交量 {peak['volume_pct']:.2f}%  距当前价 {distance:+.2f}%  {position_str}")

    print(f"\n【当前价位置】")
    print(f"  {position['position_type']}")
    print(f"  操作建议: {position['operation']}")

    print(f"\n  最近筹码峰: {position['nearest_peak']['price']:.4f}（距 {position['nearest_peak']['distance_pct']:+.2f}%）")

    print(f"\n💡 与时钟方向配合使用：")
    print(f"   建议运行: python 11_clock_trend.py {code} {asset_type}")
    print(f"   然后查看下方双重验证矩阵,组合出最终决策")

    save_path = save_result(result, f"chip_dist_{code}", subdir="12_chip")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


def analyze_combined(code: str, asset_type: str = "stock") -> dict:
    """完整双重验证分析（时钟方向 + 筹码峰）"""
    print_banner(f"v5.0 双重辅助验证 | {code}", char="═")

    # 1. 筹码峰
    chip_result = analyze_chip_distribution(code, asset_type)
    if "error" in chip_result:
        return chip_result

    # 2. 时钟方向（动态加载 11 脚本）
    print(f"\n{'─' * 60}")
    print(f"  正在加载时钟趋势分析模块...")
    print(f"{'─' * 60}")
    
    try:
        clock_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "11_clock_trend.py")
        spec = importlib.util.spec_from_file_location("clock_mod", clock_script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        clock_result = mod.analyze_clock_trend(code, asset_type)
    except Exception as e:
        clock_result = {"error": f"时钟分析失败: {e}"}
        return {"code": code, **clock_result}

    # 3. 双重验证矩阵
    long_term = clock_result.get("long_term_120d", {})
    clock_dir = long_term.get("direction", "未知")
    chip_pos = chip_result.get("current_position_analysis", {}).get("position_type", "")

    # 简化匹配（截取核心标识）
    chip_pos_simplified = chip_pos
    if "下方支撑" in chip_pos:
        chip_pos_simplified = "🟢 接近下方支撑"
    elif "上方阻力" in chip_pos:
        chip_pos_simplified = "⚠️ 接近上方阻力"
    elif "远离" in chip_pos:
        chip_pos_simplified = "🟠 远离所有筹码峰"
    elif "公允区" in chip_pos or "接近某筹码峰" in chip_pos:
        chip_pos_simplified = "🟡 接近某筹码峰"

    matrix_result = double_validation_matrix(clock_dir, chip_pos_simplified)

    final_result = {
        "code": code,
        "asset_type": asset_type,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chip_analysis": chip_result,
        "clock_analysis": clock_result,
        "double_validation": matrix_result,
    }

    # === 双重验证结论 ===
    print_banner("★ 双重验证最终结论 ★", char="═")
    print(f"\n  代码: {code}")
    print(f"  当前价: {chip_result['current_price']:.4f}")
    print(f"\n  ┌─────────────────────────────────────────────┐")
    print(f"  │ 时钟方向: {clock_dir:15s} ({long_term.get('color', '')})  │")
    print(f"  │ 筹码位置: {chip_pos_simplified:25s}     │")
    print(f"  │                                                 │")
    print(f"  │ ➜ 信号: {matrix_result['signal']:30s}    │")
    print(f"  │ ➜ 建议: {matrix_result['action']:30s}    │")
    print(f"  └─────────────────────────────────────────────┘")

    print(f"\n💡 与 6.1 8项企稳的最终交叉验证：")
    print(f"   1. 运行 04_technical_analysis.py 获取 8 项企稳判定")
    print(f"   2. 若 6.1 = 🟢 + 本验证 = 🟢 → 全力执行")
    print(f"   3. 若两者矛盾 → 以保守者为准")
    print(f"   4. 若 6.1 ≤ 🟡 + 本验证 ≤ 🟡 → 直接放入观察池")

    save_path = save_result(final_result, f"combined_{code}", subdir="12_chip")
    print(f"\n✅ 双重验证结果已保存: {save_path}")
    return final_result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：")
        print("  python 12_chip_distribution.py <代码> [类型]")
        print("  python 12_chip_distribution.py <代码> --combined  # 双重验证（时钟+筹码）")
        print("\n类型: stock(默认) / index / fund")
        print("\n示例：")
        print("  python 12_chip_distribution.py 600519")
        print("  python 12_chip_distribution.py 600519 stock --combined")
        print("  python 12_chip_distribution.py 000300 index")
        print("  python 12_chip_distribution.py 001938 fund")
        sys.exit(1)

    code = sys.argv[1]
    
    if "--combined" in sys.argv:
        asset_type = "stock"
        for arg in sys.argv[2:]:
            if arg in ["stock", "index", "fund"]:
                asset_type = arg
        analyze_combined(code, asset_type)
    else:
        asset_type = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[2] in ["stock", "index", "fund"] else "stock"
        analyze_chip_distribution(code, asset_type)
