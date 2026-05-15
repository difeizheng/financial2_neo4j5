"""Knowledge graph financial data extractor.

Extracts parameters, metrics, and time-series data from a FinancialGraph
and its associated snapshot for the finance benefit analysis page.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from financial_kg.models.graph import FinancialGraph


# ── Cell ID helpers ──────────────────────────────────────────────────────────

def _cell_id(sheet: str, row: int, col: str) -> str:
    return f"{sheet}_{row}_{col}"


def _get_val(snapshot_values: dict, sheet: str, row: int, col: str, default: Any = None) -> Any:
    cid = _cell_id(sheet, row, col)
    return snapshot_values.get(cid, default)


def _get_numeric(snapshot_values: dict, sheet: str, row: int, col: str, default: float = 0.0) -> float:
    v = _get_val(snapshot_values, sheet, row, col)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ── Indicator lookup helpers ────────────────────────────────────────────────

def _find_indicator(graph: FinancialGraph, name_substr: str, sheet_substr: str = "") -> dict | None:
    """Find first indicator whose name (and optionally sheet) contains the substring.
    Prioritizes indicators with time_series data."""
    best = None
    for ind in graph.indicators.values():
        if name_substr in (ind.name or ""):
            if not sheet_substr or sheet_substr in (ind.sheet or ""):
                if best is None:
                    best = ind
                # Prefer indicators with time series
                if (ind.time_series or {}) and not (best.time_series or {}):
                    best = ind
    return best


def _find_indicators(graph: FinancialGraph, name_substr: str, sheet_substr: str = "") -> list:
    """Find all matching indicators."""
    result = []
    for ind in graph.indicators.values():
        if name_substr in (ind.name or ""):
            if not sheet_substr or sheet_substr in (ind.sheet or ""):
                result.append(ind)
    return result


# ── Public extractors ───────────────────────────────────────────────────────

def extract_base_params(graph: FinancialGraph, snapshot_values: dict) -> dict[str, Any]:
    """Extract base parameters from the parameter input table (参数输入表).

    Returns a dict keyed by parameter name with {value, unit, description}.
    """
    sheet = "参数输入表"
    params: dict[str, dict] = {}

    # Scan rows for parameter entries (name in col D, value in col I, unit in col J)
    for row in range(4, 300):
        name = _get_val(snapshot_values, sheet, row, "D")
        if not name or not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue

        value = _get_val(snapshot_values, sheet, row, "I")
        unit = _get_val(snapshot_values, sheet, row, "J") or ""
        desc = _get_val(snapshot_values, sheet, row, "E") or ""

        params[name] = {"value": value, "unit": str(unit), "description": str(desc)}

    return params


def extract_params_with_cell_ids(graph: FinancialGraph, snapshot_values: dict) -> dict[str, dict]:
    """Extract base parameters including cell_id mapping for recalculation.

    Returns a dict keyed by parameter name with {value, unit, description, cell_id}.
    """
    sheet = "参数输入表"
    params: dict[str, dict] = {}

    for row in range(4, 300):
        name = _get_val(snapshot_values, sheet, row, "D")
        if not name or not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue

        value = _get_val(snapshot_values, sheet, row, "I")
        unit = _get_val(snapshot_values, sheet, row, "J") or ""
        desc = _get_val(snapshot_values, sheet, row, "E") or ""
        cell_id = _cell_id(sheet, row, "I")

        params[name] = {
            "value": value,
            "unit": str(unit),
            "description": str(desc),
            "cell_id": cell_id,
        }

    return params


def extract_full_table(graph: FinancialGraph, table_id: str) -> pd.DataFrame:
    """Extract a complete financial table with all time series columns.

    Returns DataFrame: 指标名称 | 单位 | 2030 | 2031 | ... | 2070
    Filters to show only operating years (years with non-zero revenue-like values).
    """
    table = graph.tables.get(table_id)
    if not table:
        return pd.DataFrame()

    # Get ordered time columns
    ts_cols = [c for c, role in table.col_roles.items() if role == "time_series"]
    periods = [table.time_period_labels.get(c, c) for c in ts_cols]

    # Get indicators sorted by row
    indicators = []
    for iid in table.indicator_ids:
        ind = graph.indicators.get(iid)
        if ind:
            indicators.append((ind.row, ind))
    indicators.sort(key=lambda x: x[0])

    # Build rows
    columns = ["指标名称", "单位"] + periods
    rows = []
    for _, ind in indicators:
        row = {"指标名称": ind.name, "单位": ind.unit or ""}
        for period in periods:
            row[period] = ind.time_series.get(period, "")
        rows.append(row)

    df = pd.DataFrame(rows, columns=columns)

    # Filter to operating years: keep year columns where at least one row has non-zero value
    year_cols = [c for c in periods if re.match(r"^\d{4}(-\d{2})?$", c)]
    if year_cols:
        active_cols = []
        for col in year_cols:
            if col in df.columns:
                numeric_vals = pd.to_numeric(df[col], errors="coerce")
                if numeric_vals.abs().sum() > 0:
                    active_cols.append(col)
        if active_cols:
            df = df[["指标名称", "单位"] + active_cols]

    return df


def extract_financial_metrics(graph: FinancialGraph, snapshot_values: dict) -> dict[str, float]:
    """Extract key financial metrics (IRR, NPV, payback, DSCR) from indicators."""
    metrics: dict[str, float] = {}

    # IRR from cash flow sheets
    irr_pre = _find_indicator(graph, "全投资内部收益率（税前）")
    irr_post = _find_indicator(graph, "全投资内部收益率（税后）")
    irr_equity = _find_indicator(graph, "资本金内部收益率")

    if irr_pre and irr_pre.summary_value is not None:
        metrics["irr_pre_tax"] = float(irr_pre.summary_value)
    if irr_post and irr_post.summary_value is not None:
        metrics["irr_post_tax"] = float(irr_post.summary_value)
    if irr_equity and irr_equity.summary_value is not None:
        metrics["irr_equity"] = float(irr_equity.summary_value)

    # NPV — try multiple name patterns (no indicator has "财务净现值" in graph)
    npv_found = False
    for pattern in [("净现值", "税后"), ("净现值", "全投资"), ("净现值", ""), ("NPV", "")]:
        npv_ind = _find_indicator(graph, pattern[0], pattern[1])
        if npv_ind and npv_ind.summary_value is not None:
            try:
                val = float(npv_ind.summary_value)
                if abs(val) > 0:
                    metrics["npv_post_tax"] = val
                    npv_found = True
                    break
            except (ValueError, TypeError):
                pass
    if not npv_found:
        # Fallback: try 全投资 indicator summary
        for ind in graph.indicators.values():
            name = ind.name or ""
            if "净现值" in name and ind.summary_value is not None:
                try:
                    val = float(ind.summary_value)
                    if abs(val) > 0:
                        metrics["npv_post_tax"] = val
                        break
                except (ValueError, TypeError):
                    pass

    # Payback period — 投资回收期
    payback = _find_indicator(graph, "投资回收期")
    if payback and payback.summary_value is not None:
        try:
            metrics["payback_post_tax"] = float(payback.summary_value)
        except (ValueError, TypeError):
            pass

    # DSCR — compute from EBITDA / debt_service time series if no dedicated DSCR indicator
    dscr_values = []
    # First check if there's a dedicated DSCR indicator
    dscr_indicators = _find_indicators(graph, "DSCR")
    if not dscr_indicators:
        dscr_indicators = _find_indicators(graph, "偿债备付率")
    if dscr_indicators:
        for ind in dscr_indicators:
            ts = ind.time_series or {}
            for v in ts.values():
                try:
                    fv = float(v)
                    if 0 < fv < 100:
                        dscr_values.append(fv)
                except (ValueError, TypeError):
                    continue
    else:
        # Compute from EBITDA and debt_service indicators
        ebitda_ind = _find_indicator(graph, "息税折旧摊销前利润", "全投资")
        if not ebitda_ind:
            ebitda_ind = _find_indicator(graph, "息税前利润", "全投资")
        debt_ind = None
        for name_search in [("当期还本付息", ""), ("还本付息合计", "")]:
            candidates = _find_indicators(graph, name_search[0], name_search[1])
            for c in candidates:
                ts = c.time_series or {}
                if any(isinstance(v, (int, float)) and v > 0 for v in ts.values()):
                    debt_ind = c
                    break
            if debt_ind:
                break

        if ebitda_ind and debt_ind:
            ebitda_ts = ebitda_ind.time_series or {}
            debt_ts = debt_ind.time_series or {}
            for period, ebitda_val in ebitda_ts.items():
                debt_val = debt_ts.get(period, 0)
                try:
                    e = float(ebitda_val)
                    d = float(debt_val)
                    if d > 0 and e > 0:
                        dscr_values.append(round(e / d, 2))
                except (ValueError, TypeError):
                    continue

    if dscr_values:
        metrics["avg_dscr"] = sum(dscr_values) / len(dscr_values)
        metrics["min_dscr"] = min(dscr_values)
        metrics["max_dscr"] = max(dscr_values)

    # Total investment
    total_inv = _find_indicator(graph, "项目总投资")
    if total_inv and total_inv.summary_value is not None:
        try:
            metrics["total_investment"] = float(total_inv.summary_value)
        except (ValueError, TypeError):
            pass

    # Net cash flow total
    net_cf = _find_indicator(graph, "净现金流", "全投资")
    if net_cf and net_cf.summary_value is not None:
        try:
            metrics["cumulative_cashflow"] = float(net_cf.summary_value)
        except (ValueError, TypeError):
            pass

    return metrics


def extract_yearly_data(graph: FinancialGraph, snapshot_values: dict) -> list[dict]:
    """Extract year-by-year financial data from cash flow indicators.

    Returns a list of dicts with year, revenue, operating_cost, ebitda,
    income_tax, debt_service, net_cashflow, cumulative_cashflow, dscr.
    """
    yearly: dict[str, dict] = {}

    # Revenue — from profit statement or cash flow (表8)
    for name_search in ["营业收入", "售电收入", "电费收入"]:
        revenue_ind = _find_indicator(graph, name_search)
        if revenue_ind and (revenue_ind.time_series or {}):
            break
    if revenue_ind:
        for period, val in (revenue_ind.time_series or {}).items():
            year = _extract_year(period)
            if year:
                yearly.setdefault(year, {"year": year})
                try:
                    yearly[year]["revenue"] = float(val)
                except (ValueError, TypeError):
                    pass

    # Operating cost — try multiple names
    op_cost_ind = None
    for name_search in [("经营成本", "全投资"), ("经营成本", ""), ("营业成本", "全投资"), ("营业成本", "")]:
        op_cost_ind = _find_indicator(graph, name_search[0], name_search[1])
        if op_cost_ind and (op_cost_ind.time_series or {}):
            break
    if op_cost_ind:
        for period, val in (op_cost_ind.time_series or {}).items():
            year = _extract_year(period)
            if year:
                yearly.setdefault(year, {"year": year})
                try:
                    yearly[year]["operating_cost"] = float(val)
                except (ValueError, TypeError):
                    pass

    # EBITDA — MUST use 息税折旧摊销前利润, NOT 息税前利润 (EBIT ≠ EBITDA)
    ebitda_ind = None
    for name_search in [("息税折旧摊销前利润", "全投资"), ("息税折旧摊销前利润", ""),
                         ("息税前利润", "全投资"), ("息税前利润", ""), ("EBITDA", "")]:
        ebitda_ind = _find_indicator(graph, name_search[0], name_search[1])
        if ebitda_ind and (ebitda_ind.time_series or {}):
            break
    if ebitda_ind:
        for period, val in (ebitda_ind.time_series or {}).items():
            year = _extract_year(period)
            if year:
                yearly.setdefault(year, {"year": year})
                try:
                    yearly[year]["ebitda"] = float(val)
                except (ValueError, TypeError):
                    pass

    # Income tax
    tax_ind = None
    for name_search in [("所得税", "全投资"), ("所得税", ""), ("应纳所得税", "")]:
        tax_ind = _find_indicator(graph, name_search[0], name_search[1])
        if tax_ind and (tax_ind.time_series or {}):
            break
    if tax_ind:
        for period, val in (tax_ind.time_series or {}).items():
            year = _extract_year(period)
            if year:
                yearly.setdefault(year, {"year": year})
                try:
                    yearly[year]["income_tax"] = float(val)
                except (ValueError, TypeError):
                    pass

    # Debt service — 当期还本付息 with valid numeric time series
    ds_ind = None
    for name_search in [("当期还本付息", ""), ("还本付息合计", ""), ("还本付息", "")]:
        candidates = _find_indicators(graph, name_search[0], name_search[1])
        for c in candidates:
            ts = c.time_series or {}
            has_valid = any(isinstance(v, (int, float)) and v > 0 for v in ts.values())
            if has_valid:
                ds_ind = c
                break
        if ds_ind:
            break
    if ds_ind:
        for period, val in (ds_ind.time_series or {}).items():
            year = _extract_year(period)
            if year:
                yearly.setdefault(year, {"year": year})
                try:
                    yearly[year]["debt_service"] = float(val)
                except (ValueError, TypeError):
                    pass

    # Net cash flow
    ncf_ind = _find_indicator(graph, "净现金流量", "全投资")
    if not ncf_ind:
        ncf_ind = _find_indicator(graph, "累计盈余资金")
    if ncf_ind:
        for period, val in (ncf_ind.time_series or {}).items():
            year = _extract_year(period)
            if year:
                yearly.setdefault(year, {"year": year})
                try:
                    yearly[year]["net_cashflow"] = float(val)
                except (ValueError, TypeError):
                    pass

    # DSCR — compute from EBITDA / debt_service if not available as indicator
    dscr_ind = _find_indicator(graph, "DSCR")
    if not dscr_ind:
        dscr_ind = _find_indicator(graph, "偿债备付率")
    if dscr_ind:
        for period, val in (dscr_ind.time_series or {}).items():
            year = _extract_year(period)
            if year and year in yearly:
                try:
                    yearly[year]["dscr"] = round(float(val), 2)
                except (ValueError, TypeError):
                    pass
    else:
        # Compute DSCR = (EBITDA - income_tax) / debt_service
        # EBITDA should be 全投资口径 (much larger than 资本金)
        for entry in yearly.values():
            ebitda = entry.get("ebitda", 0)
            tax = entry.get("income_tax", 0)
            ds = entry.get("debt_service", 0)
            if ds and ds > 0 and ebitda > 0:
                entry["dscr"] = round((ebitda - tax) / ds, 2)

    # If revenue not found from profit statement, try 表8 cash flow
    if not yearly:
        revenue_ind = _find_indicator(graph, "营业收入", "表8")
        if revenue_ind:
            for period, val in (revenue_ind.time_series or {}).items():
                year = _extract_year(period)
                if year:
                    yearly.setdefault(year, {"year": year})
                    try:
                        yearly[year]["revenue"] = float(val)
                    except (ValueError, TypeError):
                        pass

    # Sort by year and compute cumulative
    result = sorted(yearly.values(), key=lambda x: x.get("year", 0))
    cumulative = 0.0
    for entry in result:
        nc = entry.get("net_cashflow", 0.0)
        cumulative += nc
        entry["cumulative_cashflow"] = cumulative

    return result


def list_finance_tables(graph: FinancialGraph) -> list[dict]:
    """List all financial statement tables in the graph."""
    finance_keywords = ["利润", "现金流", "资产负债", "全投资", "资本金", "效益", "偿债",
                        "资金", "还本", "折旧", "成本", "收入", "税金"]
    tables = []
    for tbl in graph.tables.values():
        name = tbl.name or ""
        if any(k in name for k in finance_keywords):
            tables.append({
                "id": tbl.id,
                "name": name,
                "sheet": tbl.sheet,
                "indicator_count": len(tbl.indicator_ids),
            })
    return tables


# ── Internal helpers ────────────────────────────────────────────────────────

def _extract_year(period: str) -> int | None:
    """Extract a 4-digit year from a period string like '2030', '2030-12', '2030-07-31'."""
    if not period:
        return None
    m = re.match(r"(\d{4})", period)
    if m:
        return int(m.group(1))
    return None
