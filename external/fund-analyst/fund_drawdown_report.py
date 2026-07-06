#!/usr/bin/env python3
"""
Fetch 1/3/6-month fund returns, peer rankings, and max drawdown from Eastmoney/Tiantian Fund.

Example:
    python fund_drawdown_report.py 017811
    python fund_drawdown_report.py 017811 000001 --format json
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup


STAGE_LABELS = ("近1月", "近3月", "近6月")
MONTH_WINDOWS = {"近1月": 1, "近3月": 3, "近6月": 6}
PROFILE_WINDOWS = {"近1年": 12, "近3年": 36, "成立以来": None}
DEFAULT_DRAWDOWN_THRESHOLD_PCT = 5.0

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    )
}


@dataclass
class StageMetric:
    label: str
    return_pct: float | None
    peer_avg_pct: float | None
    hs300_pct: float | None
    peer_rank: int | None
    peer_total: int | None
    rank_change: int | None
    rank_change_direction: str | None
    quartile: str | None


@dataclass
class DrawdownMetric:
    label: str
    months: int
    start_date: str | None
    end_date: str | None
    max_drawdown_pct: float | None
    peak_date: str | None
    trough_date: str | None
    nav_points: int


@dataclass
class DrawdownWindowMetric:
    label: str
    months: int | None
    start_date: str | None
    end_date: str | None
    latest_nav: float | None
    high_nav: float | None
    high_date: str | None
    low_nav: float | None
    low_date: str | None
    current_drawdown_pct: float | None
    max_drawdown_pct: float | None
    peak_date: str | None
    trough_date: str | None
    nav_points: int


@dataclass
class DrawdownEvent:
    start_date: str
    trough_date: str
    end_date: str | None
    max_drawdown_pct: float
    recovery_days: int | None
    duration_days: int
    recovered: bool


@dataclass
class CurrentDrawdown:
    peak_date: str
    peak_nav: float
    current_date: str
    current_nav: float
    drawdown_pct: float
    days_since_peak: int


@dataclass
class DrawdownProfile:
    latest_nav_date: str | None
    latest_nav: float | None
    drawdown_threshold_pct: float
    windows: list[DrawdownWindowMetric]
    total_drawdowns: int
    max_historic_drawdown_pct: float | None
    avg_recovery_days: float | None
    recent_5_drawdowns: list[DrawdownEvent]
    current_drawdown: CurrentDrawdown | None


@dataclass
class RecentStrengthMetric:
    label: str
    return_pct: float | None
    peer_avg_pct: float | None
    hs300_pct: float | None
    peer_rank: int | None
    peer_total: int | None
    peer_rank_pct: float | None
    top_band: str | None
    max_drawdown_pct: float | None
    drawdown_peak_date: str | None
    drawdown_trough_date: str | None
    rank_pass: bool | None
    positive_return_pass: bool | None
    benchmark_return_pass: bool | None
    passed: bool
    reasons: list[str]


@dataclass
class FourDimensionMetric:
    label: str
    drawdown_pct: float | None
    return_pct: float | None
    peer_avg_pct: float | None
    peer_rank: int | None
    peer_total: int | None
    drawdown_recorded: bool
    positive_return_pass: bool | None
    positive_peer_avg_pass: bool | None
    top5_rank_pass: bool | None
    passed: bool
    reasons: list[str]


@dataclass
class FourDimensionGate:
    rule: str
    required_periods: list[str]
    rank_limit: int
    all_periods_required: bool
    passed: bool
    passed_periods: int
    failed_periods: int
    unknown_periods: int
    decision_reference: str
    metrics: list[FourDimensionMetric]


@dataclass
class RecentStrengthGate:
    rule: str
    required_periods: list[str]
    top_rank_pct_limit: float
    strongest_rank_pct_limit: float
    all_periods_required: bool
    passed: bool
    passed_periods: int
    failed_periods: int
    unknown_periods: int
    decision_reference: str
    metrics: list[RecentStrengthMetric]


@dataclass
class FundReport:
    fund_code: str
    latest_nav_date: str | None
    stage_metrics: list[StageMetric]
    drawdowns: list[DrawdownMetric]
    source: str
    recent_strength: RecentStrengthGate | None = None
    four_dimension_gate: FourDimensionGate | None = None
    drawdown_profile: DrawdownProfile | None = None


class FundDataError(RuntimeError):
    pass


def parse_percent(text: str) -> float | None:
    cleaned = text.strip().replace("%", "").replace(",", "")
    if cleaned in {"", "---", "--", "-"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(text: str) -> int | None:
    cleaned = re.sub(r"[^\d-]", "", text.strip())
    if cleaned in {"", "-", "---"}:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def subtract_months(value: date, months: int) -> date:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def pct_change(current: float, base: float) -> float | None:
    if base == 0:
        return None
    return round((current / base - 1) * 100, 4)


def fetch_text(url: str, referer: str | None = None, timeout: int = 20) -> str:
    headers = dict(HTTP_HEADERS)
    if referer:
        headers["Referer"] = referer
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def extract_apidata_content(script_text: str) -> str:
    match = re.search(r"var\s+apidata\s*=\s*\{\s*content\s*:\s*\"(.*)\"\s*\}\s*;?\s*$", script_text, re.S)
    if not match:
        raise FundDataError("Could not parse stage-performance payload from FundArchivesDatas.aspx")
    content = match.group(1)
    return content.replace(r"\/", "/").replace(r"\"" , '"')


def parse_peer_rank(text: str) -> tuple[int | None, int | None]:
    parts = re.findall(r"\d+", text)
    if len(parts) >= 2:
        return int(parts[0]), int(parts[1])
    if len(parts) == 1:
        return int(parts[0]), None
    return None, None


def calc_peer_rank_pct(peer_rank: int | None, peer_total: int | None) -> float | None:
    if peer_rank is None or peer_total in (None, 0):
        return None
    return round(peer_rank / peer_total * 100, 2)


def fetch_stage_metrics(fund_code: str) -> list[StageMetric]:
    url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jdzf&code={fund_code}&rt=0"
    text = fetch_text(url, referer=f"https://fundf10.eastmoney.com/jdzf_{fund_code}.html")
    soup = BeautifulSoup(extract_apidata_content(text), "html.parser")

    metrics: list[StageMetric] = []
    for ul in soup.select("div.jdzfnew > ul"):
        if "fcol" in ul.get("class", []):
            continue

        cells = ul.find_all("li", recursive=False)
        if len(cells) < 7:
            continue

        label = cells[0].get_text(strip=True)
        if label not in STAGE_LABELS:
            continue

        peer_rank, peer_total = parse_peer_rank(cells[4].get_text("|", strip=True))
        rank_change_text = cells[5].get_text("", strip=True)
        rank_change_direction = None
        if "↑" in rank_change_text:
            rank_change_direction = "up"
        elif "↓" in rank_change_text:
            rank_change_direction = "down"

        quartile_node = cells[6].select_one(".sifen")
        quartile = quartile_node.get_text(strip=True) if quartile_node else cells[6].get_text(strip=True)
        if quartile in {"", "---", "--"}:
            quartile = None

        metrics.append(
            StageMetric(
                label=label,
                return_pct=parse_percent(cells[1].get_text(strip=True)),
                peer_avg_pct=parse_percent(cells[2].get_text(strip=True)),
                hs300_pct=parse_percent(cells[3].get_text(strip=True)),
                peer_rank=peer_rank,
                peer_total=peer_total,
                rank_change=parse_int(rank_change_text),
                rank_change_direction=rank_change_direction,
                quartile=quartile,
            )
        )

    missing = set(STAGE_LABELS) - {item.label for item in metrics}
    if missing:
        raise FundDataError(f"Missing stage metrics for {fund_code}: {', '.join(sorted(missing))}")
    return sorted(metrics, key=lambda item: STAGE_LABELS.index(item.label))


def fetch_nav_page(
    fund_code: str,
    page_index: int,
    start_date: date | None = None,
    end_date: date | None = None,
    page_size: int = 20,
) -> dict:
    params = {
        "fundCode": fund_code,
        "pageIndex": page_index,
        "pageSize": page_size,
        "startDate": start_date.isoformat() if start_date else "",
        "endDate": end_date.isoformat() if end_date else "",
    }
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"https://api.fund.eastmoney.com/f10/lsjz?{query}"
    text = fetch_text(url, referer=f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html")
    payload = json.loads(text)
    if payload.get("ErrCode") != 0:
        raise FundDataError(f"History NAV API error for {fund_code}: {payload.get('ErrMsg')}")
    return payload


def fetch_nav_history(
    fund_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    page_size: int = 20,
) -> list[dict]:
    payload = fetch_nav_page(fund_code, 1, start_date, end_date, page_size=page_size)
    data = payload.get("Data") or {}
    rows = list(data.get("LSJZList", []) or [])

    total_count = int(payload.get("TotalCount") or len(rows))
    page_size = int(payload.get("PageSize") or len(rows) or 1)
    page_index = int(payload.get("PageIndex") or 1)
    total_pages = (total_count + page_size - 1) // page_size

    for next_page in range(page_index + 1, total_pages + 1):
        next_payload = fetch_nav_page(fund_code, next_page, start_date, end_date, page_size=page_size)
        next_data = next_payload.get("Data") or {}
        rows.extend(next_data.get("LSJZList", []) or [])

    return rows


def parse_nav_points(rows: Iterable[dict]) -> list[tuple[date, float]]:
    points: list[tuple[date, float]] = []
    for row in rows:
        raw_date = row.get("FSRQ")
        raw_nav = row.get("LJJZ") or row.get("DWJZ")
        if not raw_date or raw_nav in {None, "", "---"}:
            continue
        try:
            points.append((datetime.strptime(raw_date, "%Y-%m-%d").date(), float(raw_nav)))
        except (TypeError, ValueError):
            continue
    return sorted(points, key=lambda item: item[0])


def fetch_nav_trend_points(fund_code: str) -> list[tuple[date, float]]:
    url = f"https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
    text = fetch_text(url, referer=f"https://fund.eastmoney.com/{fund_code}.html")
    match = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);", text, re.S)
    if not match:
        raise FundDataError(f"Could not parse Data_netWorthTrend for {fund_code}")

    try:
        trend = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise FundDataError(f"Invalid Data_netWorthTrend JSON for {fund_code}: {exc}") from exc

    points: list[tuple[date, float]] = []
    for item in trend:
        raw_date = item.get("x")
        raw_nav = item.get("y")
        if raw_date is None or raw_nav in {None, "", "---"}:
            continue
        try:
            nav_date = datetime.fromtimestamp(int(raw_date) / 1000).date()
            points.append((nav_date, float(raw_nav)))
        except (TypeError, ValueError, OSError):
            continue

    return sorted(points, key=lambda item: item[0])


def fetch_all_nav_points(fund_code: str) -> list[tuple[date, float]]:
    try:
        points = fetch_nav_trend_points(fund_code)
        if points:
            return points
    except (FundDataError, requests.RequestException):
        pass

    return parse_nav_points(fetch_nav_history(fund_code, None, None))


def calc_max_drawdown(points: list[tuple[date, float]], label: str, months: int) -> DrawdownMetric:
    if len(points) < 2:
        return DrawdownMetric(label, months, None, None, None, None, None, len(points))

    peak_date, peak_nav = points[0]
    max_dd = 0.0
    max_peak_date = peak_date
    max_trough_date = peak_date

    for current_date, nav in points:
        if nav > peak_nav:
            peak_date, peak_nav = current_date, nav
        drawdown = nav / peak_nav - 1
        if drawdown < max_dd:
            max_dd = drawdown
            max_peak_date = peak_date
            max_trough_date = current_date

    return DrawdownMetric(
        label=label,
        months=months,
        start_date=points[0][0].isoformat(),
        end_date=points[-1][0].isoformat(),
        max_drawdown_pct=round(max_dd * 100, 4),
        peak_date=max_peak_date.isoformat(),
        trough_date=max_trough_date.isoformat(),
        nav_points=len(points),
    )


def calc_drawdown_window(
    points: list[tuple[date, float]],
    label: str,
    months: int | None,
) -> DrawdownWindowMetric:
    if len(points) < 2:
        latest_date = points[-1][0].isoformat() if points else None
        latest_nav = points[-1][1] if points else None
        return DrawdownWindowMetric(
            label=label,
            months=months,
            start_date=latest_date,
            end_date=latest_date,
            latest_nav=latest_nav,
            high_nav=latest_nav,
            high_date=latest_date,
            low_nav=latest_nav,
            low_date=latest_date,
            current_drawdown_pct=None,
            max_drawdown_pct=None,
            peak_date=None,
            trough_date=None,
            nav_points=len(points),
        )

    peak_date, peak_nav = points[0]
    max_dd = 0.0
    max_peak_date = peak_date
    max_trough_date = peak_date

    high_date, high_nav = max(points, key=lambda item: item[1])
    low_date, low_nav = min(points, key=lambda item: item[1])

    for current_date, nav in points:
        if nav > peak_nav:
            peak_date, peak_nav = current_date, nav
        drawdown = nav / peak_nav - 1
        if drawdown < max_dd:
            max_dd = drawdown
            max_peak_date = peak_date
            max_trough_date = current_date

    latest_date, latest_nav = points[-1]
    current_drawdown = pct_change(latest_nav, high_nav)

    return DrawdownWindowMetric(
        label=label,
        months=months,
        start_date=points[0][0].isoformat(),
        end_date=latest_date.isoformat(),
        latest_nav=round(latest_nav, 4),
        high_nav=round(high_nav, 4),
        high_date=high_date.isoformat(),
        low_nav=round(low_nav, 4),
        low_date=low_date.isoformat(),
        current_drawdown_pct=current_drawdown,
        max_drawdown_pct=round(max_dd * 100, 4),
        peak_date=max_peak_date.isoformat(),
        trough_date=max_trough_date.isoformat(),
        nav_points=len(points),
    )


def find_drawdown_events(
    points: list[tuple[date, float]],
    threshold_pct: float = DEFAULT_DRAWDOWN_THRESHOLD_PCT,
) -> list[DrawdownEvent]:
    if len(points) < 2:
        return []

    threshold = -abs(threshold_pct) / 100
    events: list[DrawdownEvent] = []
    peak_date, peak_nav = points[0]
    in_drawdown = False
    start_date = points[0][0]
    trough_date = points[0][0]
    max_dd = 0.0

    for current_date, nav in points[1:]:
        if nav > peak_nav:
            if in_drawdown:
                recovery_days = (current_date - start_date).days
                events.append(
                    DrawdownEvent(
                        start_date=start_date.isoformat(),
                        trough_date=trough_date.isoformat(),
                        end_date=current_date.isoformat(),
                        max_drawdown_pct=round(max_dd * 100, 2),
                        recovery_days=recovery_days,
                        duration_days=recovery_days,
                        recovered=True,
                    )
                )
                in_drawdown = False
                max_dd = 0.0
            peak_date, peak_nav = current_date, nav
            continue

        drawdown = nav / peak_nav - 1
        if drawdown <= threshold:
            if not in_drawdown:
                in_drawdown = True
                start_date = peak_date
                trough_date = current_date
                max_dd = drawdown
            elif drawdown < max_dd:
                trough_date = current_date
                max_dd = drawdown

    if in_drawdown:
        last_date = points[-1][0]
        events.append(
            DrawdownEvent(
                start_date=start_date.isoformat(),
                trough_date=trough_date.isoformat(),
                end_date=None,
                max_drawdown_pct=round(max_dd * 100, 2),
                recovery_days=None,
                duration_days=(last_date - start_date).days,
                recovered=False,
            )
        )

    return events


def build_current_drawdown(points: list[tuple[date, float]]) -> CurrentDrawdown | None:
    if len(points) < 2:
        return None

    peak_date, peak_nav = max(points, key=lambda item: item[1])
    current_date, current_nav = points[-1]
    if current_nav >= peak_nav:
        return None

    drawdown = pct_change(current_nav, peak_nav)
    if drawdown is None:
        return None

    return CurrentDrawdown(
        peak_date=peak_date.isoformat(),
        peak_nav=round(peak_nav, 4),
        current_date=current_date.isoformat(),
        current_nav=round(current_nav, 4),
        drawdown_pct=drawdown,
        days_since_peak=(current_date - peak_date).days,
    )


def build_drawdown_profile(
    points: list[tuple[date, float]],
    threshold_pct: float = DEFAULT_DRAWDOWN_THRESHOLD_PCT,
) -> DrawdownProfile:
    if not points:
        return DrawdownProfile(
            latest_nav_date=None,
            latest_nav=None,
            drawdown_threshold_pct=threshold_pct,
            windows=[],
            total_drawdowns=0,
            max_historic_drawdown_pct=None,
            avg_recovery_days=None,
            recent_5_drawdowns=[],
            current_drawdown=None,
        )

    latest_date, latest_nav = points[-1]
    windows: list[DrawdownWindowMetric] = []
    for label, months in PROFILE_WINDOWS.items():
        if months is None:
            window_points = points
        else:
            window_start = subtract_months(latest_date, months)
            window_points = [point for point in points if point[0] >= window_start]
        windows.append(calc_drawdown_window(window_points, label, months))

    events = find_drawdown_events(points, threshold_pct)
    recovered_events = [event for event in events if event.recovery_days is not None]
    avg_recovery_days = (
        round(sum(event.recovery_days for event in recovered_events if event.recovery_days is not None) / len(recovered_events), 1)
        if recovered_events
        else None
    )
    max_historic_drawdown_pct = min((event.max_drawdown_pct for event in events), default=None)

    return DrawdownProfile(
        latest_nav_date=latest_date.isoformat(),
        latest_nav=round(latest_nav, 4),
        drawdown_threshold_pct=threshold_pct,
        windows=windows,
        total_drawdowns=len(events),
        max_historic_drawdown_pct=max_historic_drawdown_pct,
        avg_recovery_days=avg_recovery_days,
        recent_5_drawdowns=events[-5:],
        current_drawdown=build_current_drawdown(points),
    )


def evaluate_recent_strength(
    report: FundReport,
    top_rank_pct_limit: float = 10.0,
    strongest_rank_pct_limit: float = 5.0,
    all_periods_required: bool = True,
) -> RecentStrengthGate:
    """Evaluate whether recent 1/3/6-month data can support selection/holding.

    The gate intentionally requires positive absolute return before comparing
    against the peer benchmark, so "falling less than the benchmark" is not
    mistaken for a strong fund signal.
    """
    drawdowns_by_label = {item.label: item for item in report.drawdowns}
    metrics: list[RecentStrengthMetric] = []

    for metric in report.stage_metrics:
        peer_rank_pct = calc_peer_rank_pct(metric.peer_rank, metric.peer_total)
        dd = drawdowns_by_label.get(metric.label)

        rank_pass = peer_rank_pct is not None and peer_rank_pct <= top_rank_pct_limit
        positive_return_pass = metric.return_pct is not None and metric.return_pct > 0
        benchmark_return_pass = (
            metric.return_pct is not None
            and metric.peer_avg_pct is not None
            and metric.return_pct > metric.peer_avg_pct
        )

        top_band = None
        if peer_rank_pct is not None:
            if peer_rank_pct <= strongest_rank_pct_limit:
                top_band = f"top_{strongest_rank_pct_limit:g}%"
            elif peer_rank_pct <= top_rank_pct_limit:
                top_band = f"top_{strongest_rank_pct_limit:g}-{top_rank_pct_limit:g}%"

        reasons: list[str] = []
        if peer_rank_pct is None:
            reasons.append("同赛道排名缺失")
        elif not rank_pass:
            reasons.append(f"同赛道排名前{peer_rank_pct}%未进入前{top_rank_pct_limit:g}%")

        if metric.return_pct is None:
            reasons.append("涨跌幅缺失")
        elif not positive_return_pass:
            reasons.append("涨跌幅不是正值")

        if metric.peer_avg_pct is None:
            reasons.append("基准赛道涨跌幅缺失")
        elif metric.return_pct is not None and not benchmark_return_pass:
            reasons.append("涨幅未超过基准赛道")

        passed = bool(rank_pass and positive_return_pass and benchmark_return_pass)
        metrics.append(
            RecentStrengthMetric(
                label=metric.label,
                return_pct=metric.return_pct,
                peer_avg_pct=metric.peer_avg_pct,
                hs300_pct=metric.hs300_pct,
                peer_rank=metric.peer_rank,
                peer_total=metric.peer_total,
                peer_rank_pct=peer_rank_pct,
                top_band=top_band,
                max_drawdown_pct=dd.max_drawdown_pct if dd else None,
                drawdown_peak_date=dd.peak_date if dd else None,
                drawdown_trough_date=dd.trough_date if dd else None,
                rank_pass=rank_pass if peer_rank_pct is not None else None,
                positive_return_pass=positive_return_pass if metric.return_pct is not None else None,
                benchmark_return_pass=benchmark_return_pass if metric.peer_avg_pct is not None else None,
                passed=passed,
                reasons=reasons,
            )
        )

    passed_periods = sum(1 for item in metrics if item.passed)
    unknown_periods = sum(
        1
        for item in metrics
        if item.peer_rank_pct is None or item.return_pct is None or item.peer_avg_pct is None
    )
    failed_periods = len(metrics) - passed_periods - unknown_periods
    passed = passed_periods == len(metrics) if all_periods_required else passed_periods > 0

    if passed:
        decision_reference = "可作为基金入选/继续持有的正向参考标准之一"
    elif passed_periods > 0:
        decision_reference = "仅部分周期满足，只能作为观察项，不能单独支持入选或加仓"
    elif unknown_periods:
        decision_reference = "关键数据缺失，不能作为入选/持有正向依据"
    else:
        decision_reference = "不满足近期同赛道强势标准，不作为入选/持有正向依据"

    return RecentStrengthGate(
        rule=(
            "近1/3/6月均需满足：同赛道排名前5%-10%，基金涨跌幅为正，"
            "且涨幅高于基准赛道/同类平均；避免把跌幅更小误判为强势"
        ),
        required_periods=list(STAGE_LABELS),
        top_rank_pct_limit=top_rank_pct_limit,
        strongest_rank_pct_limit=strongest_rank_pct_limit,
        all_periods_required=all_periods_required,
        passed=passed,
        passed_periods=passed_periods,
        failed_periods=failed_periods,
        unknown_periods=unknown_periods,
        decision_reference=decision_reference,
        metrics=metrics,
    )


def evaluate_four_dimension_gate(
    report: FundReport,
    rank_limit: int = 5,
    all_periods_required: bool = True,
) -> FourDimensionGate:
    """Strict 4D gate: drawdown + fund return + peer avg + peer rank.

    Drawdown is reported as a risk dimension. The strict pass/fail dimensions
    follow the user rule: fund return must be positive, peer average must be
    positive, and peer rank must be top 5.
    """
    drawdowns_by_label = {item.label: item for item in report.drawdowns}
    metrics: list[FourDimensionMetric] = []

    for metric in report.stage_metrics:
        dd = drawdowns_by_label.get(metric.label)
        positive_return_pass = metric.return_pct is not None and metric.return_pct > 0
        positive_peer_avg_pass = metric.peer_avg_pct is not None and metric.peer_avg_pct > 0
        top5_rank_pass = metric.peer_rank is not None and metric.peer_rank <= rank_limit

        reasons: list[str] = []
        if metric.return_pct is None:
            reasons.append("基金涨跌幅缺失")
        elif not positive_return_pass:
            reasons.append("基金涨跌幅不是正数")

        if metric.peer_avg_pct is None:
            reasons.append("同赛道平均涨跌幅缺失")
        elif not positive_peer_avg_pass:
            reasons.append("同赛道平均涨跌幅不是正数")

        if metric.peer_rank is None:
            reasons.append("同赛道排名缺失")
        elif not top5_rank_pass:
            reasons.append(f"同赛道排名第{metric.peer_rank}名，未进入前{rank_limit}名")

        passed = bool(positive_return_pass and positive_peer_avg_pass and top5_rank_pass)
        metrics.append(
            FourDimensionMetric(
                label=metric.label,
                drawdown_pct=dd.max_drawdown_pct if dd else None,
                return_pct=metric.return_pct,
                peer_avg_pct=metric.peer_avg_pct,
                peer_rank=metric.peer_rank,
                peer_total=metric.peer_total,
                drawdown_recorded=dd is not None and dd.max_drawdown_pct is not None,
                positive_return_pass=positive_return_pass if metric.return_pct is not None else None,
                positive_peer_avg_pass=positive_peer_avg_pass if metric.peer_avg_pct is not None else None,
                top5_rank_pass=top5_rank_pass if metric.peer_rank is not None else None,
                passed=passed,
                reasons=reasons,
            )
        )

    passed_periods = sum(1 for item in metrics if item.passed)
    unknown_periods = sum(
        1
        for item in metrics
        if item.return_pct is None or item.peer_avg_pct is None or item.peer_rank is None
    )
    failed_periods = len(metrics) - passed_periods - unknown_periods
    passed = passed_periods == len(metrics) if all_periods_required else passed_periods > 0

    if passed:
        decision_reference = "四维严格闸门通过：回撤已记录，涨跌幅/同赛道平均为正且排名进入前5名"
    elif passed_periods:
        decision_reference = "四维严格闸门仅部分周期通过，只能作为观察项，不支持强买入/强加仓"
    elif unknown_periods:
        decision_reference = "四维严格闸门数据缺失，不支持强动作"
    else:
        decision_reference = "四维严格闸门不通过，不支持强买入/强加仓"

    return FourDimensionGate(
        rule="每个窗口必须记录回撤；基金涨跌幅>0、同赛道平均涨跌幅>0、同赛道排名<=前5名",
        required_periods=list(STAGE_LABELS),
        rank_limit=rank_limit,
        all_periods_required=all_periods_required,
        passed=passed,
        passed_periods=passed_periods,
        failed_periods=failed_periods,
        unknown_periods=unknown_periods,
        decision_reference=decision_reference,
        metrics=metrics,
    )


def build_report(fund_code: str) -> FundReport:
    fund_code = fund_code.strip()
    if not re.fullmatch(r"\d{6}", fund_code):
        raise FundDataError(f"Invalid fund code: {fund_code}")

    stage_metrics = fetch_stage_metrics(fund_code)
    all_points = fetch_all_nav_points(fund_code)
    latest_points = all_points[-10:]
    if not latest_points:
        raise FundDataError(f"No NAV history found for {fund_code}")

    latest_nav_date = max(point[0] for point in latest_points)
    drawdowns: list[DrawdownMetric] = []
    for label, months in MONTH_WINDOWS.items():
        window_start = subtract_months(latest_nav_date, months)
        points = [point for point in all_points if window_start <= point[0] <= latest_nav_date]
        drawdowns.append(calc_max_drawdown(points, label, months))

    report = FundReport(
        fund_code=fund_code,
        latest_nav_date=latest_nav_date.isoformat(),
        stage_metrics=stage_metrics,
        drawdowns=drawdowns,
        source=(
            "Eastmoney/Tiantian Fund: FundArchivesDatas.aspx + "
            "fund.eastmoney.com/pingzhongdata + api.fund.eastmoney.com/f10/lsjz fallback"
        ),
        drawdown_profile=build_drawdown_profile(all_points),
    )
    report.recent_strength = evaluate_recent_strength(report)
    report.four_dimension_gate = evaluate_four_dimension_gate(report)
    return report


def flatten_report(report: FundReport) -> list[dict]:
    drawdowns_by_label = {item.label: item for item in report.drawdowns}
    profile_windows_by_label = {
        item.label: item
        for item in (report.drawdown_profile.windows if report.drawdown_profile else [])
    }
    profile_1y = profile_windows_by_label.get("近1年")
    profile_3y = profile_windows_by_label.get("近3年")
    profile_since = profile_windows_by_label.get("成立以来")
    current_drawdown = report.drawdown_profile.current_drawdown if report.drawdown_profile else None
    recent_strength_by_label = {
        item.label: item
        for item in (report.recent_strength.metrics if report.recent_strength else [])
    }
    four_dimension_by_label = {
        item.label: item
        for item in (report.four_dimension_gate.metrics if report.four_dimension_gate else [])
    }
    rows: list[dict] = []
    for metric in report.stage_metrics:
        dd = drawdowns_by_label.get(metric.label)
        strength = recent_strength_by_label.get(metric.label)
        four_dimension = four_dimension_by_label.get(metric.label)
        rows.append(
            {
                "fund_code": report.fund_code,
                "latest_nav_date": report.latest_nav_date,
                "period": metric.label,
                "return_pct": metric.return_pct,
                "peer_rank": metric.peer_rank,
                "peer_total": metric.peer_total,
                "peer_rank_pct": strength.peer_rank_pct if strength else calc_peer_rank_pct(metric.peer_rank, metric.peer_total),
                "peer_avg_pct": metric.peer_avg_pct,
                "benchmark_excess_pct": (
                    round(metric.return_pct - metric.peer_avg_pct, 2)
                    if metric.return_pct is not None and metric.peer_avg_pct is not None
                    else None
                ),
                "hs300_pct": metric.hs300_pct,
                "quartile": metric.quartile,
                "max_drawdown_pct": dd.max_drawdown_pct if dd else None,
                "drawdown_peak_date": dd.peak_date if dd else None,
                "drawdown_trough_date": dd.trough_date if dd else None,
                "drawdown_start_date": dd.start_date if dd else None,
                "drawdown_end_date": dd.end_date if dd else None,
                "nav_points": dd.nav_points if dd else None,
                "recent_strength_pass": strength.passed if strength else None,
                "recent_strength_reasons": "；".join(strength.reasons) if strength and strength.reasons else "",
                "four_dimension_pass": four_dimension.passed if four_dimension else None,
                "four_dimension_reasons": "；".join(four_dimension.reasons) if four_dimension and four_dimension.reasons else "",
                "max_drawdown_1y_pct": profile_1y.max_drawdown_pct if profile_1y else None,
                "max_drawdown_3y_pct": profile_3y.max_drawdown_pct if profile_3y else None,
                "max_drawdown_since_inception_pct": profile_since.max_drawdown_pct if profile_since else None,
                "current_drawdown_pct": current_drawdown.drawdown_pct if current_drawdown else 0.0,
                "total_drawdowns_over_threshold": report.drawdown_profile.total_drawdowns if report.drawdown_profile else None,
                "avg_recovery_days": report.drawdown_profile.avg_recovery_days if report.drawdown_profile else None,
            }
        )
    return rows


def build_recent_strength_reference(fund_code: str) -> dict:
    try:
        report = build_report(fund_code)
        return {
            "fund_code": report.fund_code,
            "latest_nav_date": report.latest_nav_date,
            "source": report.source,
            "recent_strength": asdict(report.recent_strength) if report.recent_strength else None,
            "four_dimension_gate": asdict(report.four_dimension_gate) if report.four_dimension_gate else None,
            "drawdown_profile": asdict(report.drawdown_profile) if report.drawdown_profile else None,
            "rows": flatten_report(report),
        }
    except FundDataError as exc:
        return {"fund_code": fund_code, "error": str(exc)}
    except requests.RequestException as exc:
        return {"fund_code": fund_code, "error": f"Network error: {exc}"}


def print_table(rows: list[dict]) -> None:
    headers = [
        "fund_code",
        "period",
        "return_pct",
        "peer_rank",
        "peer_total",
        "peer_rank_pct",
        "peer_avg_pct",
        "benchmark_excess_pct",
        "max_drawdown_pct",
        "four_dimension_pass",
        "max_drawdown_1y_pct",
        "max_drawdown_3y_pct",
        "max_drawdown_since_inception_pct",
        "current_drawdown_pct",
        "avg_recovery_days",
        "recent_strength_pass",
        "drawdown_peak_date",
        "drawdown_trough_date",
        "quartile",
    ]
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len("" if row[header] is None else str(row[header])))

    print("  ".join(header.ljust(widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))
    for row in rows:
        print("  ".join(("" if row[header] is None else str(row[header])).ljust(widths[header]) for header in headers))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch fund returns, peer rankings, and max drawdown.")
    parser.add_argument("fund_codes", nargs="+", help="6-digit fund codes, e.g. 017811 000001")
    parser.add_argument("--format", choices=("table", "json"), default="table", help="Output format")
    parser.add_argument("--csv", type=Path, help="Optional CSV output path")
    args = parser.parse_args()

    reports = [build_report(code) for code in args.fund_codes]
    rows = [row for report in reports for row in flatten_report(report)]

    if args.format == "json":
        print(json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2))
    else:
        print_table(rows)

    if args.csv:
        write_csv(args.csv, rows)
        print(f"\nCSV written: {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except FundDataError as exc:
        print(f"Data error: {exc}", file=sys.stderr)
        raise SystemExit(3)
