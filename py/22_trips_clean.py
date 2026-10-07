"""대여이력 정제와 표시(flag) — 단계 2A-2 (런북 05)

[무엇을]
2A-1이 저장한 상세 Parquet(data/processed/rent/, 2023-07 ~ 2026-06, 약 1.2억 건)을 읽어 DuckDB 테이블 `trips`를 만든다.
지우지 않고 **표시**한다 — 짧은 대여(헛대여)는 고장 신호라서 버리면 안 된다.
  - 완전 중복(같은 자전거·대여일시·대여소) 제거
  - bad_time   반납이 없거나 대여보다 앞선 행
  - speed_kmh  이용거리 ÷ 이용시간
  - sham       헛대여 후보: 같은 대여소 반납 & 2 ~ 3분 (04b에서 정한 기준. 2026년 파일에만 0 ~ 1분 기록이 있어 아래를 2분으로 자른다)
  - sham_wide  같은 대여소 반납 & 2 ~ 5분 (민감도)
  - round_trip 왕복: 같은 대여소 반납 & 3분 초과
  - dist_bad   거리 기록 오류: 다른 대여소인데 거리 0, 또는 35km/h 초과
[확인하는 것] 중복 제거 건수, bad_time 비율, 연도별 이용시간·거리·속도의 중앙값·99% 값, dist_bad·헛대여·왕복 비율,
             이용자종류·자전거구분 분포 → output/tables/panel_2a2_checks.csv, panel_2a2_by_year.csv, panel_2a2_types.csv
[입력]  data/processed/rent/*/*/*.parquet
[출력]  data/db/dareungi.duckdb 의 trips 테이블(위 표시 열 포함), output/tables/panel_2a2_*.csv
[실행]  .venv/bin/python py/22_trips_clean.py   (약 5 ~ 10분. 적재 스크립트와 동시에 돌리지 않는다 — DB 파일을 잠근다)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "output/tables"


def main():
    con = connect()
    rent = (ROOT / "data/processed/rent/*/*/*.parquet").as_posix()
    con.execute(f"CREATE OR REPLACE VIEW trips_raw AS SELECT * FROM read_parquet('{rent}', hive_partitioning = true)")
    n_raw = con.execute("SELECT count(*) FROM trips_raw").fetchone()[0]
    con.execute("""
    CREATE OR REPLACE TABLE trips AS
    SELECT DISTINCT ON (bike_id, rent_dt, rent_st) *,
      (rtn_dt IS NULL OR rtn_dt < rent_dt)                        AS bad_time,
      use_m / 1000.0 / NULLIF(use_min / 60.0, 0)                  AS speed_kmh,
      (rent_st = rtn_st AND use_min BETWEEN 2 AND 3)              AS sham,
      (rent_st = rtn_st AND use_min BETWEEN 2 AND 5)              AS sham_wide,
      (rent_st = rtn_st AND use_min > 3)                          AS round_trip,
      ((use_m = 0 AND rent_st <> rtn_st)
       OR use_m / 1000.0 / NULLIF(use_min / 60.0, 0) > 35)       AS dist_bad
    FROM trips_raw
    WHERE bike_id IS NOT NULL
    """)
    n = con.execute("SELECT count(*) FROM trips").fetchone()[0]
    n_nobike = con.execute("SELECT count(*) FROM trips_raw WHERE bike_id IS NULL").fetchone()[0]
    checks = pd.DataFrame([
        {"항목": "원본 행", "값": n_raw},
        {"항목": "자전거번호 없는 행(제외)", "값": n_nobike},
        {"항목": "완전 중복 제거 건수", "값": n_raw - n_nobike - n},
        {"항목": "trips 행", "값": n},
    ] + [{"항목": k, "값": v} for k, v in zip(
        ["bad_time 비율", "sham(2~3분) 비율", "sham_wide(2~5분) 비율", "round_trip 비율", "dist_bad 비율"],
        con.execute("SELECT avg(bad_time::INT), avg(sham::INT), avg(sham_wide::INT), avg(round_trip::INT), avg(dist_bad::INT) FROM trips").fetchone())])
    checks.to_csv(T / "panel_2a2_checks.csv", index=False)
    by_year = con.sql("""
    SELECT y, count(*) AS n,
           median(use_min) AS min_med, quantile_cont(use_min, 0.99) AS min_p99,
           median(use_m) AS m_med, quantile_cont(use_m, 0.99) AS m_p99,
           median(speed_kmh) AS kmh_med, quantile_cont(speed_kmh, 0.99) AS kmh_p99,
           avg(bad_time::INT) AS bad_time, avg(dist_bad::INT) AS dist_bad,
           avg((use_m = 0 AND rent_st <> rtn_st)::INT) AS dist0_other_st, avg((speed_kmh > 35)::INT) AS over35,
           avg(sham::INT) AS sham, avg(sham_wide::INT) AS sham_wide, avg(round_trip::INT) AS round_trip,
           avg((use_min <= 1)::INT) AS le1min
    FROM trips GROUP BY y ORDER BY y""").df()
    by_year.round(4).to_csv(T / "panel_2a2_by_year.csv", index=False)
    types = con.sql("""
    SELECT 'user_type' AS col, user_type AS value, y, count(*) AS n FROM trips GROUP BY ALL
    UNION ALL
    SELECT 'bike_type', bike_type, y, count(*) FROM trips GROUP BY ALL
    ORDER BY 1, 3, 4 DESC""").df()
    types.to_csv(T / "panel_2a2_types.csv", index=False)
    bikes_type = con.sql("""
    SELECT bike_type, count(DISTINCT bike_id) AS bikes FROM trips WHERE bike_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""").df()
    pd.set_option("display.width", 250)
    print(checks.to_string(index=False)); print(by_year.round(3).to_string(index=False))
    print(types.pivot_table(index=["col", "value"], columns="y", values="n", aggfunc="sum").fillna(0).astype(int).to_string())
    print(bikes_type.to_string(index=False))


if __name__ == "__main__":
    main()
