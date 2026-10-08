"""매주 갱신 — 단계 3A-5 (런북 07). 매주 월요일 07:30 (daily.py 뒤)

[무엇을]
  1. score_risk: 가장 최근 **완결된 주**의 패널(panel_latest)에 고정된 위험 모형(output/models/risk_spec.json, risk_*_final.txt)을 적용한다.
       위험률(1,000km당, 보정 배율 적용), 기대 신고 수(위험률 × 최근 4주 평균 km), 그 주 백분위와 등급(점검 권장 95% / 주의 80% / 보통 50%),
       위험을 '높인' 변수 3개의 이유 문구(SHAP), 마지막 반납 대여소·자치구. 체크포인트 H(2B): 공단 화면용. 시민 화면은 등급·이유만.
       순위 규칙(risk_spec): 최근 4주 주행이 0인 자전거는 위험률 순위에서 제외(등급 '산정 제외').
  2. forecast_weekly: 부품별 다음 7일 신고 예측 = 구조 나이브(최근 4주 신고율 × 지난주 대여량, 1C 테스트에서 가장 안정적이었던 것).
       원천은 actuals.py의 daily_district(2023-07 ~ 어제, 파일 + API). 80% 구간은 forecast_track의 과거 오차 분포로 보정.
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
    latest["reasons"] = [" / ".join([x for x in (phrase(f, v) for f, c, v in sorted(zip(names, row, vrow), key=lambda t: -t[1]) if c > 0 and f in phr) if x][:3])
                         for row, vrow in zip(contrib, vals)]                    # 문구가 None인 방향은 건너뛴다
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
    SELECT o.*, l.last_st, l.last_rtn_dt, s.st_name AS last_st_name, s.district
    FROM out_ o
    LEFT JOIN (SELECT bike_id, arg_max(rtn_st, rtn_dt) AS last_st, max(rtn_dt) AS last_rtn_dt FROM trips_live WHERE rtn_dt IS NOT NULL GROUP BY 1) l USING (bike_id)
    LEFT JOIN {station} s ON l.last_st = s.st_no""")
    con.unregister("out_")
    g = con.sql("SELECT grade, count(*) n FROM bike_risk_latest GROUP BY 1 ORDER BY 2 DESC").df()
    return latest.wk.iloc[0].date(), len(out), g


def forecast_weekly(con, today):
    """구조 나이브: 부품별 최근 4주 신고율 × 지난주 대여량(서울 전체). 원천은 daily_district(actuals.py, 2023-07 ~ 어제).
    80% 구간은 forecast_track(같은 규칙으로 다시 계산한 과거 52주)의 상대오차 10% · 90% 분위로 보정한다(1C의 구간 보정과 같은 방식)."""
    con.execute("CREATE TABLE IF NOT EXISTS forecast_weekly (issue_date DATE, part VARCHAR, method VARCHAR, pred DOUBLE, lo80 DOUBLE, hi80 DOUBLE, note VARCHAR)")
    con.execute("DELETE FROM forecast_weekly WHERE issue_date = ?", [today])
    if not table_exists(con, "daily_district"):
        for part in PARTS.values():
            con.execute("INSERT INTO forecast_weekly VALUES (?, ?, 'none', NULL, NULL, NULL, '일별 실적(daily_district) 없음 — actuals.py 먼저')", [today, part])
        return 0, False
    d = con.execute("SELECT date, sum(rentals) AS rentals, sum(reports) AS reports, sum(n_tire) AS n_tire, sum(n_chain) AS n_chain, "
                    "sum(n_saddle) AS n_saddle, sum(n_pedal) AS n_pedal FROM daily_district GROUP BY 1 ORDER BY 1").df()
    d["date"] = pd.to_datetime(d.date)
    d = d.set_index("date").asfreq("D", fill_value=0)
    t0 = pd.Timestamp(today)
    lvl = d[(d.index >= t0 - pd.DateOffset(days=35)) & (d.index < t0 - pd.DateOffset(days=7))]
    last = d[(d.index >= t0 - pd.DateOffset(days=7)) & (d.index < t0)]
    tr = con.execute("SELECT * FROM forecast_track WHERE actual IS NOT NULL").df() if table_exists(con, "forecast_track") else pd.DataFrame()
    cols = {"전체": "reports", "타이어": "n_tire", "체인": "n_chain", "안장": "n_saddle", "페달": "n_pedal"}
    for part, col in cols.items():
        rate = lvl[col].sum() / lvl.rentals.sum() if lvl.rentals.sum() else 0
        pred = rate * last.rentals.sum()
        t = tr[tr.part == part].sort_values("week").tail(52) if len(tr) else tr
        if len(t) >= 12:
            rel = (t.actual - t.pred) / t.pred
            lo, hi = float(rel.quantile(0.1)), float(rel.quantile(0.9))
            note = f"구간 = 과거 {len(t)}주 상대오차 10 ~ 90% ({lo:+.0%} ~ {hi:+.0%})"
        else:
            lo, hi, note = -0.2, 0.25, "구간 = 기본값(과거 실적 12주 미만)"
        con.execute("INSERT INTO forecast_weekly VALUES (?, ?, 'S1 구조 나이브', ?, ?, ?, ?)",
                    [today, part, pred, max(pred * (1 + lo), 0), pred * (1 + hi), "최근 4주 신고율 × 지난주 대여량. " + note])
    return len(d), True


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
            for t in ("bike_risk_latest", "forecast_weekly", "forecast_track", "data_status"):
                publish(con, t)
            log_run(con, "weekly", "ok", f"점수 주 {wk} {n}대, 예측 {'산출' if done else '대기'}")
            print(f"weekly: 위험 점수 {wk} 주, {n:,}대"); print(grades.to_string(index=False)); print(f"주간 예측: {'산출' if done else '대기'}"); print(con.sql("SELECT part, round(pred) pred, round(lo80) lo, round(hi80) hi, note FROM forecast_weekly WHERE issue_date = (SELECT max(issue_date) FROM forecast_weekly)").df().to_string(index=False))
        except Exception as e:
            log_run(con, "weekly", "fail", str(e)); alert(f"weekly 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
