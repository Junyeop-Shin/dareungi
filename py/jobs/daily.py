"""매일 갱신(런북 07, 3A-5·3A-7). 매일 07:00, collect_* 작업 뒤에 돈다.

순서:
  1. trips_live_x: 최근 21일 API 대여이력에 2A-7과 같은 파생 열을 붙인다(통행 구분, 직선거리·고도차,
     대여 시각 날씨, 직전 반납 대여소와 다른 곳에서 빌렸는지).
  2. 주별 요약(bw_usage·bw_fail)은 이번 주와 지난주를 매일 다시 만든다. 그보다 앞선 주는 바뀌지 않는다.
  3. 패널(panel)을 주별 요약 전체로 다시 계산한다(2A-9와 같은 SQL, py/panel_sql.py). panel_latest는 가장 최근 완결 주.
  4. 데이터 품질 점검(3A-7): 시간대별 대여가 지난주 같은 시간의 30% 미만, 작업 실패, 26시간 넘게 안 돈 작업.
  5. bike_card(시민 조회용, 런북 08)와 reported_recent를 만들어 스냅샷으로 발행한다.
  6. actuals.refresh()로 weekly_actual, daily_reports, forecast_track을 갱신해 발행한다.

주의: init_ops.py로 과거 주별 요약을 넣어 둬야 '마지막 신고 이후 누적'이 끊기지 않는다.
반기 파일(~2026-06-30)과 API 수집 시작(2026-10-04) 사이 석 달은 따로 채워야 한다. tbCycleRentData는 과거 날짜도
조회되므로 backfill_rent.py(또는 collect_rent.py YYYY-MM-DD)로 받는다. 채우기 전에는 그 기간 누적이 적게 잡힌다.

출력: ops.duckdb trips_live_x, bw_usage, bw_fail, panel, panel_latest, bike_card, reported_recent, quality_log; serving/*.parquet
실행: .venv/bin/python py/jobs/daily.py
      .venv/bin/python py/jobs/daily.py --rebuild-from 2026-06-29   # 백필 뒤 한 번, 그 주부터 주별 요약을 API 자료로 다시 만든다
"""
import datetime as dt
import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, alert, connect, job_lock, log_run, table_exists   # noqa: E402
from panel_sql import BW_FAIL_SELECT, BW_USAGE_SELECT, PANEL0_SQL, PANEL_SQL, REPAIR_H, cl_cols_expr, sh_cl_expr   # noqa: E402
from publish import build_status, publish   # noqa: E402
import actuals                               # noqa: E402

DETOUR = 1.18          # 우회율 중앙값(2A-4). 이동거리가 잘못 찍힌 행을 직선거리로 채울 때 곱한다
LIVE_DAYS = 21
REBUILD_FROM = None    # --rebuild-from YYYY-MM-DD(월요일)로 주면 그 주부터 주별 요약을 다시 만든다


def build_trips_live_x(con, since=None):
    hol = (ROOT / "data/raw/calendar/holidays_*.csv").as_posix()      # 연도별 파일 전부(2016 ~ 2026 + py/build/holidays.py로 받은 해)
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
      FROM trips_live WHERE bike_id IS NOT NULL AND rent_dt >= {f"TIMESTAMP '{since}'" if since else f'now() - INTERVAL {LIVE_DAYS} DAY'}),
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


