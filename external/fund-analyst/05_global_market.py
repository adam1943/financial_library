"""
05_global_market.py —— 全球市场数据（对应 v4.0 skill 1.5 节）
==============================================================
获取字段：
  · 纳斯达克100 / 标普500 PE分位、涨跌
  · 恒生科技 / 恒生互联网 PE分位、AH溢价指数
  · 美元/人民币、港币/人民币
  · 美联储利率、10Y美债收益率
  · VIX恐慌指数

用法：
    python 05_global_market.py
"""

import akshare as ak
import pandas as pd
from datetime import datetime

from config import get_logger, with_retry, with_cache, save_result, print_banner

logger = get_logger(__name__)


@with_retry()
@with_cache(cache_type="daily")
def get_us_indices() -> dict:
    """获取美股主要指数"""
    result = {}
    try:
        # 纳斯达克100
        ndx = ak.index_us_stock_sina(symbol=".NDX")
        if not ndx.empty:
            latest = ndx.iloc[-1]
            d30 = ndx.iloc[-22] if len(ndx) >= 22 else ndx.iloc[0]
            result["纳斯达克100"] = {
                "latest_close": float(latest["close"]),
                "date": str(latest["date"]),
                "return_30d_pct": round((float(latest["close"]) / float(d30["close"]) - 1) * 100, 2),
            }
    except Exception as e:
        logger.warning(f"纳指100获取失败: {e}")

    try:
        # 标普500
        spx = ak.index_us_stock_sina(symbol=".INX")
        if not spx.empty:
            latest = spx.iloc[-1]
            d30 = spx.iloc[-22] if len(spx) >= 22 else spx.iloc[0]
            result["标普500"] = {
                "latest_close": float(latest["close"]),
                "date": str(latest["date"]),
                "return_30d_pct": round((float(latest["close"]) / float(d30["close"]) - 1) * 100, 2),
            }
    except Exception as e:
        logger.warning(f"标普500获取失败: {e}")

    try:
        # 道琼斯
        dji = ak.index_us_stock_sina(symbol=".DJI")
        if not dji.empty:
            latest = dji.iloc[-1]
            d30 = dji.iloc[-22] if len(dji) >= 22 else dji.iloc[0]
            result["道琼斯"] = {
                "latest_close": float(latest["close"]),
                "date": str(latest["date"]),
                "return_30d_pct": round((float(latest["close"]) / float(d30["close"]) - 1) * 100, 2),
            }
    except Exception as e:
        logger.warning(f"道琼斯获取失败: {e}")

    return result


@with_retry()
@with_cache(cache_type="daily")
def get_hk_indices() -> dict:
    """港股主要指数"""
    result = {}
    try:
        # 恒生指数
        hsi = ak.stock_hk_index_daily_sina(symbol="HSI")
        if not hsi.empty:
            hsi = hsi.sort_values("date").tail(60)
            latest = hsi.iloc[-1]
            d30 = hsi.iloc[-22] if len(hsi) >= 22 else hsi.iloc[0]
            result["恒生指数"] = {
                "latest_close": float(latest["close"]),
                "date": str(latest["date"]),
                "return_30d_pct": round((float(latest["close"]) / float(d30["close"]) - 1) * 100, 2),
            }
    except Exception as e:
        logger.warning(f"恒生指数获取失败: {e}")

    try:
        # 恒生科技
        hst = ak.stock_hk_index_daily_sina(symbol="HSTECH")
        if not hst.empty:
            hst = hst.sort_values("date").tail(60)
            latest = hst.iloc[-1]
            d30 = hst.iloc[-22] if len(hst) >= 22 else hst.iloc[0]
            result["恒生科技"] = {
                "latest_close": float(latest["close"]),
                "date": str(latest["date"]),
                "return_30d_pct": round((float(latest["close"]) / float(d30["close"]) - 1) * 100, 2),
            }
    except Exception as e:
        logger.warning(f"恒生科技获取失败: {e}")

    return result


@with_retry()
@with_cache(cache_type="daily")
def get_exchange_rates() -> dict:
    """获取汇率"""
    result = {}
    try:
        # 美元/人民币中间价
        usd_cny = ak.currency_latest(symbol="USDCNY")
        result["美元/人民币"] = {
            "latest_rate": float(usd_cny) if usd_cny else None,
        }
    except Exception:
        try:
            df = ak.currency_boc_sina(symbol="USDCNY")
            if not df.empty:
                latest = df.iloc[-1]
                result["美元/人民币"] = {
                    "latest_rate": float(latest.iloc[-1]),
                    "date": str(latest.iloc[0]),
                }
        except Exception as e:
            logger.warning(f"美元/人民币汇率获取失败: {e}")

    try:
        hkd_cny = ak.currency_boc_sina(symbol="HKDCNY")
        if not hkd_cny.empty:
            latest = hkd_cny.iloc[-1]
            result["港币/人民币"] = {
                "latest_rate": float(latest.iloc[-1]),
                "date": str(latest.iloc[0]),
            }
    except Exception as e:
        logger.warning(f"港币/人民币汇率获取失败: {e}")

    return result


