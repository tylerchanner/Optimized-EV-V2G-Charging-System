# optimiser.py
import pulp
from pulp import (
    LpProblem, LpMinimize, LpVariable, LpBinary,
    lpSum, PULP_CBC_CMD
)

def run_optimiser(
    solar_forecast:           list[float],
    grid_prices:              list[float],
    grid_demand:              list[float],
    deadline_hour:            int,
    required_energy:          float,
    eco_mode:                 bool    = False,
    cycle_degradation_cost:   float   = 1.0,    # £ per full (in+out) cycle
    battery_capacity:         float   = 75.0,   # kWh
    max_charge_rate:          float   = 11.0,   # kW
    max_discharge_rate:       float   = 11.0,   # kW
    initial_soc:              float   = 37.5,   # kWh
    switch_penalty:           float   = 0.05,   # £ per discharge event
    v2g_sell_price:           float   = 0.10,   # £/kWh
    co2_price_per_kg:         float   = 0.0,    # £ you’ll pay per kg CO₂
    emission_factor:          float   = 0.233,  # kg CO₂ per kWh grid draw
    grid_demand_threshold:    float   = 30000
) -> dict:
    """
    Modes:
      - eco_mode=False → cost-minimisation as before.
      - eco_mode=True  → solar-only V2G-arbitrage, end at initial_soc.
    """

    # Horizon
    H = min(deadline_hour, len(solar_forecast))
    sf, gp, gd = solar_forecast[:H], grid_prices[:H], grid_demand[:H]

    # DEBUG: dump all inputs
    print("Optimiser inputs (first H hours):")
    print("Hour |   PV[kW] | Price[£/kWh] | Demand[kW]")
    for h in range(H):
        print(f"{h:02d}   | {sf[h]:7.2f}  |    {gp[h]:6.2f}    |  {gd[h]:7.2f}")
    print("────────────────────────────────────────────────────────")

    # Unit wear & CO₂ costs
    wear_cost       = cycle_degradation_cost / (2 * battery_capacity)
    co2_cost_per_kwh= co2_price_per_kg * emission_factor

    # Build LP
    model = LpProblem("EV_Optimisation", LpMinimize)

    # Variables
    cPV = [LpVariable(f"cPV_{h}", 0, sf[h])             for h in range(H)]
    cG  = [LpVariable(f"cG_{h}",  0, max_charge_rate)   for h in range(H)]
    dG  = [LpVariable(f"dG_{h}",  0, max_discharge_rate)for h in range(H)]
    yPV = [LpVariable(f"yPV_{h}", cat=LpBinary)         for h in range(H)]
    yG  = [LpVariable(f"yG_{h}",  cat=LpBinary)         for h in range(H)]
    yD  = [LpVariable(f"yD_{h}",  cat=LpBinary)         for h in range(H)]
    E   = [LpVariable(f"E_{h}",   0, battery_capacity)  for h in range(H+1)]

    # 1) Initial SoC
    model += E[0] == initial_soc

    # 2) Hourly constraints
    for h in range(H):
        # balance
        model += E[h+1] == E[h] + cPV[h] + cG[h] - dG[h]
        # link flows ↔ binaries
        model += cPV[h] <= sf[h] * yPV[h]
        model += cG[h]  <= max_charge_rate * yG[h]
        model += dG[h]  <= max_discharge_rate * yD[h]
        # exactly one action
        model += yPV[h] + yG[h] + yD[h] == 1
        # V2G gating
        if gd[h] < grid_demand_threshold:
            model += yD[h] == 0
        # only discharge if SoC enough
        model += E[h] >= (required_energy if not eco_mode else initial_soc) * yD[h]

    # 3) Final SoC
    if eco_mode:
        # end at starting SoC to complete a full solar→V2G cycle
        model += E[H] == initial_soc, "EcoFinalSoC"
        # forbid any grid charging
        for h in range(H):
            model += yG[h] == 0
    else:
        # hit your required energy
        model += E[H] == required_energy, "FinalSoC"

    # 4) Build per-hour cost vs profit terms
    cost_terms = []
    for h in range(H):
        # cost to pay
        cost = (
            gp[h]*cG[h]
          - v2g_sell_price*dG[h]
          + wear_cost*(cPV[h] + cG[h] + dG[h])
          + switch_penalty*yD[h]
          + co2_cost_per_kwh*cG[h]
        )
        cost_terms.append(cost)

    profit_terms = []
    for h in range(H):
        # profit from V2G arbitrage (ignores CO₂ cost)
        prof = (
            v2g_sell_price*dG[h]
          - gp[h]*cG[h]
          - wear_cost*(cPV[h] + cG[h] + dG[h])
          - switch_penalty*yD[h]
        )
        profit_terms.append(prof)

    # 5) Objective
    if eco_mode:
        # maximise profit = minimise -profit_terms
        model += -lpSum(profit_terms), "EcoProfitObjective"
    else:
        # minimise full cost
        model += lpSum(cost_terms), "CostObjective"

    # Solve
    status = model.solve(PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != 'Optimal':
        raise RuntimeError(f"Solver failed ({pulp.LpStatus[status]})")

    # Extract
    solar_charging   = [v.value() for v in cPV] + [0.0]
    grid_charging    = [v.value() for v in cG]  + [0.0]
    grid_discharging = [v.value() for v in dG]  + [0.0]
    battery_soc      = [v.value() for v in E]

    # Summaries
    net_cost       = sum(grid_charging[h]*gp[h] - grid_discharging[h]*v2g_sell_price
                         for h in range(H))
    co2_emitted    = sum(grid_charging[h]*emission_factor for h in range(H))
    co2_avoided    = sum(solar_charging[:-1])*emission_factor

    # … after computing net_cost, co2_emitted, co2_avoided …

    # DEBUG: dump the optimiser’s outputs
    print(">>> Optimiser outputs:")
    print(" solar_charging:   ", [f"{x:.2f}" for x in solar_charging])
    print(" grid_charging:    ", [f"{x:.2f}" for x in grid_charging])
    print(" grid_discharging: ", [f"{x:.2f}" for x in grid_discharging])
    print(" battery_soc:      ", [f"{x:.2f}" for x in battery_soc])
    print(" net_cost:         ", f"{net_cost:.2f}")
    print(" co2_emitted_kg:   ", f"{co2_emitted:.2f}")
    print(" co2_avoided_kg:   ", f"{co2_avoided:.2f}")
    print(" filled_by_deadline:", f"{battery_soc[H]:.2f}")
    print("────────────────────────────────────────────────────────")

    return {
        'solar_charging': solar_charging,
        'grid_charging': grid_charging,
        'grid_discharging': grid_discharging,
        'battery_soc': battery_soc,
        'net_cost': net_cost,
        'co2_emitted_kg': co2_emitted,
        'co2_avoided_kg': co2_avoided,
        'filled_by_deadline': battery_soc[H]
    }


def compute_baseline_cost(grid_prices, required_energy, max_charge_rate):
    hours_prices = sorted(
        [(grid_prices[h], h) for h in range(len(grid_prices))],
        key=lambda x: x[0]
    )
    remaining   = required_energy
    cost        = 0.0
    charge_plan = [0.0] * len(grid_prices)

    for price, h in hours_prices:
        charge = min(max_charge_rate, remaining)
        charge_plan[h] = charge
        cost += charge * price
        remaining -= charge
        if remaining <= 1e-6:
            break

    return cost, charge_plan
