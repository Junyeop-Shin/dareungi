"""서울(108) ASOS 시간자료를 받아 DuckDB wx_h 테이블을 만든다(런북 05, 2A-5).

공공데이터포털 '기상청 지상(종관, ASOS) 시간자료' API로 2023-07-01 ~ 2026-06-30 시간별 기온·강수·습도·풍속을
받아 CSV로 저장한다. CSV가 이미 있으면 다시 받지 않는다. wx_h는 정시마다 한 행이고, 빠진 시간은 앞 값으로
채우며, 직전 3시간 강수 합(rain_prev3h)을 붙인다. 통행에 붙이면 rain_now > 0은 비 오는 중 주행,
rain_prev3h > 0은 젖은 노면 주행(비 그친 직후 포함)으로 쓴다.

입력: 공공데이터포털 API(.env의 DATA_GO_KR_KEY). 한 달씩, 페이지당 999행
출력: data/raw/weather/asos_hourly_108_202307_202606.csv, DuckDB wx_hourly·wx_h, output/tables/panel_2a5_wx_checks.csv
실행: .venv/bin/python py/panel/25_weather_hourly.py  (API 약 40회, 1~2분)
"""
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
RAW = ROOT / "data/raw/weather/asos_hourly_108_202307_202606.csv"
URL = "http://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
START, END = "2023-07-01", "2026-06-30"


def fetch_month(a, b):
    """a ~ b('YYYYMMDD') 구간을 페이지를 넘기며 받는다"""
    rows, page = [], 1
    while True:
        for attempt in range(4):
            r = requests.get(URL, params=dict(serviceKey=os.environ["DATA_GO_KR_KEY"], dataType="JSON", dataCd="ASOS", dateCd="HR",
                                              startDt=a, startHh="00", endDt=b, endHh="23", stnIds="108", numOfRows=999, pageNo=page), timeout=60)
            try:
                js = r.json(); break
            except ValueError:                    # 트래픽 제한 등은 XML 오류로 온다. 잠시 쉬고 재시도
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"{a}: JSON 응답 없음: {r.text[:200]}")
        body = js["response"]["body"]
        items = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
        rows += items
        if page * 999 >= int(body.get("totalCount", 0)):
            break
        page += 1
        time.sleep(0.3)
    return rows


def download():
    out = []
    for p in pd.period_range(START, END, freq="M"):
        a, b = p.start_time.strftime("%Y%m%d"), p.end_time.strftime("%Y%m%d")
        rows = fetch_month(a, b)
        out += rows
        print(p, len(rows), flush=True)
        time.sleep(0.3)
    df = pd.DataFrame(out)
    df.to_csv(RAW, index=False)
    return df


def main():
    raw = pd.read_csv(RAW, dtype=str) if RAW.exists() else download()
    w = pd.DataFrame({"ts": pd.to_datetime(raw.tm), "temp": pd.to_numeric(raw.ta, errors="coerce"),
                      "rain_mm": pd.to_numeric(raw.rn, errors="coerce").fillna(0.0),      # 빈칸 = 강수 없음(기상청 표기)
                      "humidity": pd.to_numeric(raw.hm, errors="coerce"), "wind": pd.to_numeric(raw.ws, errors="coerce")})
    w = w.drop_duplicates("ts").set_index("ts").sort_index()
    full = pd.date_range(f"{START} 00:00", f"{END} 23:00", freq="h")
    n_missing = int(len(full) - w.index.isin(full).sum())
    w = w.reindex(full)
    w["temp"] = w.temp.ffill(); w["humidity"] = w.humidity.ffill(); w["wind"] = w.wind.ffill(); w["rain_mm"] = w.rain_mm.fillna(0.0)
    w = w.rename_axis("ts").reset_index()
    con = connect()
    con.execute("CREATE OR REPLACE TABLE wx_hourly AS SELECT * FROM w")
    con.execute("""
    CREATE OR REPLACE TABLE wx_h AS
    SELECT ts, temp, rain_mm, humidity, wind,
           sum(rain_mm) OVER (ORDER BY ts ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS rain_prev3h
    FROM wx_hourly""")
    chk = pd.DataFrame([{"항목": "시간 행(기대 " + str(len(full)) + ")", "값": len(w)}, {"항목": "빠진 시간(앞 값으로 채움)", "값": n_missing},
                        {"항목": "비 온 시간 비율", "값": float((w.rain_mm > 0).mean())}, {"항목": "기온 30℃ 이상 시간 비율", "값": float((w.temp >= 30).mean())},
                        {"항목": "기온 0℃ 이하 시간 비율", "값": float((w.temp <= 0).mean())}, {"항목": "연 강수량 2024(mm)", "값": float(w[w.ts.dt.year == 2024].rain_mm.sum())}])
    chk.to_csv(ROOT / "output/tables/panel_2a5_wx_checks.csv", index=False)
    print(chk.to_string(index=False))


if __name__ == "__main__":
    main()
