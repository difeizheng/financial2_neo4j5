"""Financial metrics calculation engine.

Pure Python + numpy implementation of IRR, NPV, DSCR, loan schedules,
depreciation, and sensitivity analysis. No dependency on graph models.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class YearProjection:
    """Single year financial projection."""
    year: int
    revenue: float = 0.0
    operating_cost: float = 0.0
    ebitda: float = 0.0
    income_tax: float = 0.0
    depreciation: float = 0.0
    debt_service: float = 0.0
    principal_payment: float = 0.0
    interest_payment: float = 0.0
    net_cashflow: float = 0.0
    cumulative_cashflow: float = 0.0
    dscr: Optional[float] = None


@dataclass
class FinanceParams:
    """Financial scenario parameters."""
    # Investment
    epc_cost: float = 7098.59
    land_cost: float = 74.00
    supervision_cost: float = 411.00
    contingency: float = 210.00
    working_capital: float = 622.61

    # Financing
    loan_amount: float = 7098.59
    loan_rate: float = 0.015
    grace_periods: int = 5
    total_periods: int = 30
    mgmt_fee_rate: float = 0.0025
    commitment_fee_rate: float = 0.0025

    # Revenue
    electricity_price: float = 0.1876
    base_sales_volume: float = 144.75
    ramp_up_years: int = 6
    ramp_up_first_year_rate: float = 0.50
    loss_rate: float = 0.2475

    # Cost
    material_rate: float = 0.005
    insurance_rate: float = 0.002
    maintenance_rate_early: float = 0.006
    maintenance_rate_late: float = 0.012
    staff_cost_year1: float = 1.0
    staff_count: int = 20
    cpi_rate: float = 0.0346
    power_price: float = 0.1055

    # Depreciation
    depreciation_years: int = 15
    salvage_rate: float = 0.05

    # Tax
    income_tax_rate: float = 0.30

    # Timing
    construction_years: int = 2
    start_year: int = 2026


@dataclass
class FinanceResult:
    yearly: list[YearProjection] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    sensitivity: dict = field(default_factory=dict)


# ──────────────────────────────────────────────
# Core calculations
# ──────────────────────────────────────────────

def calculate_npv(cash_flows: list[float], discount_rate: float) -> float:
    """Net Present Value."""
    return sum(cf / (1 + discount_rate) ** i for i, cf in enumerate(cash_flows))


def calculate_irr(
    cash_flows: list[float],
    guess: float = 0.1,
    max_iter: int = 1000,
    tol: float = 1e-10,
) -> Optional[float]:
    """Internal Rate of Return via Newton-Raphson."""
    rate = guess
    for _ in range(max_iter):
        npv = sum(cf / (1 + rate) ** i for i, cf in enumerate(cash_flows))
        if abs(npv) < tol:
            return rate
        d_npv = sum(
            -i * cf / (1 + rate) ** (i + 1)
            for i, cf in enumerate(cash_flows)
        )
        if abs(d_npv) < 1e-15:
            break
        rate -= npv / d_npv
        if rate <= -1.0:
            rate = -0.99
    return None


def calculate_payback_period(cash_flows: list[float]) -> float:
    """Discounted payback period (years, fractional interpolation)."""
    cumulative = 0.0
    for i, cf in enumerate(cash_flows):
        cumulative += cf
        if cumulative >= 0:
            prev = cumulative - cf
            return i - 1 + (0 - prev) / cf if cf != 0 else float("inf")
    return float("inf")


def calculate_equivalent_annuity(
    principal: float, rate: float, periods: int
) -> float:
    """Annual payment for equivalent principal-and-interest loan."""
    if rate == 0:
        return principal / periods if periods > 0 else 0.0
    return principal * rate * (1 + rate) ** periods / (
        (1 + rate) ** periods - 1
    )


def generate_loan_schedule(
    principal: float,
    annual_rate: float,
    total_periods: int,
    grace_periods: int,
) -> list[dict]:
    """Year-by-year loan repayment schedule.

    Grace period: interest only.
    Repayment period: equivalent principal-and-interest.
    """
    schedule = []
    remaining = principal

    repayment_periods = total_periods - grace_periods
    if repayment_periods > 0:
        annual_payment = calculate_equivalent_annuity(
            principal, annual_rate, repayment_periods
        )
    else:
        annual_payment = 0.0

    for year in range(total_periods):
        interest = remaining * annual_rate

        if year < grace_periods:
            principal_payment = 0.0
            debt_service = interest
        else:
            principal_payment = annual_payment - interest
            if principal_payment > remaining:
                principal_payment = remaining
            debt_service = principal_payment + interest

        remaining -= principal_payment
        if remaining < 1e-6:
            remaining = 0.0

        schedule.append(
            {
                "year": year + 1,
                "interest": interest,
                "principal": principal_payment,
                "debt_service": debt_service,
                "remaining": remaining,
            }
        )

    return schedule


def calculate_straight_line_depreciation(
    original_value: float, salvage_rate: float, years: int
) -> list[dict]:
    """Straight-line depreciation schedule."""
    salvage_value = original_value * salvage_rate
    depreciable = original_value - salvage_value
    annual_dep = depreciable / years if years > 0 else 0.0

    result = []
    accumulated = 0.0
    book_value = original_value

    for year in range(1, years + 1):
        dep = annual_dep
        accumulated += dep
        book_value -= dep
        result.append(
            {
                "year": year,
                "depreciation": dep,
                "accumulated": accumulated,
                "book_value": book_value,
            }
        )

    return result


# ──────────────────────────────────────────────
# Simulation engine
# ──────────────────────────────────────────────

def _coerce_params(params: FinanceParams) -> None:
    """Ensure int fields are int (data_editor returns float)."""
    for attr in ["grace_periods", "total_periods", "ramp_up_years",
                 "staff_count", "depreciation_years", "construction_years", "start_year"]:
        val = getattr(params, attr)
        if isinstance(val, float):
            setattr(params, attr, int(val))


def run_finance_simulation(params: FinanceParams) -> FinanceResult:
    """Run full 30-year financial projection."""
    # Coerce int fields from data_editor (may return float)
    _coerce_params(params)

    # Derived values
    fixed_asset_value = params.epc_cost + params.land_cost + params.supervision_cost
    # Financing fees (management + commitment, charged during construction)
    mgmt_fee = params.loan_amount * params.mgmt_fee_rate
    commitment_fee = params.loan_amount * params.commitment_fee_rate
    total_investment = fixed_asset_value + params.contingency + params.working_capital + mgmt_fee + commitment_fee

    # Depreciation
    dep_schedule = calculate_straight_line_depreciation(
        fixed_asset_value, params.salvage_rate, params.depreciation_years
    )
    dep_by_year = {d["year"]: d["depreciation"] for d in dep_schedule}

    # Loan schedule
    loan_sched = generate_loan_schedule(
        params.loan_amount, params.loan_rate, params.total_periods, params.grace_periods
    )

    # Cost base (annual, fixed-asset-ratio based)
    annual_material = fixed_asset_value * params.material_rate
    annual_insurance = fixed_asset_value * params.insurance_rate

    # Revenue ramp-up
    ramp_rates = []
    current = params.ramp_up_first_year_rate
    step = (1.0 - params.ramp_up_first_year_rate) / (params.ramp_up_years - 1)
    for y in range(params.ramp_up_years):
        rate = current + step * y
        ramp_rates.append(min(rate, 1.0))
    # Pad to total periods
    while len(ramp_rates) < params.total_periods:
        ramp_rates.append(1.0)

    # Cumulative cash flow tracking
    cumulative = 0.0
    yearly_projections: list[YearProjection] = []

    for year_idx in range(params.total_periods):
        year_num = year_idx + 1
        is_construction = year_idx < params.construction_years
        op_year = year_num - params.construction_years  # 1-based operating year

        if is_construction:
            # Construction years: no revenue, no operating cost
            proj = YearProjection(
                year=year_num,
                revenue=0.0,
                operating_cost=0.0,
                ebitda=0.0,
                income_tax=0.0,
                depreciation=0.0,
                debt_service=0.0,
                principal_payment=0.0,
                interest_payment=0.0,
                net_cashflow=0.0,
                cumulative_cashflow=cumulative,
            )
            yearly_projections.append(proj)
            continue

        # Revenue
        ramp = ramp_rates[op_year - 1] if op_year - 1 < len(ramp_rates) else 1.0
        sales_volume = params.base_sales_volume * ramp
        revenue = sales_volume * params.electricity_price * 100  # GWh × USD/kWh × 100 = 万美元

        # Operating cost
        # Purchase power cost
        purchase_volume = sales_volume / (1 - params.loss_rate)
        power_cost = purchase_volume * params.power_price * 100  # GWh × USD/kWh × 100 = 万美元

        # Maintenance (rate changes after year 15)
        maint_rate = (
            params.maintenance_rate_early
            if op_year <= 15
            else params.maintenance_rate_late
        )
        maintenance = fixed_asset_value * maint_rate

        # Staff cost (CPI-adjusted)
        staff_cost = params.staff_cost_year1 * (1 + params.cpi_rate) ** (op_year - 1) * params.staff_count

        # Material + insurance
        operating_cost = power_cost + maintenance + staff_cost + annual_material + annual_insurance

        # EBITDA
        ebitda = revenue - operating_cost

        # Depreciation
        depreciation = dep_by_year.get(op_year, 0.0)

        # Tax (on EBITDA - depreciation, i.e. operating profit)
        operating_profit = ebitda - depreciation
        income_tax = max(0.0, operating_profit * params.income_tax_rate)

        # Debt service
        ls = loan_sched[year_idx] if year_idx < len(loan_sched) else {}
        debt_service = ls.get("debt_service", 0.0)
        principal_payment = ls.get("principal", 0.0)
        interest_payment = ls.get("interest", 0.0)

        # Net cashflow: EBITDA - tax - debt_service
        net_cashflow = ebitda - income_tax - debt_service
        cumulative += net_cashflow

        # DSCR
        dscr = (ebitda - income_tax) / debt_service if debt_service > 0 else None

        proj = YearProjection(
            year=year_num,
            revenue=revenue,
            operating_cost=operating_cost,
            ebitda=ebitda,
            income_tax=income_tax,
            depreciation=depreciation,
            debt_service=debt_service,
            principal_payment=principal_payment,
            interest_payment=interest_payment,
            net_cashflow=net_cashflow,
            cumulative_cashflow=cumulative,
            dscr=dscr,
        )
        yearly_projections.append(proj)

    # Summary metrics
    # Full investment IRR: total investment outflow + operating EBITDA (pre-tax) or EBITDA-tax (post-tax)
    op_years = [yp for yp in yearly_projections if yp.year > params.construction_years]
    n_op = len(op_years)

    # Pre-tax full investment flows
    pre_tax_flows = [-total_investment]
    for yp in op_years:
        pre_tax_flows.append(yp.ebitda)

    # Post-tax full investment flows: EBITDA - income_tax (not subtracting debt service, since full investment ignores financing)
    post_tax_flows = [-total_investment]
    for yp in op_years:
        post_tax_flows.append(yp.ebitda - yp.income_tax)

    irr_pre = calculate_irr(pre_tax_flows)
    irr_post = calculate_irr(post_tax_flows)

    # NPV at 3% discount
    npv_pre = calculate_npv(pre_tax_flows, 0.03)
    npv_post = calculate_npv(post_tax_flows, 0.03)

    # Payback period
    payback_pre = calculate_payback_period(pre_tax_flows)
    payback_post = calculate_payback_period(post_tax_flows)

    # DSCR stats
    dscr_values = [yp.dscr for yp in yearly_projections if yp.dscr is not None]
    avg_dscr = sum(dscr_values) / len(dscr_values) if dscr_values else 0.0
    min_dscr = min(dscr_values) if dscr_values else 0.0

    # Annual averages (operating period only)
    avg_revenue = sum(y.revenue for y in op_years) / n_op if n_op else 0
    avg_op_cost = sum(y.operating_cost for y in op_years) / n_op if n_op else 0
    avg_total_cost = avg_op_cost + sum(y.depreciation for y in op_years) / n_op if n_op else 0
    avg_profit = sum(y.ebitda - y.depreciation for y in op_years) / n_op if n_op else 0
    avg_tax = sum(y.income_tax for y in op_years) / n_op if n_op else 0
    avg_net_profit = sum(y.ebitda - y.depreciation - y.income_tax for y in op_years) / n_op if n_op else 0
    avg_net_cf = sum(y.net_cashflow for y in op_years) / n_op if n_op else 0

    # Equity IRR: equity outflow + net cashflow (after debt service)
    equity_investment = total_investment - params.loan_amount
    equity_flows = [-equity_investment]
    for yp in op_years:
        equity_flows.append(yp.net_cashflow)
    irr_equity = calculate_irr(equity_flows)

    metrics = {
        "total_investment": total_investment,
        "fixed_asset_value": fixed_asset_value,
        "equity_investment": equity_investment,
        "loan_amount": params.loan_amount,
        "avg_revenue": avg_revenue,
        "avg_operating_cost": avg_op_cost,
        "avg_total_cost": avg_total_cost,
        "avg_profit": avg_profit,
        "avg_tax": avg_tax,
        "avg_net_profit": avg_net_profit,
        "avg_net_cashflow": avg_net_cf,
        "irr_pre_tax": irr_pre,
        "irr_post_tax": irr_post,
        "irr_equity": irr_equity,
        "npv_pre_tax": npv_pre,
        "npv_post_tax": npv_post,
        "payback_pre_tax": payback_pre,
        "payback_post_tax": payback_post,
        "avg_dscr": avg_dscr,
        "min_dscr": min_dscr,
        "cumulative_cashflow": cumulative,
    }

    return FinanceResult(
        yearly=yearly_projections,
        metrics=metrics,
    )


# ──────────────────────────────────────────────
# Sensitivity analysis
# ──────────────────────────────────────────────

def run_sensitivity_analysis(
    base_params: FinanceParams,
    factors: Optional[list[str]] = None,
    deltas: Optional[list[float]] = None,
) -> dict:
    """Multi-factor sensitivity analysis.

    Each factor is adjusted ±delta and re-simulated.
    """
    if factors is None:
        factors = ["revenue", "operating_cost", "investment"]
    if deltas is None:
        deltas = [0.10]

    param_map = {
        "revenue": "electricity_price",
        "operating_cost": "power_price",
        "investment": "epc_cost",
    }

    base_result = run_finance_simulation(base_params)
    base_metrics = base_result.metrics

    matrix: dict[str, dict] = {}

    for factor in factors:
        param_name = param_map.get(factor)
        if not param_name:
            continue
        base_value = getattr(base_params, param_name)
        factor_matrix = {}

        for delta in deltas:
            for sign in [1, -1]:
                multiplier = 1 + sign * delta
                modified = FinanceParams()
                for k, v in base_params.__dict__.items():
                    setattr(modified, k, v)
                setattr(modified, param_name, base_value * multiplier)

                result = run_finance_simulation(modified)
                label = f"{sign * delta:+.0%}"
                factor_matrix[label] = {
                    "irr_pre_tax": result.metrics["irr_pre_tax"],
                    "irr_post_tax": result.metrics["irr_post_tax"],
                    "npv_pre_tax": result.metrics["npv_pre_tax"],
                    "npv_post_tax": result.metrics["npv_post_tax"],
                    "payback_post_tax": result.metrics["payback_post_tax"],
                    "avg_dscr": result.metrics["avg_dscr"],
                    "min_dscr": result.metrics["min_dscr"],
                }

        matrix[factor] = factor_matrix

    return {
        "base": {
            "irr_pre_tax": base_metrics["irr_pre_tax"],
            "irr_post_tax": base_metrics["irr_post_tax"],
            "npv_pre_tax": base_metrics["npv_pre_tax"],
            "npv_post_tax": base_metrics["npv_post_tax"],
            "payback_post_tax": base_metrics["payback_post_tax"],
            "avg_dscr": base_metrics["avg_dscr"],
            "min_dscr": base_metrics["min_dscr"],
        },
        "matrix": matrix,
    }
