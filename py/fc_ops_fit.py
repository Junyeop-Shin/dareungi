"""운영 주간 예측용 대여량 모형 두 개를 맞추고 고정한다(py/fc_ops.py가 읽는다).

  kma  1C의 K2: 음이항, 달력 + 날씨(E2), 기상청 과거 예보로 학습
  cal  1C의 시나리오 D: 음이항, 달력만

먼저 2023년까지로 맞춘 모형으로 2024 검증 구간을 fc_ops.py의 함수로 다시 예측해, 1C 방식(fc.future_frame +
fc_models.predict_s1)으로 낸 값과 같은지 확인한다. 예보판은 1C 노트북의 S1_B(output/tables/fc_valid.csv)와도 맞춘다.
같은 표에 나이브(지난주 그대로)와 '최근 4주 신고율 × 지난주 대여량'(2026-10-08까지 운영에 쓰던 방식)도 넣는다.
80% 구간 보정값(상대오차 10% · 90% 분위)도 이 검증 예측에서 구한다. 1C가 테스트 전에 한 방식과 같다.
그다음 1C 테스트 때처럼 2024-12-31까지로 다시 맞춰 저장한다. 테스트 구간은 쓰지 않는다.

입력: data/processed/daily_master_v2.parquet, data/interim/weather_forecast/kma_by_origin.parquet,
      output/models/kma_calibration.json(kma_forecast.py)
출력: output/models/expo_ops.json(모형 두 개의 식·계수·alpha), fc_ops_spec.json(평년기온 계수, 80% 구간 보정값, 재현 결과),
      output/tables/fc_ops_replay_valid.csv
실행: .venv/bin/python py/fc_ops_fit.py   (1 ~ 2분)
"""
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
import fc                                                    # noqa: E402
import fc_ops                                                # noqa: E402
from fc_expo import E2_WX, expo_rows                         # noqa: E402
from fc_models import predict_s1                             # noqa: E402

TEST_REFIT_END = "2024-12-31"        # 1C 테스트 때 다시 맞춘 범위와 같게


def fit_models(m, end):
    """1C와 같은 식으로 두 대여량 모형을 맞춘다"""
    cal = fc.fit_expo(m, weather=False, end=end)
    rows = expo_rows(m, fc.TRAIN_DAYS[0], end, "B")
    kma = fc.fit_nb_fc("rentals ~ " + fc.EXPO_CAL + E2_WX, rows.assign(tavg_anom=rows.tavg - rows.tavg_normal))[0]
    return {"kma": kma, "cal": cal}


def check_inputs(m, coef):
    """fc_ops의 달력·수준 계산이 마스터와 같은지 확인한다"""
    c = fc_ops.calendar(m.date.min(), m.date.max(), coef)
    for col in ("dow", "is_holiday", "after_off", "bridge", "fs1", "fc2", "tavg_normal"):
        assert np.allclose(c[col].to_numpy(float), m[col].to_numpy(float)), col
    d = m.set_index("date")
    lv = fc_ops.levels(d, bad=d.bad, no_service=d.no_service)
    for col in ["expo_lvl", "expo_lvl7"] + ["lvl_" + p for p in fc_ops.PARTS]:
        assert np.allclose(lv[col].to_numpy(), d[col].to_numpy(), equal_nan=True), col
    print("달력·평년기온·신고율·대여 수준: 마스터와 같음")


