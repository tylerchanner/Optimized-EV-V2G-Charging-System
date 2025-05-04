# ⚡ Smart EV Charging Optimiser

## 🔧 Getting Started

### 1. Launch the App

1. **Unzip** `V2G_Flask_App.zip` into a folder.
2. **Open Command Prompt** in that folder:
   - Navigate to `V2G_Flask_App` in File Explorer.
   - Click the address bar, type `cmd`, and press **Enter**.
3. **Install dependencies** (first time only):
   ```bash
   pip install -r requirements.txt
   ```
4. **Run the Flask server**:
   ```bash
   python app.py
   ```
5. Visit [http://127.0.0.1:5000/](http://127.0.0.1:5000/) in your browser.

---

## 🌞 Dashboard (`/`)

The landing page shows:

- Solar Radiation (W/m²)
- Cloud Cover (%)
- Temperature (°C)
- Predicted PV Generation (MW)

Each chart visualises the next 48 hours of data for easy planning.

### 💾 Saved Plans Overview

At the bottom of the Dashboard, your **10 most recent charging plans** are listed.

- Click any plan to open a **modal** with:
  - 📄 Plan Summary
  - ✏️ Rename
  - 📥 Download
  - 🗑️ Delete

---

## 📅 Planner (`/planner`)

Use the Planner to simulate and schedule custom charging plans.

### 🧮 Basic Mode

Input:
- Required Range (miles)
- Day (0 = tomorrow)
- Deadline Hour (0–23)
- Optional: Eco Mode to optimise cost

Click **Run Simulation**.

### 🗺️ Route Planner Mode

Input:
- Origin and Destination (auto-suggested via Google Maps API)
- The app calculates the distance and converts it to required range
- Add Day, Deadline Hour, and optionally enable Eco Mode

Click **Compute & Simulate**.

---

## 📊 Simulation Results

Two result tabs will appear after running a simulation:

### 1. Summary
Shows battery usage, solar/grid charging, V2G discharge, and full cost breakdown.

### 2. Hourly Schedule Table
- Displays Solar/Grid charging/discharging
- Tracks battery SoC across hours
- Colour-coded rows (green, yellow, blue)

---

## 💾 Download or Save

- 📥 **Download Plan**: Export schedule as a clean `.txt` file.
- 💾 **Save Plan**:
  - Prompts you to name the plan before saving
  - Saves to the Saved Trips page and Dashboard

---

## 📚 Saved Trips (`/saved_trips`)

Full overview of saved plans:

- 👁️ **View**: Opens modal with full details and actions
- ✏️ **Rename** the plan live
- 📥 **Download**
- 🗑️ **Delete**

You can manage up to 10 stored plans here.

---

## ⚙️ Settings (`/settings`)

Update app-wide configurations:

### EV & Battery Setup
- Battery Capacity
- Initial SoC
- Charging/discharging rates
- Energy per mile

### Cost Settings
- V2G Sell Price
- Degradation Cost per kWh

### 🚗 Vehicle Templates
- Choose from 100+ built-in EV models
- Auto-fill settings and save

Changes apply immediately for new simulations.

---
