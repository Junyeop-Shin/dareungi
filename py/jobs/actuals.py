"""일별·주별 실적과 예측 성적 스냅샷(08a 설계서 2절).

대시보드의 실적 그래프와 예측 성적표에 쓰는 표를 만든다.

  daily_district   날짜 × 자치구: 대여 건수, 신고 건수(고유 자전거 + 등록일시), 부품별 신고 행 수(타이어·체인·안장·페달).
                   2023-07-01 ~ 2026-06-30은 분석 DB의 trips·fail_loc에서 한 번 계산해 daily_district_hist로 두고,
                   그 뒤는 운영 DB의 trips_live·fail_live(API 수집분)에서 매일 다시 계산한다.
                   대여는 대여한 대여소의 자치구, 신고는 신고 직전 마지막 반납 대여소의 자치구로 센다(2A-8과 같은 규칙).
  weekly_actual    주(월요일) × 자치구 합계. 열: week, district, rentals, reports, 타이어, 체인, 안장, 페달
  daily_reports    날짜 × 자치구 × 부품 신고 건수(전체는 고유 신고, 부품은 행 수). 요일 × 주 히트맵용
  forecast_track   주 × 부품(서울 전체): 그 주 월요일 기준 예측, 실제, 나이브(지난주 그대로).
                   예측은 weekly.py와 같다(최근 4주 신고율 × 대여량 예측, py/fc_ops.py). pred_kma(예보판), pred_cal(달력판),
                   pred(보여 준 쪽)를 둔다. weekly.py가 발행한 주는 그 값을 그대로 쓰고, 그 전 주는
                     예보판: fc_kma_hist(1C 과거 예보 파일로 한 번 계산, `actuals.py track_hist`)
                     달력판: 그 월요일까지의 자료로 다시 계산
  fc_compare       운영 주(SWITCH_FROM 이후) 두 판의 최근 12주 MAE%. 달력판이 3%p 넘게 낮으면 경보(교체는 사람이 정한다)

주의: 같은 대여소에 1분 이하로 반납한 대여는 뺀다. 2026년 파일과 실시간 API에만 있는 기록이다(05 패널 규칙).

실행: .venv/bin/python py/jobs/actuals.py hist   # 과거분 한 번. 분석 DB를 다른 프로세스가 쓰지 않을 때
      .venv/bin/python py/jobs/actuals.py track_hist   # 과거 주 예보판 한 번(data/interim/weather_forecast/kma_by_origin.parquet 필요)
      .venv/bin/python py/jobs/actuals.py        # 운영분 갱신과 스냅샷 표 생성. daily.py가 매일 부른다
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
import fc_ops   # noqa: E402
from common import ROOT, alert, connect, job_lock, log_run, table_exists   # noqa: E402

KINDS = {"타이어": "n_tire", "체인": "n_chain", "안장": "n_saddle", "페달": "n_pedal", "단말기": "n_terminal", "기타": "n_etc"}      # 고장신고 API의 유형 6가지 전부
HIST_END = "2026-06-30"
SWITCH_FROM = "2026-10-12"    # 두 판을 운영에서 비교하기 시작한 주. 교체 조건: 최근 12주 전체 MAE%가 달력판이 3%p 넘게 낮음
SWITCH_WEEKS, SWITCH_GAP = 12, 3.0
PART_SUM = ", ".join(f"sum(CASE WHEN kind = '{k}' THEN 1 ELSE 0 END) AS {v}" for k, v in KINDS.items())


def _daily_sql(trips, fail, station, start, end):
    """대여·신고를 날짜 × 자치구로 묶는 SQL. fail에는 last_st(직전 반납 대여소) 열이 있어야 한다."""
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
    """분석 DB에서 2023-07-01 ~ 2026-06-30 daily_district_hist를 만들어 운영 DB에 넣는다."""
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


def _mondays(d, weeks):
    return [m for m in d.index if m.weekday() == 0 and m - pd.DateOffset(days=35) >= d.index[0]][-weeks:]


def track_hist():
    """과거 주 예보판 예측을 1C 과거 예보 파일(원점 ~ 2026-10-07)로 계산해 fc_kma_hist에 둔다. 한 번만 돌린다."""
    kma = pd.read_parquet(ROOT / "data/interim/weather_forecast/kma_by_origin.parquet")
    with job_lock():
        con = connect()
        try:
            d = fc_ops.daily_from_district(con.execute("SELECT * FROM daily_district").df())
            models, spec = fc_ops.load_models()
            bad, ns = fc_ops.master_flags()
            rows = []
            for m in _mondays(d, 1000):
                wx = kma[kma.origin == m].sort_values("lead")
                if len(wx) != 7:
                    continue
                r = fc_ops.forecast(d, m, wx, models, spec, bad=bad, no_service=ns)
                rows += [{"week": m.date(), "part": fc_ops.ALL[p], "pred_kma": v} for p, v in r["kma"].items()]
            h = pd.DataFrame(rows)
            con.register("h_", h)
            con.execute("CREATE OR REPLACE TABLE fc_kma_hist AS SELECT * FROM h_")
            con.unregister("h_")
            log_run(con, "actuals_track_hist", "ok", f"{h.week.min()} ~ {h.week.max()} {h.week.nunique()}주")
            print(f"fc_kma_hist: {h.week.nunique()}주 ({h.week.min()} ~ {h.week.max()})")
        finally:
            con.close()


def forecast_track(con, daily, weeks=60):
    """주별 예측(예보판·달력판·보여 준 쪽)·실제·나이브. 각 주는 그 월요일 전 자료만 쓴다."""
    d = fc_ops.daily_from_district(daily)
    full = d.asfreq("D", fill_value=0)
    models, spec = fc_ops.load_models()
    bad, ns = fc_ops.master_flags()
    issued = con.execute("SELECT issue_date AS week, part, pred_kma, pred_cal, method FROM forecast_weekly").df() \
        if table_exists(con, "forecast_weekly") and "pred_cal" in con.execute("SELECT * FROM forecast_weekly LIMIT 0").df() else pd.DataFrame()
    issued = issued[issued.pred_cal.notna()] if len(issued) else issued
    hist = con.execute("SELECT * FROM fc_kma_hist").df() if table_exists(con, "fc_kma_hist") else pd.DataFrame(columns=["week", "part", "pred_kma"])
    rows = []
    for m in _mondays(d, weeks):
        nxt = full[(full.index >= m) & (full.index < m + pd.DateOffset(days=7))]
        last = full[(full.index >= m - pd.DateOffset(days=7)) & (full.index < m)]
        done = len(nxt) == 7 and nxt.index.max() <= d.index.max()
        cal = fc_ops.forecast(d, m, None, models, spec, bad=bad, no_service=ns)["cal"]
        for p, part in fc_ops.ALL.items():
            row = {"week": m.date(), "part": part, "pred_kma": None, "pred_cal": cal[p],
                   "naive": float(last[p].sum()), "actual": float(nxt[p].sum()) if done else None}
            i = issued[(pd.to_datetime(issued.week) == m) & (issued.part == part)] if len(issued) else issued
            if len(i):
                row.update(pred_kma=i.pred_kma.iloc[0], pred_cal=i.pred_cal.iloc[0])
            else:
                h = hist[(pd.to_datetime(hist.week) == m) & (hist.part == part)]
                if len(h):
                    row["pred_kma"] = float(h.pred_kma.iloc[0])
            rows.append(row)
    t = pd.DataFrame(rows)
    t["method"] = t.pred_kma.notna().map({True: "kma", False: "cal"})
    t["pred"] = t.pred_kma.fillna(t.pred_cal)
    return t


def compare(track):
    """운영 주의 두 판 성적(전체 신고 기준). 12주가 차고 달력판이 3%p 넘게 낮으면 경보"""
    t = track[(pd.to_datetime(track.week) >= SWITCH_FROM) & track.actual.notna() & track.pred_kma.notna() & track.pred_cal.notna()]
    rows = []
    for part, g in t.groupby("part"):
        g = g.sort_values("week").tail(SWITCH_WEEKS)
        rows.append({"part": part, "weeks": len(g), "mae_kma": (g.pred_kma - g.actual).abs().mean() / g.actual.mean() * 100,
                     "mae_cal": (g.pred_cal - g.actual).abs().mean() / g.actual.mean() * 100})
    c = pd.DataFrame(rows, columns=["part", "weeks", "mae_kma", "mae_cal"])
    tot = c[c.part == "전체"]
    if len(tot) and tot.weeks.iloc[0] >= SWITCH_WEEKS and tot.mae_kma.iloc[0] - tot.mae_cal.iloc[0] > SWITCH_GAP:
        alert(f"주간 예측 교체 조건 충족: 최근 {SWITCH_WEEKS}주 전체 MAE 예보판 {tot.mae_kma.iloc[0]:.1f}% · 달력판 {tot.mae_cal.iloc[0]:.1f}%")
    return c


RETRAIN_WEEKS = 12            # 재학습 검토 조건(사람 결정 2026-10-10): 최근 12주 전체 신고 오차가 기준값(지난주와 같다고 가정)보다 크면 알린다


def retrain_check(track):
    """대여량 모형을 다시 맞출 때가 됐는지 본다. 반환: (조건 충족 여부, 예측 오차 %, 기준값 오차 %, 본 주 수). 12주가 안 차면 (False, …)
    오차 = 주별 |예측 − 실제|의 평균 ÷ 실제의 평균(전체 신고). 예보판·달력판 교체 규칙(compare)과 같은 12주, 같은 정의를 쓴다"""
    t = track[(track.part == "전체") & track.actual.notna() & track.pred.notna()].sort_values("week").tail(RETRAIN_WEEKS)
    if len(t) < RETRAIN_WEEKS:
        return False, None, None, len(t)
    mp, mn = ((t[c] - t.actual).abs().mean() / t.actual.mean() * 100 for c in ("pred", "naive"))
    return bool(mp > mn), float(mp), float(mn), len(t)


def refresh():
    """운영분(daily_district_hist 다음 날 ~ 어제)을 다시 계산하고 스냅샷 표를 만든다."""
    with job_lock():
        con = connect()
        try:
            if not table_exists(con, "daily_district_hist"):
                raise RuntimeError("daily_district_hist 없음 — `actuals.py hist`를 먼저")
            start = (pd.Timestamp(con.execute("SELECT max(date) FROM daily_district_hist").fetchone()[0]) + pd.DateOffset(days=1)).date()
            end = (pd.Timestamp.now().normalize() - pd.DateOffset(days=1)).date()
            station = "station_live" if table_exists(con, "station_live") else "station_base"
            con.execute("""CREATE OR REPLACE TEMP VIEW trips_dedup AS
                SELECT DISTINCT ON (bike_id, rent_dt, rent_st) * FROM trips_live WHERE bike_id IS NOT NULL""")
            con.execute("""CREATE OR REPLACE TEMP TABLE fail_loc_live AS
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
            unp = " UNION ALL ".join(["SELECT date, district, '전체' AS part, reports FROM daily_district"] +
                                     [f"SELECT date, district, '{k}', {v} FROM daily_district" for k, v in KINDS.items()])
            con.execute(f"CREATE OR REPLACE TABLE daily_reports AS {unp}")
            daily = con.execute("SELECT * FROM daily_district").df()
            ft = forecast_track(con, daily)
            cmp_ = compare(ft)
            hit, mp, mn, nw = retrain_check(ft)
            if hit and dt.date.today().weekday() == 0:          # 새 주가 채워지는 월요일에만 알린다(매일 같은 알림이 반복되지 않게)
                alert(f"대여량 모형 재학습 검토: 최근 {nw}주 전체 신고 오차가 예측 {mp:.1f}% · 기준값 {mn:.1f}%로 기준값보다 크다(docs/pipeline.md 재학습 기준)")
            today = dt.date.today()
            if today.month == 1 and today.day <= 7 and today.weekday() == 0:          # 조건에 안 걸려도 해마다 1월 첫 월요일에 한 번(사람 결정 2026-10-10)
                alert(f"대여량 모형 정기 재학습 시기({today.year}년 1월): 새해 공휴일 표를 넣고 학습 끝을 지난해 말까지 늘려 다시 맞춘다(docs/pipeline.md 재학습 기준)")
            for name, df in (("forecast_track", ft), ("fc_compare", cmp_)):
                con.register("x_", df)
                con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM x_")
                con.unregister("x_")
            # 대여가 너무 적거나 신고가 0인 날은 수집 공백일 가능성이 크다
            gaps = con.sql("""SELECT date, sum(rentals) r, sum(reports) f FROM daily_district_live GROUP BY 1
                               HAVING sum(rentals) < 1000 OR sum(reports) = 0 ORDER BY 1""").df()
            log_run(con, "actuals", "ok", f"live {start}~{end}, 공백 의심 {len(gaps)}일")
            print(f"actuals: 운영분 {start} ~ {end}, weekly_actual {con.execute('SELECT count(DISTINCT week) FROM weekly_actual').fetchone()[0]}주, "
                  f"forecast_track {ft.week.nunique()}주, 공백 의심 {len(gaps)}일")
            if len(gaps):
                print(gaps.to_string(index=False))
        except Exception as e:          # daily.py가 끝난 뒤 따로 불리므로, 여기서 실패하면 daily는 '성공'으로 남는다. 직접 기록하고 알린다
            log_run(con, "actuals", "fail", str(e)); alert(f"actuals 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hist":
        build_hist()
    elif len(sys.argv) > 1 and sys.argv[1] == "track_hist":
        track_hist()
    else:
        refresh()
