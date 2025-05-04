import os
import io
import json
import uuid
import os
from dotenv import load_dotenv


from flask import Flask, render_template, request, send_file, jsonify,redirect,url_for
import googlemaps
from googlemaps import Client as GoogleMapsClient
from weather_utils import WeatherFetcher
from cnn_forecaster import CNNForecaster
from utils import load_settings, generate_summary, format_charging_plan
from demand_simulation import generate_grid_demand_realistic
from optimiser import run_optimiser
from datetime import datetime, timedelta, timezone
load_dotenv()
app = Flask(__name__)
GMAPS = googlemaps.Client(key=os.getenv("GOOGLE_API_KEY"))
CACHE_FILE = "dashboard_weather_cache.json"

if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)

@app.route("/planner", methods=["GET", "POST"])
def planner():
    import pytz

    settings  = load_settings()
    max_range = settings["battery_capacity"] / settings["energy_per_mile"]

    # Forecasting setup
    wf = WeatherFetcher(settings["latitude"], settings["longitude"])
    forecaster = CNNForecaster(
        model_path    = settings["model_name"],
        scaler_X_path = settings["scaler_X_path"],
        scaler_y_path = settings["scaler_y_path"]
    )

    if request.method == "POST":
        mode     = request.form.get("mode", "basic")
        eco_mode = "eco_mode" in request.form
        error    = None

        # ── 1) Determine required_range ────────────────────
        if mode == "route":
            origin_addr = request.form.get("origin_addr", "").strip()
            dest_addr   = request.form.get("dest_addr",  "").strip()
            if not origin_addr or not dest_addr:
                error = "Please enter both origin and destination."
            else:
                orig_lat = request.form.get("origin_lat")
                orig_lng = request.form.get("origin_lng")
                dest_lat = request.form.get("dest_lat")
                dest_lng = request.form.get("dest_lng")

                if not (orig_lat and orig_lng):
                    geo = GMAPS.geocode(origin_addr)
                    if not geo:
                        error = f"Could not geocode origin: {origin_addr}"
                    else:
                        loc = geo[0]["geometry"]["location"]
                        orig_lat, orig_lng = loc["lat"], loc["lng"]

                if not error and not (dest_lat and dest_lng):
                    geo = GMAPS.geocode(dest_addr)
                    if not geo:
                        error = f"Could not geocode destination: {dest_addr}"
                    else:
                        loc = geo[0]["geometry"]["location"]
                        dest_lat, dest_lng = loc["lat"], loc["lng"]

            if error:
                return render_template("planner.html",
                                       mode=mode, result=None,
                                       error=error, max_range=max_range)

            orig = (float(orig_lat), float(orig_lng))
            dest = (float(dest_lat), float(dest_lng))
            matrix = GMAPS.distance_matrix(orig, dest, units="imperial")
            elem = matrix["rows"][0]["elements"][0]
            if elem.get("status") != "OK":
                error = "Could not compute route between those points."
                return render_template("planner.html",
                                       mode=mode, result=None,
                                       error=error, max_range=max_range)

            dist_m = elem["distance"]["value"]  # metres
            required_range = dist_m / 1609.344   # miles
            if required_range > max_range:
                error = f"Your battery can only support up to {max_range:.1f} miles."
                return render_template("planner.html",
                                       mode=mode, result=None,
                                       error=error, max_range=max_range)
        else:
            try:
                required_range = float(request.form["range"])
                if not (0 <= required_range <= max_range):
                    raise ValueError
            except:
                error = "Please enter a valid required range."
            if error:
                return render_template("planner.html",
                                       mode=mode, result=None,
                                       error=error, max_range=max_range)

        # ── 2) Determine simulation window ────────────────
        now_local = datetime.now()
        # round up to next hour if any minutes/seconds
        if now_local.minute > 0:
            start_local = now_local + timedelta(hours=1)
        else:
            start_local = now_local
        start_local = start_local.replace(minute=0, second=0, microsecond=0)
        sim_start_naive = start_local.hour

        day         = int(request.form.get("day", 0))
        deadline_hr = int(request.form.get("deadline", 0))
        abs_target  = day*24 + deadline_hr
        if abs_target < sim_start_naive:
            abs_target += 24
        deadline_hour = abs_target - sim_start_naive

        # ── 3) Fetch PV history + forecast ────────────────
        now_utc = datetime.now(timezone.utc)
        if any((now_utc.minute, now_utc.second, now_utc.microsecond)):
            now_utc = (now_utc + timedelta(hours=1)) \
                      .replace(minute=0, second=0, microsecond=0)

        seq_hours  = forecaster.seq_length
        hist_start = now_utc - timedelta(hours=seq_hours)
        future_end = now_utc + timedelta(hours=deadline_hour)

        hist_df   = wf.fetch_range(hist_start, now_utc)
        future_df = wf.fetch_range(now_utc, future_end)

        # ── 4) Align and slice future_df to local window ──
        tz = pytz.timezone("Europe/London")
        # make start_local tz-aware
        start_local = tz.localize(start_local)
        end_local = start_local + timedelta(hours=deadline_hour + 1)

        # convert DataFrame to local tz and filter
        future_df["datetime"] = future_df["datetime"].dt.tz_convert("Europe/London")
        future_df = future_df[
            (future_df["datetime"] >= start_local) &
            (future_df["datetime"] <  end_local)
        ].reset_index(drop=True)

        # ── 5) Build solar array ──────────────────────────
        solar = forecaster.predict(
            historical_df=hist_df,
            future_df    = future_df,
            horizon      = len(future_df)
        ).tolist()

        # debug print
        for dt, pv in zip(future_df["datetime"], solar):
            print(f"{dt.strftime('%Y-%m-%d %H:%M')} → PV: {pv:.2f} kW")

        # ── 6) Simulate demand & build tariff ────────────
        demand = generate_grid_demand_realistic(len(solar))
        tariff_schedule = [
            0.10 if dt.hour < 7 else
            0.15 if dt.hour < 15 else
            0.30 if dt.hour < 19 else
            0.20
            for dt in future_df["datetime"]
        ]

        # ── 7) Run optimiser ─────────────────────────────
        required_energy = required_range * settings["energy_per_mile"]
        result = run_optimiser(
            solar_forecast         = solar,
            grid_prices            = tariff_schedule,
            grid_demand            = demand,
            deadline_hour          = deadline_hour,
            required_energy        = required_energy,
            eco_mode               = eco_mode,
            cycle_degradation_cost = settings["cycle_degradation_cost"],
            battery_capacity       = settings["battery_capacity"],
            max_charge_rate        = settings["charge_rate"],
            max_discharge_rate     = settings["discharge_rate"],
            initial_soc            = settings["initial_soc"],
            switch_penalty         = settings["switch_penalty"],
            v2g_sell_price         = settings["v2g_sell_price"],
            co2_price_per_kg       = settings.get("co2_price_per_kg", 0.0),
            emission_factor        = settings.get("emission_factor", 0.233),
            grid_demand_threshold  = 30000
        )

        # ── 8) Compute metrics ───────────────────────────
        avg_tariff      = sum(tariff_schedule) / len(tariff_schedule)
        baseline_cost   = required_energy * avg_tariff
        grid_in_cost    = sum(ch * tariff_schedule[i]
                              for i, ch in enumerate(result["grid_charging"]))
        grid_out_rev    = sum(dch * settings["v2g_sell_price"]
                              for dch in result["grid_discharging"])
        net_cost        = grid_in_cost - grid_out_rev
        co2_emitted_kg  = sum(ch * settings.get("emission_factor", 0.233)
                              for ch in result["grid_charging"])
        co2_avoided_kg  = sum(result["solar_charging"]) \
                          * settings.get("emission_factor", 0.233)
        net_co2_saved_kg= co2_avoided_kg - co2_emitted_kg

        # ── 9) Generate UI summary ───────────────────────
        summary = generate_summary(
            result,
            required_range,
            deadline_hour,
            grid_prices     = tariff_schedule,
            energy_per_mile = settings["energy_per_mile"],
            max_charge_rate = settings["charge_rate"]
        )

        # ── 10) Handle “Save Plan” ───────────────────────
        if request.form.get("save_plan") == "true":
            plan = {
                "id":               uuid.uuid4().hex[:8],
                "name":             request.form.get("plan_name","").strip() or "Untitled",
                "mode":             mode,
                "range":            required_range,
                "start_hour":       sim_start_naive,
                "deadline_hour":    deadline_hour,
                "result":           result,
                "baseline_cost":    baseline_cost,
                "net_cost":         net_cost,
                "money_saved":      baseline_cost - net_cost,
                "co2_emitted_kg":   co2_emitted_kg,
                "co2_avoided_kg":   co2_avoided_kg,
                "net_co2_saved_kg": net_co2_saved_kg,
                "day_offset":       day
            }
            saved = json.load(open("saved_plans.json")) if os.path.exists("saved_plans.json") else []
            saved.append(plan)
            with open("saved_plans.json","w") as f:
                json.dump(saved, f, indent=2)
            return redirect(url_for("saved_trips"))

        # ── 11) Render results ───────────────────────────
        return render_template(
            "planner.html",
            mode             = mode,
            result           = result,
            summary          = summary,
            start_hour       = sim_start_naive,
            deadline_hour    = deadline_hour,
            calculated_range = required_range,
            max_range        = max_range,
            baseline_cost    = baseline_cost,
            net_cost         = net_cost,
            money_saved      = baseline_cost - net_cost,
            co2_avoided_kg   = co2_avoided_kg,
            co2_emitted_kg   = co2_emitted_kg,
            net_co2_saved_kg = net_co2_saved_kg,
            co2_saved_kg     = net_co2_saved_kg,
            day_offset       = day
        )

    # GET → empty form
    return render_template(
        "planner.html",
        mode           = "basic",
        result         = None,
        max_range      = max_range,
        google_api_key = os.getenv("GOOGLE_API_KEY")
    )



