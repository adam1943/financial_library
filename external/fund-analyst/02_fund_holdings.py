"""
02_fund_holdings.py —— 持仓数据获取（对应 v4.0 skill 1.2 节）
==============================================================
获取字段：
  · 最新季报十大重仓股（代码/名称/占比）
  · 持仓与上季度对比（加仓/减仓/新进/退出）

用法：
    python 02_fund_holdings.py <基金代码>
    python 02_fund_holdings.py 001938
"""

import sys
import akshare as ak
import pandas as pd
from datetime import datetime

from config import get_logger, with_retry, with_cache, save_result, print_banner

logger = get_logger(__name__)


@with_retry()
@with_cache(cache_type="quarterly")
def get_fund_holdings(fund_code: str) -> pd.DataFrame:
    """
    获取基金十大重仓股历史数据（多个季度）
    数据源：akshare - 天天基金
    """
    try:
        # 获取基金重仓股明细（默认返回多个季度）
        df = ak.fund_portfolio_hold_em(symbol=fund_code)
        if df.empty:
            logger.warning(f"基金{fund_code}持仓数据为空")
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.error(f"获取基金{fund_code}持仓失败: {e}")
        return pd.DataFrame()


def parse_latest_two_quarters(holdings_df: pd.DataFrame) -> dict:
    """
    解析最近两个季度的持仓，计算加减仓变化
    """
    if holdings_df.empty:
        return {"error": "持仓数据为空"}

    # 季度字段识别（akshare字段一般是"季度"）
    quarter_col = None
    for candidate in ["季度", "报告期", "报告日期"]:
        if candidate in holdings_df.columns:
            quarter_col = candidate
            break

    if quarter_col is None:
        return {"error": f"无法识别季度字段，实际列：{list(holdings_df.columns)}"}

    quarters = sorted(holdings_df[quarter_col].unique(), reverse=True)
    if len(quarters) < 1:
        return {"error": "至少需要1个季度数据"}

    latest_q = quarters[0]
    prev_q = quarters[1] if len(quarters) >= 2 else None

    latest_holdings = holdings_df[holdings_df[quarter_col] == latest_q].copy()
    prev_holdings = holdings_df[holdings_df[quarter_col] == prev_q].copy() if prev_q else pd.DataFrame()

    # 标准化字段名
    stock_col = "股票代码" if "股票代码" in holdings_df.columns else "代码"
    name_col = "股票名称" if "股票名称" in holdings_df.columns else "名称"
    ratio_col = None
    for candidate in ["占净值比例", "持仓占净值比", "占比", "持仓比例"]:
        if candidate in holdings_df.columns:
            ratio_col = candidate
            break

    # 转换最新持仓为list[dict]
    top10_list = []
    for _, row in latest_holdings.head(10).iterrows():
        top10_list.append({
            "code": str(row.get(stock_col, "")),
            "name": str(row.get(name_col, "")),
            "ratio": float(row[ratio_col]) if ratio_col and pd.notna(row.get(ratio_col)) else None,
        })

    # 计算加减仓
    changes = []
    if not prev_holdings.empty and stock_col:
        latest_codes = set(latest_holdings[stock_col].astype(str))
        prev_codes = set(prev_holdings[stock_col].astype(str))

        # 新进
        new_in = latest_codes - prev_codes
        # 退出
        new_out = prev_codes - latest_codes
        # 共同持有
        common = latest_codes & prev_codes

        for code in new_in:
            row = latest_holdings[latest_holdings[stock_col].astype(str) == code].iloc[0]
            changes.append({
                "code": code,
                "name": str(row.get(name_col, "")),
                "action": "新进",
                "latest_ratio": float(row[ratio_col]) if ratio_col and pd.notna(row.get(ratio_col)) else None,
                "prev_ratio": None,
                "change": None,
            })

        for code in new_out:
            row = prev_holdings[prev_holdings[stock_col].astype(str) == code].iloc[0]
            changes.append({
                "code": code,
                "name": str(row.get(name_col, "")),
                "action": "退出",
                "latest_ratio": None,
                "prev_ratio": float(row[ratio_col]) if ratio_col and pd.notna(row.get(ratio_col)) else None,
                "change": None,
            })

        for code in common:
            latest_row = latest_holdings[latest_holdings[stock_col].astype(str) == code].iloc[0]
            prev_row = prev_holdings[prev_holdings[stock_col].astype(str) == code].iloc[0]
            if ratio_col:
                lr = float(latest_row[ratio_col]) if pd.notna(latest_row.get(ratio_col)) else 0
                pr = float(prev_row[ratio_col]) if pd.notna(prev_row.get(ratio_col)) else 0
                delta = lr - pr
                action = "加仓" if delta > 0.1 else ("减仓" if delta < -0.1 else "持平")
                changes.append({
                    "code": code,
                    "name": str(latest_row.get(name_col, "")),
                    "action": action,
                    "latest_ratio": round(lr, 2),
                    "prev_ratio": round(pr, 2),
                    "change": round(delta, 2),
                })

    # 行业分布统计
    industry_exposure = analyze_industry_exposure(top10_list)

    return {
        "latest_quarter": str(latest_q),
        "prev_quarter": str(prev_q) if prev_q else None,
        "top10_holdings": top10_list,
        "holdings_changes": changes,
        "industry_exposure": industry_exposure,
        "total_top10_ratio": round(sum(h["ratio"] for h in top10_list if h["ratio"]), 2),
    }


