# weather_utils.py

import pandas as pd
import requests_cache
from retry_requests import retry
import openmeteo_requests
from datetime import datetime, timedelta, timezone

class WeatherFetcher:
    def __init__(self, latitude, longitude, tz="Europe/London"):
        self.latitude  = latitude
        self.longitude = longitude
        self.tz        = tz
        # cache + retry
        cache = requests_cache.CachedSession('.cache', expire_after=3600)
        self.client = openmeteo_requests.Client(
            session=retry(cache, retries=5, backoff_factor=0.2)
        )

    def fetch_range(self, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
        """
        Fetches hourly temperature, cloud_cover & solar_radiation
        between two UTC datetimes, returns a tz-aware Europe/London DataFrame.
        """
        # ensure tz-aware
        if start_utc.tzinfo is None:
            start_utc = start_utc.replace(tzinfo=timezone.utc)
        if end_utc.tzinfo is None:
            end_utc = end_utc.replace(tzinfo=timezone.utc)

        # pick the right endpoint
        today_utc = datetime.now(timezone.utc).date()
        url = (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
            if start_utc.date() < today_utc
            else "https://api.open-meteo.com/v1/forecast"
        )
        params = {
            "latitude":   self.latitude,
            "longitude":  self.longitude,
            "start_date": start_utc.date().isoformat(),
            "end_date":   end_utc.date().isoformat(),
            "hourly":     "temperature_2m,cloudcover,shortwave_radiation",
            "timezone":   "UTC"
        }

        resp   = self.client.weather_api(url, params=params)[0]
        hourly = resp.Hourly()
        times  = pd.date_range(
            start  = pd.to_datetime(hourly.Time(),    unit="s", utc=True),
            end    = pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq   = pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left"
        )

        df = pd.DataFrame({
            "datetime":             times,
            "temperature_2m":       hourly.Variables(0).ValuesAsNumpy(),
            "cloud_cover_%":        hourly.Variables(1).ValuesAsNumpy(),
            "solar_radiation_W_m2": hourly.Variables(2).ValuesAsNumpy(),
        })

        # convert to local tz
        df["datetime"] = df["datetime"].dt.tz_convert(self.tz)
        return df

    def get_hist_and_future(
        self,
        seq_hours: int,
        forecast_hours: int
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns (hist_df, future_df) where:
         • hist_df   has the last seq_hours up to now
         • future_df has the next forecast_hours from now
        All with Europe/London tz on the 'datetime' column.
        """
        now_utc   = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hist_start = now_utc - timedelta(hours=seq_hours)
        future_end = now_utc + timedelta(hours=forecast_hours)

        full = self.fetch_range(hist_start, future_end)

        # slice by timestamp rather than by index
        hist_df = full[
            (full["datetime"] >= hist_start) &
            (full["datetime"] <  now_utc)
        ].reset_index(drop=True)

        future_df = full[
            (full["datetime"] >= now_utc) &
            (full["datetime"] <= future_end)
        ].reset_index(drop=True)

        return hist_df, future_df