@app.route("/download", methods=["POST"])
def download_plan():
    settings   = load_settings()
    wf         = WeatherFetcher(settings["latitude"], settings["longitude"])
    forecaster = CNNForecaster(
        model_path    = settings["model_name"],
        scaler_X_path = settings["scaler_X_path"],
        scaler_y_path = settings["scaler_y_path"]
    )

    # parse inputs
    required_range = float(request.form["range"])
    day            = int(request.form["day"])
    target_hr      = int(request.form["deadline"])
    eco_mode       = (request.form.get("eco_mode") == "True")
    sim_start      = int(request.form["start_hour"])

    # compute deadline_hour
    abs_target = day*24 + target_hr
    if abs_target < sim_start:
        abs_target += 24
    deadline_hour = abs_target - sim_start

    # fetch & predict solar
    now_utc    = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    seq_hours  = forecaster.seq_length
    hist_start = now_utc - timedelta(hours=seq_hours)
    future_end = now_utc + timedelta(hours=deadline_hour)

    hist_df   = wf.fetch_range(hist_start, now_utc)
    future_df = wf.fetch_range(now_utc, future_end)

    raw_preds = forecaster.predict(
        historical_df=hist_df,
        future_df    = future_df,
        horizon      = len(future_df)
    )
    solar = [
        float(p) if sr >= 30.0 else 0.0
        for p, sr in zip(raw_preds, future_df["solar_radiation_W_m2"])
    ]

    # simulate demand & tariff
    demand = generate_grid_demand_realistic(len(solar))
    tariff_schedule = [
        0.10 if (sim_start+h) % 24 < 7 else
        0.15 if (sim_start+h) % 24 < 15 else
        0.30 if (sim_start+h) % 24 < 19 else
        0.20
        for h in range(len(solar))
    ]

    # run optimiser
    required_energy = required_range * settings["energy_per_mile"]
    result = run_optimiser(
        solar_forecast=solar,
        grid_prices=tariff_schedule,
        grid_demand=demand,
        deadline_hour=deadline_hour,
        required_energy=required_energy,
        eco_mode=eco_mode,

        cycle_degradation_cost=settings["cycle_degradation_cost"],
        switch_penalty=settings["switch_penalty"],

        battery_capacity=settings["battery_capacity"],
        max_charge_rate=settings["charge_rate"],
        max_discharge_rate=settings["discharge_rate"],
        initial_soc=settings["initial_soc"],
        v2g_sell_price=settings["v2g_sell_price"],
        grid_demand_threshold=30000
    )

    # send plan text
    day_offset = int(request.form.get("day", 0))
    plan_text = format_charging_plan(
        result,
        sim_start,
        deadline_hour,
        day_offset
    )
    buf = io.BytesIO(plan_text.encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment   = True,
        download_name   = "charging_plan.txt",
        mimetype        = "text/plain"
    )
