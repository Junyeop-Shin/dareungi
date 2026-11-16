"""TOPIS 실시간 도로 속도 수집(런북 10 R1). 15분마다 깨우고, 받을지는 시간대로 정한다.

추천 동선의 이동시간을 그 시각 도로 상황으로 보정하려고, 서울 큰길(도시고속도로·주간선·보조간선·기타도로)의
링크 5,337개마다 속도와 통과 시간을 받아 raw_traffic에 쌓는다.
  - TrafficInfo는 XML만 되고(json은 ERROR-301) 링크 하나에 호출 한 번이다. 4개씩 동시에 불러 한 번에 약 1.5분
  - 링크 목록은 RoadInfo(도로 구분별 도로) → LinkWithLoad(도로별 링크)로 받아 topis_links에 두고 7일마다 새로 받는다

주기(결정 로그 2026-11-01): 근무조(07–10, 13–16, 19–21시) 15분, 그 밖 06–22시 1시간, 22–06시 받지 않음.
호출 한도: 한도 오류가 나면 그때까지 받은 것을 저장하고, 경보를 보내고, 그날 근무조는 30분 간격으로 바꾼다
  (traffic_quota에 날짜를 남긴다). 한도 오류 코드는 공개 자료에서 찾지 못해 메시지의 '트래픽'·'제한'으로 알아본다.
API를 받는 동안은 잠금을 잡지 않는다. 같은 시각 칸은 지우고 다시 넣으므로 다시 돌려도 결과가 같다.

출력: ops.duckdb raw_traffic(snap_ts, link_id, spd, trv_s), topis_links, traffic_quota, job_runs
실행: .venv/bin/python py/jobs/collect_traffic.py          # 저장소 루트에서, 타이머
      .venv/bin/python py/jobs/collect_traffic.py --now    # 시간대와 상관없이 지금 한 번
"""
import datetime as dt
import os
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import SEOUL, alert, connect, job_lock, log_run, mask_keys, rebal_band, table_exists, upsert_rows   # noqa: E402

ROAD_DIVS = ("02", "03", "04", "05")      # 도시고속도로, 주간선, 보조간선, 기타도로(RoadDivInfo)
LINKS_MAX_AGE_DAYS = 7


class QuotaHit(Exception):
    pass


def slot(now, fallback=False):
    """15분 단위로 내린 시각. 받을 차례가 아니면 None. fallback(그날 한도에 걸림)이면 근무조도 30분."""
    t = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    every = {"shift": 30 if fallback else 15, "day": 60, "night": None}[rebal_band(t)]
    return t if every and t.minute % every == 0 else None


def get_xml(path, retries=3):
    """XML 한 번 받기 → (코드, 행 목록). 한도 오류면 QuotaHit. 오류 메시지의 인증키는 가린다."""
    url = f"{SEOUL}/{os.environ['SEOUL_API_KEY']}/xml/{path}"
    for i in range(retries):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            code = root.findtext("RESULT/CODE") or root.findtext("CODE")      # 서버 오류는 <RESULT>가 맨 위로 온다
            msg = root.findtext("RESULT/MESSAGE") or root.findtext("MESSAGE") or ""
            if "트래픽" in msg or "제한" in msg:
                raise QuotaHit(f"{code} {msg}")
            if code not in ("INFO-000", "INFO-200"):
                raise RuntimeError(f"{code} {msg}")
            return code, [{c.tag: c.text for c in row} for row in root.findall("row")]
        except QuotaHit:
            raise
        except Exception as e:
            if i == retries - 1:
                raise RuntimeError(mask_keys(f"{path}: {e}")) from None
            time.sleep(2 ** i)


def fetch_links():
    roads = [r for cd in ROAD_DIVS for r in get_xml(f"RoadInfo/1/1000/{cd}")[1]]
    with ThreadPoolExecutor(4) as ex:
        out = list(ex.map(lambda r: get_xml(f"LinkWithLoad/1/1000/{r['axis_cd']}")[1], roads))
    div = {r["axis_cd"]: r["road_div_cd"] for r in roads}
    d = pd.DataFrame([x for o in out for x in o]).drop_duplicates("link_id")
    d["road_div_cd"] = d.axis_cd.map(div)
    d["link_id"] = d.link_id.astype("int64")
    d["fetched"] = dt.date.today()
    return d


