"""
10_hardcore_screening.py —— 硬核财务筛选（★v5.0新增，对应 skill Step 0 投资分析底层框架）
==========================================================================================
吸收 20 年实战股民"商业模式 → 财务表现 → 市场定价"三层框架，对个股进行"故事股 vs 实力股"判定。

核心功能：
  · 0.2 伟大公司四条件评估（无差别产品 + 最广泛用户 + 自由定价 + 市场垄断）
  · 0.3 财务硬核三类数据（收入规模+成长性 / EBIT margin / 自由现金流）
  · 0.4 故事股识别清单（一票否决）
  · 输出 Step 0 完整预筛报告

注意：伟大公司四条件中"无差别产品/广泛用户/垄断"涉及定性判断，
脚本会输出可量化指标供大模型进行最终判定。

用法：
    # 评估单只股票
    python 10_hardcore_screening.py <股票代码>
    python 10_hardcore_screening.py 600519

    # 评估某只基金的前3大重仓股（自动调用02脚本获取）
    python 10_hardcore_screening.py --fund 001938
"""

import sys
import os
import importlib.util
import akshare as ak
import pandas as pd
from datetime import datetime

from config import get_logger, with_retry, with_cache, save_result, print_banner

logger = get_logger(__name__)


# ============ 财务硬核三类数据 ============

@with_retry()
@with_cache(cache_type="quarterly")
def get_revenue_and_growth(code: str) -> dict:
    """
    第一类：收入规模与成长性
    门槛：年收入 > 1亿美元（约7亿人民币），增速 15-20% 为理想区间
    """
    try:
        # 优先使用 akshare 的财务摘要接口
        symbol = f"SH{code}" if code.startswith("6") else f"SZ{code}"

        # 利润表（含营业收入）
        df = ak.stock_profit_sheet_by_quarterly_em(symbol=symbol)
        if df.empty:
            return {"code": code, "error": "利润表为空"}

        # 营业收入字段识别
        revenue_col = None
        for col in ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "营业总收入", "营业收入"]:
            if col in df.columns:
                revenue_col = col
                break

        if not revenue_col:
            return {"code": code, "error": f"无法识别营收字段。列：{list(df.columns)[:10]}"}

        # 取最近4季度TTM
        recent_4q = df.head(4)
        ttm_revenue = sum(float(x) if pd.notna(x) else 0 for x in recent_4q[revenue_col])

        # 取上一年同期4季度（5-8季度）
        if len(df) >= 8:
            prev_4q = df.iloc[4:8]
            prev_ttm = sum(float(x) if pd.notna(x) else 0 for x in prev_4q[revenue_col])
            growth_yoy = (ttm_revenue - prev_ttm) / prev_ttm * 100 if prev_ttm > 0 else None
        else:
            growth_yoy = None

        # 规模判定（按人民币，1亿美元≈7亿人民币）
        revenue_billion_cny = ttm_revenue / 1e8
        if revenue_billion_cny > 70:  # 10亿美元
            scale_rating = "🟢 优秀（>10亿美元）"
        elif revenue_billion_cny > 7:  # 1亿美元
            scale_rating = "🟢 合格（1-10亿美元）"
        else:
            scale_rating = "🔴 不合格（<1亿美元）"

        # 成长性判定
        if growth_yoy is None:
            growth_rating = "数据不足"
        elif 15 <= growth_yoy <= 20:
            growth_rating = "🟢 理想区（15-20%）"
        elif 10 <= growth_yoy < 15 or 20 < growth_yoy <= 30:
            growth_rating = "🟡 健康（10-30%）"
        elif growth_yoy > 50:
            growth_rating = "🟠 过快（>50%，可持续性存疑）"
        elif growth_yoy < 10 and growth_yoy > 0:
            growth_rating = "🟡 偏低（动力不足）"
        elif growth_yoy < 0:
            growth_rating = "🔴 负增长"
        else:
            growth_rating = "🟢 健康"

        return {
            "code": code,
            "revenue_ttm_billion_cny": round(revenue_billion_cny, 2),
            "revenue_ttm_billion_usd": round(revenue_billion_cny / 7, 2),
            "scale_rating": scale_rating,
            "growth_yoy_pct": round(growth_yoy, 2) if growth_yoy is not None else None,
            "growth_rating": growth_rating,
        }
    except Exception as e:
        logger.error(f"获取{code}营收失败: {e}")
        return {"code": code, "error": str(e)}


