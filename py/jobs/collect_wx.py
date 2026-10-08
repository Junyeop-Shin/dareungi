"""날씨 수집 — 단계 3A-4 (런북 07). 관측은 매일 06:30, 예보는 05:20·17:20

[무엇을]
  관측  ASOS 시간자료(서울 108) D−1(빠진 날은 따라잡기, 최근 7일 다시) → wx_hourly_live(ts, temp, rain_mm, humidity, wind)
  예보  단기예보(VilageFcstInfoService_2.0, 격자 60·127 = 1C 시나리오 B와 같은 격자)의 05시·17시 발표분을 **발표 시각과 함께** fcst_archive에
        (issue_ts, fcst_ts, category, value). 몇 달 모이면 1C의 '실제 예보' 백테스트에 그대로 쓸 수 있다.
  중기예보(MidFcstInfoService, 06·18시 발표): 서울 기온(getMidTa, 11B10101)은 4 ~ 10일 뒤 최저·최고, 수도권 육상(getMidLandFcst, 11B00000)은 강수확률·하늘.
        단기예보가 +103시간(약 4일)까지라 5일 뒤부터는 중기예보만 있다. 같은 fcst_archive에 src = 'mid'로 쌓는다. (2026-10-07 승인 확인)
[출력] ops.duckdb: wx_hourly_live, fcst_archive, job_runs
[실행] .venv/bin/python py/jobs/collect_wx.py obs      # 관측
       .venv/bin/python py/jobs/collect_wx.py fcst     # 예보(가장 최근 05 또는 17시 발표분)
       .venv/bin/python py/jobs/collect_wx.py          # 둘 다
"""
import datetime as dt
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import alert, connect, job_lock, log_run, table_exists, upsert_rows   # noqa: E402

ASOS = "http://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
VILAGE = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
MID = "http://apis.data.go.kr/1360000/MidFcstInfoService/"
MID_ON = True            # 2026-10-07 공공데이터포털 승인 확인


def _get_json(url, params, tries=4):
    for i in range(tries):
        r = requests.get(url, params={**params, "serviceKey": os.environ["DATA_GO_KR_KEY"]}, timeout=60)
        try:
            return r.json()
        except ValueError:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"JSON 응답 없음: {r.text[:200]}")


def fetch_asos_day(d):
    js = _get_json(ASOS, dict(dataType="JSON", dataCd="ASOS", dateCd="HR", startDt=d.strftime("%Y%m%d"), startHh="00",
                              endDt=d.strftime("%Y%m%d"), endHh="23", stnIds="108", numOfRows=24, pageNo=1))
    body = js["response"]["body"]
    items = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
    if not items:
        return None
    w = pd.DataFrame(items)
    return pd.DataFrame({"ts": pd.to_datetime(w.tm), "temp": pd.to_numeric(w.ta, errors="coerce"), "rain_mm": pd.to_numeric(w.rn, errors="coerce").fillna(0.0),
                         "humidity": pd.to_numeric(w.hm, errors="coerce"), "wind": pd.to_numeric(w.ws, errors="coerce"), "d": str(d)})


def run_obs(days=7):
    with job_lock():
        con = connect()
        try:
            con.execute("CREATE TABLE IF NOT EXISTS wx_hourly_live (ts TIMESTAMP, temp DOUBLE, rain_mm DOUBLE, humidity DOUBLE, wind DOUBLE, d VARCHAR)")
            got = []
            for d in pd.date_range(dt.date.today() - dt.timedelta(days=days), dt.date.today() - dt.timedelta(days=1)).date:
                w = fetch_asos_day(d)
                if w is None or len(w) < 20:
                    continue
                con.execute("DELETE FROM wx_hourly_live WHERE d = ?", [str(d)])
                con.register("w_", w); con.execute("INSERT INTO wx_hourly_live BY NAME SELECT * FROM w_"); con.unregister("w_")
                got.append(str(d)); time.sleep(0.3)
            log_run(con, "collect_wx_obs", "ok", f"{len(got)}일")
            print(f"collect_wx obs: {len(got)}일 ({got[0] if got else '-'} ~ {got[-1] if got else '-'})")
        except Exception as e:
            log_run(con, "collect_wx_obs", "fail", str(e)); alert(f"collect_wx_obs 실패: {e}")
            raise
        finally:
            con.close()