def fetch_speeds(ids):
    """링크마다 속도를 받는다 → (표, 한도에 걸렸나, 실패한 링크 수). 한도에 걸리면 남은 링크는 부르지 않는다."""
    hit, failed = [], []

    def one(i):
        if hit:
            return None
        try:
            return get_xml(f"TrafficInfo/1/5/{i}")[1]
        except QuotaHit as e:
            hit.append(str(e))
        except RuntimeError:
            failed.append(i)

    with ThreadPoolExecutor(16) as ex:      # 시드니 서버는 호출당 0.45초라 4개면 12분, 16개면 약 3분(2026-11-16 시험, 오류 0)
        rows = [r for out in ex.map(one, ids) if out for r in out]
    d = pd.DataFrame(rows)
    if len(d):
        d = pd.DataFrame({"link_id": d.link_id.astype("int64"), "spd": pd.to_numeric(d.prcs_spd, errors="coerce"),
                          "trv_s": pd.to_numeric(d.prcs_trv_time, errors="coerce")})
    return d, (hit[0] if hit else None), len(failed)


def state(con, today):
    """(쓸 수 있는 링크 목록 또는 None, 그날 한도에 걸렸나)"""
    links = None
    if table_exists(con, "topis_links"):
        links = con.execute("SELECT * FROM topis_links").df()
        if links.empty or (today - pd.Timestamp(links.fetched.max()).date()).days >= LINKS_MAX_AGE_DAYS:
            links = None
    hit = table_exists(con, "traffic_quota") and con.execute("SELECT count(*) FROM traffic_quota WHERE day = ?", [today]).fetchone()[0] > 0
    return links, hit


def run(t, links):
    new_links = links is None
    try:
        if new_links:
            links = fetch_links()
        sp, quota, n_failed = fetch_speeds(links.link_id.tolist())
    except Exception as e:
        alert(f"collect_traffic 실패: {e}")
        with job_lock(tries=450):
            con = connect(); log_run(con, "collect_traffic", "fail", str(e)); con.close()
        raise
    if len(sp):
        sp.insert(0, "snap_ts", t)
    with job_lock(tries=450):
        con = connect()
        try:
            if new_links:
                upsert_rows(con, "topis_links", links, "TRUE")
            upsert_rows(con, "raw_traffic", sp, "snap_ts = ?", (t,))
            if quota:
                con.execute("CREATE TABLE IF NOT EXISTS traffic_quota (day DATE PRIMARY KEY, hit_at TIMESTAMP, msg VARCHAR)")
                con.execute("INSERT OR IGNORE INTO traffic_quota VALUES (?, now(), ?)", [t.date(), quota])
                alert(f"collect_traffic: 호출 한도에 걸려 오늘 근무조는 30분 간격으로 바꾼다({len(sp)}/{len(links)}개 받음, {quota})")
            status = "ok" if not quota and n_failed <= 0.05 * len(links) else "warn"
            if n_failed > 0.05 * len(links):
                alert(f"collect_traffic: 링크 {n_failed}/{len(links)}개를 받지 못했다")
            log_run(con, "collect_traffic", status, f"{t:%H:%M} {len(sp)}/{len(links)}개, 실패 {n_failed}" + (", 한도" if quota else ""))
            print(f"collect_traffic: {t:%m-%d %H:%M} {len(sp)}/{len(links)}개, 실패 {n_failed}" + (f", 한도: {quota}" if quota else ""))
        finally:
            con.close()


if __name__ == "__main__":
    now = dt.datetime.now()
    if slot(now) is None and "--now" not in sys.argv:      # 한도 전환은 받을 칸을 줄이기만 하므로 여기서 끝나면 DB를 볼 필요가 없다
        sys.exit(0)
    with job_lock(tries=450):
        con = connect()
        try:
            links, hit = state(con, now.date())
        finally:
            con.close()
    t = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0) if "--now" in sys.argv else slot(now, hit)
    if t is not None:
        run(t, links)
