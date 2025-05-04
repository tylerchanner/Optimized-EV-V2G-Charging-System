# cnn_forecaster.py

import os, re, joblib
import numpy as np
import pandas as pd
from tensorflow.keras.models import load_model

class CNNForecaster:
    def __init__(self, model_path, scaler_X_path, scaler_y_path):
        # 1) Load model (no compile needed for inference)
        self.model = load_model(model_path, compile=False)

        # 2) Parse seq_length from filename (e.g., CNN_24_64_...)
        m = re.search(r'CNN_(\d+)_', os.path.basename(model_path))
        if not m:
            raise ValueError("Could not parse seq_length from model filename")
        self.seq_length = int(m.group(1))

        # 3) Define expected input feature columns
        self.feature_cols = [
            "solar_radiation_W_m2",
            "temperature_2m",
            "time_of_day",
            "irradiance_present"
        ]

        # 4) Load pre-fitted scalers
        self.scaler_X = joblib.load(scaler_X_path)
        self.scaler_y = joblib.load(scaler_y_path)

    def _engineer(self, df):
        df = df.copy()
        df['time_of_day'] = pd.to_datetime(df['datetime']).dt.hour
        df['irradiance_present'] = (df['solar_radiation_W_m2'] > 0).astype(int)
        return df[self.feature_cols]

    def predict(self, historical_df, future_df=None, horizon=None):
        # ─── Feature engineering ───────────────────────────────
        hist_feats = self._engineer(historical_df)
        fut_feats = self._engineer(future_df) if future_df is not None else None
        all_feats = pd.concat([hist_feats, fut_feats], ignore_index=True) if fut_feats is not None else hist_feats

        # ─── Scaling features ───────────────────────────────────
        hist_scaled = self.scaler_X.transform(hist_feats)
        fut_scaled = self.scaler_X.transform(fut_feats) if fut_feats is not None else None

        # ─── Padding if historical data is too short ────────────
        if len(hist_scaled) < self.seq_length:
            need = self.seq_length - len(hist_scaled)
            if fut_scaled is None or len(fut_scaled) < need:
                raise ValueError(f"Need at least {self.seq_length} total points for CNN input.")
            pad = fut_scaled[:need]
            hist_scaled = np.vstack([pad, hist_scaled])
            fut_scaled = fut_scaled[need:]

        # ─── Create initial prediction window ───────────────────
        X_win = hist_scaled[-self.seq_length:].reshape(1, self.seq_length, -1)

        # ─── Set forecast horizon ───────────────────────────────
        if horizon is None:
            horizon = len(fut_scaled) if fut_scaled is not None else 24

        # ─── Generate forecasts ─────────────────────────────────
        preds = []
        for i in range(horizon):
            norm_p = float(self.model.predict(X_win, verbose=0).flatten()[0])
            mw_p = self.scaler_y.inverse_transform([[norm_p]])[0, 0]

            # Scale from MW to realistic household-level kW
            scaled_kw_p = (mw_p * 1000) * (3.25 / 5000)  # 3.25 kW home, 5 MW solar farm baseline
            preds.append(scaled_kw_p)

            new_row = fut_scaled[i] if fut_scaled is not None and i < len(fut_scaled) else X_win[0, -1, :]
            X_win = np.concatenate([X_win[:, 1:, :], new_row.reshape(1, 1, -1)], axis=1)

        # ─── Post-correct: Zero out predictions where no sun ───
        # ─── Convert future_df to Europe/London, align by time, then apply irradiance threshold ───
        fut_local = future_df.copy()
        fut_local['datetime'] = fut_local['datetime'].dt.tz_convert('Europe/London')
        fut_local = fut_local.sort_values('datetime').reset_index(drop=True)

        irradiance = fut_local['solar_radiation_W_m2'].values[:horizon]
        threshold = 30.0  # W/m²

        solar = [
            pred if ir >= threshold else 0.0
            for pred, ir in zip(preds, irradiance)
        ]

        return np.array(solar)

        return np.array(solar)


