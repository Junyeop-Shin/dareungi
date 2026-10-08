"""매주 갱신 — 단계 3A-5 (런북 07). 매주 월요일 07:30 (daily.py 뒤)

[무엇을]
  1. score_risk: 가장 최근 **완결된 주**의 패널(panel_latest)에 고정된 위험 모형(output/models/risk_spec.json, risk_*_final.txt)을 적용한다.
       위험률(1,000km당, 보정 배율 적용), 기대 신고 수(위험률 × 최근 4주 평균 km), 그 주 백분위와 등급(점검 권장 95% / 주의 80% / 보통 50%),
       위험을 '높인' 변수 3개의 이유 문구(SHAP), 마지막 반납 대여소·자치구. 체크포인트 H(2B): 공단 화면용. 시민 화면은 등급·이유만.
       순위 규칙(risk_spec): 최근 4주 주행이 0인 자전거는 위험률 순위에서 제외(등급 '산정 제외').
  2. forecast_weekly: 부품별 다음 7일 신고 예측. **API로 모은 일별 자료가 35일 이상 쌓인 뒤에만** 돈다(1C 모형의 '최근 4주 신고율' 입력 때문).
       그 전에는 '데이터 부족'으로 기록하고 빈 표를 둔다. 돌 때는 구조 나이브(최근 4주 신고율 × 지난주 대여량)로 시작한다(1C 테스트에서 가장 안정적이었던 것).
       1C 선택 모형(음이항)은 기상청 중기예보 API 승인 뒤 붙인다(단기예보만으로는 3 ~ 6일 뒤 날씨가 없다).
  3. 스냅샷 발행.
[출력] ops.duckdb: bike_risk_latest, forecast_weekly; serving/bike_risk_latest.parquet, forecast_weekly.parquet
[실행] .venv/bin/python py/jobs/weekly.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, alert, connect, job_lock, log_run, table_exists   # noqa: E402
from publish import build_status, publish   # noqa: E402
from risk_lgb import prep   # noqa: E402

PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}
KIND = {"타이어": "fail_tire", "체인": "fail_chain", "안장": "fail_saddle", "페달": "fail_pedal"}


def score_risk(con):
    spec = json.load(open(ROOT / "output/models/risk_spec.json", encoding="utf-8"))
    latest = con.execute("SELECT * FROM panel_latest").df()
    latest["wk"] = pd.to_datetime(latest.wk)
    latest = prep(latest)
    latest["bike_type"] = pd.Categorical(latest.bike_type.astype(str), categories=["새싹자전거", "일반자전거"])   # 학습 때와 같은 범주
    X = latest[spec["features"]]
    m = spec["models"]["all"]
    booster = lgb.Booster(model_file=str(ROOT / "output/models" / m["file"]))
    raw = booster.predict(X, raw_score=True)
    latest["rate_1000km"] = np.exp(raw) * 1000 * m["calibration_scale"]
    latest["expected_reports"] = np.exp(raw) * m["calibration_scale"] * latest.km_4w / 4
    ranked = latest.km_4w > 0
    latest["pct"] = np.where(ranked, latest.rate_1000km.where(ranked).rank(pct=True), np.nan)
    cut = spec["grade_by_weekly_percentile"]
    latest["grade"] = np.select([~ranked, latest.pct >= cut["점검 권장"], latest.pct >= cut["주의"], latest.pct >= cut["보통"]],
                                ["산정 제외", "점검 권장", "주의", "보통"], default="낮음")
    contrib = booster.predict(X, pred_contrib=True)[:, :-1]
    names = np.array(spec["features"]); phr = spec["reason_phrases"]
    med = {f: float(np.nanmedian(latest[f].astype(float))) for f in names if f in phr}          # 그 주 중앙값: 문구 방향을 정한다
    def phrase(f, val):
        p = phr[f]
        if isinstance(p, str):
            return p
        return p["high"] if (val is not None and not np.isnan(val) and val >= med[f]) else p["low"]
    vals = latest[list(names)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)     # 범주형·결측은 NaN → low 문구
    latest["reasons"] = [" / ".join([phrase(f, v) for f, c, v in sorted(zip(names, row, vrow), key=lambda t: -t[1]) if c > 0 and f in phr][:3])
                         for row, vrow in zip(contrib, vals)]
    for part in ("tire", "chain", "saddle", "pedal"):
        mp = spec["models"][part]
        b = lgb.Booster(model_file=str(ROOT / "output/models" / mp["file"]))
        latest[f"rate_{part}"] = np.exp(b.predict(X, raw_score=True)) * 1000 * mp["calibration_scale"]
    out = latest[["bike_id", "wk", "rate_1000km", "expected_reports", "pct", "grade", "reasons", "rate_tire", "rate_chain", "rate_saddle", "rate_pedal",
                  "km_4w", "km_since_rep", "rep_13w", "sham_2w", "bike_type"]].copy()
    out["model_version"] = spec["version"]
    con.register("out_", out)
    station = "station_live" if table_exists(con, "station_live") else "station_base"
    con.execute(f"""
    CREATE OR REPLACE TABLE bike_risk_latest AS
    SELECT o.*, l.last_st, s.st_name AS last_st_name, s.district
    FROM out_ o
    LEFT JOIN (SELECT bike_id, arg_max(rtn_st, rtn_dt) AS last_st FROM trips_live WHERE rtn_dt IS NOT NULL GROUP BY 1) l USING (bike_id)
    LEFT JOIN {station} s ON l.last_st = s.st_no""")
    con.unregister("out_")
    g = con.sql("SELECT grade, count(*) n FROM bike_risk_latest GROUP BY 1 ORDER BY 2 DESC").df()
    return latest.wk.iloc[0].date(), len(out), g


def forecast_weekly(con, today):
    """구조 나이브: 부품별 최근 4주 신고율 × 지난주 대여량. 일별 자료 35일 미만이면 건너뛴다"""
    con.execute("CREATE TABLE IF NOT EXISTS forecast_weekly (issue_date DATE, part VARCHAR, method VARCHAR, pred DOUBLE, lo80 DOUBLE, hi80 DOUBLE, note VARCHAR)")
    days = con.execute("SELECT count(DISTINCT CAST(q_hour AS DATE)) FROM rent_hours_done WHERE n > 0").fetchone()[0]
    con.execute("DELETE FROM forecast_weekly WHERE issue_date = ?", [today])
    if days < 35:
        for part in PARTS.values():
            con.execute("INSERT INTO forecast_weekly VALUES (?, ?, 'none', NULL, NULL, NULL, ?)", [today, part, f"API 일별 자료 {days}일 — 35일 이상 쌓이면 산출"])
        return days, False
    d = con.sql("""
    WITH r AS (SELECT CAST(q_hour AS DATE) AS d, sum(n) AS rentals FROM rent_hours_done GROUP BY 1),
         f AS (SELECT CAST(reg_ts AS DATE) AS d, kind, count(*) AS n FROM fail_live GROUP BY 1, 2)
    SELECT r.d, r.rentals, f.kind, coalesce(f.n, 0) AS n FROM r LEFT JOIN f USING (d) WHERE r.d < current_date ORDER BY 1""").df()
    last7 = d[d.d >= today - dt.timedelta(days=7)].groupby("d").rentals.first().sum()
    lvl = d[(d.d >= today - dt.timedelta(days=35)) & (d.d < today - dt.timedelta(days=7))]
    expo = lvl.groupby("d").rentals.first().sum()
    for kind, part in list(KIND.items()) + [(None, "total_faults")]:
        n = lvl.n.sum() if kind is None else lvl[lvl.kind == kind].n.sum()
        rate = n / expo if expo else 0
        pred = rate * last7
        con.execute("INSERT INTO forecast_weekly VALUES (?, ?, 'S1 구조 나이브', ?, ?, ?, ?)",
                    [today, PARTS[part], pred, pred * 0.8, pred * 1.25, "최근 4주 신고율 × 지난주 대여량(1C 테스트 기준 구간 ±20 ~ 25%)"])
    return days, True


def run():
    today = dt.date.today()
    with job_lock():
        con = connect()
        try:
            if not table_exists(con, "panel_latest"):
                raise RuntimeError("panel_latest 없음 — daily.py 먼저")
            wk, n, grades = score_risk(con)
            days, done = forecast_weekly(con, today)
            build_status(con)
            for t in ("bike_risk_latest", "forecast_weekly", "data_status"):
                publish(con, t)
            log_run(con, "weekly", "ok", f"점수 주 {wk} {n}대, 예측 {'산출' if done else f'대기({days}일)'}")
            print(f"weekly: 위험 점수 {wk} 주, {n:,}대"); print(grades.to_string(index=False)); print(f"주간 예측: {'산출' if done else f'데이터 부족({days}일)'}")
        except Exception as e:
            log_run(con, "weekly", "fail", str(e)); alert(f"weekly 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
