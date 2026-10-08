"""연속 헛대여 경보. 매시 20분, 대여이력 수집 직후에 돈다(런북 07).

최근 7일 대여이력에서 같은 자전거가 같은 대여소에 2~3분 만에 반납되는 일이 두 번 연속 생기면 경보를 만든다.
2B-5에서 확인한 규칙으로, 이런 자전거는 7일 안에 46%가 신고됐다(기준선의 5.2배).
같은 자전거의 24시간 안 중복 경보는 첫 번째만 남기고, 마지막 반납 대여소와 그 뒤 신고 여부를 붙인다.

출력: ops.duckdb alerts_live(bike_id, alert_ts, last_st, st_name, district, n_sham_7d, reported_after)
실행: .venv/bin/python py/jobs/detect_sham.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import alert, connect, job_lock, log_run, table_exists   # noqa: E402

SQL = """
CREATE OR REPLACE TABLE alerts_live AS
WITH t AS (
  SELECT bike_id, rent_dt, rtn_st, (rent_st = rtn_st AND use_min BETWEEN 2 AND 3) AS sham
  FROM trips_live WHERE rent_dt >= now() - INTERVAL 7 DAY AND bike_id IS NOT NULL),
s AS (SELECT *, lag(sham) OVER w AS prev_sham FROM t WINDOW w AS (PARTITION BY bike_id ORDER BY rent_dt)),
a AS (SELECT bike_id, rent_dt AS alert_ts, rtn_st AS last_st,
             lag(rent_dt) OVER (PARTITION BY bike_id ORDER BY rent_dt) AS prev_alert
      FROM s WHERE sham AND prev_sham),
b AS (SELECT * FROM a WHERE prev_alert IS NULL OR alert_ts - prev_alert > INTERVAL 24 HOUR),
n AS (SELECT bike_id, count(*) AS n_sham_7d FROM t WHERE sham GROUP BY 1)
SELECT b.bike_id, b.alert_ts, b.last_st, st.st_name, st.district, n.n_sham_7d,
       EXISTS (SELECT 1 FROM {fail} f WHERE f.bike_id = b.bike_id AND f.reg_ts >= b.alert_ts) AS reported_after
FROM b LEFT JOIN n USING (bike_id) LEFT JOIN {station} st ON b.last_st = st.st_no
ORDER BY b.alert_ts DESC"""


def run():
    with job_lock():
        con = connect()
        try:
            if not table_exists(con, "raw_rent"):
                raise RuntimeError("raw_rent 없음 — collect_rent를 먼저")
            fail = "fail_live" if table_exists(con, "raw_fail") else "(SELECT NULL::INTEGER AS bike_id, NULL::TIMESTAMP AS reg_ts WHERE FALSE)"
            station = "station_live" if table_exists(con, "station_live") else "station_base"
            con.execute(SQL.format(fail=fail, station=station))
            n, n24 = con.execute("SELECT count(*), sum((alert_ts >= now() - INTERVAL 24 HOUR)::INT) FROM alerts_live").fetchone()
            log_run(con, "detect_sham", "ok", f"7일 {n}건, 24시간 {n24}건")
            print(f"detect_sham: 최근 7일 경보 {n}건, 24시간 {n24}건")
        except Exception as e:
            log_run(con, "detect_sham", "fail", str(e)); alert(f"detect_sham 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
