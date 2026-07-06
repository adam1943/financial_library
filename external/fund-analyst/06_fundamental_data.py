"""
06_fundamental_data.py —— 基本面锚定数据（对应 v4.0 skill 1.6 节）
==================================================================
获取字段：
  · 统计局行业利润（规模以上工业企业利润）
  · 重仓股最近4季度净利润、自由现金流
  · 重仓股动态PE、PB、股息率
  · 真金白银估值判定（真实PE、回本年限、FCF收益率）

用法：
    python 06_fundamental_data.py <股票代码>
    python 06_fundamental_data.py 600519
"""

import sys
import akshare as ak
import pandas as pd
from datetime import datetime

from config import get_logger, with_retry, with_cache, save_result, print_banner

logger = get_logger(__name__)


@with_retry()
@with_cache(cache_type="daily")
def get_stock_valuation(code: str) -> dict:
    """获取个股估值指标（PE/PB/股息率）"""
    try:
        # 个股估值指标
        df = ak.stock_a_indicator_lg(symbol=code)
        if not df.empty:
            latest = df.iloc[-1]
            return {
                "code": code,
                "date": str(latest.get("trade_date", "")),
                "pe_ttm": float(latest["pe_ttm"]) if pd.notna(latest.get("pe_ttm")) else None,
                "pb": float(latest["pb"]) if pd.notna(latest.get("pb")) else None,
                "ps_ttm": float(latest["ps_ttm"]) if pd.notna(latest.get("ps_ttm")) else None,
                "dv_ratio": float(latest["dv_ratio"]) if pd.notna(latest.get("dv_ratio")) else None,
                "dv_ttm": float(latest["dv_ttm"]) if pd.notna(latest.get("dv_ttm")) else None,
                "total_mv_billion": float(latest["total_mv"]) / 1e4 if pd.notna(latest.get("total_mv")) else None,  # 万元→亿元
            }
    except Exception as e:
        logger.error(f"获取个股{code}估值失败: {e}")
    return {"code": code, "error": "估值数据获取失败"}


@with_retry()
@with_cache(cache_type="quarterly")
def get_stock_profit(code: str) -> dict:
    """获取个股最近4季度净利润 & 现金流"""
    try:
        # 利润表
        profit_df = ak.stock_profit_sheet_by_quarterly_em(symbol=f"SH{code}" if code.startswith("6") else f"SZ{code}")
        if profit_df.empty:
            return {"code": code, "error": "利润表为空"}

        # 取最近4季度
        profit_df = profit_df.head(4)
        
        # 净利润字段
        net_profit_col = None
        for col in ["NETPROFIT", "净利润", "归属于母公司股东的净利润"]:
            if col in profit_df.columns:
                net_profit_col = col
                break

        result = {"code": code, "quarters": []}
        total_profit_4q = 0
        if net_profit_col:
            for _, row in profit_df.iterrows():
                report_date = str(row.get("REPORT_DATE", row.get("报告期", "")))
                profit = float(row[net_profit_col]) if pd.notna(row[net_profit_col]) else 0
                total_profit_4q += profit
                result["quarters"].append({
                    "date": report_date[:10],
                    "net_profit_billion": round(profit / 1e8, 2),
                })
            result["net_profit_ttm_billion"] = round(total_profit_4q / 1e8, 2)

        return result
    except Exception as e:
        logger.error(f"获取个股{code}利润失败: {e}")
        return {"code": code, "error": str(e)}


@with_retry()
@with_cache(cache_type="quarterly")
def get_stock_cashflow(code: str) -> dict:
    """获取自由现金流（经营现金流 - 资本开支）"""
    try:
        cf_df = ak.stock_cash_flow_sheet_by_quarterly_em(symbol=f"SH{code}" if code.startswith("6") else f"SZ{code}")
        if cf_df.empty:
            return {"code": code, "error": "现金流量表为空"}

        cf_df = cf_df.head(4)

        # 经营现金流
        op_cf_col = None
        for col in ["NETCASH_OPERATE", "经营活动产生的现金流量净额"]:
            if col in cf_df.columns:
                op_cf_col = col
                break

        # 资本开支
        capex_col = None
        for col in ["CONSTRUCT_LONG_ASSET", "购建固定资产、无形资产和其他长期资产支付的现金"]:
            if col in cf_df.columns:
                capex_col = col
                break

        total_op_cf = 0
        total_capex = 0
        if op_cf_col:
            total_op_cf = sum(float(x) if pd.notna(x) else 0 for x in cf_df[op_cf_col])
        if capex_col:
            total_capex = sum(float(x) if pd.notna(x) else 0 for x in cf_df[capex_col])

        fcf = total_op_cf - total_capex

        return {
            "code": code,
            "operating_cashflow_ttm_billion": round(total_op_cf / 1e8, 2),
            "capex_ttm_billion": round(total_capex / 1e8, 2),
            "free_cashflow_ttm_billion": round(fcf / 1e8, 2),
        }
    except Exception as e:
        logger.error(f"获取个股{code}现金流失败: {e}")
        return {"code": code, "error": str(e)}


