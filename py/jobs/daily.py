"""매일 갱신 — 단계 3A-5·3A-7 (런북 07). 매일 07:00 (collect_* 뒤)

[무엇을]
  1. trips_live_x: 최근 21일 API 대여이력에 2A-7과 같은 표시·지형·날씨·재배치를 붙인다(통행 구분, 직선거리·고도차, 대여 시각 날씨, 직전 반납 대비 이동).
  2. 주별 요약(bw_usage·bw_fail)을 이번 주(월요일이면 지난주도)만 다시 만들어 교체한다 — 끝난 주의 요약은 변하지 않으므로.
  3. 패널(panel)을 주별 요약 전체 위에서 다시 계산한다(2A-9와 같은 SQL, py/panel_sql.py). panel_latest = 가장 최근 완결 주.
  4. 데이터 품질 점검(3A-7): 시간대별 대여 건수가 지난주 같은 시간의 30% 미만, 작업 실패, 26시간 이상 안 돈 작업, 신고 0건인 날.
  5. bike_card(시민 조회용 집계, 런북 08 정의) → 스냅샷 발행.
[전제] init_ops.py로 과거 주별 요약이 들어 있어야 '마지막 신고 이후 누적'이 이어진다.
[알려진 공백] 반기 파일(~2026-06-30)과 API 수집 시작(2026-10-04) 사이 석 달은 비어 있다. tbCycleRentData는 과거 날짜도 조회되므로
       collect_rent.py YYYY-MM-DD 로 하루씩 채울 수 있다(약 100일 × 24시간대). 채우기 전까지 그 기간의 누적은 과소.
[출력] ops.duckdb: trips_live_x, bw_usage, bw_fail, panel, panel_latest, bike_card, quality_log; serving/*.parquet
[실행] .venv/bin/python py/jobs/daily.py
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, alert, connect, job_lock, log_run, table_exists   # noqa: E402
from panel_sql import BW_FAIL_SELECT, BW_USAGE_SELECT, PANEL0_SQL, PANEL_SQL, cl_cols_expr, sh_cl_expr   # noqa: E402
from publish import build_status, publish   # noqa: E402

DETOUR = 1.18          # 2A-4 우회율 중앙값(거리 오류 행을 채울 때)
LIVE_DAYS = 21


def build_trips_live_x(con):
    hol = (ROOT / "data/raw/calendar/holidays_2016_2026.csv").as_posix()
    con.execute(f"CREATE OR REPLACE TABLE holidays AS SELECT DISTINCT CAST(date AS DATE) AS date FROM read_csv('{hol}', header = true)")
    station = "station_live" if table_exists(con, "station_live") else "station_base"
    con.execute(f"""
    CREATE OR REPLACE TABLE trips_live_x AS
    WITH t AS (
      SELECT DISTINCT ON (bike_id, rent_dt, rent_st) *,
        (rtn_dt IS NULL OR rtn_dt < rent_dt) AS bad_time,
        use_m / 1000.0 / NULLIF(use_min / 60.0, 0) AS speed_kmh,
        (rent_st = rtn_st AND use_min BETWEEN 2 AND 3) AS sham,
        (rent_st = rtn_st AND use_min > 3) AS round_trip,
        ((use_m = 0 AND rent_st <> rtn_st) OR use_m / 1000.0 / NULLIF(use_min / 60.0, 0) > 35) AS dist_bad
      FROM trips_live WHERE bike_id IS NOT NULL AND rent_dt >= now() - INTERVAL {LIVE_DAYS} DAY),
    x AS (
      SELECT t.*,
        (isodow(t.rent_dt) <= 5 AND h.date IS NULL AND (hour(t.rent_dt) BETWEEN 7 AND 9 OR hour(t.rent_dt) BETWEEN 17 AND 19) AND NOT t.round_trip) AS commute,
        (isodow(t.rent_dt) >= 6 OR h.date IS NOT NULL OR t.round_trip OR sa.han_river = 1 OR sb.han_river = 1) AS leisure,
        2 * 6371000 * asin(sqrt(pow(sin(radians(sb.lat - sa.lat) / 2), 2) + cos(radians(sa.lat)) * cos(radians(sb.lat)) * pow(sin(radians(sb.lon - sa.lon) / 2), 2))) AS straight_m,
        greatest(sb.elev_m - sa.elev_m, 0) AS climb_m, greatest(sa.elev_m - sb.elev_m, 0) AS descent_m,
        w.temp, w.rain_mm AS rain_now, w.rain_prev3h
      FROM t
      LEFT JOIN {station} sa ON t.rent_st = sa.st_no LEFT JOIN {station} sb ON t.rtn_st = sb.st_no
      LEFT JOIN holidays h ON CAST(t.rent_dt AS DATE) = h.date
      LEFT JOIN (SELECT ts, temp, rain_mm, sum(rain_mm) OVER (ORDER BY ts ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS rain_prev3h FROM wx_hourly_live) w
        ON date_trunc('hour', t.rent_dt) = w.ts)
    SELECT *,
      CASE WHEN dist_bad AND straight_m IS NOT NULL THEN straight_m * {DETOUR} WHEN dist_bad THEN use_min / 60.0 * 8.6 * 1000 ELSE use_m END AS use_m_fixed,
      prev_rtn_st IS NOT NULL AND rent_st <> prev_rtn_st AS relocated
    FROM (SELECT *, lag(rtn_st) OVER (PARTITION BY bike_id ORDER BY rent_dt) AS prev_rtn_st FROM x)""")


def refresh_weeks(con, today):
    cut = today - dt.timedelta(days=today.weekday())                 # 이번 주 월요일
    if today.weekday() == 0:
        cut -= dt.timedelta(days=7)                                   # 월요일이면 지난주도
    con.execute("DELETE FROM bw_usage WHERE wk >= ?", [cut])
    con.execute("INSERT INTO bw_usage BY NAME " + BW_USAGE_SELECT.format(sh_cl=sh_cl_expr(), src="trips_live_x", cluster="station_cluster",
                                                                        extra_where=f"AND rent_dt >= DATE '{cut}'"))
    if table_exists(con, "raw_fail"):
        con.execute("""CREATE OR REPLACE TEMP TABLE fail_gap_live AS
        SELECT f.bike_id, f.reg_ts, f.kind, n.rent_dt AS next_rent_dt, date_diff('hour', f.reg_ts, n.rent_dt) AS gap_h
        FROM fail_live f ASOF LEFT JOIN (SELECT bike_id, rent_dt FROM trips_live) n ON f.bike_id = n.bike_id AND f.reg_ts <= n.rent_dt""")
        con.execute("DELETE FROM bw_fail WHERE wk >= ?", [cut])
        con.execute("INSERT INTO bw_fail BY NAME " + BW_FAIL_SELECT.format(src="fail_gap_live", extra_where=f"WHERE reg_ts >= DATE '{cut}'"))
    return cut


def rebuild_panel(con, today):
    con.execute(PANEL0_SQL.format(cl_cols=cl_cols_expr()))
    con.execute(PANEL_SQL)
    this_mon = today - dt.timedelta(days=today.weekday())
    con.execute(f"""CREATE OR REPLACE TABLE panel_latest AS
    SELECT p.*, b.bike_type, b.age_known, CASE WHEN b.age_known THEN date_diff('day', CAST(b.first_dt AS DATE), p.wk) END AS age_days,
           b.km_pre + p.km_cum AS km_total, b.trips_pre
    FROM panel p JOIN bike_info b USING (bike_id)
    WHERE p.wk = (SELECT max(wk) FROM panel WHERE wk < DATE '{this_mon}')""")
    return con.execute("SELECT max(wk), count(*) FROM panel_latest").fetchone()


def quality_checks(con):
    con.execute("CREATE TABLE IF NOT EXISTS quality_log (ts TIMESTAMP, check_name VARCHAR, detail VARCHAR)")
    issues = []
    drop = con.sql("""SELECT q_hour, n, lag(n, 168) OVER (ORDER BY q_hour) AS n_lastweek FROM rent_hours_done
                      QUALIFY n < 0.3 * n_lastweek AND n_lastweek > 100 AND q_hour >= now() - INTERVAL 2 DAY""").df()
    if len(drop):
        issues.append(("대여 건수 급감", ", ".join(f"{r.q_hour:%m-%d %H}시 {r.n}/{r.n_lastweek}" for r in drop.itertuples())))
    fails = con.sql("SELECT job, max(ts) AS ts FROM job_runs WHERE status = 'fail' AND ts >= now() - INTERVAL 1 DAY GROUP BY 1").df()
    if len(fails):
        issues.append(("작업 실패", ", ".join(f"{r.job}@{r.ts:%m-%d %H:%M}" for r in fails.itertuples())))
    stale = con.sql("""SELECT job, max(ts) AS last_ok FROM job_runs WHERE status = 'ok' GROUP BY 1
                       HAVING max(ts) < now() - INTERVAL 26 HOUR AND job IN ('collect_rent', 'collect_fail', 'collect_wx_obs')""").df()
    if len(stale):
        issues.append(("안 도는 작업", ", ".join(f"{r.job}(마지막 {r.last_ok:%m-%d %H:%M})" for r in stale.itertuples())))
    for name, detail in issues:
        con.execute("INSERT INTO quality_log VALUES (now(), ?, ?)", [name, detail])
        alert(f"{name}: {detail}")
    return issues


def build_bike_card(con):
    con.execute("""
    CREATE OR REPLACE TABLE bike_card AS
    WITH t AS (SELECT bike_id, count(*) FILTER (WHERE rent_dt >= now() - INTERVAL 30 DAY) AS trips_30d,
                      sum(use_m) FILTER (WHERE rent_dt >= now() - INTERVAL 30 DAY) / 1000.0 AS km_30d
               FROM trips_live WHERE NOT (rent_st = rtn_st AND use_min <= 1) GROUP BY 1),
         f AS (SELECT bike_id, string_agg(strftime(reg_ts, '%m/%d') || ' ' || kind, ', ' ORDER BY reg_ts DESC) AS reports_90d
               FROM (SELECT DISTINCT bike_id, reg_ts, kind FROM fail_live WHERE reg_ts >= now() - INTERVAL 90 DAY) GROUP BY 1)
    SELECT b.bike_id, coalesce(t.trips_30d, 0) AS trips_30d, round(coalesce(t.km_30d, 0), 1) AS km_30d, f.reports_90d,
           round(p.km_since_rep) AS km_since_rep, CASE WHEN b.age_known THEN round(date_diff('day', CAST(b.first_dt AS DATE), current_date) / 365.0, 1) END AS age_years,
           b.bike_type
    FROM bike_info b LEFT JOIN t USING (bike_id) LEFT JOIN f USING (bike_id) LEFT JOIN panel_latest p USING (bike_id)""")


def run():
    today = dt.date.today()
    with job_lock():
        con = connect()
        try:
            for need in ("raw_rent", "bw_usage", "bike_info"):
                if not table_exists(con, need):
                    raise RuntimeError(f"{need} 없음 — collect_rent / init_ops 먼저")
            build_trips_live_x(con)
            cut = refresh_weeks(con, today)
            wk, n = rebuild_panel(con, today)
            issues = quality_checks(con)
            build_bike_card(con)
            build_status(con)
            for t in ("bike_card", "data_status", "alerts_live"):
                if table_exists(con, t):
                    publish(con, t)
            log_run(con, "daily", "ok", f"주별 요약 {cut}~ 교체, 패널 최신 주 {wk} {n}대, 품질 이슈 {len(issues)}")
            print(f"daily: 주별 요약 {cut}~ 교체, 패널 최신 완결 주 {wk} ({n:,}대), 품질 이슈 {len(issues)}건")
        except Exception as e:
            log_run(con, "daily", "fail", str(e)); alert(f"daily 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
