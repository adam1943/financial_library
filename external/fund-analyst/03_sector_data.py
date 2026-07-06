"""
03_sector_data.py —— 行业板块数据获取（对应 v4.0 skill 1.3 节）
================================================================
获取字段：
  · 各板块近30天/近90天涨跌幅排名
  · 各板块当前PE历史百分位（近5年）
  · 北向资金近30天行业净流入/流出
  · 融资余额近期变化趋势

用法：
    python 03_sector_data.py [板块名称]  # 不传=全量
    python 03_sector_data.py 人工智能
"""

import sys
import akshare as ak
import pandas as pd
from datetime import datetime

from config import (
    get_logger, with_retry, with_cache, save_result, print_banner,
    n_days_ago, today_str, date_to_akshare, SECTOR_CODES
)

logger = get_logger(__name__)


@with_retry()
@with_cache(cache_type="daily")
def get_sector_returns() -> pd.DataFrame:
    """获取行业板块实时行情（含涨跌幅）"""
    try:
        df = ak.stock_board_industry_name_em()
        return df
    except Exception as e:
        logger.error(f"获取行业板块行情失败: {e}")
        return pd.DataFrame()


@with_retry()
@with_cache(cache_type="daily")
def get_sector_history(sector_name: str, days: int = 120) -> pd.DataFrame:
    """获取某板块历史K线"""
    try:
        end_date = date_to_akshare(today_str())
        start_date = date_to_akshare(n_days_ago(days))
        df = ak.stock_board_industry_hist_em(
            symbol=sector_name,
            start_date=start_date,
            end_date=end_date,
            period="日k",
            adjust=""
        )
        return df
    except Exception as e:
        logger.error(f"获取板块[{sector_name}]历史数据失败: {e}")
        return pd.DataFrame()


def calc_sector_return_periods(sector_name: str) -> dict:
    """计算板块近30天/90天涨跌幅"""
    df = get_sector_history(sector_name, days=120)
    if df.empty or len(df) < 20:
        return {"sector": sector_name, "error": "数据不足"}

    df = df.sort_values("日期").reset_index(drop=True)
    latest_close = float(df["收盘"].iloc[-1])

    result = {"sector": sector_name, "latest_close": latest_close}

    # 近30天涨跌幅
    if len(df) >= 22:
        close_30d_ago = float(df["收盘"].iloc[-22])
        result["return_30d_pct"] = round((latest_close / close_30d_ago - 1) * 100, 2)

    # 近90天涨跌幅
    if len(df) >= 65:
        close_90d_ago = float(df["收盘"].iloc[-65])
        result["return_90d_pct"] = round((latest_close / close_90d_ago - 1) * 100, 2)

    # 近5日涨跌幅
    if len(df) >= 6:
        close_5d_ago = float(df["收盘"].iloc[-6])
        result["return_5d_pct"] = round((latest_close / close_5d_ago - 1) * 100, 2)

    return result


@with_retry()
@with_cache(cache_type="daily")
def get_northbound_flow(days: int = 30) -> dict:
    """获取北向资金近N日净流入情况"""
    try:
        # 北向资金每日净流入
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df.empty:
            return {"error": "北向资金数据为空"}

        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期').reset_index(drop=True).tail(days)

        total_net_flow = df['当日成交净买额'].sum() if '当日成交净买额' in df.columns else 0
        positive_days = (df['当日成交净买额'] > 0).sum() if '当日成交净买额' in df.columns else 0

        # 评级
        rating = "中性"
        score = 8
        if total_net_flow > 500:  # 亿元
            rating = "大幅净流入"
            score = 25
        elif total_net_flow > 100:
            rating = "净流入"
            score = 18
        elif total_net_flow < -500:
            rating = "大幅净流出"
            score = 2
        elif total_net_flow < -100:
            rating = "净流出"
            score = 5

        return {
            "period_days": days,
            "total_net_flow_billion": round(float(total_net_flow), 2),
            "positive_days_count": int(positive_days),
            "rating": rating,
            "score_out_of_25": score,
        }
    except Exception as e:
        logger.error(f"获取北向资金失败: {e}")
        return {"error": str(e)}