@with_retry()
@with_cache(cache_type="quarterly")
def get_ebit_margin(code: str) -> dict:
    """
    第二类：运营利润率（EBIT margin）
    判定：> 0 健康，< 0 持续多季度 = 结构性亏损
    """
    try:
        symbol = f"SH{code}" if code.startswith("6") else f"SZ{code}"
        df = ak.stock_profit_sheet_by_quarterly_em(symbol=symbol)
        if df.empty:
            return {"code": code, "error": "利润表为空"}

        # 字段识别
        revenue_col = None
        for col in ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME"]:
            if col in df.columns:
                revenue_col = col
                break

        # 营业利润字段
        op_profit_col = None
        for col in ["OPERATE_PROFIT", "营业利润"]:
            if col in df.columns:
                op_profit_col = col
                break

        if not revenue_col or not op_profit_col:
            return {"code": code, "error": "字段不匹配"}

        # 计算近4季度EBIT margin
        recent_4q = df.head(4)
        margins = []
        for _, row in recent_4q.iterrows():
            rev = float(row[revenue_col]) if pd.notna(row[revenue_col]) else 0
            op = float(row[op_profit_col]) if pd.notna(row[op_profit_col]) else 0
            if rev > 0:
                margins.append({
                    "report_date": str(row.get("REPORT_DATE", row.get("报告期", "")))[:10],
                    "revenue_billion": round(rev / 1e8, 2),
                    "op_profit_billion": round(op / 1e8, 2),
                    "ebit_margin_pct": round(op / rev * 100, 2),
                })

        if not margins:
            return {"code": code, "error": "无有效季度数据"}

        avg_margin = sum(m["ebit_margin_pct"] for m in margins) / len(margins)

        # 判定
        negative_count = sum(1 for m in margins if m["ebit_margin_pct"] < 0)
        if negative_count >= 4:
            rating = "🔴 结构性亏损（连续≥4季EBIT<0）"
            warning = "⚠️ 商业模式可能存在根本性问题"
        elif negative_count >= 2:
            rating = "🟠 多季亏损（警戒）"
            warning = "需密切关注盈利能力修复"
        elif avg_margin < 5:
            rating = "🟡 临界（margin较低）"
            warning = "需与同业比较判定"
        elif avg_margin < 15:
            rating = "🟢 健康"
            warning = ""
        else:
            rating = "🟢 优秀（margin较高）"
            warning = ""

        return {
            "code": code,
            "recent_4q_margins": margins,
            "avg_ebit_margin_pct": round(avg_margin, 2),
            "negative_quarters": negative_count,
            "rating": rating,
            "warning": warning,
        }
    except Exception as e:
        logger.error(f"获取{code}EBIT失败: {e}")
        return {"code": code, "error": str(e)}


@with_retry()
@with_cache(cache_type="quarterly")
def get_free_cashflow_health(code: str) -> dict:
    """
    第三类：现金流（运营现金流必须为正，自由现金流最好为正）
    """
    try:
        symbol = f"SH{code}" if code.startswith("6") else f"SZ{code}"
        cf_df = ak.stock_cash_flow_sheet_by_quarterly_em(symbol=symbol)
        if cf_df.empty:
            return {"code": code, "error": "现金流量表为空"}

        recent_4q = cf_df.head(4)

        op_cf_col = None
        for col in ["NETCASH_OPERATE", "经营活动产生的现金流量净额"]:
            if col in recent_4q.columns:
                op_cf_col = col
                break

        capex_col = None
        for col in ["CONSTRUCT_LONG_ASSET", "购建固定资产、无形资产和其他长期资产支付的现金"]:
            if col in recent_4q.columns:
                capex_col = col
                break

        if not op_cf_col:
            return {"code": code, "error": "经营现金流字段缺失"}

        total_op_cf = sum(float(x) if pd.notna(x) else 0 for x in recent_4q[op_cf_col])
        total_capex = sum(float(x) if pd.notna(x) else 0 for x in recent_4q[capex_col]) if capex_col else 0
        fcf = total_op_cf - total_capex

        # 取市值（从 indicator 接口）
        try:
            ind_df = ak.stock_a_indicator_lg(symbol=code)
            mv_billion = float(ind_df.iloc[-1]["total_mv"]) / 1e4 if not ind_df.empty else None
        except Exception:
            mv_billion = None

        fcf_yield = (fcf / 1e8) / mv_billion * 100 if mv_billion else None

        # 综合判定
        op_cf_positive = total_op_cf > 0
        fcf_positive = fcf > 0

        if not op_cf_positive:
            rating = "🔴 高风险（运营现金流为负，不挣钱）"
            warning = "⚠️ 商业运营本身无法产生现金，结构性问题"
        elif not fcf_positive:
            rating = "🟡 警戒（运营现金流正，但 FCF 为负）"
            warning = "资本开支过大，需关注扩张是否过度"
        elif fcf_yield and fcf_yield > 5:
            rating = "🟢 优秀（FCF 收益率 > 5%）"
            warning = ""
        elif fcf_yield and fcf_yield > 0:
            rating = "🟢 合格（FCF 为正）"
            warning = ""
        else:
            rating = "🟡 一般"
            warning = ""

        return {
            "code": code,
            "operating_cashflow_ttm_billion": round(total_op_cf / 1e8, 2),
            "capex_ttm_billion": round(total_capex / 1e8, 2),
            "fcf_ttm_billion": round(fcf / 1e8, 2),
            "market_cap_billion": mv_billion,
            "fcf_yield_pct": round(fcf_yield, 2) if fcf_yield else None,
            "op_cf_positive": op_cf_positive,
            "fcf_positive": fcf_positive,
            "rating": rating,
            "warning": warning,
        }
    except Exception as e:
        logger.error(f"获取{code}现金流失败: {e}")
        return {"code": code, "error": str(e)}