def replay(m, models, spec):
    """2024 검증 원점을 fc_ops.forecast로 다시 예측한다"""
    base = fc.make_base(m)
    v = base[(base.split == "valid") & base.clean]
    kma = pd.read_parquet(fc.ROOT / "data/interim/weather_forecast/kma_by_origin.parquet")
    d = m.set_index("date")
    rows = []
    # 1C 방식(달력판): 날씨 시나리오 A 표에 날씨 없는 모형. 독립된 계산 경로로 fc_ops와 비교한다
    oi = np.sort(v.i.unique())
    F = fc.future_frame(m, oi, "A")
    E = models["cal"].predict(F).to_numpy()
    last_r = fc.sum_back(m.rentals, 7).to_numpy()
    for p in fc_ops.PARTS:
        x = predict_s1(F, p, E)
        rows += [{"origin": m.date[i], "part": p, "model": "1C 방식 달력판", "pred": pr} for i, pr in zip(x.oi, x.pred)]
        b = v[v.part == p]
        rows += [{"origin": o, "part": p, "model": "B1 나이브", "pred": pr} for o, pr in zip(b.origin, b.B1)]
        rows += [{"origin": o, "part": p, "model": "S1 지난주 대여량", "pred": m["lvl_" + p].iloc[i] * last_r[i]} for o, i in zip(b.origin, b.i)]
    for o in sorted(v.origin.unique()):
        wx = kma[kma.origin == o].sort_values("lead")
        r = fc_ops.forecast(d, o, wx, models, spec, bad=d.bad, no_service=d.no_service)
        for p in fc_ops.PARTS:
            rows += [{"origin": o, "part": p, "model": "S1_" + k, "pred": r[k][p]} for k in ("kma", "cal")]
    preds = pd.DataFrame(rows)
    s = fc.score(preds, base)
    s = s[s.split == "valid"]
    tab = s.pivot(index="model", columns="part", values="MAEpct").rename(columns=fc_ops.PARTS)
    ref_b = pd.read_csv(fc.ROOT / "output/tables/fc_valid.csv").query("model == 'S1_B'").set_index("part").MAEpct.rename(fc_ops.PARTS)
    tab.loc["1C S1_B(노트북)"] = ref_b
    tab = tab[list(fc_ops.PARTS.values())].reindex(["B1 나이브", "S1 지난주 대여량", "S1_cal", "1C 방식 달력판", "S1_kma", "1C S1_B(노트북)"]).round(2)
    preds = preds[preds.model.isin(["S1_kma", "S1_cal"])]
    pv = preds.merge(base[["origin", "part", "y"]], on=["origin", "part"])
    pv["r"] = (pv.y - pv.pred) / pv.pred
    q = pv.groupby(["model", "part"]).r.quantile([0.1, 0.9]).unstack()
    intervals = {k: {fc_ops.PARTS[p]: [float(q.loc[("S1_" + k, p), 0.1]), float(q.loc[("S1_" + k, p), 0.9])] for p in fc_ops.PARTS} for k in ("kma", "cal")}
    return tab, intervals


def main():
    m = fc.load_master()
    spec = {"normal_coef": fc_ops.normal_coef(m)}
    check_inputs(m, spec["normal_coef"])

    tab, intervals = replay(m, fit_models(m, fc.TRAIN_DAYS[1]), spec)
    tab.to_csv(fc.ROOT / "output/tables/fc_ops_replay_valid.csv")
    print("2024 검증 구간 MAE%(원점 360개):"); print(tab.to_string())
    diff = (tab.loc["S1_kma"] - tab.loc["1C S1_B(노트북)"]).abs().max(), (tab.loc["S1_cal"] - tab.loc["1C 방식 달력판"]).abs().max()
    print(f"1C 숫자와의 최대 차이: 예보판 {diff[0]:.3f}%p, 달력판 {diff[1]:.3f}%p")
    if max(diff) > 0.005:
        raise SystemExit("1C 숫자가 재현되지 않는다 — 저장하지 않는다")

    models = fit_models(m, TEST_REFIT_END)
    saved = {k: {"formula": r.model.formula, "alpha": float(r.model.family.alpha), "params": {n: float(c) for n, c in r.params.items()}}
             for k, r in models.items()}
    # 계수로 직접 계산한 예측이 statsmodels 예측과 같은지 검증 구간 표로 확인한다
    F = fc.future_frame(m, np.where(m.date.between("2024-01-01", "2024-12-25"))[0], "B")
    F = F.assign(tavg_anom=F.tavg - F.tavg_normal)
    for k, r in models.items():
        assert np.allclose(fc_ops.ExpoModel(saved[k]["params"]).predict(F), r.predict(F), rtol=1e-9), k
    json.dump(saved, open(fc_ops.MODEL_FILE, "w"), ensure_ascii=False, indent=1)
    spec.update({
        "train": [fc.TRAIN_DAYS[0], TEST_REFIT_END], "fitted": str(dt.date.today()),
        "intervals_80": intervals, "interval_source": "2024 검증 구간 상대오차 10% · 90% 분위(2023년까지로 맞춘 모형)",
        "replay_valid_maepct": {i: {c: float(v) for c, v in r.items()} for i, r in tab.iterrows()},
        "shown": "kma", "fallback": "cal",
        "switch_rule": "2026-10-12 이후 운영 주로 12주 누적 MAE%를 비교해 달력판이 예보판보다 3%p 넘게 낮으면 교체를 검토한다",
    })
    json.dump(spec, open(fc_ops.SPEC_FILE, "w"), ensure_ascii=False, indent=1)
    print("저장:", fc_ops.MODEL_FILE.name, fc_ops.SPEC_FILE.name)
    print("80% 구간(예보판):", {p: [round(a, 3), round(b, 3)] for p, (a, b) in intervals["kma"].items()})


if __name__ == "__main__":
    main()