def fetch_vilage(base_date, base_time, nx=60, ny=127):
    items, page = [], 1
    while True:
        body = _get_json(VILAGE, dict(pageNo=page, numOfRows=1000, dataType="JSON", base_date=base_date, base_time=base_time, nx=nx, ny=ny))["response"]["body"]
        items += body["items"]["item"]
        if len(items) >= int(body["totalCount"]):
            return items
        page += 1


def fetch_mid(tm_fc):
    """중기예보: 발표 시각(YYYYMMDDHHMM) 기준 4 ~ 10일 뒤. 돌려주는 것: (fcst_date, category, value) 목록"""
    out = []
    ta = _get_json(MID + "getMidTa", dict(pageNo=1, numOfRows=10, dataType="JSON", regId="11B10101", tmFc=tm_fc))["response"]["body"]["items"]["item"][0]
    land = _get_json(MID + "getMidLandFcst", dict(pageNo=1, numOfRows=10, dataType="JSON", regId="11B00000", tmFc=tm_fc))["response"]["body"]["items"]["item"][0]
    base = dt.datetime.strptime(tm_fc[:8], "%Y%m%d").date()
    for k in range(3, 11):
        d = base + dt.timedelta(days=k)
        for key, cat in ((f"taMin{k}", "TMN"), (f"taMax{k}", "TMX")):
            if key in ta and ta[key] not in ("", None):
                out.append((d, cat, str(ta[key])))
        for key, cat in ((f"rnSt{k}Am", "POP_AM"), (f"rnSt{k}Pm", "POP_PM"), (f"rnSt{k}", "POP"), (f"wf{k}Am", "WF_AM"), (f"wf{k}Pm", "WF_PM"), (f"wf{k}", "WF")):
            if key in land and land[key] not in ("", None):
                out.append((d, cat, str(land[key])))
    return out


def run_mid():
    now = dt.datetime.now()
    hh = "1800" if now.hour >= 18 or now.hour < 6 else "0600"
    base_date = (now - dt.timedelta(days=1)).date() if now.hour < 6 else now.date()
    tm_fc = f"{base_date:%Y%m%d}{hh}"
    issue = dt.datetime.strptime(tm_fc, "%Y%m%d%H%M")
    with job_lock():
        con = connect()
        try:
            rows = fetch_mid(tm_fc)
            df = pd.DataFrame(rows, columns=["fcst_date", "category", "value"])
            df = pd.DataFrame({"issue_ts": issue, "fcst_ts": pd.to_datetime(df.fcst_date), "category": df.category, "value": df.value, "src": "mid"})
            n = upsert_rows(con, "fcst_archive", df, "issue_ts = ? AND src = 'mid'", (issue,))
            log_run(con, "collect_wx_mid", "ok", f"{issue} {n}행")
            print(f"collect_wx mid: 발표 {issue} {n}행, {df.fcst_ts.min().date()} ~ {df.fcst_ts.max().date()}")
        except Exception as e:
            log_run(con, "collect_wx_mid", "fail", str(e)); alert(f"collect_wx_mid 실패: {e}")
            raise
        finally:
            con.close()


def run_fcst():
    now = dt.datetime.now()
    base_time = "1700" if now.hour >= 17 or now.hour < 5 else "0500"            # 가장 최근 발표분
    base_date = (now - dt.timedelta(days=1)).date() if now.hour < 5 else now.date()
    issue = dt.datetime.strptime(f"{base_date:%Y%m%d}{base_time}", "%Y%m%d%H%M")
    with job_lock():
        con = connect()
        try:
            items = fetch_vilage(base_date.strftime("%Y%m%d"), base_time)
            df = pd.DataFrame(items)
            df = pd.DataFrame({"issue_ts": issue, "fcst_ts": pd.to_datetime(df.fcstDate + df.fcstTime, format="%Y%m%d%H%M"),
                               "category": df.category, "value": df.fcstValue.astype(str), "src": "short"})
            n = upsert_rows(con, "fcst_archive", df, "issue_ts = ? AND src = 'short'", (issue,))
            log_run(con, "collect_wx_fcst", "ok", f"{issue} {n}행")
            print(f"collect_wx fcst: 발표 {issue} {n}행, 예보 {df.fcst_ts.min()} ~ {df.fcst_ts.max()}")
        except Exception as e:
            log_run(con, "collect_wx_fcst", "fail", str(e)); alert(f"collect_wx_fcst 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "both"
    if what in ("obs", "both"):
        run_obs()
    if what in ("fcst", "both"):
        run_fcst()
        if MID_ON:
            run_mid()
