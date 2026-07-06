#!/usr/bin/env python3
"""Run the optional fund analyst adapter and write normalized JSON output."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
KB_DIR = ROOT / "knowledge_base"
DEFAULT_EXTERNAL_DIR = ROOT / "external" / "fund-analyst"
HOLDINGS_PATH = KB_DIR / "input" / "portfolio_holdings.csv"


def load_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def normalize_fund_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(6) if digits else ""


def read_portfolio_funds(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    funds: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            code = normalize_fund_code(row.get("symbol"))
            asset_type = str(row.get("asset_type") or "").lower()
            provider = str(row.get("provider") or "").lower()
            raw_symbol = str(row.get("symbol") or "").lower()
            if not code:
                continue
            is_fund = asset_type in {"fund", "基金", "etf", "lof"} or provider == "fundgz"
            is_exchange_etf = raw_symbol.startswith(("sh", "sz"))
            if is_fund and not is_exchange_etf:
                funds.append({"fund_code": code, "fund_name": str(row.get("name") or code)})
    seen: set[str] = set()
    result = []
    for fund in funds:
        if fund["fund_code"] in seen:
            continue
        seen.add(fund["fund_code"])
        result.append(fund)
    return result


def trim_buy_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    return {
        "macro_action": plan.get("macro_action"),
        "observation_position": plan.get("observation_position"),
        "small_pullback": plan.get("small_pullback"),
        "healthy_pullback": plan.get("healthy_pullback"),
        "stop_rules": (plan.get("stop_rules") or [])[:3],
        "take_profit_rules": (plan.get("take_profit_rules") or [])[:3],
    }


def normalize_strong_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fund_code": row.get("fund_code"),
        "fund_name": row.get("fund_name"),
        "rank_type": row.get("rank_type"),
        "rank_date": row.get("rank_date"),
        "latest_date": row.get("latest_date"),
        "latest_nav": row.get("latest_nav"),
        "r1m": row.get("r1m"),
        "r3m": row.get("r3m"),
        "r6m": row.get("r6m"),
        "r1y": row.get("r1y"),
        "rank_pct_1m": row.get("1m_rank_pct"),
        "rank_pct_3m": row.get("3m_rank_pct"),
        "rank_pct_6m": row.get("6m_rank_pct"),
        "maxdd_1m": row.get("1m_maxdd_pct"),
        "maxdd_3m": row.get("3m_maxdd_pct"),
        "maxdd_6m": row.get("6m_maxdd_pct"),
        "sharpe_1m": row.get("1m_sharpe"),
        "sharpe_3m": row.get("3m_sharpe"),
        "sharpe_6m": row.get("6m_sharpe"),
        "above_ma10": row.get("above_MA10"),
        "above_ma20": row.get("above_MA20"),
        "above_ma60": row.get("above_MA60"),
        "purchase_status": row.get("申购状态"),
        "purchase_executable": row.get("purchase_executable"),
        "daily_limit": row.get("日累计限定金额"),
        "strong_fund_score": row.get("strong_fund_score"),
        "buy_plan": trim_buy_plan(row.get("v624_buy_plan")),
    }


def summarize_drawdown(reference: dict[str, Any]) -> dict[str, Any]:
    gate = reference.get("four_dimension_gate") or {}
    strength = reference.get("recent_strength") or {}
    profile = reference.get("drawdown_profile") or {}
    rows = reference.get("rows") or []
    compact_rows = []
    for row in rows:
        compact_rows.append({
            "period": row.get("period"),
            "return_pct": row.get("return_pct"),
            "peer_rank": row.get("peer_rank"),
            "peer_total": row.get("peer_total"),
            "peer_rank_pct": row.get("peer_rank_pct"),
            "peer_avg_pct": row.get("peer_avg_pct"),
            "max_drawdown_pct": row.get("max_drawdown_pct"),
            "four_dimension_pass": row.get("four_dimension_pass"),
            "recent_strength_pass": row.get("recent_strength_pass"),
        })
    return {
        "fund_code": reference.get("fund_code"),
        "latest_nav_date": reference.get("latest_nav_date"),
        "source": reference.get("source"),
        "recent_strength_pass": strength.get("passed"),
        "recent_strength_reasons": strength.get("reasons") or [],
        "four_dimension_pass": gate.get("passed"),
        "four_dimension_reasons": gate.get("reasons") or [],
        "drawdown_profile": {
            "total_drawdowns": profile.get("total_drawdowns"),
            "avg_recovery_days": profile.get("avg_recovery_days"),
            "worst_drawdown_pct": profile.get("worst_drawdown_pct"),
        },
        "rows": compact_rows,
    }


def run(config_path: Path, output_dir: Path, external_dir: Path) -> dict[str, Any]:
    config = read_json(config_path, {})
    fund_cfg = config.get("fund_analyst") or {}
    top = int(fund_cfg.get("strong_top", 8))
    max_candidates = int(fund_cfg.get("strong_max_candidates", 24))
    macro_state = str(fund_cfg.get("macro_state", "normal"))
    enabled = bool(fund_cfg.get("enabled", True))

    output_dir.mkdir(parents=True, exist_ok=True)
    run_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "ok": False,
        "enabled": enabled,
        "run_at": run_at,
        "source_project": str(external_dir),
        "strong_funds": {"candidates": []},
        "portfolio_fund_risks": [],
        "errors": [],
        "method_note": "基金分析师模块输出为研究筛选线索，不构成投资建议。",
    }
    if not enabled:
        payload["ok"] = True
        payload["method_note"] = "fund_analyst.enabled=false，未运行基金分析师模块。"
        return payload

    external_dir = external_dir.expanduser().resolve()
    if not external_dir.exists():
        raise FileNotFoundError(f"Fund analyst source not found: {external_dir}")

    old_path = list(sys.path)
    cwd = Path.cwd()
    try:
        sys.path.insert(0, str(external_dir))
        os.chdir(external_dir)
        strong_mod = load_module(external_dir / "20_strong_fund_screener.py", "external_fund_strong_screener")
        strong = strong_mod.screen_strong_funds(
            top=top,
            max_candidates=max_candidates,
            macro_state=macro_state,
        )
        payload["strong_funds"] = {
            "analysis_time": strong.get("analysis_time"),
            "types": strong.get("types"),
            "macro_state": strong.get("macro_state"),
            "macro_note": strong.get("macro_note"),
            "source": strong.get("source"),
            "candidates": [normalize_strong_candidate(row) for row in strong.get("candidates", [])],
        }
    except Exception as exc:
        payload["errors"].append({"module": "strong_funds", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=4)})
    finally:
        os.chdir(cwd)
        sys.path[:] = old_path

    portfolio_funds = read_portfolio_funds(HOLDINGS_PATH)
    if portfolio_funds:
        old_path = list(sys.path)
        cwd = Path.cwd()
        try:
            sys.path.insert(0, str(external_dir))
            os.chdir(external_dir)
            drawdown_mod = load_module(external_dir / "fund_drawdown_report.py", "external_fund_drawdown_report")
            for fund in portfolio_funds[: int(fund_cfg.get("portfolio_risk_limit", 8))]:
                try:
                    reference = drawdown_mod.build_recent_strength_reference(fund["fund_code"])
                    compact = summarize_drawdown(reference)
                    compact["fund_name"] = fund["fund_name"]
                    payload["portfolio_fund_risks"].append(compact)
                except Exception as exc:
                    payload["portfolio_fund_risks"].append({"fund_code": fund["fund_code"], "fund_name": fund["fund_name"], "error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            payload["errors"].append({"module": "portfolio_fund_risks", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=4)})
        finally:
            os.chdir(cwd)
            sys.path[:] = old_path

    payload["ok"] = not payload["errors"] or bool(payload["strong_funds"].get("candidates") or payload["portfolio_fund_risks"])
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run optional fund analyst integration.")
    parser.add_argument("--config", default=str(KB_DIR / "config.json"))
    parser.add_argument("--output", default=str(KB_DIR / "fund_analyst"))
    parser.add_argument("--external-dir", default=str(DEFAULT_EXTERNAL_DIR))
    args = parser.parse_args(argv)

    payload = run(Path(args.config), Path(args.output), Path(args.external_dir))
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    history = out_dir / f"{payload['run_at'][:10]}.json"
    history.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": payload["ok"],
        "run_at": payload["run_at"],
        "latest": str(latest),
        "strong_candidates": len(payload.get("strong_funds", {}).get("candidates", [])),
        "portfolio_funds": len(payload.get("portfolio_fund_risks", [])),
        "errors": payload.get("errors", []),
    }, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