@with_retry()
@with_cache(cache_type="daily")
def get_us_treasury_yield() -> dict:
    """美债收益率"""
    try:
        df = ak.bond_zh_us_rate()
        if not df.empty:
            df = df.sort_values("日期").tail(5)
            latest = df.iloc[-1]
            # 取10Y美债
            y10_col = None
            for col in ["美国国债收益率10年", "美国国债收益率10年期", "10年期"]:
                if col in df.columns:
                    y10_col = col
                    break

            if y10_col:
                y10_latest = float(latest[y10_col]) if pd.notna(latest[y10_col]) else None
                return {
                    "10Y_yield": y10_latest,
                    "date": str(latest["日期"]),
                    "interpretation": (
                        "高位（>4.5%，压制成长股）" if y10_latest and y10_latest > 4.5 else
                        "中性（3.5-4.5%）" if y10_latest and y10_latest >= 3.5 else
                        "低位（<3.5%，利好成长股）" if y10_latest else "未知"
                    ),
                }
    except Exception as e:
        logger.warning(f"美债收益率获取失败: {e}")
    return {"error": "美债数据获取失败"}


@with_retry()
@with_cache(cache_type="daily")
def get_vix() -> dict:
    """VIX恐慌指数"""
    try:
        df = ak.index_us_stock_sina(symbol=".VIX")
        if not df.empty:
            df = df.sort_values("date").tail(30)
            latest = df.iloc[-1]
            vix_val = float(latest["close"])

            if vix_val > 30:
                sentiment = "🔴 恐慌 (VIX>30)"
            elif vix_val > 20:
                sentiment = "🟠 紧张 (20-30)"
            elif vix_val > 15:
                sentiment = "🟡 正常 (15-20)"
            else:
                sentiment = "🟢 贪婪 (<15)"

            return {
                "vix": vix_val,
                "date": str(latest["date"]),
                "avg_30d": round(float(df["close"].mean()), 2),
                "sentiment": sentiment,
            }
    except Exception as e:
        logger.warning(f"VIX获取失败: {e}")
    return {"error": "VIX数据获取失败"}


@with_retry()
@with_cache(cache_type="daily")
def get_ah_premium() -> dict:
    """AH溢价指数"""
    try:
        # 恒生AH股溢价指数
        df = ak.stock_zh_ah_spot()
        if not df.empty:
            # 计算平均溢价
            premium_col = None
            for col in ["比价(A/H)", "溢价率", "H股溢价"]:
                if col in df.columns:
                    premium_col = col
                    break

            if premium_col:
                avg_premium = df[premium_col].mean()
                return {
                    "avg_premium": round(float(avg_premium), 2),
                    "total_stocks": len(df),
                    "interpretation": (
                        "港股极度便宜（>140）" if avg_premium > 140 else
                        "港股便宜（130-140）" if avg_premium > 130 else
                        "正常（120-130）" if avg_premium > 120 else
                        "溢价收窄（<120）"
                    ),
                }
    except Exception as e:
        logger.warning(f"AH溢价获取失败: {e}")

    # 备用：直接获取AH溢价指数
    try:
        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        ah_row = df[df["名称"].str.contains("AH", na=False)] if "名称" in df.columns else None
        if ah_row is not None and not ah_row.empty:
            return {
                "ah_index": float(ah_row.iloc[0]["最新价"]) if "最新价" in ah_row.columns else None,
            }
    except Exception:
        pass

    return {"error": "AH溢价数据获取失败"}


def analyze_global_market() -> dict:
    """主入口"""
    print_banner("全球市场数据获取")

    result = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "us_indices": get_us_indices(),
        "hk_indices": get_hk_indices(),
        "exchange_rates": get_exchange_rates(),
        "us_treasury": get_us_treasury_yield(),
        "vix": get_vix(),
        "ah_premium": get_ah_premium(),
    }

    # 打印摘要
    print("\n【美股指数】")
    for name, info in result["us_indices"].items():
        if "latest_close" in info:
            print(f"  {name}: {info['latest_close']:.2f}  近30日: {info['return_30d_pct']}%")

    print("\n【港股指数】")
    for name, info in result["hk_indices"].items():
        if "latest_close" in info:
            print(f"  {name}: {info['latest_close']:.2f}  近30日: {info['return_30d_pct']}%")

    print("\n【汇率】")
    for name, info in result["exchange_rates"].items():
        if "latest_rate" in info:
            print(f"  {name}: {info['latest_rate']}")

    print("\n【美债10Y】")
    t = result["us_treasury"]
    if "10Y_yield" in t:
        print(f"  收益率: {t['10Y_yield']}%  {t['interpretation']}")

    print("\n【VIX恐慌指数】")
    v = result["vix"]
    if "vix" in v:
        print(f"  当前: {v['vix']}  30日均: {v['avg_30d']}  情绪: {v['sentiment']}")

    print("\n【AH溢价】")
    a = result["ah_premium"]
    if "avg_premium" in a:
        print(f"  平均溢价: {a['avg_premium']}  {a['interpretation']}")

    save_path = save_result(result, "global_market", subdir="05_global")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    analyze_global_market()
