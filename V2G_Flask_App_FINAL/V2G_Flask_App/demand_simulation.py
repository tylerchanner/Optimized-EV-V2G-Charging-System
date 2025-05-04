import numpy as np
import pandas as pd
from datetime import datetime

def generate_grid_demand_realistic(hours=168, seed=None):
    if seed is not None:
        np.random.seed(seed)

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    time_index = pd.date_range(start=now, periods=hours, freq="H")

    demand = []
    for timestamp in time_index:
        hour = timestamp.hour
        weekday = timestamp.weekday()  # 0=Monday, 6=Sunday

        # Time-of-day base demand
        if hour in range(6, 9):
            base = 32000  # Morning peak
        elif hour in range(17, 21):
            base = 35000  # Evening peak
        elif hour in range(0, 6) or hour in range(22, 24):
            base = 22000  # Overnight low
        else:
            base = 27000  # Midday average

        # Weekend adjustment
        if weekday >= 5:
            base *= 0.85

        # Add natural fluctuation
        noise = np.random.normal(0, 1500)
        demand.append(base + noise)

    return demand

def generate_price_profile_realistic(demand, base_price=0.12):
    prices = []

    for d in demand:
        if d >= 34000:
            prices.append(0.30)  # Peak
        elif d >= 30000:
            prices.append(0.20)  # Mid
        elif d >= 25000:
            prices.append(0.15)  # Normal
        else:
            prices.append(0.10)  # Off-peak

    return prices
