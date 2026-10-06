"""과거의 '실제 날씨 예보'를 받아, 예보가 실제로 얼마나 틀렸는지 잰다

[왜 필요한가]
1C의 예측은 미래 7일의 날씨를 예보로만 안다고 가정한다. 처음에는 예보 오차를 '가정'으로 흉내 냈다(시나리오 C:
기온 오차의 표준편차가 당일 1.0℃ → 6일 뒤 2.5℃, 비 유무를 틀릴 확률 10% → 28%).
이 가정이 현실적인지 확인하려면 과거에 실제로 나왔던 예보가 필요하다.

[자료] Open-Meteo의 Previous Runs API (무료, 인증키 없음)
  - "며칠 전에 낸 예보"를 예측 거리별로 따로 준다: <변수>_previous_day1 = 하루 전에 낸 예보, …, _previous_day7 = 7일 전에 낸 예보
  - 기상 모형은 일본 기상청(JMA) 전지구 모형을 쓴다. 2021-03부터 기온·강수·눈의 1~7일 전 예보가 모두 있어
    학습·검증·테스트 구간을 한 가지 출처로 덮을 수 있다(확인 2026-10-05).
    다른 모형은 기간이 짧다: 미국 GFS는 강수 예보가 2024-03부터, 유럽 ECMWF는 2024-03부터, 한국 기상청 모형은 2025-03 ~ 2026-03만.
  - 한계: 한국 기상청의 공식 동네예보가 아니라 전지구 모형의 격자값이다. 관측소(서울 108)와 위치·고도가 달라
    기온이 평균 1 ~ 2℃ 낮게 나온다(계통 오차). 쓸 때는 이 차이를 보정해야 한다.

[원점과 예측 거리의 대응]
원점 t(그날 아침, t−1일까지의 정보)에서 쓸 수 있는 가장 새 예보는 t−1일에 낸 것이다.
그래서 t+k일(k = 0 ~ 6)의 날씨는 'k+1일 전에 낸 예보'(previous_day{k+1})를 쓴다.

[출력]
  data/raw/weather/openmeteo_jma_previous_runs_hourly_202103_202606.csv   받은 그대로(시간 단위)
  data/interim/weather_forecast/forecast_daily.parquet                    날짜 × 예측 거리(1~7일 전)별 일 요약: tavg, tmax, tmin, rain_mm, snow_cm
  output/tables/fc_forecast_error.csv                                     예측 거리별 예보 오차(관측 대비)

[실행]  터미널(프로젝트 폴더):  python py/weather_forecast.py            (이미 받은 원본이 있으면 다시 받지 않는다)
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/weather/openmeteo_jma_previous_runs_hourly_202103_202606.csv"
URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SEOUL = dict(latitude=37.5714, longitude=126.9658, timezone="Asia/Seoul", models="jma_seamless")   # 서울 기상관측소(108) 위치
VARS = ["temperature_2m", "precipitation", "snowfall"]          # 기온(℃), 강수(mm), 눈(cm)


def download():
    """2021-03-01 ~ 2026-06-30의 시간별 예보(1~7일 전에 낸 것)를 연도별로 받아 한 파일로 저장한다"""
    hourly = ",".join(VARS + [f"{v}_previous_day{k}" for v in VARS for k in range(1, 8)])
    parts = []
    for y in range(2021, 2027):
        a = f"{y}-03-01" if y == 2021 else f"{y}-01-01"
        b = f"{y}-06-30" if y == 2026 else f"{y}-12-31"
        for _ in range(3):                                       # 실패하면 두 번 더 시도
            r = requests.get(URL, params={**SEOUL, "start_date": a, "end_date": b, "hourly": hourly}, timeout=180)
            if r.status_code == 200:
                break
            time.sleep(5)
        r.raise_for_status()
        parts.append(pd.DataFrame(r.json()["hourly"]))
        time.sleep(1)
    out = pd.concat(parts, ignore_index=True)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(RAW, index=False)
    return out


def to_daily(h):
    """시간별 예보를 날짜 × 예측 거리별 일 요약으로 바꾼다. 24시간이 다 있는 날만 남긴다"""
    h = h.assign(date=pd.to_datetime(h.time).dt.normalize())
    out = []
    for k in range(1, 8):
        g = h.groupby("date").agg(tavg=(f"temperature_2m_previous_day{k}", "mean"), tmax=(f"temperature_2m_previous_day{k}", "max"),
                                  tmin=(f"temperature_2m_previous_day{k}", "min"), rain_mm=(f"precipitation_previous_day{k}", "sum"),
                                  snow_cm=(f"snowfall_previous_day{k}", "sum"), n=(f"temperature_2m_previous_day{k}", "count"))
        out.append(g[g.n == 24].drop(columns="n").assign(days_before=k).reset_index())
    return pd.concat(out, ignore_index=True)


def error_table(daily):
    """예측 거리별로 예보가 관측(ASOS 서울 108)과 얼마나 다른지. 학습·검증 구간만 본다(테스트 구간은 보지 않는다)"""
    obs = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")[["date", "tavg", "tmax", "tmin", "rain_mm"]]
    d = daily.merge(obs, on="date", suffixes=("", "_obs"))
    rows = []
    for (k, name, a, b) in [(k, n, a, b) for k in range(1, 8) for n, a, b in (("학습(2021-04 ~ 2023)", "2021-04-08", "2023-12-31"), ("검증(2024)", "2024-01-01", "2024-12-31"))]:
        x = d[(d.days_before == k) & d.date.between(a, b)]
        e = x.tavg - x.tavg_obs
        wet_f, wet_o = x.rain_mm >= 1, x.rain_mm_obs >= 1
        rows.append({"며칠 전 예보": k, "기간": name, "일수": len(x),
                     "평균기온 편향(℃)": e.mean(), "평균기온 오차 표준편차(℃)": e.std(),
                     "최고기온 편향(℃)": (x.tmax - x.tmax_obs).mean(), "최저기온 편향(℃)": (x.tmin - x.tmin_obs).mean(),
                     "비 유무를 틀린 비율": (wet_f != wet_o).mean(),
                     "비 온 날을 놓친 비율": ((~wet_f) & wet_o).sum() / max(wet_o.sum(), 1),
                     "맑은 날을 비로 예보한 비율": (wet_f & ~wet_o).sum() / max((~wet_o).sum(), 1),
                     "시나리오 C의 가정: 기온 오차 표준편차": 1.0 + 0.25 * (k - 1), "시나리오 C의 가정: 비 유무 틀릴 확률": 0.10 + 0.03 * (k - 1)})
    return pd.DataFrame(rows).set_index(["기간", "며칠 전 예보"])


if __name__ == "__main__":
    hourly = pd.read_csv(RAW) if RAW.exists() else download()
    daily = to_daily(hourly)
    dst = ROOT / "data/interim/weather_forecast"; dst.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(dst / "forecast_daily.parquet", index=False)
    tab = error_table(daily)
    tab.to_csv(ROOT / "output/tables/fc_forecast_error.csv")
    pd.set_option("display.width", 250)
    print(tab.round(2).to_string())