@app.route("/")
@app.route("/dashboard")
def dashboard():
    plans = json.load(open("saved_plans.json")) if os.path.exists("saved_plans.json") else []
    return render_template("dashboard.html", plans=plans)


@app.route("/saved_trips")
def saved_trips():
    settings = load_settings()
    raw = []
    if os.path.exists("saved_plans.json"):
        raw = json.load(open("saved_plans.json"))
    plans = []
    for p in raw:
        # regenerate the summary text exactly as in planner
        summary = generate_summary(
            p["result"],
            p["range"],
            p["deadline_hour"],
            grid_prices = p.get("grid_prices"),      # if you stored it
            energy_per_mile = settings["energy_per_mile"],
            max_charge_rate = settings["charge_rate"]
        )
        p["summary_text"] = summary
        plans.append(p)

    return render_template("saved_trips.html", plans=plans)

@app.route("/saved_trips/<plan_id>/download")
def download_saved_plan(plan_id):
    plans = json.load(open("saved_plans.json")) if os.path.exists("saved_plans.json") else []
    plan = next((p for p in plans if p["id"] == plan_id), None)
    if not plan:
        return "Not found", 404

    buf = io.BytesIO()
    buf.write(format_charging_plan(plan["result"], plan["start_hour"], plan["deadline_hour"]).encode("utf-8"))
    buf.seek(0)

    return send_file(buf, as_attachment=True, download_name=f"charging_plan_{plan_id}.txt", mimetype="text/plain")