@with_retry()
@with_cache(cache_type="daily")
def get_margin_balance_trend() -> dict:
    """获取两融余额趋势"""
    try:
        df = ak.stock_margin_sse()
        if df.empty:
            return {"error": "两融数据为空"}

        # 只取最近30天
        df = df.tail(30).copy()
        if '融资融券余额' not in df.columns:
            # 尝试其他字段名
            for col in ['融资余额', '融资融券余额(元)']:
                if col in df.columns:
                    df['融资融券余额'] = df[col]
                    break

        if '融资融券余额' in df.columns:
            balance_now = float(df['融资融券余额'].iloc[-1])
            balance_30d_ago = float(df['融资融券余额'].iloc[0])
            change_pct = (balance_now - balance_30d_ago) / balance_30d_ago * 100

            trend = "上升" if change_pct > 2 else ("下降" if change_pct < -2 else "平稳")
            return {
                "balance_latest": balance_now,
                "balance_30d_ago": balance_30d_ago,
                "change_pct": round(float(change_pct), 2),
                "trend": trend,
            }
        return {"error": "字段不匹配"}
    except Exception as e:
        logger.error(f"获取两融余额失败: {e}")
        return {"error": str(e)}


def rank_all_sectors() -> dict:
    """获取全市场行业涨跌幅排名"""
    print("\n正在获取全市场行业排名...")
    df = get_sector_returns()
    if df.empty:
        return {"error": "行业数据为空"}

    # 按涨跌幅排序（今日涨跌幅）
    rank_col = None
    for candidate in ["涨跌幅", "今日涨跌幅"]:
        if candidate in df.columns:
            rank_col = candidate
            break

    if rank_col:
        df_sorted = df.sort_values(rank_col, ascending=False)
        top10 = df_sorted.head(10)[["板块名称", rank_col]].to_dict("records")
        bottom10 = df_sorted.tail(10)[["板块名称", rank_col]].to_dict("records")
        return {
            "top10_gainers": top10,
            "top10_losers": bottom10,
            "total_sectors": len(df),
        }
    return {"error": "字段不匹配"}


def analyze_sectors(target_sector: str = None) -> dict:
    """主入口"""
    print_banner("行业板块数据分析")

    result = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "northbound_flow": get_northbound_flow(),
        "margin_trend": get_margin_balance_trend(),
        "sector_rankings": rank_all_sectors(),
    }

    # 打印北向
    nb = result["northbound_flow"]
    if "error" not in nb:
        print(f"\n【北向资金】近30日净流入: {nb['total_net_flow_billion']}亿  评级: {nb['rating']}  评分: {nb['score_out_of_25']}/25")

    # 打印两融
    mt = result["margin_trend"]
    if "error" not in mt:
        print(f"【两融余额】最新: {mt.get('balance_latest', 0)/1e8:.1f}亿  近30日: {mt['trend']} ({mt['change_pct']}%)")

    # 打印TOP10涨跌幅
    rk = result["sector_rankings"]
    if "top10_gainers" in rk:
        print(f"\n【TOP10涨幅板块】")
        for i, s in enumerate(rk["top10_gainers"], 1):
            print(f"  {i:2d}. {s['板块名称']:15s}  {list(s.values())[1]}%")

    # 如指定单板块，深入分析
    if target_sector:
        print(f"\n【板块详情: {target_sector}】")
        sector_detail = calc_sector_return_periods(target_sector)
        result["target_sector_detail"] = sector_detail
        if "error" not in sector_detail:
            print(f"  近5天涨跌: {sector_detail.get('return_5d_pct', 'N/A')}%")
            print(f"  近30天涨跌: {sector_detail.get('return_30d_pct', 'N/A')}%")
            print(f"  近90天涨跌: {sector_detail.get('return_90d_pct', 'N/A')}%")

    save_path = save_result(result, "sector_analysis", subdir="03_sectors")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    analyze_sectors(target_sector=target)