def calc_real_valuation(stock_data: dict) -> dict:
    """真金白银估值计算"""
    valuation = stock_data.get("valuation", {})
    profit = stock_data.get("profit", {})
    cashflow = stock_data.get("cashflow", {})

    result = {
        "code": stock_data.get("code"),
        "pe_ttm": valuation.get("pe_ttm"),
        "pb": valuation.get("pb"),
        "dv_ratio_pct": valuation.get("dv_ratio"),
        "market_cap_billion": valuation.get("total_mv_billion"),
        "net_profit_ttm_billion": profit.get("net_profit_ttm_billion"),
        "free_cashflow_ttm_billion": cashflow.get("free_cashflow_ttm_billion"),
    }

    # 真实PE = 市值 / TTM净利润
    mv = valuation.get("total_mv_billion")
    np_ttm = profit.get("net_profit_ttm_billion")
    if mv and np_ttm and np_ttm > 0:
        real_pe = mv / np_ttm
        result["real_pe"] = round(real_pe, 2)
        result["payback_years"] = round(real_pe, 2)  # 回本年限近似=PE
    else:
        result["real_pe"] = None
        result["payback_years"] = None

    # 自由现金流收益率 = FCF / 市值
    fcf = cashflow.get("free_cashflow_ttm_billion")
    if mv and fcf and mv > 0:
        result["fcf_yield_pct"] = round(fcf / mv * 100, 2)
    else:
        result["fcf_yield_pct"] = None

    # 估值判定
    pe = result["real_pe"] or result["pe_ttm"]
    if pe is None:
        result["valuation_rating"] = "数据缺失"
    elif pe < 0:
        result["valuation_rating"] = "亏损（慎）"
    elif pe < 15:
        result["valuation_rating"] = "低估（safe）"
    elif pe < 25:
        result["valuation_rating"] = "合理"
    elif pe < 40:
        result["valuation_rating"] = "偏高"
    else:
        result["valuation_rating"] = "高估（危险）"

    return result


@with_retry()
@with_cache(cache_type="quarterly")
def get_industry_profit_data() -> dict:
    """统计局规模以上工业企业利润（行业基本面锚定）"""
    try:
        # 全行业利润
        df = ak.macro_china_industrial_added_value_yoy()
        if df.empty:
            return {"error": "统计局工业数据为空"}

        df = df.tail(12)
        latest = df.iloc[-1]
        return {
            "date": str(latest.get("月份", "")),
            "industrial_growth_yoy": float(latest.get("同比增长", 0)) if pd.notna(latest.get("同比增长")) else None,
            "recent_12m_avg": round(float(df["同比增长"].mean()), 2) if "同比增长" in df.columns else None,
        }
    except Exception as e:
        logger.warning(f"工业利润数据获取失败: {e}")
        return {"error": str(e)}


def analyze_fundamental(code: str) -> dict:
    """主入口"""
    print_banner(f"基本面分析 | {code}")

    stock_data = {
        "code": code,
        "valuation": get_stock_valuation(code),
        "profit": get_stock_profit(code),
        "cashflow": get_stock_cashflow(code),
    }

    result = {
        "code": code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **stock_data,
        "real_valuation": calc_real_valuation(stock_data),
        "industry_profit": get_industry_profit_data(),
    }

    # 打印
    val = result["real_valuation"]
    print(f"\n【估值指标】")
    print(f"  市值: {val.get('market_cap_billion')}亿")
    print(f"  PE(TTM): {val.get('pe_ttm')}  PB: {val.get('pb')}  股息率: {val.get('dv_ratio_pct')}%")
    print(f"  近4季度净利润(TTM): {val.get('net_profit_ttm_billion')}亿")
    print(f"  自由现金流(TTM): {val.get('free_cashflow_ttm_billion')}亿")
    print(f"\n【真金白银估值】")
    print(f"  真实PE: {val.get('real_pe')}  回本年限: {val.get('payback_years')}年")
    print(f"  FCF收益率: {val.get('fcf_yield_pct')}%")
    print(f"  估值判定: {val.get('valuation_rating')}")

    ind = result["industry_profit"]
    if "industrial_growth_yoy" in ind and ind["industrial_growth_yoy"] is not None:
        print(f"\n【行业基本面锚定】")
        print(f"  最新工业增速: {ind['industrial_growth_yoy']}%  近12月均: {ind.get('recent_12m_avg')}%")

    save_path = save_result(result, f"fundamental_{code}", subdir="06_fundamental")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 06_fundamental_data.py <股票代码>")
        print("示例: python 06_fundamental_data.py 600519")
        sys.exit(1)
    analyze_fundamental(sys.argv[1])
