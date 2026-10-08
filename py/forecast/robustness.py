"""강건성 점검: 운영 주간 예측(신고율 × 대여량 예측)과 노출 선택이 기준·기간을 바꿔도 유지되는지 본다.

1B 신고율 회귀의 강건성(기간 둘로 나누기, 시작일, 노출, 중복 병합)은 이미 1B 노트북에 있다(reg_robust, reg_start_coef,
reg_hac_sensitivity). 헛대여 기준(2분 / 2 ~ 3분 / 2 ~ 5분)은 rc_4a_ratios, 경보 규칙은 risk_alert_valid에 있다.
여기서는 그 밖의 것만 본다.

  R1 대여량 모형 계수의 연도별 안정성   두 대여량 모형(달력판·예보판)을 2021-04 ~ 12, 2022, 2023, 2024에 따로 맞춘다
  R2 신고율 창 길이                     '최근 4주'를 2·4·8·12주로 바꿔 2024 검증 구간 MAE%
  R3 이동거리 정상 기준(dist_ok)         그날 거리 0 비율 기준을 5·10·20·30%로 바꿔도 노출 = 대여건수가 가장 나은가
  R4 예측 오차의 자기상관               2024 검증 구간 월요일 원점(겹치지 않는 주)의 상대오차 자기상관, Ljung-Box
  R5 80% 범위 적중률(새 자료)           테스트 뒤 2026-07 ~ 09 주의 운영 예보판 범위 안에 실제가 든 비율
  R6 대여량 모형 학습 시작              2021-04-08(지금) 대신 거리두기 해제 뒤 2022-05-01부터 맞추면 2024 검증 MAE%가 어떤가(창 2·4주와 조합)
  R7 모형 나이(재학습 주기)             2022년 말 / 2023년 말까지로 맞춘 모형을 2023 ~ 2024 분기별로 채점해, 학습 뒤 시간이 지날수록
                                       나빠지는지 본다. 학습 시작 2021-04 / 2022-05 둘 다

테스트 구간(2025-01 ~ 2026-06)은 쓰지 않는다. 결과는 판단 근거가 아니라 보고서의 강건성 절 재료다.

입력: data/processed/daily_master_v2.parquet, data/interim/weather_forecast/kma_by_origin.parquet,
      output/models/fc_ops_spec.json, ops.duckdb forecast_track(R5)
출력: output/tables/rb_1_expo_by_year.csv, rb_2_window_valid.csv, rb_3_exposure_distok.csv, rb_4_error_acf.csv, rb_5_coverage_live.csv,
      rb_6_train_start_valid.csv, rb_7_model_age.csv
실행: .venv/bin/python py/forecast/robustness.py   (2 ~ 3분)
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
import fc                                              # noqa: E402
import fc_ops                                          # noqa: E402
from fc_expo import E2_WX, expo_rows                   # noqa: E402
from fc_ops_fit import fit_models                      # noqa: E402
from nbreg import fit_nb                               # noqa: E402

warnings.filterwarnings("ignore")
OUT = fc.ROOT / "output/tables"
YEARS = {"2021-04 ~ 12": ("2021-04-08", "2021-12-31"), "2022": ("2022-01-01", "2022-12-31"),
         "2023": ("2023-01-01", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "전체(운영 모형)": ("2021-04-08", "2024-12-31")}


def r1_expo_by_year(m):
    """두 대여량 모형을 해마다 따로 맞춰 주요 계수(배수)를 비교한다"""
    terms = {"토요일(월 대비)": "C(dow)[T.6]", "일요일(월 대비)": "C(dow)[T.7]", "공휴일": "is_holiday", "쉬는 날 다음 첫 근무일": "after_off",
             "징검다리 근무일": "bridge", "최근 대여 수준 탄력성": "np.log(expo_lvl)"}
    wx_terms = {"비 예보(있음)": "rain_any", "눈 예보": "snow_any", "한파 예보(-4℃ 이하)": "cold_wave"}
    rows = []
    for period, (a, b) in YEARS.items():
        tr = m[m.date.between(a, b) & (m.no_service == 0)].dropna(subset=["expo_lvl"])
        cal = fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL, tr)[0]
        rb = expo_rows(m, a, b, "B")
        kma = fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL + E2_WX, rb.assign(tavg_anom=rb.tavg - rb.tavg_normal))[0]
        for model, res, tm in (("달력판", cal, terms), ("예보판", kma, {**terms, **wx_terms})):
            for name, t in tm.items():
                v = res.params[t]
                rows.append({"모형": model, "항목": name, "기간": period, "값": v if t.startswith("np.log") else np.exp(v)})
            if model == "예보판":                              # 비 10mm 예보: 비 있음 + log1p(10)
                rows.append({"모형": model, "항목": "비 10mm 예보", "기간": period,
                             "값": float(np.exp(res.params["rain_any"] + res.params["np.log1p(rain_mm)"] * np.log1p(10)))})
    t = pd.DataFrame(rows).pivot_table(index=["모형", "항목"], columns="기간", values="값", sort=False)[list(YEARS)]
    t.round(3).to_csv(OUT / "rb_1_expo_by_year.csv")
    return t


def _s1_valid(m, models, window, start=None, end=None):
    """원점(기본: 2024 검증 구간)의 예보판·달력판 S1 예측. 신고율 창만 window일로 바꾼다"""
    base = fc.make_base(m)
    v = base[(base.split == "valid") & base.clean] if start is None else base[base.origin.between(start, end) & base.clean]
    d = m.set_index("date")
    lv = fc_ops.levels(d, bad=d.bad, no_service=d.no_service, window=window)
    cal = fc_ops.calendar(m.date.min(), m.date.max(), fc_ops.normal_coef(m))
    kma = pd.read_parquet(fc.ROOT / "data/interim/weather_forecast/kma_by_origin.parquet")
    rows = []
    for o in sorted(v.origin.unique()):
        f = fc_ops.frame(lv, cal, o, kma[kma.origin == o].sort_values("lead"))
        for k in ("kma", "cal"):
            rows += [{"origin": o, "part": p, "model": k, "pred": x} for p, x in fc_ops.s1(f, models[k].predict(f)).items()]
    return pd.DataFrame(rows), base


def r6_train_start(m):
    """대여량 모형을 2022-05-01부터 맞췄을 때(2023년까지) 2024 검증 MAE%. 신고율 창 2주·4주와 조합해 본다"""
    out = []
    for start in ("2021-04-08", "2022-05-01"):
        tr = m[m.date.between(start, fc.TRAIN_DAYS[1]) & (m.no_service == 0)].dropna(subset=["expo_lvl"])
        rb = expo_rows(m, start, fc.TRAIN_DAYS[1], "B")
        models = {"cal": fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL, tr)[0],
                  "kma": fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL + E2_WX, rb.assign(tavg_anom=rb.tavg - rb.tavg_normal))[0]}
        for w in (28, 14):
            preds, base = _s1_valid(m, models, w)
            s = fc.score(preds, base)
            out.append(s[s.split == "valid"].assign(학습_시작=start, 창=f"{w // 7}주"))
    t = pd.concat(out).assign(model=lambda x: x.model.map(fc_ops.METHODS)) \
        .pivot_table(index=["model", "학습_시작", "창"], columns="part", values="MAEpct", sort=False).rename(columns=fc_ops.PARTS)
    t = t[list(fc_ops.PARTS.values())]
    t.round(2).to_csv(OUT / "rb_6_train_start_valid.csv")
    return t


def _fit_expo(m, start, end):
    tr = m[m.date.between(start, end) & (m.no_service == 0)].dropna(subset=["expo_lvl"])
    rb = expo_rows(m, start, end, "B")
    return {"cal": fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL, tr)[0],
            "kma": fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL + E2_WX, rb.assign(tavg_anom=rb.tavg - rb.tavg_normal))[0]}


def r7_model_age(m):
    """학습 끝을 2022-12 / 2023-12로 두고 2023 ~ 2024 분기별 전체 MAE%. 모형 나이 = 학습 끝 뒤 몇 번째 분기인가"""
    rows = []
    for start in ("2021-04-08", "2022-05-01"):
        for end in ("2022-12-31", "2023-12-31"):
            preds, base = _s1_valid(m, _fit_expo(m, start, end), 28, start=pd.Timestamp(end) + pd.Timedelta(days=1), end="2024-12-25")
            p = preds.merge(base[["origin", "part", "y"]], on=["origin", "part"])
            p["q"] = pd.to_datetime(p.origin).dt.to_period("Q").astype(str)
            for (k, part, q), g in p.groupby(["model", "part", "q"]):
                rows.append({"모형": fc_ops.METHODS[k], "학습 시작": start, "학습 끝": end, "부품": fc_ops.PARTS[part], "분기": q,
                             "MAE%": (g.pred - g.y).abs().mean() / g.y.mean() * 100})
    t = pd.DataFrame(rows)
    t.round(2).to_csv(OUT / "rb_7_model_age.csv", index=False)
    return t[t.부품 == "전체"].pivot_table(index=["모형", "학습 시작", "학습 끝"], columns="분기", values="MAE%", sort=False)


def r2_window(m):
    models = fit_models(m, fc.TRAIN_DAYS[1])                       # 2023년까지로 맞춘 모형(검증 구간 평가용)
    out, keep = [], None
    for w in (14, 28, 56, 84):
        preds, base = _s1_valid(m, models, w)
        s = fc.score(preds, base)
        s = s[s.split == "valid"].assign(창=f"{w // 7}주")
        out.append(s)
        if w == 28:
            keep = (preds, base)
    t = pd.concat(out).assign(model=lambda x: x.model.map(fc_ops.METHODS)) \
        .pivot_table(index=["model", "창"], columns="part", values="MAEpct", sort=False).rename(columns=fc_ops.PARTS)
    t = t[list(fc_ops.PARTS.values())]
    t.round(2).to_csv(OUT / "rb_2_window_valid.csv")
    return t, keep


def r3_exposure(m):
    """1B 2절과 같은 비교(offset AIC)를 거리 정상 기준만 바꿔 다시 한다"""
    d = m[m.date.between("2021-04-08", "2024-12-31") & (m.bad == 0)].copy()
    d["year"] = d.date.dt.year
    f = "total_faults ~ C(dow) + is_holiday + after_off + fs1 + fc1 + fs2 + fc2 + np.log1p(rain_mm) + np.log1p(rain_lag1) + cold_wave + C(year)"
    rows = []
    for thr in (0.05, 0.10, 0.20, 0.30):
        dc = d[(d.dist_zero_share < thr) & (d.dist_km_raw > 0)].assign(dist=lambda x: x.dist_km_raw)
        aic = {name: fit_nb(f, dc, e)[2] for e, name in (("rentals", "대여건수"), ("use_hours", "이용시간"), ("dist", "이동거리"))}
        best = min(aic, key=aic.get)
        rows.append({"거리 0 비율 기준": f"{thr:.0%} 미만", "일수": len(dc), **{f"AIC 차이: {k}": v - aic[best] for k, v in aic.items()}, "가장 나은 노출": best})
    t = pd.DataFrame(rows).set_index("거리 0 비율 기준")
    t.round(1).to_csv(OUT / "rb_3_exposure_distok.csv")
    return t


def r4_acf(preds, base):
    """월요일 원점만(7일 합계가 겹치지 않게) 상대오차의 1 ~ 4주 자기상관"""
    p = preds.merge(base[["origin", "part", "y"]], on=["origin", "part"])
    p = p[pd.to_datetime(p.origin).dt.dayofweek == 0].sort_values("origin")
    rows = []
    for (k, part), g in p.groupby(["model", "part"]):
        e = ((g.y - g.pred) / g.pred).to_numpy()
        r = acf(e, nlags=4)
        rows.append({"모형": fc_ops.METHODS[k], "부품": fc_ops.PARTS[part], "주 수": len(e), **{f"{i}주 자기상관": r[i] for i in range(1, 5)},
                     "Ljung-Box p(4주)": float(acorr_ljungbox(e, lags=[4]).lb_pvalue.iloc[0]), "평균 상대오차": e.mean()})
    t = pd.DataFrame(rows).set_index(["모형", "부품"])
    t.round(3).to_csv(OUT / "rb_4_error_acf.csv")
    return t


def r5_coverage():
    """테스트 뒤 주(2026-07 ~)에서 운영 예보판 80% 범위의 적중률"""
    from common import connect
    spec = json.load(open(fc_ops.SPEC_FILE))
    con = connect()
    t = con.execute("SELECT * FROM forecast_track WHERE actual IS NOT NULL AND week >= DATE '2026-07-06' AND pred_kma IS NOT NULL").df()
    con.close()
    rows = []
    for part, g in t.groupby("part"):
        lo, hi = spec["intervals_80"]["kma"][part]
        inside = (g.actual >= g.pred_kma * (1 + lo)) & (g.actual <= g.pred_kma * (1 + hi))
        rows.append({"부품": part, "주 수": len(g), "범위 안": int(inside.sum()), "적중률": inside.mean(),
                     "범위보다 많음": int((g.actual > g.pred_kma * (1 + hi)).sum()), "범위보다 적음": int((g.actual < g.pred_kma * (1 + lo)).sum())})
    out = pd.DataFrame(rows).set_index("부품").reindex(list(fc_ops.PARTS.values()))
    out.round(3).to_csv(OUT / "rb_5_coverage_live.csv")
    return out


def main():
    m = fc.load_master()
    pd.set_option("display.width", 220)
    print("R1 대여량 모형 계수(배수, 탄력성은 계수 그대로)"); print(r1_expo_by_year(m).round(3).to_string())
    t2, (preds, base) = r2_window(m)
    print("\nR2 신고율 창 길이별 2024 검증 MAE%"); print(t2.round(2).to_string())
    print("\nR3 거리 정상 기준별 노출 비교(AIC 차이, 0 = 가장 나음)"); print(r3_exposure(m).round(1).to_string())
    print("\nR4 예측 오차 자기상관(2024 월요일 원점)"); print(r4_acf(preds, base).round(3).to_string())
    print("\nR5 80% 범위 적중률(2026-07 ~ 09, 예보판)"); print(r5_coverage().round(3).to_string())
    print("\nR6 대여량 모형 학습 시작별 2024 검증 MAE%"); print(r6_train_start(m).round(2).to_string())
    print("\nR7 모형 나이: 분기별 전체 MAE%"); print(r7_model_age(m).round(2).to_string())


if __name__ == "__main__":
    main()
