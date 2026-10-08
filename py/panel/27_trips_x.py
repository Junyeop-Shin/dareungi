"""통행마다 통행 구분·지형·날씨를 붙이고 자전거별 이동 순서를 만든다(런북 05, 2A-7).

만드는 테이블:
  holidays   공휴일(data/raw/calendar/holidays_2016_2026.csv)
  trips_x    trips + 통행 구분(commute/leisure) + OD 지형(직선거리, 오르막, 내리막, 우회율)
             + 대여 시각의 날씨(기온, 강수, 직전 3시간 강수) + use_m_fixed
  trips_seq  trips_x + relocated(직전 반납과 다른 대여소에서 대여됨 = 이용자 없이 옮겨짐), idle_h(직전 반납 뒤 쉰 시간)
use_m_fixed는 거리 기록이 잘못된 행(dist_bad)을 직선거리 × 우회율 중앙값으로 채운 거리다.

통행 구분은 대여소가 아니라 통행 하나하나에 붙인다.
  commute  평일(공휴일 제외) 07~09시 또는 17~19시 대여이고 왕복이 아님
  leisure  주말·공휴일, 왕복, 또는 한강 대여소 출발·도착(station.han_river, 이름 기준 임시 규칙)
둘 다 아니면 기타다. 주말 출근 시간대 통행은 leisure이고, 평일 출퇴근 시간에 한강 대여소를 오간 통행은
commute와 leisure가 둘 다 참이다.

입력: DuckDB trips, od, station, wx_h
출력: DuckDB holidays, trips_x, trips_seq, output/tables/panel_2a7_checks.csv
실행: .venv/bin/python py/panel/27_trips_x.py  (5~15분. 창 함수는 연도로 나누지 않아도 메모리 8GB에서 돌았다)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    con = connect()
    hol = (ROOT / "data/raw/calendar/holidays_2016_2026.csv").as_posix()
    con.execute(f"CREATE OR REPLACE TABLE holidays AS SELECT DISTINCT CAST(date AS DATE) AS date FROM read_csv('{hol}', header = true)")
    detour = con.execute("""
    SELECT median(t.use_m / NULLIF(o.straight_m, 0)) FROM trips t JOIN od o USING (rent_st, rtn_st)
    WHERE NOT t.dist_bad AND NOT t.round_trip AND o.straight_m > 200 AND t.y = 2024""").fetchone()[0]
    con.execute(f"""
    CREATE OR REPLACE TABLE trips_x AS
    SELECT t.*,
      (isodow(t.rent_dt) <= 5 AND h.date IS NULL
         AND (hour(t.rent_dt) BETWEEN 7 AND 9 OR hour(t.rent_dt) BETWEEN 17 AND 19)
         AND NOT t.round_trip)                                                   AS commute,
      (isodow(t.rent_dt) >= 6 OR h.date IS NOT NULL OR t.round_trip
         OR sa.han_river = 1 OR sb.han_river = 1)                                AS leisure,
      o.straight_m, o.delta_elev,
      greatest(o.delta_elev, 0)  AS climb_m,
      greatest(-o.delta_elev, 0) AS descent_m,
      t.use_m / NULLIF(o.straight_m, 0) AS detour,
      CASE WHEN t.dist_bad AND o.straight_m IS NOT NULL THEN o.straight_m * {detour}      -- 거리 오류: 직선거리 × 우회율 중앙값
           WHEN t.dist_bad THEN t.use_min / 60.0 * 8.6 * 1000                             -- 좌표도 없으면 시간 × 중앙 속도
           ELSE t.use_m END                                                      AS use_m_fixed,
      w.temp, w.rain_mm AS rain_now, w.rain_prev3h
    FROM trips t
    LEFT JOIN od o        USING (rent_st, rtn_st)
    LEFT JOIN station sa  ON t.rent_st = sa.st_no
    LEFT JOIN station sb  ON t.rtn_st  = sb.st_no
    LEFT JOIN holidays h  ON CAST(t.rent_dt AS DATE) = h.date
    LEFT JOIN wx_h w      ON date_trunc('hour', t.rent_dt) = w.ts
    """)
    con.execute("""
    CREATE OR REPLACE TABLE trips_seq AS
    SELECT *,
      prev_rtn_st IS NOT NULL AND rent_st <> prev_rtn_st AS relocated,
      date_diff('hour', prev_rtn_dt, rent_dt)            AS idle_h
    FROM (SELECT *, lag(rtn_st) OVER w AS prev_rtn_st, lag(rtn_dt) OVER w AS prev_rtn_dt
          FROM trips_x WINDOW w AS (PARTITION BY bike_id ORDER BY rent_dt))
    """)
    chk = con.sql("""
    SELECT count(*) AS n, avg(commute::INT) AS commute, avg(leisure::INT) AS leisure, avg((NOT commute AND NOT leisure)::INT) AS other,
           avg((straight_m IS NULL)::INT) AS no_od, avg((temp IS NULL)::INT) AS no_wx,
           avg((rain_now > 0)::INT) AS rain_now, avg((rain_prev3h > 0)::INT) AS wet, avg((temp >= 30)::INT) AS hot, avg((temp <= 0)::INT) AS cold,
           avg(relocated::INT) AS relocated, median(idle_h) AS idle_med_h, avg((idle_h >= 168)::INT) AS idle_7d,
           median(use_m_fixed) AS m_fixed_med, avg((use_m_fixed IS NULL)::INT) AS no_fixed
    FROM trips_seq""").df().T.reset_index()
    chk.columns = ["항목", "값"]
    chk = pd.concat([chk, pd.DataFrame([{"항목": "우회율 중앙값(use_m_fixed에 씀)", "값": detour}])], ignore_index=True)
    chk.to_csv(ROOT / "output/tables/panel_2a7_checks.csv", index=False)
    print(chk.to_string(index=False))
    print(con.sql("SELECT y, avg(relocated::INT) AS relocated, avg(commute::INT) AS commute, avg(leisure::INT) AS leisure FROM trips_seq GROUP BY y ORDER BY y").df().round(3).to_string(index=False))


if __name__ == "__main__":
    main()
