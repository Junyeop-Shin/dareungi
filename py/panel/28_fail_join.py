"""고장신고에 신고 위치와 신고 뒤 공백을 붙인다(런북 05, 2A-8).

만드는 테이블:
  fail      정리된 고장신고(fail_std.parquet의 bike, reg_ts, kind, keep) 중 2023-07-01 이후
  fail_loc  신고 직전 마지막 반납 대여소(last_st)와 반납 시각. 고장 난 위치의 대리 지표로, 단계 3의 자치구 집계에 쓴다
  fail_gap  신고 뒤 다음 대여까지 공백(gap_h)
gap_h 분포는 몇 시간(계속 운행)과 며칠(수거·정비) 두 봉우리로 갈린다. 29_panel.py의 repaired_likely 기준(48시간)은
이 분포를 보고 잡았다.

주의: reg_ts는 시간대 없는 한국 시간이라 대여 기록과 그대로 비교한다. 날짜만 있는 신고(2021-02~12)는 이 기간에 없다.

입력: data/processed/fail_std.parquet, DuckDB trips, station
출력: DuckDB fail, fail_loc, fail_gap, output/tables/panel_2a8_checks.csv, panel_2a8_gap_hist.csv, panel_2a8_district.csv
실행: .venv/bin/python py/panel/28_fail_join.py  (1~2분)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    con = connect()
    fail = (ROOT / "data/processed/fail_std.parquet").as_posix()
    con.execute(f"""
    CREATE OR REPLACE TABLE fail AS
    SELECT CAST(bike AS INTEGER) AS bike_id, CAST(reg_ts AS TIMESTAMP) AS reg_ts, kind, keep
    FROM read_parquet('{fail}') WHERE reg_ts >= TIMESTAMP '2023-07-01'""")
    con.execute("""
    CREATE OR REPLACE TABLE fail_loc AS
    SELECT f.*, t.rtn_st AS last_st, t.rtn_dt AS last_rtn_dt
    FROM fail f ASOF LEFT JOIN (SELECT bike_id, rtn_st, rtn_dt FROM trips WHERE rtn_dt IS NOT NULL) t
      ON f.bike_id = t.bike_id AND f.reg_ts >= t.rtn_dt""")
    con.execute("""
    CREATE OR REPLACE TABLE fail_gap AS
    SELECT f.*, n.rent_dt AS next_rent_dt, date_diff('hour', f.reg_ts, n.rent_dt) AS gap_h
    FROM fail_loc f ASOF LEFT JOIN (SELECT bike_id, rent_dt FROM trips) n
      ON f.bike_id = n.bike_id AND f.reg_ts <= n.rent_dt""")
    chk = con.sql("""
    SELECT count(*) AS rows, sum(keep::INT) AS rows_dedup, count(DISTINCT (bike_id, reg_ts)) AS reports,
           avg((last_st IS NOT NULL)::INT) AS has_loc, avg((next_rent_dt IS NOT NULL)::INT) AS has_next,
           median(gap_h) AS gap_med_h, avg((gap_h < 24)::INT) AS gap_lt_24h, avg((gap_h >= 48)::INT) AS gap_ge_48h,
           avg((date_diff('hour', last_rtn_dt, reg_ts) <= 24)::INT) AS loc_within_24h
    FROM fail_gap WHERE keep""").df().T.reset_index()
    chk.columns = ["항목", "값"]
    chk.to_csv(ROOT / "output/tables/panel_2a8_checks.csv", index=False)
    hist = con.sql("""
    SELECT CASE WHEN gap_h < 1 THEN '<1h' WHEN gap_h < 6 THEN '1~6h' WHEN gap_h < 24 THEN '6~24h' WHEN gap_h < 48 THEN '1~2d'
                WHEN gap_h < 168 THEN '2~7d' WHEN gap_h < 720 THEN '7~30d' WHEN gap_h IS NULL THEN '없음(끝)' ELSE '30d+' END AS gap_bin,
           count(*) AS n FROM fail_gap WHERE keep GROUP BY 1""").df()
    order = ['<1h', '1~6h', '6~24h', '1~2d', '2~7d', '7~30d', '30d+', '없음(끝)']
    hist = hist.set_index("gap_bin").reindex(order).reset_index(); hist["share"] = hist.n / hist.n.sum()
    hist.round(4).to_csv(ROOT / "output/tables/panel_2a8_gap_hist.csv", index=False)
    dist = con.sql("""
    SELECT year(f.reg_ts) AS y, s.district, count(*) AS n FROM fail_gap f JOIN station s ON f.last_st = s.st_no
    WHERE f.keep GROUP BY ALL ORDER BY 1, 3 DESC""").df()
    dist.to_csv(ROOT / "output/tables/panel_2a8_district.csv", index=False)
    pd.set_option("display.width", 250)
    print(chk.to_string(index=False)); print(hist.round(3).to_string(index=False))
    print(dist.pivot(index="district", columns="y", values="n").fillna(0).astype(int).sort_values(2024, ascending=False).head(10).to_string())


if __name__ == "__main__":
    main()
