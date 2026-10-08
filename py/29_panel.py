"""자전거×주 패널을 만든다(런북 05, 2A-9). 위험 모형(런북 06)은 이 표 하나만 읽는다.

주(월요일 시작)마다 자전거 한 대의 상태를 한 행으로 둔다. 만드는 테이블:
  bw_usage   주별 이용 요약. 대여 수, km, 분, 출퇴근·여가·왕복·비·젖은 노면·더위·추위 비중, 오르막·내리막,
             속도, 재배치, 헛대여, 출발 대여소 군집별 비중(sh_cl0~, 군집은 26_station_clusters.py)
  bw_fail    주별 신고 수(48시간 중복 병합 뒤), 부품별 신고 수, repaired_likely(신고 뒤 48시간 이상 대여 없음)
  panel0     자전거마다 첫 주~마지막 주를 빈 주까지 채운 격자에 위 두 표와 주별 강수일·동결융해일을 붙인 것
  panel      마지막 신고 이후 누적(km, 대여, 오르막, 강수일, 동결융해일), 최근 13주 신고, 최근 4주 km,
             최근 2주 헛대여, 라벨(다음 주 신고 수, 부품별)
  bike_info  차종(최빈값), 처음 등장일, 2023-06 이전 누적 km·대여, age_known
2024-01-01 이후이고 라벨이 있는 행만 bike_week.parquet로 저장한다.

주의:
  - bw_usage·bw_fail·panel0·panel의 SQL은 py/panel_sql.py에 있다. 운영(jobs/daily.py)도 같은 SQL을 쓴다.
  - 같은 대여소 0~1분 기록은 2026년 파일에만 있어 이용 요약에서 뺀다. 그대로 두면 2026년 대여 수가 4% 부푼다.
  - 설명변수는 모두 그 주 끝까지의 정보이고, 라벨(y_next 등)과 km_next만 다음 주 값이다. 누수 점검은 2A-10.
  - repaired_likely는 신고 뒤 공백으로 계산하므로 신고 주 행에서 설명변수로 쓰면 안 된다.
    대신 last_repaired는 공백이 끝난 뒤 주부터만 값이 있다.
  - 2020~2022-02 자전거번호는 QR 단말기 교체 때 새로 붙어 실제 나이를 모른다. age_known은 2022-03 이후 번호만 참.

입력: DuckDB trips_seq, fail_gap, fail, bike_year, data/interim/station_cluster.csv, data/processed/daily_master_v2.csv
출력: DuckDB station_cluster, bw_usage, bw_fail, wx_w, panel0, panel, bike_info,
      data/processed/panel/bike_week.parquet, output/tables/panel_2a9_checks.csv
실행: .venv/bin/python py/29_panel.py  (5~10분)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect   # noqa: E402
from panel_sql import (BW_FAIL_SELECT, BW_USAGE_SELECT, CLUSTER_K, PANEL0_SQL, PANEL_SQL, REPAIR_H,   # noqa: E402
                       cl_cols_expr, sh_cl_expr)

ROOT = Path(__file__).resolve().parents[1]


def main():
    con = connect()
    sc = (ROOT / "data/interim/station_cluster.csv").as_posix()
    con.execute(f"CREATE OR REPLACE TABLE station_cluster AS SELECT st_no, cluster_k{CLUSTER_K} AS cl FROM read_csv('{sc}', header = true)")
    con.execute("CREATE OR REPLACE TABLE bw_usage AS " + BW_USAGE_SELECT.format(sh_cl=sh_cl_expr(), src="trips_seq", cluster="station_cluster", extra_where=""))
    con.execute("CREATE OR REPLACE TABLE bw_fail AS " + BW_FAIL_SELECT.format(src="fail_gap", extra_where="WHERE keep", repair_h=REPAIR_H))
    master = (ROOT / "data/processed/daily_master_v2.csv").as_posix()
    con.execute(f"""
    CREATE OR REPLACE TABLE wx_w AS
    SELECT CAST(date_trunc('week', CAST(date AS DATE)) AS DATE) AS wk,
           sum(CASE WHEN rain_mm >= 1 THEN 1 ELSE 0 END) AS rain_days,
           sum(CASE WHEN tmin < 0 AND tmax > 0 THEN 1 ELSE 0 END) AS freeze_days
    FROM read_csv('{master}', header = true) GROUP BY 1""")
    con.execute(PANEL0_SQL.format(cl_cols=cl_cols_expr()))
    con.execute(PANEL_SQL)
    con.execute("""
    CREATE OR REPLACE TABLE bike_info AS
    SELECT t.bike_id, mode(t.bike_type) AS bike_type,
           coalesce(min(y.first_dt), min(t.rent_dt)) AS first_dt, coalesce(max(y.km_pre), 0) AS km_pre,
           coalesce(max(y.trips_pre), 0) AS trips_pre,
           coalesce(min(y.first_dt), min(t.rent_dt)) >= TIMESTAMP '2022-03-01' AS age_known   -- 2020 ~ 2022-02 번호는 QR 때 재부여라 실제 나이를 모른다
    FROM trips t LEFT JOIN (SELECT bike_id, min(first_dt) AS first_dt, sum(km) AS km_pre, sum(n_trips) AS trips_pre FROM bike_year GROUP BY bike_id) y USING (bike_id)
    GROUP BY t.bike_id""")
    (ROOT / "data/processed/panel").mkdir(parents=True, exist_ok=True)
    out = (ROOT / "data/processed/panel/bike_week.parquet").as_posix()
    con.execute(f"""
    COPY (SELECT p.*, b.bike_type, b.age_known,
                 CASE WHEN b.age_known THEN date_diff('day', CAST(b.first_dt AS DATE), p.wk) END AS age_days,   -- 나이를 아는 자전거만
                 date_diff('day', CAST(b.first_dt AS DATE), p.wk) AS days_since_number,                        -- 번호가 나타난 뒤 일수(전부)
                 b.km_pre + p.km_cum AS km_total, b.trips_pre
          FROM panel p JOIN bike_info b USING (bike_id)
          WHERE p.wk >= DATE '2024-01-01' AND p.y_next IS NOT NULL)
    TO '{out}' (FORMAT PARQUET)""")
    chk = con.sql(f"""
    SELECT count(*) AS rows, count(DISTINCT bike_id) AS bikes, count(DISTINCT wk) AS weeks,
           avg((y_next > 0)::INT) AS y_pos_share, avg((n_trips = 0)::INT) AS zero_use_share, avg(left_censored::INT) AS left_censored,
           min(km_since_rep) AS km_since_min, median(km_since_rep) AS km_since_med, quantile_cont(km_since_rep, 0.99) AS km_since_p99,
           avg((bike_type IS NULL)::INT) AS no_bike_type, avg(age_known::INT) AS age_known_share, median(age_days) AS age_med_known, median(sh_cl0) AS sh_cl0_med
    FROM read_parquet('{out}')""").df().T.reset_index()
    chk.columns = ["항목", "값"]
    n_rep_panel = con.execute(f"SELECT sum(n_rep) FROM read_parquet('{out}')").fetchone()[0]
    n_rep_all = con.execute("SELECT count(DISTINCT (bike_id, reg_ts)) FROM fail WHERE keep AND reg_ts >= '2024-01-01'").fetchone()[0]
    chk = pd.concat([chk, pd.DataFrame([{"항목": "패널에 들어간 2024~ 신고 비율", "값": n_rep_panel / n_rep_all}])], ignore_index=True)
    chk.to_csv(ROOT / "output/tables/panel_2a9_checks.csv", index=False)
    print(chk.to_string(index=False))
    print(con.sql(f"SELECT year(wk) AS y, count(*) n, avg(left_censored::INT) lc, avg((y_next>0)::INT) ypos, avg((n_trips=0)::INT) zero FROM read_parquet('{out}') GROUP BY 1 ORDER BY 1").df().round(3).to_string(index=False))


if __name__ == "__main__":
    main()
