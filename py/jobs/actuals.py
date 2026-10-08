"""주별·일별 실적과 예측 실적 — 08a 설계서 2절의 추가 스냅샷(사람 결정 2026-10-08: 추가)

[무엇을]
  daily_district   날짜 × 자치구: 대여 건수, 신고 건수(고유 자전거 + 등록일시), 부품별 신고 행 수(타이어·체인·안장·페달)
                   - 과거(2023-07-01 ~ 2026-06-30): 분석 DB(data/db/dareungi.duckdb)의 trips·fail_loc에서 한 번 계산해 daily_district_hist로 보관
                   - 그 뒤: 운영 DB의 trips_live(API 대여이력)·fail_live(API 고장신고)에서 매일 다시 계산
                   - 대여의 자치구 = 대여 대여소의 자치구. 신고의 자치구 = 신고 직전 마지막 반납 대여소의 자치구(2A-8과 같은 규칙)
                   - '같은 대여소 1분 이하' 대여는 뺀다(2026년 파일·실시간 API에만 있는 기록, 05 패널 규칙)
  weekly_actual    주(월요일) × 자치구: 위 표의 주 합계. 열: week, district, rentals, reports, 타이어, 체인, 안장, 페달
  daily_reports    날짜 × 자치구 × 부품: 신고 건수(부품 = 전체는 고유 신고, 나머지는 행 수). 요일×주 히트맵용
  forecast_track   주 × 부품(서울 전체): 그 주를 시작하는 월요일에 냈을(냈던) 예측, 실제, 나이브(지난주 그대로)
                   예측 규칙 = 구조 나이브(최근 4주 신고율 × 지난주 대여량, weekly.py와 같음). 과거 주는 **그때까지의 자료만으로 같은 규칙을 다시 계산**한 값이다.
[실행] .venv/bin/python py/jobs/actuals.py hist   # 과거분 한 번(분석 DB를 다른 프로세스가 쓰지 않을 때)
       .venv/bin/python py/jobs/actuals.py        # 운영분 갱신 + 세 스냅샷 표(daily.py가 매일 호출)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, connect, job_lock, log_run, table_exists   # noqa: E402

KINDS = {"타이어": "n_tire", "체인": "n_chain", "안장": "n_saddle", "페달": "n_pedal"}
HIST_END = "2026-06-30"
PART_SUM = ", ".join(f"sum(CASE WHEN kind = '{k}' THEN 1 ELSE 0 END) AS {v}" for k, v in KINDS.items())


def _daily_sql(trips, fail, station, start, end):
    """대여·신고를 날짜 × 자치구로. fail에는 last_st(직전 반납 대여소)가 있어야 한다"""
    return f"""
    WITH r AS (
      SELECT CAST(t.rent_dt AS DATE) AS date, coalesce(s.district, '(위치 미상)') AS district, count(*) AS rentals
      FROM {trips} t LEFT JOIN {station} s ON t.rent_st = s.st_no
      WHERE t.rent_dt >= TIMESTAMP '{start}' AND t.rent_dt < TIMESTAMP '{end}' + INTERVAL 1 DAY
        AND NOT (t.rent_st = t.rtn_st AND t.use_min <= 1)
      GROUP BY 1, 2),
    f AS (
      SELECT CAST(f.reg_ts AS DATE) AS date, coalesce(s.district, '(위치 미상)') AS district,
             count(DISTINCT (f.bike_id, f.reg_ts)) AS reports, {PART_SUM}
      FROM {fail} f LEFT JOIN {station} s ON f.last_st = s.st_no
      WHERE f.reg_ts >= TIMESTAMP '{start}' AND f.reg_ts < TIMESTAMP '{end}' + INTERVAL 1 DAY
      GROUP BY 1, 2)
    SELECT coalesce(r.date, f.date) AS date, coalesce(r.district, f.district) AS district,
           coalesce(r.rentals, 0) AS rentals, coalesce(f.reports, 0) AS reports,
           {", ".join(f"coalesce(f.{v}, 0) AS {v}" for v in KINDS.values())}
    FROM r FULL OUTER JOIN f ON r.date = f.date AND r.district = f.district"""


def build_hist():
    """분석 DB에서 2023-07-01 ~ 2026-06-30 daily_district_hist를 만들어 운영 DB에 넣는다"""
    with job_lock():
        con = connect()
        try:
            con.execute(f"ATTACH '{(ROOT / 'data/db/dareungi.duckdb').as_posix()}' AS ana (READ_ONLY)")
            con.execute("CREATE OR REPLACE TABLE daily_district_hist AS " +
                        _daily_sql("ana.trips", "ana.fail_loc", "ana.station", "2023-07-01", HIST_END))
            con.execute("DETACH ana")
            n, d0, d1 = con.execute("SELECT count(*), min(date), max(date) FROM daily_district_hist").fetchone()
            log_run(con, "actuals_hist", "ok", f"{d0}~{d1} {n}행")
            print(f"daily_district_hist: {n:,}행 ({d0} ~ {d1})")
        finally:
            con.close()


def forecast_track(daily, weeks=60):
    """서울 전체 일별 표로 주별 구조 나이브 예측·실제·나이브를 다시 계산한다(그 월요일까지의 자료만 사용)"""
    d = daily.groupby("date")[["rentals", "reports"] + list(KINDS.values())].sum().sort_index()
    d.index = pd.to_datetime(d.index)
    d = d.asfreq("D", fill_value=0)
    mondays = [m for m in d.index if m.weekday() == 0 and m - pd.DateOffset(days=35) >= d.index[0]]
    rows = []
    for m in mondays[-weeks:]:
        lvl = d[(d.index >= m - pd.DateOffset(days=35)) & (d.index < m - pd.DateOffset(days=7))]
        last = d[(d.index >= m - pd.DateOffset(days=7)) & (d.index < m)]
        nxt = d[(d.index >= m) & (d.index < m + pd.DateOffset(days=7))]
        full = len(nxt) == 7 and nxt.index.max() <= d.index.max()
        for part, col in [("전체", "reports")] + list(KINDS.items()):
            rate = lvl[col].sum() / lvl.rentals.sum() if lvl.rentals.sum() else float("nan")
            rows.append({"week": m.date(), "part": part, "pred": rate * last.rentals.sum(), "naive": float(last[col].sum()),
                         "actual": float(nxt[col].sum()) if full else None})
    return pd.DataFrame(rows)


def refresh():
    """운영분(daily_district_hist 다음 날 ~ 어제)을 다시 계산하고 세 스냅샷 표를 만든다"""
    with job_lock():
        con = connect()
        try:
            if not table_exists(con, "daily_district_hist"):
                raise RuntimeError("daily_district_hist 없음 — `actuals.py hist`를 먼저")
            start = (pd.Timestamp(con.execute("SELECT max(date) FROM daily_district_hist").fetchone()[0]) + pd.DateOffset(days=1)).date()
            end = (pd.Timestamp.now().normalize() - pd.DateOffset(days=1)).date()
            station = "station_live" if table_exists(con, "station_live") else "station_base"
            con.execute(f"""CREATE OR REPLACE TEMP VIEW trips_dedup AS
                SELECT DISTINCT ON (bike_id, rent_dt, rent_st) * FROM trips_live WHERE bike_id IS NOT NULL""")
            con.execute(f"""CREATE OR REPLACE TEMP TABLE fail_loc_live AS
                SELECT f.bike_id, f.reg_ts, f.kind, t.rtn_st AS last_st
                FROM (SELECT DISTINCT bike_id, reg_ts, kind FROM fail_live WHERE bike_id IS NOT NULL) f
                ASOF LEFT JOIN (SELECT bike_id, rtn_st, rtn_dt FROM trips_dedup WHERE rtn_dt IS NOT NULL) t
                  ON f.bike_id = t.bike_id AND f.reg_ts >= t.rtn_dt""")
            con.execute("CREATE OR REPLACE TABLE daily_district_live AS " + _daily_sql("trips_dedup", "fail_loc_live", station, start, end))
            con.execute("CREATE OR REPLACE TABLE daily_district AS SELECT * FROM daily_district_hist UNION ALL BY NAME SELECT * FROM daily_district_live")
            ren = ", ".join(f"sum({v}) AS {k}" for k, v in KINDS.items())
            con.execute(f"""CREATE OR REPLACE TABLE weekly_actual AS
                SELECT CAST(date_trunc('week', date) AS DATE) AS week, district, sum(rentals) AS rentals, sum(reports) AS reports, {ren}
                FROM daily_district GROUP BY 1, 2""")
            unp = " UNION ALL ".join([f"SELECT date, district, '전체' AS part, reports FROM daily_district"] +
                                     [f"SELECT date, district, '{k}', {v} FROM daily_district" for k, v in KINDS.items()])
            con.execute(f"CREATE OR REPLACE TABLE daily_reports AS {unp}")
            daily = con.execute("SELECT * FROM daily_district").df()
            ft = forecast_track(daily)
            con.register("ft_", ft)
            con.execute("CREATE OR REPLACE TABLE forecast_track AS SELECT * FROM ft_")
            con.unregister("ft_")
            # 운영 구간에서 대여·신고가 비어 있는 날(수집 공백) 점검
            gaps = con.sql(f"""SELECT date, sum(rentals) r, sum(reports) f FROM daily_district_live GROUP BY 1
                               HAVING sum(rentals) < 1000 OR sum(reports) = 0 ORDER BY 1""").df()
            log_run(con, "actuals", "ok", f"live {start}~{end}, 공백 의심 {len(gaps)}일")
            print(f"actuals: 운영분 {start} ~ {end}, weekly_actual {con.execute('SELECT count(DISTINCT week) FROM weekly_actual').fetchone()[0]}주, "
                  f"forecast_track {ft.week.nunique()}주, 공백 의심 {len(gaps)}일")
            if len(gaps):
                print(gaps.to_string(index=False))
        finally:
            con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hist":
        build_hist()
    else:
        refresh()
