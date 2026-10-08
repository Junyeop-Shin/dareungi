"""대여소 수집(런북 07, 3A-4). 매주 월요일 05:00.

tbCycleStationInfo·bikeStationMaster를 받아 날짜별 스냅샷으로 station_hist에 쌓는다. 고도는 이미 아는 대여소면
station_master.csv에서 가져오고, 새 대여소만 OpenTopoData로 받는다. 가장 최근 스냅샷은 05의 station과 같은 열로
station_live(st_no, st_id, st_name, lat, lon, district, elev_m, han_river)에 둔다.

출력: ops.duckdb station_hist, station_live, job_runs
실행: .venv/bin/python py/jobs/collect_station.py
"""
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, alert, connect, fetch_all, job_lock, log_run, upsert_rows   # noqa: E402


def elevation(df):
    out = []
    for i in range(0, len(df), 100):
        chunk = df.iloc[i:i + 100]
        loc = "|".join(f"{a},{b}" for a, b in zip(chunk.lat, chunk.lon))
        r = requests.get("https://api.opentopodata.org/v1/srtm30m", params={"locations": loc}, timeout=60)
        r.raise_for_status()
        out += [x["elevation"] for x in r.json()["results"]]
        time.sleep(1.1)
    return out


def run():
    today = str(dt.date.today())
    with job_lock():
        con = connect()
        try:
            _, a = fetch_all("tbCycleStationInfo")
            _, b = fetch_all("bikeStationMaster")
            a, b = pd.DataFrame(a), pd.DataFrame(b)
            st = pd.DataFrame({"st_no": pd.to_numeric(a.RENT_NO, errors="coerce"), "st_id": a.RENT_ID, "st_name": a.RENT_NM,
                               "lat": pd.to_numeric(a.STA_LAT, errors="coerce"), "lon": pd.to_numeric(a.STA_LONG, errors="coerce"),
                               "district": a.STA_LOC, "hold_num": pd.to_numeric(a.HOLD_NUM, errors="coerce")})
            st = st.dropna(subset=["st_no"]).astype({"st_no": int})
            bad = ~st.lat.between(37.4, 37.72) | ~st.lon.between(126.75, 127.2)
            st.loc[bad, ["lat", "lon"]] = None
            st["han_river"] = st.st_name.fillna("").str.contains("한강").astype(int)
            # 고도는 아는 대여소면 station_master.csv에서, 새 대여소만 API로 받는다
            known = pd.read_csv(ROOT / "data/interim/station_master.csv")[["st_id", "elev_m"]].dropna()
            st = st.merge(known, on="st_id", how="left")
            new = st[st.elev_m.isna() & st.lat.notna()]
            if len(new):
                st.loc[new.index, "elev_m"] = elevation(new)
            st["snapshot_date"] = today
            upsert_rows(con, "station_hist", st, "snapshot_date = ?", (today,))
            con.execute("CREATE OR REPLACE TABLE station_live AS SELECT * EXCLUDE (snapshot_date) FROM station_hist WHERE snapshot_date = (SELECT max(snapshot_date) FROM station_hist)")
            log_run(con, "collect_station", "ok", f"{len(st)}곳, 새 대여소 {len(new)}")
            print(f"collect_station: {len(st)}곳 (master {len(b)}), 새 대여소 고도 {len(new)}곳")
        except Exception as e:
            log_run(con, "collect_station", "fail", str(e)); alert(f"collect_station 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
