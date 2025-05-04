# data_fetcher.py
import pandas as pd
import requests_cache
from retry_requests import retry
import openmeteo_requests

class DataFetcher:
    def __init__(self, latitude, longitude):
        self.latitude = latitude
        self.longitude = longitude
        self.cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
        self.retry_session = retry(self.cache_session, retries=5, backoff_factor=0.2)
        self.client = openmeteo_requests.Client(session=self.retry_session)

    def fetch_weather(self, start_date, end_date,
                      hourly_params="temperature_2m,relative_humidity_2m,wind_speed_10m,cloudcover,shortwave_radiation"):
        """
        Returns DataFrame with columns:
        datetime, temperature_2m, humidity_%, wind_speed_m_s, cloud_cover_%, solar_radiation_W_m2
        """
        # 1) parse inputs
        start_dt = pd.to_datetime(start_date)
        if start_dt.tzinfo is None:
            start_dt = start_dt.tz_localize("UTC")

        # 2) choose historic vs forecast API
        url = (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
            if start_dt < pd.Timestamp.now(tz="UTC").normalize()
            else "https://api.open-meteo.com/v1/forecast"
        )
        params = {
            "latitude":  self.latitude,
            "longitude": self.longitude,
            "start_date": str(start_dt.date()),
            "end_date":   str(pd.to_datetime(end_date).date()),
            "hourly":     hourly_params,
            "timezone":   "UTC"
        }

        # 3) fetch
        responses = self.client.weather_api(url, params=params)
        r = responses[0]
        hourly = r.Hourly()

        # 4) build the time index
        start_ts = pd.to_datetime(hourly.Time(),    unit="s", utc=True)
        end_ts   = pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True)
        interval = pd.Timedelta(seconds=hourly.Interval())
        times    = pd.date_range(start=start_ts, end=end_ts,
                                 freq=interval, inclusive="left")

        # 5) extract each variable by index
        keys = hourly_params.split(",")
        vals = [hourly.Variables(i).ValuesAsNumpy() for i in range(len(keys))]

        # 6) assemble DataFrame
        df = pd.DataFrame({"datetime": times})
        for name, arr in zip(keys, vals):
            df[name] = arr

        # 7) rename to match CNNForecaster expectations
        df.rename(columns={
            "shortwave_radiation":   "solar_radiation_W_m2",
            "cloudcover":            "cloud_cover_%",
            "relative_humidity_2m":  "humidity_%",
            "wind_speed_10m":        "wind_speed_m_s"
        }, inplace=True)

        return df



