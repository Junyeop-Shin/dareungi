"""매주 갱신(런북 07, 3A-5). 매주 월요일 07:30, daily.py 뒤에 돈다.

  1. score_risk: 가장 최근 완결 주의 패널(panel_latest)에 고정해 둔 위험 모형(output/models/risk_spec.json,
     risk_*_final.txt)을 적용한다. 자전거마다 1,000km당 위험률(보정 배율 적용), 기대 신고 수(위험률 × 최근 4주 평균 km),
     다음 주 신고 확률(1 − e^−기대 신고 수)과 그 주 평균 대비 배수, 확률 기준 백분위와 등급(점검 권장 95% / 주의 80% / 보통 50%),
     다음 신고까지 예상 km·주, 위험을 높인 변수 3개의 이유 문구(SHAP. 1km당 위험을 설명하고, 주행량은 화면에 따로 보여 준다),
     마지막 반납 대여소·자치구를 붙인다. 관리자 앱(점검 대상·자전거 조회)이 쓴다.
     2026-10-23부터 화면의 등급·순위·이유(grade, risk_ratio, reasons)는 '신고 없이 손볼 자전거' 모형(정답 = 다음 주 실제 수리,
     output/models/repair_spec.json, repair_final.txt)으로 매긴다(결정 로그 2026-10-23). 대상은 그 주에 대여됐고 최근 14일 안에
     신고가 없던 자전거이고(risk_track.repair_grade), 화면 이름은 '정비 필요 가능성(평균 대비)'. 이유 문구는 repair_reasons.json.
     위 위험 모형(신고로 학습)은 화면에서 내렸지만 계속 점수를 내 report_ratio·report_grade·report_reasons로 남기고,
     자전거 조회 화면이 '다음 주 고장 신고 가능성'으로 함께 보여 주며 risk_track이 매주 신고로 채점한다.
  2. forecast_weekly: 부품별 이번 주(월 ~ 일) 신고 = 최근 4주 신고율 × 대여량 예측(py/fc_ops.py). 월요일이 아닌 날
     돌려도(따라잡기) 원점은 그 주 월요일이고, 월요일 아침까지의 자료·예보만 쓴다.
     대여량은 기상청 예보로 예측한 값(예보판, 1C의 S1_B)을 보여 주고, 예보가 하루라도 비면 달력만으로 예측한 값
     (달력판)으로 대신한다. 두 값은 매주 pred_kma, pred_cal에 같이 남긴다. 원천은 daily_district(actuals.py),
     fcst_archive(collect_wx.py). 80% 구간은 2024 검증 구간 오차로 고정한 값(output/models/fc_ops_spec.json).
  3. 스냅샷 발행.

주의: 2026-10-08까지는 '최근 4주 신고율 × 지난주 대여량'을 썼다. 2024 검증 구간에서 나이브보다 나빠서 바꿨다
      (output/tables/fc_ops_replay_valid.csv). 1C에서 고른 음이항(신고율 모형)은 테스트에서 안장만 나이브를 이겨 쓰지 않는다.

출력: ops.duckdb bike_risk_latest, forecast_weekly; serving/bike_risk_latest.parquet, forecast_weekly.parquet
실행: .venv/bin/python py/jobs/weekly.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parents[1] / "risk"))   # risk_lgb.prep(위험 모형 입력 가공)
import fc_ops   # noqa: E402
import risk_track   # noqa: E402
from common import ROOT, alert, connect, job_lock, log_run, table_exists   # noqa: E402
from publish import build_status, publish   # noqa: E402
from risk_lgb import prep   # noqa: E402

PARTS = fc_ops.ALL          # 주간 예측을 내는 유형: 네 부품 + 전체 + 단말기·기타
KIND = {"타이어": "fail_tire", "체인": "fail_chain", "안장": "fail_saddle", "페달": "fail_pedal"}


def reasons(booster, X, feats, phr, latest):
    """이유 문구: 점수를 올린(기여가 양수인) 변수 가운데 기여가 큰 셋. 값이 그 주 중앙값 이상이면 high, 아니면 low 문구.
    문구가 없거나 None인 방향은 건너뛴다(LightGBM pred_contrib = SHAP 값)"""
    contrib = booster.predict(X, pred_contrib=True)[:, :-1]
    names = np.array(feats)
    med = {f: float(np.nanmedian(latest[f].astype(float))) for f in names if f in phr}          # 그 주 중앙값: 문구 방향을 정한다
    def phrase(f, val):
        p = phr[f]
        if isinstance(p, str):
            return p
        return p["high"] if (val is not None and not np.isnan(val) and val >= med[f]) else p["low"]
    vals = latest[list(names)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)     # 범주형·결측은 NaN → low 문구
    out = []
    for row, vrow in zip(contrib, vals):
        seen = []
        for f, c, v in sorted(zip(names, row, vrow), key=lambda t: -t[1]):
            x = phrase(f, v) if c > 0 and f in phr else None
            if x and x not in seen:                  # 같은 문구(이번 주 이용이 적음 등)가 여러 변수에서 나와도 한 번만
                seen.append(x)
        out.append(" / ".join(seen[:3]))
    return out


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
    # 순위·등급은 '다음 주에 한 번이라도 신고될 확률'로 매긴다(2026-10-08 결정, docs/notes/07 R19). 1,000km당 위험률은 적게 탄
    # 자전거를 높게 쳐서 실제 다음 주 신고를 덜 맞혔다. 화면에는 %가 아니라 그 주 평균 대비 배수만 보여 준다
    # (확률의 절대 수준은 계절에 따라 20%쯤 어긋나지만 배수는 거의 그대로다)
    latest["prob_next"] = 1 - np.exp(-latest.expected_reports)
    avg = latest.prob_next.where(ranked).mean()
    latest["risk_ratio"] = (latest.prob_next / avg).where(ranked)
    latest["pct"] = np.where(ranked, latest.prob_next.where(ranked).rank(pct=True), np.nan)
    latest["km_to_report"] = 1000 / latest.rate_1000km                                     # 다음 신고까지 예상 주행거리
    latest["weeks_to_report"] = (latest.km_to_report / (latest.km_4w / 4)).where(ranked)   # 지금 타는 만큼 타면 몇 주 뒤
    cut = spec["grade_by_weekly_percentile"]
    latest["report_grade"] = np.select([~ranked, latest.pct >= cut["점검 권장"], latest.pct >= cut["주의"], latest.pct >= cut["보통"]],
                                       ["산정 제외", "점검 권장", "주의", "보통"], default="낮음")
    latest["report_ratio"] = latest.risk_ratio
    latest["report_reasons"] = reasons(booster, X, spec["features"], spec["reason_phrases"], latest)

    # 화면의 점검 권장: 신고 없이 손볼 자전거 모형(다음 주 실제 수리). 그 주에 대여됐고 최근 14일 신고 없는 자전거만 대상
    rspec = json.load(open(ROOT / "output/models/repair_spec.json", encoding="utf-8"))
    rbooster = lgb.Booster(model_file=str(ROOT / "output/models" / rspec["model"]))
    RX = latest[rspec["features"]]
    prob = rbooster.predict(RX)
    ok, ratio, pct, grade = risk_track.repair_grade(latest.n_trips, latest.days_since_rep, prob, cut)
    latest["repair_prob"], latest["risk_ratio"], latest["pct"], latest["grade"] = np.where(ok, prob, np.nan), ratio, pct, grade
    rphr = json.load(open(ROOT / "output/models/repair_reasons.json", encoding="utf-8"))["reason_phrases"]
    latest["reasons"] = reasons(rbooster, RX, rspec["features"], rphr, latest)
    for part in ("tire", "chain", "saddle", "pedal"):
        mp = spec["models"][part]
        b = lgb.Booster(model_file=str(ROOT / "output/models" / mp["file"]))
        latest[f"rate_{part}"] = np.exp(b.predict(X, raw_score=True)) * 1000 * mp["calibration_scale"]
        pp = 1 - np.exp(-latest[f"rate_{part}"] * latest.km_4w / 4 / 1000)               # 부품도 다음 주 확률 → 평균 대비 배수
        latest[f"ratio_{part}"] = (pp / pp.where(ranked).mean()).where(ranked)
    out = latest[["bike_id", "wk", "rate_1000km", "expected_reports", "prob_next", "risk_ratio", "km_to_report", "weeks_to_report", "pct", "grade", "reasons",
                  "repair_prob", "report_ratio", "report_grade", "report_reasons",
                  "rate_tire", "rate_chain", "rate_saddle", "rate_pedal", "ratio_tire", "ratio_chain", "ratio_saddle", "ratio_pedal",
                  "km_4w", "km_since_rep", "rep_13w", "sham_2w", "bike_type"]].copy()
    out["model_version"] = f"{spec['version']} + repair {rspec['fixed_on']}"
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
    """서울 전체 부품별 다음 7일 예측. 반환: (일별 실적 일수, 산출 여부, 보여 준 방식)"""
    con.execute("CREATE TABLE IF NOT EXISTS forecast_weekly (issue_date DATE, part VARCHAR, method VARCHAR, pred DOUBLE, lo80 DOUBLE, hi80 DOUBLE, note VARCHAR)")
    for col in ("pred_kma", "pred_cal"):
        con.execute(f"ALTER TABLE forecast_weekly ADD COLUMN IF NOT EXISTS {col} DOUBLE")
    con.execute("DELETE FROM forecast_weekly WHERE issue_date = ?", [today])
    ins = "INSERT INTO forecast_weekly (issue_date, part, method, pred, lo80, hi80, note, pred_kma, pred_cal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    if not table_exists(con, "daily_district"):
        for part in PARTS.values():
            con.execute(ins, [today, part, "none", None, None, None, "일별 실적(daily_district) 없음 — actuals.py 먼저", None, None])
        return 0, False, None
    d = fc_ops.daily_from_district(con.execute("SELECT * FROM daily_district").df())
    models, spec = fc_ops.load_models()
    arch = con.execute("SELECT * FROM fcst_archive WHERE issue_ts >= ?", [today - dt.timedelta(days=4)]).df() if table_exists(con, "fcst_archive") else None
    wx = fc_ops.kma_live(arch, today) if arch is not None and len(arch) else None
    past = ROOT / "data/interim/weather_forecast/kma_by_origin.parquet"     # 실시간 수집 전 주는 1C 과거 예보 파일(맥북에만 있음)
    if wx is None and past.exists():
        k = pd.read_parquet(past)
        k = k[k.origin == pd.Timestamp(today)].sort_values("lead")
        wx = k if len(k) == 7 else None
    bad, ns = fc_ops.master_flags()
    r = fc_ops.forecast(d, today, wx, models, spec, bad=bad, no_service=ns)
    shown = "kma" if r["kma"] is not None else "cal"
    note = "최근 4주 신고율 × " + ("기상청 예보로 예측한 대여량" if shown == "kma" else "달력으로 예측한 대여량(예보가 비어 대신 씀)")
    note += ". 범위 = 2024 검증 구간 오차 10 ~ 90%"
    if shown == "cal":
        alert(f"주간 예측: {today} 앞으로 7일 예보가 비어 달력판으로 냈다(fcst_archive 확인)")
    if not r["hol_known"]:
        note += ". 공휴일 표가 이 해를 덮지 않음"
        alert("주간 예측: 공휴일 표(data/raw/calendar)가 앞으로 7일을 덮지 않는다 — 새해 공휴일 추가 필요")
    for p, part in PARTS.items():
        pred = r[shown][p]
        lo, hi = spec["intervals_80"][shown][part]
        con.execute(ins, [today, part, f"S1 {fc_ops.METHODS[shown]}", pred, max(pred * (1 + lo), 0), pred * (1 + hi), note,
                          r["kma"][p] if r["kma"] else None, r["cal"][p]])
    return len(d), True, shown


def run():
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())     # 주간 예측은 언제 돌려도 그 주 월요일 ~ 일요일
    with job_lock():
        con = connect()
        try:
            if not table_exists(con, "panel_latest"):
                raise RuntimeError("panel_latest 없음 — daily.py 먼저")
            pwk = con.execute("SELECT max(wk) FROM panel_latest").fetchone()[0]
            if pwk is None or pd.Timestamp(pwk).date() != monday - dt.timedelta(days=7):   # daily가 안 돌았거나 지난주 자료가 비면 옛 주 점수가 '이번 주 추천'으로 나간다
                raise RuntimeError(f"panel_latest가 지난주({monday - dt.timedelta(days=7)})가 아니라 {pwk} 주다 — daily.py를 먼저 돌린다")
            wk, n, grades = score_risk(con)
            risk_track.save_history(con)                 # 이번 주에 누구를 추천했는지 남기고
            ev = risk_track.evaluate(con)                # 지난 추천이 그다음 주에 실제로 신고됐는지 채점한다(다음 주가 다 지난 주만)
            note = risk_track.retrain_note(ev)
            if note and today.weekday() == 0:            # 손으로 다시 돌릴 때마다 같은 알림이 가지 않게 월요일 정기 실행에서만
                alert(note)
            if len(ev):
                e = ev.iloc[-1]
                print(f"위험 모형 채점({e.wk:%Y-%m-%d} 주 점수 → 다음 주 신고): 전체 {e.base_rate:.1%}, 상위 1,000대 {e.hit_top1000:.1%}(무작위 대비 {e.lift_top1000:.1f}배), "
                      f"점검 권장 목록 {e.hit_insp:.1%}({int(e.n_insp):,}대)")
            days, done, shown = forecast_weekly(con, monday)
            build_status(con)
            for t in ("bike_risk_latest", "forecast_weekly", "forecast_track", "data_status"):
                publish(con, t)
            log_run(con, "weekly", "ok", f"점수 주 {wk} {n}대, 예측 {fc_ops.METHODS.get(shown, '대기')}")
            print(f"weekly: 위험 점수 {wk} 주, {n:,}대"); print(grades.to_string(index=False)); print(f"주간 예측: {fc_ops.METHODS.get(shown, '대기')}"); print(con.sql("SELECT part, round(pred) pred, round(pred_kma) kma, round(pred_cal) cal, round(lo80) lo, round(hi80) hi FROM forecast_weekly WHERE issue_date = (SELECT max(issue_date) FROM forecast_weekly)").df().to_string(index=False))
        except Exception as e:
            log_run(con, "weekly", "fail", str(e)); alert(f"weekly 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
