"""고장신고 수집(런북 07, 3A-4). 매일 06:10.

tbCycleFailureReport로 최근 7일 신고를 매일 다시 받아 raw_fail에 넣는다. 늦게 등록되는 신고가 있어서
며칠치를 겹쳐 받고, 받은 날짜 범위는 지우고 다시 넣는다. API 시차는 0 ~ 1일이다.

주의: 날짜 파라미터를 주면 그날부터 약 30일치가 온다. 한 번 부르고 regDttm으로 거른다.
한 행은 신고 1건 × 고장유형 1개라서 신고 건수는 COUNT(DISTINCT bikeNo || regDttm)으로 센다.

출력: ops.duckdb raw_fail, 뷰 fail_live(bike_id, reg_ts, kind), job_runs
실행: .venv/bin/python py/jobs/collect_fail.py [days=7]
      .venv/bin/python py/jobs/collect_fail.py range 2026-07-01 2026-10-07   # 과거 구간은 28일씩 나눠 받는다
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import alert, connect, fetch_all, job_lock, log_run, table_exists, upsert_rows   # noqa: E402

VIEW = """
CREATE OR REPLACE VIEW fail_live AS
SELECT TRY_CAST(regexp_extract(bikeNo, '[0-9]+') AS INTEGER) AS bike_id, TRY_CAST(regDttm AS TIMESTAMP) AS reg_ts,
       trim(mlangComCdName) AS kind, CAST(reg_date AS DATE) AS reg_date
FROM raw_fail"""


def run(days=7):
    start = dt.date.today() - dt.timedelta(days=days)
    with job_lock():
        con = connect()
        try:
            _, rows = fetch_all("tbCycleFailureReport", start.strftime("%Y%m%d"))
            df = pd.DataFrame(rows).drop(columns=["START_INDEX", "END_INDEX", "RNUM"], errors="ignore").astype(str)
            if not df.empty:
                df["reg_date"] = pd.to_datetime(df["regDttm"], errors="coerce").dt.date.astype(str)
                df = df[df.reg_date >= str(start)]
            n = upsert_rows(con, "raw_fail", df, "reg_date >= ?", (str(start),))
            if table_exists(con, "raw_fail"):
                con.execute(VIEW)
            by_day = con.sql("SELECT reg_date, count(*) AS rows, count(DISTINCT bike_id || reg_ts) AS reports FROM fail_live GROUP BY 1 ORDER BY 1").df()
            zero = [str(d) for d in pd.date_range(start, dt.date.today() - dt.timedelta(days=1)).date if str(d) not in set(by_day.reg_date.astype(str))]
            log_run(con, "collect_fail", "ok", f"{n}행 {start}~")
            print(f"collect_fail: {n}행 (reg_date ≥ {start})"); print(by_day.tail(8).to_string(index=False))
            if zero:
                alert(f"collect_fail: 신고 0건인 날 {zero} — API 지연인지 확인")
        except Exception as e:
            log_run(con, "collect_fail", "fail", str(e)); alert(f"collect_fail 실패: {e}")
            raise
        finally:
            con.close()


def run_range(a, b, step=28):
    """a ~ b를 step일씩 나눠 받는다. 조각마다 그 날짜 범위만 지우고 다시 넣으니 다시 돌려도 된다."""
    d = a
    with job_lock():
        con = connect()
        try:
            total = 0
            while d <= b:
                e = min(d + dt.timedelta(days=step - 1), b)
                _, rows = fetch_all("tbCycleFailureReport", d.strftime("%Y%m%d"))
                df = pd.DataFrame(rows).drop(columns=["START_INDEX", "END_INDEX", "RNUM"], errors="ignore").astype(str)
                if not df.empty:
                    df["reg_date"] = pd.to_datetime(df["regDttm"], errors="coerce").dt.date.astype(str)
                    df = df[(df.reg_date >= str(d)) & (df.reg_date <= str(e))]
                n = upsert_rows(con, "raw_fail", df, "reg_date BETWEEN ? AND ?", (str(d), str(e)))
                total += n
                print(f"collect_fail range: {d} ~ {e} {n}행", flush=True)
                d = e + dt.timedelta(days=1)
            con.execute(VIEW)
            log_run(con, "collect_fail_range", "ok", f"{a}~{b} {total}행")
        except Exception as ex:
            log_run(con, "collect_fail_range", "fail", str(ex)); alert(f"collect_fail range 실패: {ex}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "range":
        run_range(*(dt.datetime.strptime(x, "%Y-%m-%d").date() for x in sys.argv[2:4]))
    else:
        run(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