@app.route("/delete_plan/<plan_id>")
def delete_plan(plan_id):
    from_page = request.args.get("from_page", "dashboard")  # Default to dashboard
    file_path = "saved_plans.json"
    if os.path.exists(file_path):
        saved = json.load(open(file_path))
        saved = [p for p in saved if p["id"] != plan_id]
        with open(file_path, "w") as f:
            json.dump(saved, f, indent=2)
    return redirect(url_for(from_page))


@app.route("/clear_plans")
def clear_plans():
    if os.path.exists("saved_plans.json"):
        os.remove("saved_plans.json")
    return redirect(url_for("saved_trips"))


@app.route("/edit_plan_name/<plan_id>", methods=["POST"])
def edit_plan_name(plan_id):
    new_name = request.form.get("new_name", "").strip()
    if not new_name:
        return redirect(url_for("saved_trips"))

    file_path = "saved_plans.json"
    if not os.path.exists(file_path):
        return redirect(url_for("saved_trips"))

    plans = json.load(open(file_path))
    for plan in plans:
        if plan["id"] == plan_id:
            plan["name"] = new_name
            break

    with open(file_path, "w") as f:
        json.dump(plans, f, indent=2)

    return redirect(url_for("saved_trips"))


@app.route("/api/dashboard-weather")
def api_dashboard_weather():
    settings   = load_settings()
    wf         = WeatherFetcher(settings["latitude"], settings["longitude"])
    forecaster = CNNForecaster(
        model_path    =settings["model_name"],
        scaler_X_path =settings["scaler_X_path"],
        scaler_y_path =settings["scaler_y_path"]
    )

    # always next 48 h
    seq_hours      = forecaster.seq_length
    forecast_hours = 48

    now_utc    = datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    hist_start = now_utc - timedelta(hours=seq_hours)
    future_end = now_utc + timedelta(hours=forecast_hours)

    hist_df   = wf.fetch_range(hist_start, now_utc)
    future_df = wf.fetch_range(now_utc, future_end)

    raw_preds = forecaster.predict(
        historical_df=hist_df,
        future_df    =future_df,
        horizon      =len(future_df)
    )
    solar = [
        float(p) if sr>=30.0 else 0.0
        for p,sr in zip(raw_preds, future_df["solar_radiation_W_m2"])
    ]

    payload = {
        "labels":           future_df["datetime"].dt.strftime("%a %H:%M").tolist(),
        "solar_radiation":  future_df["solar_radiation_W_m2"].tolist(),
        "cloud_cover":      future_df["cloud_cover_%"].tolist(),
        "temperature":      future_df["temperature_2m"].tolist(),
        "predicted_pv":     solar,
    }
    return jsonify(payload)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    settings_file = "settings.json"
    cars_file     = "cars.json"

    # Load the full settings dict (including model paths, coords, etc)
    current = load_settings()

    if request.method == "POST":
        # Only override the fields the user can actually change:
        editable_fields = {
            "battery_capacity":      float,
            "initial_soc":           float,  # in kWh
            "charge_rate":           float,
            "discharge_rate":        float,
            "energy_per_mile":       float,
            "cycle_degradation_cost":float,
            "switch_penalty":        float,
            "v2g_sell_price":        float,
            "car_name":              str,
        }

        for key, caster in editable_fields.items():
            if key in request.form:
                current[key] = caster(request.form[key])

        # Write the merged settings back out
        with open(settings_file, "w") as f:
            json.dump(current, f, indent=2)

        return redirect(url_for("settings"))

    # GET → render the form with all settings (incl. the static ones)
    cars = json.load(open(cars_file)) if os.path.exists(cars_file) else {}
    return render_template(
        "settings.html",
        settings=current,
        cars=cars
    )


if __name__ == "__main__":
    app.run(debug=True)
