"""대여소 실시간 대수 수집(런북 10 R1). 5분마다 깨우고, 받을지는 시간대로 정한다.

bikeList(공공자전거 실시간 대여정보)로 대여소마다 지금 있는 자전거 수와 거치대 수를 받아 raw_stock에 쌓는다.
재배치 화면의 현황·부족 예측·추천 동선의 출발 재고이고, 5분 간격의 급변은 트럭이 다녀간 순간을 보여 준다.

주기(결정 로그 2026-11-01): 근무조(07–10, 13–16, 19–21시) 5분, 그 밖 06–22시 15분, 22–06시 30분.
  타이머가 조금 늦게 깨워도 같은 칸에 들어가도록 시각을 5분 단위로 내려서 판단하고 저장한다.
API를 받는 동안은 잠금을 잡지 않는다(다른 작업을 막지 않게). DB에 쓸 때만 잡는다.
같은 시각 칸은 지우고 다시 넣으므로 몇 번을 돌려도 결과가 같다.

출력: ops.duckdb raw_stock(snap_ts, st_id, racks, bikes, shared), 뷰 stock_live(+ st_no), job_runs
실행: .venv/bin/python py/jobs/collect_stock.py          # 저장소 루트에서, 타이머
      .venv/bin/python py/jobs/collect_stock.py --now    # 시간대와 상관없이 지금 한 번
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import alert, connect, fetch, job_lock, log_run, rebal_band, table_exists, upsert_rows   # noqa: E402

EVERY_MIN = {"shift": 5, "day": 15, "night": 30}
VIEW = """
CREATE OR REPLACE VIEW stock_live AS
SELECT s.*, l.st_no FROM raw_stock s LEFT JOIN station_live l USING (st_id)"""


def slot(now):
    """5분 단위로 내린 시각. 받을 차례가 아니면 None."""
    t = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
    return t if t.minute % EVERY_MIN[rebal_band(t)] == 0 else None


def fetch_stock():
    """전체 대여소를 받는다. list_total_count가 전체 수가 아니라 그 쪽의 행 수로 와서, 짧은 쪽이 나올 때까지 넘긴다."""
    rows, s = [], 1
    while True:
        _, r = fetch("bikeList", s, s + 999)
        rows += r
        if len(r) < 1000:
            break
        s += 1000
    d = pd.DataFrame(rows)
    # 숫자로 저장한다(하루 약 41만 행이라 문자열보다 작다)
    return pd.DataFrame({"st_id": d.stationId, "racks": d.rackTotCnt.astype(int), "bikes": d.parkingBikeTotCnt.astype(int),
                         "shared": d.shared.astype(int)})


def run(t):
    try:
        st = fetch_stock()
        st.insert(0, "snap_ts", t)
    except Exception as e:
        alert(f"collect_stock 실패: {e}")
        with job_lock(tries=450):
            con = connect(); log_run(con, "collect_stock", "fail", str(e)); con.close()
        raise
    with job_lock(tries=450):
        con = connect()
        try:
            upsert_rows(con, "raw_stock", st, "snap_ts = ?", (t,))
            if table_exists(con, "station_live"):
                con.execute(VIEW)
            log_run(con, "collect_stock", "ok", f"{t:%H:%M} {len(st)}곳")
            print(f"collect_stock: {t:%m-%d %H:%M} {len(st)}곳, 0대 {(st.bikes == 0).mean():.1%}")
        finally:
            con.close()


if __name__ == "__main__":
    now = dt.datetime.now()
    t = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0) if "--now" in sys.argv else slot(now)
    if t is not None:
        run(t)