def refresh_weeks(con, today, cut=None):
    if cut is None:
        # 이번 주와 지난주를 매일 다시 만든다. 월요일에만 지난주를 만들면 그날 실행을 놓쳤을 때 일요일 대여가 빠지고,
        # 하루 늦게 들어온 신고와 '신고 뒤 48시간 대여 없음(수리 추정)' 판정이 일요일 저녁 신고에서 몇 시간 만에 굳어 버린다
        cut = today - dt.timedelta(days=today.weekday() + 7)          # 지난주 월요일
    con.execute("DELETE FROM bw_usage WHERE wk >= ?", [cut])
    con.execute("INSERT INTO bw_usage BY NAME " + BW_USAGE_SELECT.format(sh_cl=sh_cl_expr(), src="trips_live_x", cluster="station_cluster",
                                                                        extra_where=f"AND rent_dt >= DATE '{cut}'"))
    if table_exists(con, "raw_fail"):
        con.execute("""CREATE OR REPLACE TEMP TABLE fail_gap_live AS
        SELECT f.bike_id, f.reg_ts, f.kind, n.rent_dt AS next_rent_dt, date_diff('hour', f.reg_ts, n.rent_dt) AS gap_h
        FROM fail_live f ASOF LEFT JOIN (SELECT bike_id, rent_dt FROM trips_live) n ON f.bike_id = n.bike_id AND f.reg_ts <= n.rent_dt""")
        con.execute("DELETE FROM bw_fail WHERE wk >= ?", [cut])
        con.execute("INSERT INTO bw_fail BY NAME " + BW_FAIL_SELECT.format(src="fail_gap_live", extra_where=f"WHERE reg_ts >= DATE '{cut}'", repair_h=REPAIR_H))
    return cut


def add_new_bikes(con):
    """API에서 처음 보이는 자전거(분석 DB 이후 도입)를 bike_info에 넣는다.
    2022-03 이후 번호라서 처음 나타난 날을 도입일로 봐도 된다."""
    n0 = con.execute("SELECT count(*) FROM bike_info").fetchone()[0]
    con.execute("""INSERT INTO bike_info BY NAME
        SELECT bike_id, mode(bike_type) AS bike_type, min(rent_dt) AS first_dt, 0.0 AS km_pre, 0 AS trips_pre, TRUE AS age_known
        FROM trips_live WHERE bike_id IS NOT NULL AND bike_id NOT IN (SELECT bike_id FROM bike_info) GROUP BY bike_id""")
    return con.execute("SELECT count(*) FROM bike_info").fetchone()[0] - n0


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
    # 수집은 26시간, 매주 작업(위험 점수·주간 예측)은 8일 넘게 성공이 없으면 경보. cron은 잠든 동안 놓친 실행을 따라잡지 않는다
    stale = con.sql("""SELECT job, max(ts) AS last_ok FROM job_runs WHERE status = 'ok' GROUP BY 1
                       HAVING (job IN ('collect_rent', 'collect_fail', 'collect_wx_obs') AND max(ts) < now() - INTERVAL 26 HOUR)
                           OR (job = 'weekly' AND max(ts) < now() - INTERVAL 8 DAY)""").df()
    if len(stale):
        issues.append(("안 도는 작업", ", ".join(f"{r.job}(마지막 {r.last_ok:%m-%d %H:%M})" for r in stale.itertuples())))
    for name, detail in issues:
        con.execute("INSERT INTO quality_log VALUES (now(), ?, ?)", [name, detail])
        alert(f"{name}: {detail}")
    return issues


REPORT_DAYS = 7           # 이 기간 안에 신고된 자전거는 점검 권장 목록에서 빼고 '최근 신고' 목록으로 따로 보여 준다


