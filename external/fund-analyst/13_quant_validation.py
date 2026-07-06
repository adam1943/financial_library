"""
13_quant_validation.py —— v6.1 量化验证闸门
================================================
借鉴 Qlib / Lean / vectorbt / Freqtrade 的共性做法：
  · 买入信号必须经过回测验证
  · 纳入交易成本与止损执行
  · 检查最大回撤、夏普、Calmar、月度胜率
  · 做滚动窗口稳定性验证，避免只在某一段行情有效

用法：
    python 13_quant_validation.py <基金代码> [normal|neutral|downtrend]
    python 13_quant_validation.py 001938 normal
"""

import sys
from datetime import datetime

import akshare as ak
import numpy as np
import pandas as pd

from config import (
    calc_stop_prices,
    get_logger,
    get_risk_profile,
    save_result,
    print_banner,
    with_cache,
    with_retry,
)

logger = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@with_retry()
@with_cache(cache_type="daily")
def get_fund_nav(fund_code: str, years: int = 5) -> pd.DataFrame:
    """获取基金单位净值或累计净值走势。"""
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df.empty:
            df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        if df.empty:
            return pd.DataFrame()
        df["净值日期"] = pd.to_datetime(df["净值日期"])
        df = df.sort_values("净值日期").reset_index(drop=True)
        cutoff = datetime.now() - pd.Timedelta(days=years * 365)
        return df[df["净值日期"] >= cutoff].copy()
    except Exception as exc:
        logger.error(f"获取基金{fund_code}净值失败: {exc}")
        return pd.DataFrame()


def prepare_nav(nav_df: pd.DataFrame) -> pd.DataFrame:
    """标准化净值数据并计算基础指标。"""
    if nav_df.empty:
        return pd.DataFrame()
    nav_col = "单位净值" if "单位净值" in nav_df.columns else "累计净值"
    df = nav_df[["净值日期", nav_col]].copy()
    df = df.rename(columns={"净值日期": "date", nav_col: "nav"})
    df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    df["ret"] = df["nav"].pct_change().fillna(0)
    df["ma20"] = df["nav"].rolling(20).mean()
    df["ma60"] = df["nav"].rolling(60).mean()
    df["ma120"] = df["nav"].rolling(120).mean()
    df["ma60_slope_10d"] = df["ma60"] / df["ma60"].shift(10) - 1
    df["ret20"] = df["nav"] / df["nav"].shift(20) - 1
    df["vol20_ann"] = df["ret"].rolling(20).std() * np.sqrt(252)
    return df


def max_drawdown(equity: pd.Series) -> dict:
    """计算最大回撤及持续天数。"""
    if equity.empty:
        return {"max_drawdown_pct": 0, "max_drawdown_days": 0}
    running_max = equity.cummax()
    dd = equity / running_max - 1
    max_dd = float(dd.min())
    end_idx = int(dd.idxmin()) if len(dd) else 0
    peak_idx = int(equity.loc[:end_idx].idxmax()) if end_idx > 0 else 0
    days = end_idx - peak_idx
    return {
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_days": int(days),
    }


