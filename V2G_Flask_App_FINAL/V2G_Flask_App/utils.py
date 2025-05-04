from optimiser import compute_baseline_cost
import calendar
import os
import json
import requests
from datetime import datetime,timedelta


def generate_summary(
    result, required_range, deadline_hour,
    grid_prices=None, energy_per_mile=0.25, max_charge_rate=11.0
):
    # compute with passed energy_per_mile
    required_energy = required_range * energy_per_mile
    start_soc = result["battery_soc"][0]
    end_soc   = result["battery_soc"][deadline_hour]

    solar_used = sum(result["solar_charging"])
    grid_used  = sum(result["grid_charging"])
    discharged = sum(result["grid_discharging"])
    net_cost   = result["net_cost"]

    lines = [
        "🔋 Battery Summary",
        f"• Start SoC: {start_soc:.2f} kWh",
        f"• End SoC by deadline: {end_soc:.2f} kWh",
        f"• Required energy: {required_energy:.2f} kWh\n",
        "☀️ Solar & Grid Use",
        f"• Solar Used: {solar_used:.2f} kWh",
        f"• Grid Used: {grid_used:.2f} kWh",
        f"• Discharged to Grid (V2G): {discharged:.2f} kWh\n",
        "💷 Cost Analysis",
        f"• Net Cost: {'-' if net_cost<0 else ''}£{abs(net_cost):.2f}"
    ]

    if grid_prices:
        baseline_cost, _ = compute_baseline_cost(grid_prices, required_energy, max_charge_rate)
        savings = baseline_cost - net_cost
        lines += [
          f"• Full Grid Baseline Cost: £{baseline_cost:.2f}",
          f"• Savings vs Baseline: £{savings:.2f}\n"
        ]

    # … rest of your suggestions logic …
    return "\n".join(lines)




def format_charging_plan(
    result,
    sim_start_hour: int,
    deadline_hour: int,
    day_offset: int
) -> str:
    """
    Builds the downloadable plan text.

    sim_start_hour: hour of day that simulation begins (0–23)
    day_offset:     number of days after today (0 = tomorrow)
    """

    def label(h):
        return (sim_start_hour + h) % 24

    def day_index(h):
        return (sim_start_hour + h) // 24

    def fmt_hour(h):
        hh = label(h)
        suffix = "AM" if hh < 12 else "PM"
        disp   = hh if 1 <= hh <= 12 else (12 if hh == 0 else hh - 12)
        return f"{disp:02d}:00 {suffix}"

    # 1) Group continuous blocks by action
    blocks = {"Solar Charging": [], "Grid Charging": [], "Discharging": []}
    active = {k: None for k in blocks}

    for h in range(deadline_hour + 1):
        acts = {
            "Solar Charging":   result["solar_charging"][h] > 0,
            "Grid Charging":    result["grid_charging"][h]  > 0,
            "Discharging":      result["grid_discharging"][h] > 0
        }
        for act, is_on in acts.items():
            if is_on and active[act] is None:
                active[act] = h
            if not is_on and active[act] is not None:
                blocks[act].append((active[act], h-1))
                active[act] = None

    # close any still-open blocks
    for act, start in active.items():
        if start is not None:
            blocks[act].append((start, deadline_hour))

    # 2) Compute real start date
    today = datetime.now().date()
    start_date = today + timedelta(days=day_offset + 1)
    header = [
        "🔌 SMART EV CHARGING PLAN",
        f"Simulation starts on {start_date.strftime('%A %d %b %Y')} at {fmt_hour(0)}",
        ""
    ]

    # 3) Build per-day schedule
    body = []
    # figure out how many days appear in the plan
    max_day = max((day_index(r[1]) for bl in blocks.values() for r in bl), default=0)
    for di in range(max_day + 1):
        this_date = start_date + timedelta(days=di)
        entries = []
        for act, ranges in blocks.items():
            for (s, e) in ranges:
                if day_index(s) == di:
                    entries.append(f"{act}: {fmt_hour(s)} – {fmt_hour(e+1)}")
        if entries:
            body.append(f"📅 {this_date.strftime('%A %d %b')}")
            body.extend(f"• {line}" for line in entries)
            body.append("")  # blank line

    return "\n".join(header + body)

def group_hours_to_ranges(hours):
    if not hours:
        return []

    hours.sort()
    ranges = []
    start = hours[0]

    for i in range(1, len(hours)):
        if hours[i] != hours[i - 1] + 1:
            ranges.append(f"{format_hour(start)} – {format_hour(hours[i-1] + 1)}")
            start = hours[i]
    ranges.append(f"{format_hour(start)} – {format_hour(hours[-1] + 1)}")
    return ranges

def format_hour(h):
    hour = h % 24
    suffix = "AM" if hour < 12 else "PM"
    display = hour if 1 <= hour <= 12 else (12 if hour == 0 else hour - 12)
    return f"{display} {suffix}"

def load_settings():
    defaults = {
      "battery_capacity": 75.0,
      "initial_soc": 37.5,
      "charge_rate": 11.0,
      "discharge_rate": 11.0,
      "energy_per_mile": 0.25,
      "min_soc_limit": 0.5,
      "degradation_cost_per_kwh": 0.01,
      "v2g_sell_price": 0.10,
    }
    if os.path.exists("settings.json"):
        with open("settings.json") as f:
            user = json.load(f)
        defaults.update(user)
    return defaults