def analyze_industry_exposure(top10_list: list) -> dict:
    """
    对前10大重仓股进行行业分类（需查询个股所属行业）
    """
    industry_map = {}
    for stock in top10_list:
        code = stock["code"]
        try:
            # 获取个股所属行业（akshare）
            ind_df = ak.stock_individual_info_em(symbol=code)
            if not ind_df.empty:
                industry = ind_df.loc[ind_df['item'] == '行业', 'value'].values
                industry_name = industry[0] if len(industry) > 0 else "未知"
                industry_map[industry_name] = industry_map.get(industry_name, 0) + (stock.get("ratio") or 0)
        except Exception:
            industry_map["未知"] = industry_map.get("未知", 0) + (stock.get("ratio") or 0)

    # 排序并输出
    sorted_ind = sorted(industry_map.items(), key=lambda x: x[1], reverse=True)
    return {
        "distribution": [{"industry": k, "ratio": round(v, 2)} for k, v in sorted_ind],
        "max_industry": sorted_ind[0][0] if sorted_ind else None,
        "max_industry_ratio": round(sorted_ind[0][1], 2) if sorted_ind else 0,
        "concentration_warning": sorted_ind[0][1] > 40 if sorted_ind else False,
    }


def analyze_holdings(fund_code: str) -> dict:
    """主入口"""
    print_banner(f"基金持仓分析 | {fund_code}")

    holdings_df = get_fund_holdings(fund_code)
    if holdings_df.empty:
        print("⚠️  未获取到持仓数据")
        return {"fund_code": fund_code, "error": "无持仓数据"}

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **parse_latest_two_quarters(holdings_df),
    }

    # 打印摘要
    print(f"\n最新季度: {result.get('latest_quarter')}")
    print(f"对比季度: {result.get('prev_quarter')}")
    print(f"\n【前10重仓股】")
    for i, h in enumerate(result.get("top10_holdings", []), 1):
        print(f"  {i:2d}. {h['code']:8s}  {h['name']:12s}  占比 {h['ratio']}%")

    print(f"\n前十持仓集中度: {result.get('total_top10_ratio')}%（<50%为佳）")

    ind_exp = result.get("industry_exposure", {})
    print(f"\n【行业分布】最大行业: {ind_exp.get('max_industry')} ({ind_exp.get('max_industry_ratio')}%)")
    if ind_exp.get("concentration_warning"):
        print("⚠️  行业集中度过高警告（单一行业>40%）")

    changes = result.get("holdings_changes", [])
    new_in = [c for c in changes if c["action"] == "新进"]
    new_out = [c for c in changes if c["action"] == "退出"]
    add = [c for c in changes if c["action"] == "加仓"]
    reduce = [c for c in changes if c["action"] == "减仓"]
    print(f"\n【调仓方向】新进{len(new_in)} / 退出{len(new_out)} / 加仓{len(add)} / 减仓{len(reduce)}")

    save_path = save_result(result, f"holdings_{fund_code}", subdir="02_holdings")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 02_fund_holdings.py <基金代码>")
        print("示例: python 02_fund_holdings.py 001938")
        sys.exit(1)

    fund_code = sys.argv[1]
    analyze_holdings(fund_code)