def performance_metrics(df: pd.DataFrame, equity_col: str) -> dict:
    """计算常用策略评价指标。"""
    if df.empty or equity_col not in df.columns:
        return {"error": "数据不足"}

    equity = df[equity_col].astype(float)
    returns = equity.pct_change().fillna(0)
    drawdown_series = equity / equity.cummax() - 1
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    years = max((df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25, 1 / 365.25)
    ann_return = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0
    downside = returns[returns < 0].std() * np.sqrt(252)
    sortino = ann_return / downside if downside and downside > 0 else 0
    dd = max_drawdown(equity)
    calmar = ann_return / abs(dd["max_drawdown_pct"] / 100) if dd["max_drawdown_pct"] < 0 else 0

    monthly = df.set_index("date")[equity_col].resample("ME").last().pct_change().dropna()
    monthly_win_rate = (monthly > 0).mean() * 100 if len(monthly) else 0
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = gains / losses if losses > 0 else np.inf
    omega_losses = abs(returns[returns <= 0].sum())
    omega = gains / omega_losses if omega_losses > 0 else np.inf
    ulcer_index = np.sqrt(np.mean(np.square(drawdown_series))) * 100
    pain_index = abs(drawdown_series).mean() * 100
    var_5d_95 = np.percentile(returns.dropna(), 5) * np.sqrt(5) if len(returns.dropna()) else 0

    return {
        "total_return_pct": round(total_return * 100, 2),
        "annual_return_pct": round(ann_return * 100, 2),
        "annual_volatility_pct": round(ann_vol * 100, 2),
        "sharpe": round(float(sharpe), 2),
        "sortino": round(float(sortino), 2),
        "calmar": round(float(calmar), 2),
        "ulcer_index_pct": round(float(ulcer_index), 2),
        "pain_index_pct": round(float(pain_index), 2),
        "omega": round(float(omega), 2) if np.isfinite(omega) else "inf",
        "monthly_win_rate_pct": round(float(monthly_win_rate), 2),
        "profit_factor": round(float(profit_factor), 2) if np.isfinite(profit_factor) else "inf",
        "var_5d_95_pct": round(float(var_5d_95) * 100, 2),
        **dd,
    }


def backtest_risk_control_strategy(
    df: pd.DataFrame,
    market_state: str = "normal",
    fee_rate: float = 0.002,
) -> pd.DataFrame:
    """
    趋势-止损组合策略。

    使用上一交易日信号决定下一交易日是否持仓，尽量避免未来函数。
    """
    if len(df) < 80:
        return pd.DataFrame()

    risk_profile = get_risk_profile(market_state)
    stop_policy = calc_stop_prices(1.0, risk_profile["state"])
    hard_stop = stop_policy["hard_stop_pct"] / 100
    trailing_buffer = risk_profile["trailing_buffer_pct"] / 100

    bt = df.copy().reset_index(drop=True)
    bt["raw_entry"] = (
        (bt["nav"] > bt["ma60"])
        & (bt["ma20"] > bt["ma60"])
        & (bt["ma60_slope_10d"] > 0)
        & (bt["ret20"] > 0)
    )
    bt["raw_exit"] = (
        (bt["nav"] < bt["ma60"])
        | (bt["ma20"] < bt["ma60"])
        | (bt["ma60_slope_10d"] < -0.01)
    )

    equity = 1.0
    buy_hold_equity = 1.0
    in_position = False
    entry_price = None
    peak_price = None
    position = []
    equity_curve = []
    bh_curve = []
    trades = []

    for i, row in bt.iterrows():
        if i > 0:
            buy_hold_equity *= 1 + float(row["ret"])
            if in_position:
                equity *= 1 + float(row["ret"])

        price = float(row["nav"])
        if in_position:
            peak_price = max(peak_price, price)
            entry_dd = price / entry_price - 1
            trailing_dd = price / peak_price - 1
            prev_exit = bool(bt.loc[i - 1, "raw_exit"]) if i > 0 else False
            stop_hit = entry_dd <= hard_stop or trailing_dd <= -trailing_buffer
            if prev_exit or stop_hit:
                equity *= 1 - fee_rate
                trades.append({
                    "date": str(row["date"].date()),
                    "type": "sell",
                    "price": price,
                    "reason": "stop" if stop_hit else "trend_exit",
                })
                in_position = False
                entry_price = None
                peak_price = None

        if not in_position and i > 0 and bool(bt.loc[i - 1, "raw_entry"]):
            equity *= 1 - fee_rate
            in_position = True
            entry_price = price
            peak_price = price
            trades.append({
                "date": str(row["date"].date()),
                "type": "buy",
                "price": price,
                "reason": "trend_entry",
            })

        position.append(1 if in_position else 0)
        equity_curve.append(equity)
        bh_curve.append(buy_hold_equity)

    bt["strategy_equity"] = equity_curve
    bt["buy_hold_equity"] = bh_curve
    bt["position"] = position
    bt.attrs["trades"] = trades
    bt.attrs["fee_rate"] = fee_rate
    bt.attrs["market_state"] = risk_profile["state"]
    return bt


def rolling_stability(df: pd.DataFrame, market_state: str = "normal", window: int = 252) -> dict:
    """按滚动窗口检查策略是否只在个别年份有效。"""
    if len(df) < window:
        return {
            "windows": 0,
            "positive_window_rate_pct": 0,
            "worst_window_return_pct": None,
            "worst_window_drawdown_pct": None,
            "note": "数据不足，无法做滚动稳定性验证",
        }

    records = []
    step = max(window // 2, 60)
    for start in range(0, len(df) - window + 1, step):
        seg = df.iloc[start:start + window].reset_index(drop=True)
        bt = backtest_risk_control_strategy(seg, market_state=market_state)
        if bt.empty:
            continue
        metrics = performance_metrics(bt, "strategy_equity")
        records.append(metrics)

    if not records:
        return {
            "windows": 0,
            "positive_window_rate_pct": 0,
            "worst_window_return_pct": None,
            "worst_window_drawdown_pct": None,
            "note": "滚动窗口回测失败",
        }

    returns = [r["total_return_pct"] for r in records]
    drawdowns = [r["max_drawdown_pct"] for r in records]
    return {
        "windows": len(records),
        "positive_window_rate_pct": round(sum(r > 0 for r in returns) / len(returns) * 100, 2),
        "worst_window_return_pct": round(min(returns), 2),
        "worst_window_drawdown_pct": round(min(drawdowns), 2),
        "window_metrics": records,
    }


def multi_risk_flags(metrics: dict) -> list:
    """v6.3 多维风险画像：识别 Sharpe/最大回撤之外的持有痛感。"""
    flags = []

    def val(key: str, default: float = 0.0) -> float:
        try:
            return float(metrics.get(key, default))
        except (TypeError, ValueError):
            return default

    if val("sortino") < 0.5:
        flags.append("Sortino<0.5（下行风险收益不足）")
    if val("calmar") < 0.2:
        flags.append("Calmar<0.2（收益/回撤比偏弱）")
    if val("ulcer_index_pct") > 15:
        flags.append("Ulcer>15%（深回撤或长时间回撤压力大）")
    if val("pain_index_pct") > 10:
        flags.append("Pain>10%（平均持有痛感偏高）")
    if val("omega") < 1.0:
        flags.append("Omega<1.0（正收益补偿不足）")
    return flags


def build_validation_gate(strategy: dict, buy_hold: dict, stability: dict, recent_vol_pct: float) -> dict:
    """将回测指标转换成买入闸门。"""
    score = 0
    reasons = []
    risk_flags = multi_risk_flags(strategy)

    max_dd = abs(strategy.get("max_drawdown_pct", 0))
    bh_dd = abs(buy_hold.get("max_drawdown_pct", 0))
    if max_dd <= 8:
        score += 30
        reasons.append("最大回撤≤8%")
    elif max_dd <= 12:
        score += 20
        reasons.append("最大回撤≤12%")
    elif bh_dd and max_dd <= bh_dd * 0.7:
        score += 15
        reasons.append("回撤显著低于买入持有")
    else:
        reasons.append("最大回撤控制不足")

    if strategy.get("calmar", 0) >= 0.8 and strategy.get("sharpe", 0) >= 0.8:
        score += 25
        reasons.append("Calmar与Sharpe均较健康")
    elif strategy.get("calmar", 0) >= 0.4 or strategy.get("sharpe", 0) >= 0.5:
        score += 15
        reasons.append("风险收益比基本合格")
    else:
        reasons.append("风险收益比偏弱")

    if strategy.get("max_drawdown_pct", 0) > buy_hold.get("max_drawdown_pct", 0):
        score += 15
        reasons.append("策略回撤优于买入持有")
    if strategy.get("total_return_pct", 0) >= 0:
        score += 5
        reasons.append("策略总收益为正")

    positive_rate = stability.get("positive_window_rate_pct", 0)
    if positive_rate >= 70:
        score += 15
        reasons.append("滚动窗口稳定性较好")
    elif positive_rate >= 50:
        score += 8
        reasons.append("滚动窗口稳定性一般")
    else:
        reasons.append("滚动窗口稳定性不足")

    if recent_vol_pct <= 18:
        score += 10
        reasons.append("近期波动率可控")
    elif recent_vol_pct <= 28:
        score += 5
        reasons.append("近期波动率偏高但可观察")
    else:
        reasons.append("近期波动率过高")

    if risk_flags:
        penalty = min(20, len(risk_flags) * 6)
        score = max(0, score - penalty)
        reasons.append(f"多维风险扣分-{penalty}: " + "；".join(risk_flags))
    if len(risk_flags) >= 3 and score >= 55:
        score = 54
        reasons.append("三项以上多维风险触发，量化闸门强制降为不通过")

    if score >= 75:
        decision = "🟢 量化验证通过"
        action = "允许进入最终买入决策，但仍需大盘/重仓股/情绪闸门同时通过"
    elif score >= 55:
        decision = "🟡 量化验证半通过"
        action = "只能轻仓或观察，仓位上限减半"
    else:
        decision = "🔴 量化验证不通过"
        action = "禁止新买入，等待策略稳定性或趋势修复"

    return {
        "score": round(score, 1),
        "decision": decision,
        "action": action,
        "reasons": reasons,
        "multi_risk_flags": risk_flags,
    }


def analyze_quant_validation(fund_code: str, market_state: str = "normal") -> dict:
    """主入口：执行 v6.3 量化验证闸门。"""
    print_banner(f"v6.3 多维量化验证闸门 | {fund_code}", char="═")
    nav_df = get_fund_nav(fund_code, years=5)
    df = prepare_nav(nav_df)
    if df.empty or len(df) < 120:
        result = {
            "fund_code": fund_code,
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": "净值数据不足，无法回测",
        }
        print("⚠️  净值数据不足，无法执行量化验证")
        return result

    bt = backtest_risk_control_strategy(df, market_state=market_state)
    strategy_metrics = performance_metrics(bt, "strategy_equity")
    buy_hold_metrics = performance_metrics(bt, "buy_hold_equity")
    stability = rolling_stability(df, market_state=market_state)
    recent_vol = float(df["vol20_ann"].iloc[-1] * 100) if pd.notna(df["vol20_ann"].iloc[-1]) else 0
    gate = build_validation_gate(strategy_metrics, buy_hold_metrics, stability, recent_vol)

    result = {
        "fund_code": fund_code,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_points": len(df),
        "market_state": get_risk_profile(market_state)["state"],
        "fee_rate": bt.attrs.get("fee_rate", 0.002),
        "strategy_metrics": strategy_metrics,
        "buy_hold_metrics": buy_hold_metrics,
        "rolling_stability": stability,
        "recent_20d_annual_volatility_pct": round(recent_vol, 2),
        "trade_count": len(bt.attrs.get("trades", [])),
        "recent_trades": bt.attrs.get("trades", [])[-6:],
        "validation_gate": gate,
        "method_note": "使用趋势-止损组合规则做保守回测，并叠加Sortino/Ulcer/Pain/Omega多维风险画像；结果只用于过滤脆弱信号，不保证未来收益",
    }

    print(f"\n【回测样本】{len(df)} 个净值点  交易成本: {result['fee_rate']*100:.2f}%/次")
    print("\n【策略表现】")
    print(f"  总收益: {strategy_metrics['total_return_pct']}%  年化: {strategy_metrics['annual_return_pct']}%")
    print(f"  最大回撤: {strategy_metrics['max_drawdown_pct']}%  Sharpe: {strategy_metrics['sharpe']}  Calmar: {strategy_metrics['calmar']}")
    print(f"  多维风险: Sortino {strategy_metrics['sortino']}  Ulcer {strategy_metrics['ulcer_index_pct']}%  Pain {strategy_metrics['pain_index_pct']}%  Omega {strategy_metrics['omega']}")
    print(f"  月度胜率: {strategy_metrics['monthly_win_rate_pct']}%  5日VaR(95%): {strategy_metrics['var_5d_95_pct']}%")
    print("\n【买入持有对比】")
    print(f"  总收益: {buy_hold_metrics['total_return_pct']}%  最大回撤: {buy_hold_metrics['max_drawdown_pct']}%")
    print("\n【滚动稳定性】")
    print(f"  窗口数: {stability['windows']}  正收益窗口: {stability['positive_window_rate_pct']}%")
    print(f"  最差窗口收益: {stability['worst_window_return_pct']}%  最差窗口回撤: {stability['worst_window_drawdown_pct']}%")
    print(f"\n【验证结论】{gate['decision']}  得分 {gate['score']}/100")
    print(f"  动作: {gate['action']}")
    for reason in gate["reasons"]:
        print(f"  · {reason}")

    save_path = save_result(result, f"quant_validation_{fund_code}", subdir="13_quant_validation")
    print(f"\n✅ 结果已保存: {save_path}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 13_quant_validation.py <基金代码> [normal|neutral|downtrend]")
        print("示例: python 13_quant_validation.py 001938 normal")
        sys.exit(1)

    code = sys.argv[1]
    state = sys.argv[2] if len(sys.argv) >= 3 else "normal"
    analyze_quant_validation(code, state)
