"""자전거×주 패널 SQL. 분석(py/29_panel.py)과 운영(py/jobs/daily.py)이 같은 정의를 쓰도록 한곳에 둔다.

2A-9의 주별 이용 요약(BW_USAGE_SELECT), 주별 신고(BW_FAIL_SELECT), 격자(PANEL0_SQL), 누적·라벨(PANEL_SQL)을
문자열로 갖고 있다. 운영에서는 통행 원천이 trips_seq(파일)가 아니라 trips_live_x(API)라서 {src} 자리에
원천 테이블 이름을 넣어 쓴다.

주의: 정의를 바꾸면 분석 패널(bike_week.parquet)과 운영 패널이 어긋난다. 바꾸면 둘 다 다시 만든다.
"""
CLUSTER_K = 5     # 대여소 군집 수(station_cluster.csv의 cluster_k5)
REPAIR_H = 48     # 신고 뒤 이 시간 이상 대여가 없으면 수거·정비된 것으로 본다(2A-8 gap_h 분포)

BW_USAGE_SELECT = """
SELECT bike_id, CAST(date_trunc('week', rent_dt) AS DATE) AS wk,
  count(*) AS n_trips, sum(use_m_fixed) / 1000.0 AS km, sum(use_min) AS minutes,
  avg(CAST(commute AS DOUBLE)) AS sh_commute, avg(CAST(leisure AS DOUBLE)) AS sh_leisure, avg(CAST(round_trip AS DOUBLE)) AS sh_round,
  avg(CASE WHEN rain_now > 0 THEN 1.0 ELSE 0 END) AS sh_rain, avg(CASE WHEN rain_prev3h > 0 THEN 1.0 ELSE 0 END) AS sh_wet,
  avg(CASE WHEN temp >= 30 THEN 1.0 ELSE 0 END) AS sh_hot, avg(CASE WHEN temp <= 0 THEN 1.0 ELSE 0 END) AS sh_cold,
  sum(climb_m) AS climb_m, sum(descent_m) AS descent_m,
  median(speed_kmh) AS speed_med, stddev(speed_kmh) AS speed_sd,
  sum(CAST(relocated AS INTEGER)) AS n_reloc, sum(CAST(sham AS INTEGER)) AS n_sham,
  {sh_cl}
FROM {src} t LEFT JOIN {cluster} c ON t.rent_st = c.st_no
WHERE NOT bad_time AND NOT (rent_st = rtn_st AND use_min <= 1) {extra_where}
GROUP BY ALL"""

BW_FAIL_SELECT = """
SELECT bike_id, CAST(date_trunc('week', reg_ts) AS DATE) AS wk,
  count(DISTINCT reg_ts) AS n_rep,
  sum(CASE WHEN kind = '타이어' THEN 1 ELSE 0 END) AS n_tire, sum(CASE WHEN kind = '체인' THEN 1 ELSE 0 END) AS n_chain,
  sum(CASE WHEN kind = '안장' THEN 1 ELSE 0 END) AS n_saddle, sum(CASE WHEN kind = '페달' THEN 1 ELSE 0 END) AS n_pedal,
  max(CASE WHEN gap_h >= {repair_h} OR gap_h IS NULL THEN 1 ELSE 0 END) AS repaired_likely
FROM {src} {extra_where} GROUP BY ALL"""

PANEL0_SQL = """
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
LEFT JOIN wx_w     x ON x.wk = w.wk"""

PANEL_SQL = """
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
FROM a WINDOW s AS (PARTITION BY bike_id, seg ORDER BY wk ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"""


def sh_cl_expr(k=CLUSTER_K):
    return ", ".join(f"avg(CASE WHEN c.cl = {i} THEN 1.0 ELSE 0 END) AS sh_cl{i}" for i in range(k))


def cl_cols_expr(k=CLUSTER_K):
    return ", ".join(f"u.sh_cl{i}" for i in range(k))