# ============ 0.2 伟大公司四条件评估 ============

@with_retry()
@with_cache(cache_type="quarterly")
def evaluate_great_company(code: str, financial_data: dict = None) -> dict:
    """
    伟大公司四条件评估
    ① 无差别产品  ② 最广泛用户  ③ 自由定价  ④ 市场垄断
    
    定性条件无法完全自动化，但可以基于可量化指标给出参考评分。
    """
    indicators = {
        "criterion_1_undifferentiated_product": {
            "name": "无差别产品",
            "auto_assessment": None,
            "manual_review_needed": True,
            "evidence": "需人工/AI判断：产品对所有用户体验是否一致，标准化程度",
        },
        "criterion_2_broadest_users": {
            "name": "最广泛用户",
            "auto_assessment": None,
            "manual_review_needed": True,
            "evidence": "需人工/AI判断：用户基数（数千万到数十亿）",
        },
        "criterion_3_free_pricing": {
            "name": "自由定价",
            "auto_assessment": None,
            "manual_review_needed": False,
            "evidence": "可基于毛利率与同业比较推断",
        },
        "criterion_4_market_monopoly": {
            "name": "市场垄断",
            "auto_assessment": None,
            "manual_review_needed": True,
            "evidence": "需人工/AI判断：市占率/护城河",
        },
    }

    # ③ 自由定价的量化代理：高毛利率 = 一定的定价权
    if financial_data:
        ebit = financial_data.get("ebit", {})
        margin = ebit.get("avg_ebit_margin_pct")
        if margin is not None:
            if margin > 25:
                indicators["criterion_3_free_pricing"]["auto_assessment"] = True
                indicators["criterion_3_free_pricing"]["evidence"] = f"EBIT margin {margin}% 高 → 较强定价权"
            elif margin > 15:
                indicators["criterion_3_free_pricing"]["auto_assessment"] = True
                indicators["criterion_3_free_pricing"]["evidence"] = f"EBIT margin {margin}% 中等 → 一定定价权"
            else:
                indicators["criterion_3_free_pricing"]["auto_assessment"] = False
                indicators["criterion_3_free_pricing"]["evidence"] = f"EBIT margin {margin}% 低 → 价格压力大"

    # 获取个股基本信息（行业等）作为人工判断辅助
    try:
        info_df = ak.stock_individual_info_em(symbol=code)
        info_dict = dict(zip(info_df['item'], info_df['value'])) if not info_df.empty else {}
        industry = info_dict.get("行业", "未知")
        stock_name = info_dict.get("股票简称", "未知")
    except Exception:
        industry = "未知"
        stock_name = "未知"

    # 自动评估总数（仅 #3 可自动）
    auto_count = sum(1 for c in indicators.values() if c["auto_assessment"] is True)

    return {
        "code": code,
        "stock_name": stock_name,
        "industry": industry,
        "criteria": indicators,
        "auto_assessment_count": auto_count,
        "note": "完整四条件评估需结合定性判断（建议AI补全#1#2#4）",
    }


# ============ 0.4 故事股识别 ============

