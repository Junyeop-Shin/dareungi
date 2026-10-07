"""자전거×주 패널 — 단계 2A-9 (런북 05). 06 문서의 위험 모형은 이 표 하나만 읽는다

[무엇을]
주(월요일 시작) 단위로 자전거 한 대의 상태표를 만든다.
  (1) bw_usage  주별 이용 요약(대여 수, km, 분, 출퇴근·여가·왕복·비·젖은 노면·더위·추위 비중, 오르막·내리막, 속도, 재배치, 헛대여)
  (2) bw_fail   주별 신고(48시간 중복 병합 후), 부품별, 수리 추정(repaired_likely = 신고 뒤 48시간 이상 공백)
  (3) panel0    자전거마다 첫 주 ~ 마지막 주의 빈 주를 포함한 격자에 (1)(2)와 주별 날씨(강수일·동결융해일)를 붙임
  (4) panel     '마지막 신고 이후' 누적(km, 대여, 오르막, 강수일, 동결융해일), 최근 13주 신고, 최근 4주 km, 최근 2주 헛대여, 라벨(다음 주 신고)
  (5) bike_info 차종(최빈값), 처음 등장일, 2023-06 이전 누적 km, age_known(2022-03 이후 번호만 실제 나이. 그 전 번호는 QR 재부여)
  (+) 그 주 대여의 출발 대여소 군집(2A-6, CLUSTER_K) 비중 sh_cl0 ~
  (6) 저장      2024-01-01 이후 & 라벨이 있는 행만 data/processed/panel/bike_week.parquet
[주의]
  - 2026년 파일에만 있는 '같은 대여소 0 ~ 1분' 기록은 이용 요약에서 뺀다(그 전 파일에는 없는 종류라 2026년 대여 수가 4% 부푼다).
  - 모든 설명변수는 그 주 끝까지의 정보, 라벨(y_next 등)만 다음 주 값이다(누수 점검은 2A-10).
  - repaired_likely는 신고 '이후' 공백으로 계산된다. 신고 주의 행에서 설명변수로 쓰지 않는다(last_repaired는 공백이 끝난 뒤부터만 값이 있다).
[입력]  DuckDB trips_seq, fail_gap, bike_year, data/processed/daily_master_v2.csv
[출력]  DuckDB bw_usage, bw_fail, panel0, panel, bike_info, data/processed/panel/bike_week.parquet, output/tables/panel_2a9_checks.csv
[실행]  .venv/bin/python py/29_panel.py   (약 5 ~ 10분)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPAIR_H = 48          # 신고 뒤 공백이 이보다 길면 수거·정비로 본다(2A-8 분포에서 확인)
CLUSTER_K = 5          # 대여소 군집 수(2A-6, 사람이 정한다). data/interim/station_cluster.csv의 cluster_k{K} 열을 쓴다


def main():
    con = connect()
    sc = (ROOT / "data/interim/station_cluster.csv").as_posix()
    con.execute(f"CREATE OR REPLACE TABLE station_cluster AS SELECT st_no, cluster_k{CLUSTER_K} AS cl FROM read_csv('{sc}', header = true)")
    sh_cl = ", ".join(f"avg(CASE WHEN c.cl = {i} THEN 1.0 ELSE 0 END) AS sh_cl{i}" for i in range(CLUSTER_K))   # 그 주 대여의 출발 대여소 군집별 비중
    con.execute(f"""
    CREATE OR REPLACE TABLE bw_usage AS
    SELECT bike_id, CAST(date_trunc('week', rent_dt) AS DATE) AS wk,
      count(*) AS n_trips, sum(use_m_fixed) / 1000.0 AS km, sum(use_min) AS minutes,
      avg(CAST(commute AS DOUBLE)) AS sh_commute, avg(CAST(leisure AS DOUBLE)) AS sh_leisure, avg(CAST(round_trip AS DOUBLE)) AS sh_round,
      avg(CASE WHEN rain_now > 0 THEN 1.0 ELSE 0 END) AS sh_rain, avg(CASE WHEN rain_prev3h > 0 THEN 1.0 ELSE 0 END) AS sh_wet,
      avg(CASE WHEN temp >= 30 THEN 1.0 ELSE 0 END) AS sh_hot, avg(CASE WHEN temp <= 0 THEN 1.0 ELSE 0 END) AS sh_cold,
      sum(climb_m) AS climb_m, sum(descent_m) AS descent_m,
      median(speed_kmh) AS speed_med, stddev(speed_kmh) AS speed_sd,
      sum(CAST(relocated AS INTEGER)) AS n_reloc, sum(CAST(sham AS INTEGER)) AS n_sham,
      {sh_cl}
    FROM trips_seq t LEFT JOIN station_cluster c ON t.rent_st = c.st_no
    WHERE NOT bad_time AND NOT (rent_st = rtn_st AND use_min <= 1)
    GROUP BY ALL""")
    con.execute(f"""
    CREATE OR REPLACE TABLE bw_fail AS
    SELECT bike_id, CAST(date_trunc('week', reg_ts) AS DATE) AS wk,
      count(DISTINCT reg_ts) AS n_rep,
      sum(CASE WHEN kind = '타이어' THEN 1 ELSE 0 END) AS n_tire, sum(CASE WHEN kind = '체인' THEN 1 ELSE 0 END) AS n_chain,
      sum(CASE WHEN kind = '안장' THEN 1 ELSE 0 END) AS n_saddle, sum(CASE WHEN kind = '페달' THEN 1 ELSE 0 END) AS n_pedal,
      max(CASE WHEN gap_h >= {REPAIR_H} OR gap_h IS NULL THEN 1 ELSE 0 END) AS repaired_likely
    FROM fail_gap WHERE keep GROUP BY ALL""")
    master = (ROOT / "data/processed/daily_master_v2.csv").as_posix()
    con.execute(f"""
    CREATE OR REPLACE TABLE wx_w AS
    SELECT CAST(date_trunc('week', CAST(date AS DATE)) AS DATE) AS wk,
           sum(CASE WHEN rain_mm >= 1 THEN 1 ELSE 0 END) AS rain_days,
           sum(CASE WHEN tmin < 0 AND tmax > 0 THEN 1 ELSE 0 END) AS freeze_days
    FROM read_csv('{master}', header = true) GROUP BY 1""")
    cl_cols = ", ".join(f"u.sh_cl{i}" for i in range(CLUSTER_K))
    con.execute(f"""
    CREATE OR REPLACE TABLE panel0 AS
    WITH span AS (SELECT bike_id, min(wk) AS w0, max(wk) AS w1 FROM bw_usage GROUP BY bike_id),
         weeks AS (SELECT DISTINCT wk FROM bw_usage)
    SELECT s.bike_id, w.wk,
      coalesce(u.n_trips, 0) AS n_trips, coalesce(u.km, 0) AS km, coalesce(u.minutes, 0) AS minutes,
      u.sh_commute, u.sh_leisure, u.sh_round, u.sh_rain, u.sh_wet, u.sh_hot, u.sh_cold,
      coalesce(u.climb_m, 0) AS climb_m, coalesce(u.descent_m, 0) AS descent_m, u.speed_med, u.speed_sd,
      coalesce(u.n_reloc, 0) AS n_reloc, coalesce(u.n_sham, 0) AS n_sham, {cl_cols},
      coalesce(f.n_rep, 0) AS n_rep, coalesce(f.n_tire, 0) AS n_tire, coalesce(f.n_chain, 0) AS n_chain,
      coalesce(f.n_saddle, 0) AS n_saddle, coalesce(f.n_pedal, 0) AS n_pedal, coalesce(f.repaired_likely, 0) AS repaired_likely,
      x.rain_days, x.freeze_days
    FROM span s JOIN weeks w ON w.wk BETWEEN s.w0 AND s.w1
    LEFT JOIN bw_usage u ON u.bike_id = s.bike_id AND u.wk = w.wk
    LEFT JOIN bw_fail  f ON f.bike_id = s.bike_id AND f.wk = w.wk
    LEFT JOIN wx_w     x ON x.wk = w.wk""")
    con.execute("""
    CREATE OR REPLACE TABLE panel AS
    WITH a AS (
      SELECT *, sum(CASE WHEN n_rep > 0 THEN 1 ELSE 0 END) OVER (PARTITION BY bike_id ORDER BY wk ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS seg
      FROM panel0)
    SELECT *,
      CASE WHEN seg = 0 THEN sum(km) OVER s ELSE sum(km) OVER s - first_value(km) OVER s END AS km_since_rep,
      CASE WHEN seg = 0 THEN sum(n_trips) OVER s ELSE sum(n_trips) OVER s - first_value(n_trips) OVER s END AS trips_since_rep,
      CASE WHEN seg = 0 THEN sum(climb_m) OVER s ELSE sum(climb_m) OVER s - first_value(climb_m) OVER s END AS climb_since_rep,
      CASE WHEN seg = 0 THEN sum(rain_days) OVER s ELSE sum(rain_days) OVER s - first_value(rain_days) OVER s END AS rain_days_since_rep,
      CASE WHEN seg = 0 THEN sum(freeze_days) OVER s ELSE sum(freeze_days) OVER s - first_value(freeze_days) OVER s END AS freeze_days_since_rep,
      CASE WHEN seg > 0 AND sum(n_trips) OVER s - first_value(n_trips) OVER s > 0 THEN first_value(repaired_likely) OVER s END AS last_repaired,
      sum(km) OVER (PARTITION BY bike_id ORDER BY wk ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS km_cum,
      seg = 0 AS left_censored,
      date_diff('day', max(CASE WHEN n_rep > 0 THEN wk END) OVER (PARTITION BY bike_id ORDER BY wk ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), wk) AS days_since_rep,
      sum(n_rep) OVER (PARTITION BY bike_id ORDER BY wk ROWS BETWEEN 12 PRECEDING AND CURRENT ROW) AS rep_13w,
      sum(km) OVER (PARTITION BY bike_id ORDER BY wk ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS km_4w,
      sum(n_sham) OVER (PARTITION BY bike_id ORDER BY wk ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS sham_2w,
      lead(n_rep) OVER (PARTITION BY bike_id ORDER BY wk) AS y_next,
      lead(n_tire) OVER (PARTITION BY bike_id ORDER BY wk) AS y_tire_next, lead(n_chain) OVER (PARTITION BY bike_id ORDER BY wk) AS y_chain_next,
      lead(n_saddle) OVER (PARTITION BY bike_id ORDER BY wk) AS y_saddle_next, lead(n_pedal) OVER (PARTITION BY bike_id ORDER BY wk) AS y_pedal_next,
      lead(km) OVER (PARTITION BY bike_id ORDER BY wk) AS km_next
    FROM a WINDOW s AS (PARTITION BY bike_id, seg ORDER BY wk ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)""")
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
