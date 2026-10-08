"""대여이력 수집 — 단계 3A-3 (런북 07). 매시 15분에 실행

[무엇을] 서울 열린데이터광장 tbCycleRentData(시간대별 대여이력)로 직전에 끝난 시간대 + 빠진 시간대 전부를 받아 ops.duckdb의 raw_rent에 넣는다.
  - 멱등: 같은 시간대는 지우고 다시 넣는다
  - 따라잡기: rent_hours_done의 마지막 시간대 다음부터 지금까지(처음 실행이면 최근 3일)
  - 3시간 전 시간대는 한 번 더 받는다(늦게 반영되는 건)
  - 원자료는 문자열 그대로. 표준화는 뷰 trips_live에서(05 패널과 같은 열 이름)
[주의] 실시간 API에는 0 ~ 1분 대여가 그대로 들어온다(2026년 파일과 같음). 05 패널 규칙대로 "같은 대여소 ≤ 1분"은 이용 요약에서 뺀다.
[출력] ops.duckdb: raw_rent, rent_hours_done, 뷰 trips_live, job_runs
[실행] .venv/bin/python py/jobs/collect_rent.py            (저장소 루트, cron)
       .venv/bin/python py/jobs/collect_rent.py 2026-10-06  (그 날짜 24시간을 다시 받는다)
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import alert, connect, fetch_all, job_lock, log_run, table_exists, upsert_rows   # noqa: E402

VIEW = """
CREATE OR REPLACE VIEW trips_live AS
SELECT TRY_CAST(regexp_extract(BIKE_ID, '[0-9]+') AS INTEGER) AS bike_id,
       TRY_CAST(RENT_DT AS TIMESTAMP) AS rent_dt, TRY_CAST(RTN_DT AS TIMESTAMP) AS rtn_dt,
       TRY_CAST(RENT_ID AS INTEGER) AS rent_st, TRY_CAST(RTN_ID AS INTEGER) AS rtn_st,      -- 단계 0: RENT_ID = 파일의 대여소번호 체계
       TRY_CAST(USE_MIN AS DOUBLE) AS use_min, TRY_CAST(USE_DST AS DOUBLE) AS use_m,
       BIKE_SE_CD AS bike_type, USR_CLS_CD AS user_type, q_hour
FROM raw_rent"""


def pending_hours(con, now, backfill_days=3):
    con.execute("CREATE TABLE IF NOT EXISTS rent_hours_done (q_hour TIMESTAMP PRIMARY KEY, n BIGINT, fetched_at TIMESTAMP)")
    last = con.execute("SELECT max(q_hour) FROM rent_hours_done").fetchone()[0]
    end = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    start = last + dt.timedelta(hours=1) if last else end - dt.timedelta(days=backfill_days)
    hours = [start + dt.timedelta(hours=i) for i in range(int((end - start).total_seconds() // 3600) + 1)]
    hours.append(end - dt.timedelta(hours=2))
    return sorted(set(h for h in hours if h <= end))


def save_hour(con, h, rows):
    df = pd.DataFrame(rows).drop(columns=["START_INDEX", "END_INDEX", "RNUM"], errors="ignore").astype(str) if rows else None
    if df is not None:
        df["q_hour"] = h
    upsert_rows(con, "raw_rent", df, "q_hour = ?", (h,))
    con.execute("INSERT OR REPLACE INTO rent_hours_done VALUES (?, ?, now())", [h, len(rows)])


def run(hours=None):
    with job_lock():
        con = connect()
        try:
            hs = hours or pending_hours(con, dt.datetime.now())
            n = 0
            for h in hs:
                _, rows = fetch_all("tbCycleRentData", h.strftime("%Y-%m-%d"), h.strftime("%H"))
                save_hour(con, h, rows)
                n += len(rows)
            if table_exists(con, "raw_rent"):
                con.execute(VIEW)
            log_run(con, "collect_rent", "ok", f"{len(hs)}시간대 {n}건")
            print(f"collect_rent: {len(hs)}시간대 {n}건 ({hs[0]:%m-%d %H}시 ~ {hs[-1]:%m-%d %H}시)")
        except Exception as e:
            log_run(con, "collect_rent", "fail", str(e)); alert(f"collect_rent 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        d = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
        run([d + dt.timedelta(hours=i) for i in range(24)])
    else:
        run()