def detect_story_stock(financial_summary: dict) -> dict:
    """
    故事股识别清单（一票否决）
    """
    flags = {
        "结构性亏损": False,
        "无现金流": False,
        "高负债": False,  # 需补充负债数据
        "营收规模过小": False,
        "持续负增长": False,
    }
    triggers = []

    ebit = financial_summary.get("ebit", {})
    if ebit.get("negative_quarters", 0) >= 4:
        flags["结构性亏损"] = True
        triggers.append(f"EBIT 连续 {ebit.get('negative_quarters')} 季 < 0")

    cf = financial_summary.get("cashflow", {})
    if not cf.get("op_cf_positive", True):
        flags["无现金流"] = True
        triggers.append(f"运营现金流为负 ({cf.get('operating_cashflow_ttm_billion')}亿)")

    rev = financial_summary.get("revenue", {})
    if rev.get("revenue_ttm_billion_usd", 999) < 1:
        flags["营收规模过小"] = True
        triggers.append(f"年营收仅 {rev.get('revenue_ttm_billion_usd')}亿美元 (< 1亿门槛)")

    growth = rev.get("growth_yoy_pct")
    if growth is not None and growth < -10:
        flags["持续负增长"] = True
        triggers.append(f"营收同比下降 {growth}%")

    is_story_stock = any(flags.values())

    return {
        "is_story_stock": is_story_stock,
        "flags": flags,
        "trigger_reasons": triggers,
        "verdict": "🔴 故事股 — 一票否决,直接放弃" if is_story_stock else "🟢 非故事股 — 通过初筛",
    }


# ============ 主分析函数 ============

def hardcore_screen_stock(code: str) -> dict:
    """主入口：对单只股票执行 Step 0 完整预筛"""
    print_banner(f"Step 0 硬核预筛 | {code}", char="═")

    # 1. 财务硬核三类
    print("\n📊 正在获取财务数据...")
    revenue = get_revenue_and_growth(code)
    ebit = get_ebit_margin(code)
    cashflow = get_free_cashflow_health(code)

    financial_summary = {
        "revenue": revenue,
        "ebit": ebit,
        "cashflow": cashflow,
    }

    # 2. 故事股识别
    story_check = detect_story_stock(financial_summary)

    # 3. 伟大公司四条件
    great_company = evaluate_great_company(code, financial_summary)

    result = {
        "code": code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stock_name": great_company.get("stock_name"),
        "industry": great_company.get("industry"),
        "financial_hardcore": financial_summary,
        "story_stock_check": story_check,
        "great_company_evaluation": great_company,
    }

    # === 综合判定 ===
    if story_check["is_story_stock"]:
        overall_verdict = "🔴 不通过 Step 0 — 不分析此基金,建议放弃"
    elif (
        revenue.get("scale_rating", "").startswith("🔴") or
        ebit.get("rating", "").startswith("🔴") or
        cashflow.get("rating", "").startswith("🔴")
    ):
        overall_verdict = "🟠 严重警戒 — 多项硬核指标不达标,谨慎评估"
    elif (
        revenue.get("scale_rating", "").startswith("🟢") and
        ebit.get("rating", "").startswith("🟢") and
        cashflow.get("rating", "").startswith("🟢")
    ):
        overall_verdict = "🟢 通过 Step 0 — 进入完整分析流程"
    else:
        overall_verdict = "🟡 部分通过 — 需结合伟大公司评估和定性判断"

    result["overall_verdict"] = overall_verdict

    # === 打印 ===
    print(f"\n股票名称: {result['stock_name']}  行业: {result['industry']}")

    print(f"\n【财务硬核三类】")
    if "error" not in revenue:
        print(f"  ① 收入: {revenue.get('revenue_ttm_billion_cny')}亿人民币 ≈ {revenue.get('revenue_ttm_billion_usd')}亿美元")
        print(f"     规模: {revenue.get('scale_rating')}")
        print(f"     增速: {revenue.get('growth_yoy_pct')}%  → {revenue.get('growth_rating')}")
    if "error" not in ebit:
        print(f"  ② EBIT margin (近4季均): {ebit.get('avg_ebit_margin_pct')}%")
        print(f"     评级: {ebit.get('rating')}")
        if ebit.get("warning"):
            print(f"     ⚠️  {ebit['warning']}")
    if "error" not in cashflow:
        print(f"  ③ 经营现金流(TTM): {cashflow.get('operating_cashflow_ttm_billion')}亿")
        print(f"     自由现金流(TTM): {cashflow.get('fcf_ttm_billion')}亿")
        print(f"     FCF 收益率: {cashflow.get('fcf_yield_pct')}%")
        print(f"     评级: {cashflow.get('rating')}")
        if cashflow.get("warning"):
            print(f"     ⚠️  {cashflow['warning']}")

    print(f"\n【故事股清单检查】")
    for flag, triggered in story_check["flags"].items():
        mark = "🔴" if triggered else "🟢"
        print(f"  {mark} {flag}: {'是' if triggered else '否'}")
    if story_check["trigger_reasons"]:
        print(f"\n  触发原因:")
        for r in story_check["trigger_reasons"]:
            print(f"    · {r}")
    print(f"\n  判定: {story_check['verdict']}")

    print(f"\n【伟大公司四条件】（仅 #3 可自动评估）")
    for k, c in great_company["criteria"].items():
        if c["auto_assessment"] is True:
            mark = "✅"
        elif c["auto_assessment"] is False:
            mark = "❌"
        else:
            mark = "❓"
        print(f"  {mark} {c['name']}: {c['evidence']}")
    print(f"\n  注: {great_company['note']}")

    print(f"\n{'═' * 60}")
    print(f"  Step 0 综合判定: {overall_verdict}")
    print(f"{'═' * 60}")

    save_path = save_result(result, f"hardcore_{code}", subdir="10_hardcore")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