def build_reported_recent(con):
    """최근 REPORT_DAYS일 안에 고장 신고된 자전거 목록을 만든다.

    열: 마지막 신고 시각, 신고 부품, 신고 건수, 신고 뒤 대여 횟수·첫 대여 시각, 신고 당시 자리(st_at_report),
        신고 뒤 첫 대여가 시작된 대여소(first_rent_st), 둘이 같은지(same_st_after), 마지막 반납 대여소·시각.
    점검 대상 화면의 '최근 고장 신고된 자전거' 목록과, 점검 권장 목록에서 뺄 자전거를 가리는 데 쓴다.

    신고가 곧바로 대여를 막지는 않는다(2025-07 ~ 11 신고의 30%가 24시간 안에 다시 대여, 대부분 그 자리에서. docs/notes/07 R13·R18).
    자전거는 빌리지 않으면 움직이지 않으므로, 신고 뒤 첫 대여가 신고 당시 자리에서 시작했으면 수거되지 않은 채 다시 운행된 것이고
    다시 신고될 위험도 두 배쯤 높다. 다른 곳에서 시작했으면 수거·수리 뒤 재배치로 본다. 신고 뒤 대여가 없으면 수리 대기로 본다.
    """
    station = "station_live" if table_exists(con, "station_live") else "station_base"
    con.execute(f"""
    CREATE OR REPLACE TABLE reported_recent AS
    WITH f AS (SELECT DISTINCT bike_id, reg_ts, kind FROM fail_live
               WHERE bike_id IS NOT NULL AND reg_ts >= now() - INTERVAL {REPORT_DAYS} DAY),
         b AS (SELECT bike_id, max(reg_ts) AS last_report_ts, count(DISTINCT reg_ts) AS n_reports,
                      string_agg(DISTINCT kind, ', ' ORDER BY kind) AS kinds
               FROM f GROUP BY 1),
         t AS (SELECT DISTINCT ON (bike_id, rent_dt, rent_st) bike_id, rent_dt, rtn_dt, rent_st, rtn_st FROM trips_live WHERE bike_id IS NOT NULL)
    SELECT b.*,
           (SELECT count(*) FROM t WHERE t.bike_id = b.bike_id AND t.rent_dt > b.last_report_ts) AS rentals_after,
           (SELECT min(t.rent_dt) FROM t WHERE t.bike_id = b.bike_id AND t.rent_dt > b.last_report_ts) AS first_rent_after,
           (SELECT arg_max(t.rtn_st, t.rent_dt) FROM t WHERE t.bike_id = b.bike_id AND t.rent_dt <= b.last_report_ts) AS st_at_report,
           (SELECT arg_min(t.rent_st, t.rent_dt) FROM t WHERE t.bike_id = b.bike_id AND t.rent_dt > b.last_report_ts) AS first_rent_st,
           l.last_st, l.last_rtn_dt, s.st_name AS last_st_name, s.district
    FROM b
    LEFT JOIN (SELECT bike_id, arg_max(rtn_st, rtn_dt) AS last_st, max(rtn_dt) AS last_rtn_dt FROM t WHERE rtn_dt IS NOT NULL GROUP BY 1) l USING (bike_id)
    LEFT JOIN {station} s ON l.last_st = s.st_no""")
    con.execute("ALTER TABLE reported_recent ADD COLUMN same_st_after BOOLEAN")
    con.execute("UPDATE reported_recent SET same_st_after = first_rent_st = st_at_report WHERE first_rent_st IS NOT NULL")


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
            since = REBUILD_FROM
            build_trips_live_x(con, since)
            cut = refresh_weeks(con, today, dt.date.fromisoformat(since) if since else None)
            n_new = add_new_bikes(con)
            wk, n = rebuild_panel(con, today)
            issues = quality_checks(con)
            build_bike_card(con)
            build_reported_recent(con)
            build_status(con)
            for t in ("bike_card", "reported_recent", "data_status", "alerts_live"):
                if table_exists(con, t):
                    publish(con, t)
            log_run(con, "daily", "ok", f"주별 요약 {cut}~ 교체, 새 자전거 {n_new}, 패널 최신 주 {wk} {n}대, 품질 이슈 {len(issues)}")
            print(f"daily: 주별 요약 {cut}~ 교체, 새 자전거 {n_new}대, 패널 최신 완결 주 {wk} ({n:,}대), 품질 이슈 {len(issues)}건")
        except Exception as e:
            log_run(con, "daily", "fail", str(e)); alert(f"daily 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    if "--rebuild-from" in sys.argv:
        REBUILD_FROM = sys.argv[sys.argv.index("--rebuild-from") + 1]
    run()
    actuals.refresh()                       # 실적·예측 성적 표. 잠금은 안에서 따로 잡는다
    from publish import run as publish_run
    publish_run(["weekly_actual", "daily_reports", "forecast_track", "data_status"])
