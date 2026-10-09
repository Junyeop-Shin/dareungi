"""사후 분석(부록 B): 최근 4주 신고율(lvl) 계수를 1로 고정한 음이항.

테스트 평가(1C-9) 뒤에 보니, 선택한 음이항은 log(lvl) 계수가 0.60~0.97로 1보다 작아서 신고율이
내려간 테스트 구간에서 수준을 다 따라가지 못하고 13~20% 과대예측했다. 여기서는 그 계수를 1로 고정한
판(구조 나이브 S1 × 요일·날씨 효과)을 만들어 검증·테스트에서 원래 판과 나란히 채점한다.

주의: 테스트 결과를 본 뒤에 한 수정이라 여기 나오는 테스트 성능은 최종 성능이 아니라 진단이 맞는지
보는 참고치다. 보고서에는 부록 B에 '테스트 뒤 사후 수정'으로만 싣고, 선택 모형(output/models/selection.csv)은
바꾸지 않는다.

입력: data/processed/daily_master_v2.parquet(fc.load_master), 기상청 예보(시나리오 B)
출력: output/tables/fc_posthoc_lvl1.csv       검증·테스트 skill, bias%, 계수(원래 판 / 계수 1 고정 / 구조 나이브)
      output/tables/fc_posthoc_lvl1_half.csv  테스트 반기별
실행: .venv/bin/python py/forecast/fc_posthoc.py          검증 구간만 계산해 화면에 보인다(표는 그대로 둔다)
      .venv/bin/python py/forecast/fc_posthoc.py --test   테스트 구간까지 채점해 위 표를 다시 쓴다(2026-10-07에 한 번 돌렸다)
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
warnings.filterwarnings("ignore")
from fc import EXPO_CAL, PARTS, TRAIN_DAYS, fit_nb_fc, future_frame, load_master, make_base, score     # noqa: E402
from fc_expo import E2_WX, expo_rows                                                                  # noqa: E402
from fc_models import GLM_CAL, GLM_WX, predict_s1                                                     # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RHS_FREE = GLM_CAL + GLM_WX                                   # 원래 판: np.log(lvl) 계수 추정
RHS_FIXED = RHS_FREE.replace("np.log(lvl) + ", "")            # 고정 판: lvl은 offset으로(계수 1)


def fit_part(m, part, end, fixed):
    """부품 하나의 신고율 모형. fixed=True면 log(lvl)을 노출과 함께 offset에 넣는다"""
    tr = m[m.date.between(TRAIN_DAYS[0], end) & (m.bad == 0)].assign(lvl=lambda t: t["lvl_" + part]).dropna(subset=["lvl"])
    off = np.log(tr.rentals) + (np.log(tr.lvl) if fixed else 0)
    return fit_nb_fc(f"{part} ~ {RHS_FIXED if fixed else RHS_FREE}", tr, offset=off)


def predict_part(fit, f, part, expo_hat, fixed):
    """미래 7일 표 f의 날별 기대 건수를 원점별 7일 합계로 더한다"""
    res, _ = fit
    g = f.assign(lvl=f["lvl_" + part])
    off = np.log(expo_hat) + (np.log(g.lvl.to_numpy()) if fixed else 0)
    mu = np.asarray(res.predict(g, offset=off)).reshape(-1, 7)
    return pd.DataFrame({"oi": f.oi.to_numpy().reshape(-1, 7)[:, 0], "pred": mu.sum(axis=1)})


def run_split(m, base, split, end):
    """split(valid/test) 원점을 end까지 학습한 모형으로 예측한다. 대여량은 K2(기상청 예보로 학습한 음이항)"""
    oi = base[(base.split == split) & (base.part == "total_faults")].i.to_numpy()
    rows_B = expo_rows(m, TRAIN_DAYS[0], end, "B")
    expo_B = fit_nb_fc("rentals ~ " + EXPO_CAL + E2_WX, rows_B.assign(tavg_anom=rows_B.tavg - rows_B.tavg_normal))[0]
    F = future_frame(m, oi, "B")
    E = np.asarray(expo_B.predict(F.assign(tavg_anom=F.tavg - F.tavg_normal)))
    preds, coefs = [], {}
    for part in PARTS:
        def add(df, model):
            preds.append(df.assign(origin=m.date.to_numpy()[df.oi.to_numpy()], part=part, model=model)[["origin", "part", "model", "pred"]])
        add(predict_s1(F, part, E), "S1 구조 나이브")
        free = fit_part(m, part, end, fixed=False)
        coefs[part] = float(free[0].params["np.log(lvl)"])
        add(predict_part(free, F, part, E, fixed=False), "원래 판(계수 추정)")
        add(predict_part(fit_part(m, part, end, fixed=True), F, part, E, fixed=True), "사후: 계수 1 고정")
    p = pd.concat(preds, ignore_index=True)
    sc = score(p, base)
    sc = sc[sc.split == split].copy()
    sc["bias%"] = sc.bias / (sc.MAE / (sc.MAEpct / 100)) * 100       # 실제 평균 = MAE / MAE%
    sc["log(lvl) 계수(원래 판)"] = sc.part.map(coefs).round(3)
    return p, sc


def main():
    with_test = "--test" in sys.argv                 # 테스트 구간은 명시했을 때만 연다
    m = load_master()
    base = make_base(m, include_test=with_test)
    out, half = [], []
    for split, end in [("valid", TRAIN_DAYS[1])] + ([("test", "2024-12-31")] if with_test else []):
        p, sc = run_split(m, base, split, end)
        out.append(sc)
        if split == "test":
            b = base[base.split == "test"][["origin", "part", "y", "B1"]]
            d = p.merge(b, on=["origin", "part"])
            d["반기"] = np.where(d.origin < "2025-07-01", "2025 상반기", np.where(d.origin < "2026-01-01", "2025 하반기", "2026 상반기"))
            d["ae"] = (d.pred - d.y).abs(); d["ae1"] = (d.B1 - d.y).abs(); d["err"] = d.pred - d.y
            g = d.groupby(["반기", "part", "model"]).agg(MAE=("ae", "mean"), MAE1=("ae1", "mean"), y=("y", "mean"), err=("err", "mean")).reset_index()
            g["skill"] = 1 - g.MAE / g.MAE1; g["bias%"] = g.err / g.y * 100
            g["부품"] = g.part.map(PARTS)
            half = g[["반기", "부품", "model", "skill", "bias%"]].round(3)
    tab = pd.concat(out)
    tab["부품"] = tab.part.map(PARTS)
    tab = tab[["split", "부품", "model", "n", "MAE", "MAEpct", "skill", "bias%", "log(lvl) 계수(원래 판)"]].round(3)
    if with_test:
        tab.to_csv(ROOT / "output/tables/fc_posthoc_lvl1.csv", index=False)
        half.to_csv(ROOT / "output/tables/fc_posthoc_lvl1_half.csv", index=False)
    else:
        print("검증 구간만 계산했다(--test 없음). 저장된 표는 그대로 둔다.")
    print(tab.pivot(index=["split", "부품"], columns="model", values="skill").round(3).to_string())
    print(tab.pivot(index=["split", "부품"], columns="model", values="bias%").round(1).to_string())


if __name__ == "__main__":
    main()