def screen_fund_top_holdings(fund_code: str) -> dict:
    """对某只基金的前 3 大重仓股执行硬核预筛"""
    print_banner(f"基金重仓股硬核预筛 | {fund_code}", char="═")

    # 动态加载 02 脚本获取持仓
    holdings_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_fund_holdings.py")
    spec = importlib.util.spec_from_file_location("holdings_mod", holdings_script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    holdings_df = mod.get_fund_holdings(fund_code)
    if holdings_df.empty:
        print("⚠️  未获取到持仓数据")
        return {"fund_code": fund_code, "error": "无持仓数据"}

    parsed = mod.parse_latest_two_quarters(holdings_df)
    top_holdings = parsed.get("top10_holdings", [])[:3]

    if not top_holdings:
        return {"fund_code": fund_code, "error": "无重仓股"}

    print(f"\n基金 {fund_code} 前 3 大重仓股:")
    for i, h in enumerate(top_holdings, 1):
        print(f"  {i}. {h['code']} {h['name']} (占比 {h['ratio']}%)")

    # 逐一硬核预筛
    results = {}
    pass_count = 0
    fail_count = 0
    warn_count = 0

    for h in top_holdings:
        code = h["code"]
        name = h["name"]
        try:
            stock_result = hardcore_screen_stock(code)
            results[code] = {
                "name": name,
                "ratio": h["ratio"],
                "verdict": stock_result["overall_verdict"],
                "is_story_stock": stock_result["story_stock_check"]["is_story_stock"],
                "details": stock_result,
            }
            if "🟢" in stock_result["overall_verdict"]:
                pass_count += 1
            elif "🔴" in stock_result["overall_verdict"]:
                fail_count += 1
            else:
                warn_count += 1
        except Exception as e:
            results[code] = {"name": name, "error": str(e)}

    # === 基金级别综合判定 ===
    print_banner(f"基金 {fund_code} 综合判定", char="═")

    if fail_count >= 2:
        fund_verdict = "🔴 基金不通过 Step 0 — 多只重仓股是故事股,建议放弃"
    elif pass_count >= 2:
        fund_verdict = "🟢 基金通过 Step 0 — 重仓股质量较高"
    else:
        fund_verdict = "🟡 基金部分通过 — 需结合定性评估"

    print(f"\n  通过: {pass_count}/3   警戒: {warn_count}/3   不通过: {fail_count}/3")
    print(f"  基金综合判定: {fund_verdict}")

    final_result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "top3_holdings_screening": results,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "fund_verdict": fund_verdict,
    }

    save_path = save_result(final_result, f"fund_hardcore_{fund_code}", subdir="10_hardcore")
    print(f"\n✅ 基金硬核预筛已保存: {save_path}")
    return final_result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：")
        print("  python 10_hardcore_screening.py <股票代码>      # 单只股票")
        print("  python 10_hardcore_screening.py --fund <基金代码>  # 基金前3重仓股")
        print("\n示例：")
        print("  python 10_hardcore_screening.py 600519")
        print("  python 10_hardcore_screening.py --fund 001938")
        sys.exit(1)

    if sys.argv[1] == "--fund":
        if len(sys.argv) < 3:
            print("缺少基金代码")
            sys.exit(1)
        screen_fund_top_holdings(sys.argv[2])
    else:
        hardcore_screen_stock(sys.argv[1])
