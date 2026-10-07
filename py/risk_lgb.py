"""개체 위험 모형 — 단계 2B-3: LightGBM 포아송 + 주행거리 offset (런북 06)

[무엇을]
자전거×주 패널에서 "다음 주 신고 건수"를 포아송으로 학습하되, 다음 주 주행거리의 로그를 offset(init_score)으로 넣는다.
그러면 모형은 "얼마나 탈까"가 아니라 **1km당 신고율(위험률)**만 배운다. 예측에는 offset이 들어가지 않으므로
  risk_rate  = exp(raw_score) × 1,000  : 다음 주 1,000km당 기대 신고 수(점검 우선순위의 기본값)
  risk_count = exp(raw_score) × 최근 4주 평균 km : 다음 주 기대 신고 수(수거 관점)
전체 모형 1개 + 부품별(타이어·체인·안장·페달) 4개.
[규칙]
  - 학습 2024-01 ~ 2025-06, 검증 2025-07 ~ 12. 조기 종료·하이퍼파라미터·보정은 검증으로만. 테스트(2026 ~)는 읽지 않는다(load_panel 기본).
  - 학습·검증의 '학습 행'은 다음 주 주행이 있는 행(km_next > 0)만(offset이 정의되려면). 순위 채점은 그 주의 모든 자전거로.
  - km_next·repaired_likely는 설명변수에 넣지 않는다(라벨 쪽 정보). last_repaired는 공백이 끝난 뒤부터만 값이 있다.
  - 하이퍼파라미터 격자(num_leaves × min_data_in_leaf)는 전체 모형에서 prec@1000으로 고르고, 부품 모형은 그 설정을 쓴다.
[입력]  data/processed/panel/bike_week.parquet, py/risk_eval.py
[출력]  output/models/risk_{all,tire,chain,saddle,pedal}.txt, output/tables/risk_eval_valid.csv(행 추가), risk_grid_all.csv,
        risk_shap_valid.csv(변수별 평균 |기여|), risk_calibration_valid.csv(10분위 보정 곡선), risk_calibration_scale.csv
[실행]  .venv/bin/python py/risk_lgb.py   (약 10 ~ 20분)
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from risk_eval import BASELINES, evaluate, load_panel   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FEATS = [
    "km", "n_trips", "minutes", "speed_med", "speed_sd",                                   # 이번 주 이용
    "sh_commute", "sh_leisure", "sh_round", "sh_rain", "sh_wet", "sh_hot", "sh_cold",       # 어떻게 탔나
    "climb_per_km", "descent_per_km", "reloc_per_trip",
    "sh_cl0", "sh_cl1", "sh_cl2", "sh_cl3", "sh_cl4",                                       # 출발 대여소 군집 비중(k = 5)
    "km_since_rep", "trips_since_rep", "climb_since_rep", "days_since_rep",                 # 얼마나 닳았나
    "rain_days_since_rep", "freeze_days_since_rep", "age_days", "age_known", "km_total",
    "rep_13w", "km_4w", "sham_2w", "left_censored", "last_repaired",                        # 이력·신호
    "bike_type", "month",
]
TARGETS = {"all": "y_next", "tire": "y_tire_next", "chain": "y_chain_next", "saddle": "y_saddle_next", "pedal": "y_pedal_next"}
BASE = dict(objective="poisson", learning_rate=0.05, feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, seed=42, verbose=-1, num_threads=6)
GRID = [(nl, md) for nl in (31, 63, 127) for md in (200, 500, 2000)]


def prep(df):
    df = df.copy()
    df["climb_per_km"] = df.climb_m / df.km.where(df.km > 0)
    df["descent_per_km"] = df.descent_m / df.km.where(df.km > 0)
    df["reloc_per_trip"] = df.n_reloc / df.n_trips.where(df.n_trips > 0)
    df["month"] = df.wk.dt.month
    df["bike_type"] = df.bike_type.astype("category")
    for c in ("age_known", "left_censored"):
        df[c] = df[c].astype(float)
    return df


def fit(tr, va, target, num_leaves, min_data, rounds=5000):
    """offset = log(km_next). 검증으로 조기 종료. 돌려주는 것: 모형"""
    dtr = lgb.Dataset(tr[FEATS], label=tr[target], init_score=np.log(tr.km_next), categorical_feature=["bike_type"], free_raw_data=False)
    dva = lgb.Dataset(va[FEATS], label=va[target], init_score=np.log(va.km_next), reference=dtr)
    params = {**BASE, "num_leaves": num_leaves, "min_data_in_leaf": min_data}
    return lgb.train(params, dtr, num_boost_round=rounds, valid_sets=[dva], callbacks=[lgb.early_stopping(200, verbose=False)])


def score(model, df):
    """순위 점수 두 개. offset은 예측에 안 들어간다 → raw_score = log(1km당 신고율)"""
    raw = model.predict(df[FEATS], raw_score=True)
    return np.exp(raw) * 1000, np.exp(raw) * df.km_4w.to_numpy() / 4


def main():
    p = prep(load_panel())
    tr_all, va_all = p[p.split == "train"], p[p.split == "valid"]
    tr, va = tr_all[tr_all.km_next > 0], va_all[va_all.km_next > 0]
    results = pd.read_csv(ROOT / "output/tables/risk_eval_valid.csv", index_col=0)
    results = results[results.index.isin([n for n, _ in BASELINES])]        # 베이스라인 행만 남기고 모형 행은 새로

    # 1) 전체 모형: 격자
    grid = []
    for nl, md in GRID:
        m = fit(tr, va, "y_next", nl, md)
        rate, cnt = score(m, va_all)
        ev = evaluate(va_all.assign(risk_rate=rate), "risk_rate")
        grid.append({"num_leaves": nl, "min_data_in_leaf": md, "best_iter": m.best_iteration, "valid_poisson": m.best_score["valid_0"]["poisson"], **ev})
        print(f"격자 nl={nl} md={md} iter={m.best_iteration} prec@1000={ev['prec@1000']:.4f} lift@5%={ev['lift@5%']:.3f}", flush=True)
    grid = pd.DataFrame(grid)
    grid.round(4).to_csv(ROOT / "output/tables/risk_grid_all.csv", index=False)
    best = grid.sort_values("prec@1000", ascending=False).iloc[0]
    nl, md = int(best.num_leaves), int(best.min_data_in_leaf)

    # 2) 전체 + 부품별 최종(검증 기준) 모형
    shap_rows, cal_rows, spec = [], [], {"features": FEATS, "num_leaves": nl, "min_data_in_leaf": md, "models": {}}
    for name, target in TARGETS.items():
        m = fit(tr, va, target, nl, md)
        m.save_model(str(ROOT / f"output/models/risk_{name}.txt"))
        rate, cnt = score(m, va_all)
        v = va_all.assign(risk_rate=rate, risk_count=cnt, y=va_all[target])
        for col, lab in (("risk_rate", "위험률"), ("risk_count", "기대 신고 수")):
            ev = evaluate(v.assign(y_next=v.y), col)
            results.loc[f"LGB {name} {lab}"] = ev
        # 보정: 예측 합 = 실제 합
        vv = v[v.km_next > 0]
        pred = np.exp(m.predict(vv[FEATS], raw_score=True)) * vv.km_next
        scale = float(vv.y.sum() / pred.sum())
        vv = vv.assign(pred=pred * scale, dec=pd.qcut(vv.risk_rate, 10, labels=False, duplicates="drop"))
        g = vv.groupby("dec").agg(pred_rate=("pred", "sum"), y=("y", "sum"), km=("km_next", "sum"), n=("y", "size"))
        g["pred_per_1000km"] = g.pred_rate / g.km * 1000; g["actual_per_1000km"] = g.y / g.km * 1000
        cal_rows.append(g.reset_index().assign(model=name)[["model", "dec", "n", "pred_per_1000km", "actual_per_1000km"]])
        spec["models"][name] = {"file": f"risk_{name}.txt", "best_iteration": m.best_iteration, "calibration_scale": scale}
        # SHAP(기여도) — LightGBM 내장
        samp = vv.sample(min(200_000, len(vv)), random_state=42)
        contrib = m.predict(samp[FEATS], pred_contrib=True)[:, :-1]
        shap_rows.append(pd.DataFrame({"model": name, "feature": FEATS, "mean_abs_contrib": np.abs(contrib).mean(axis=0)}))
        print(f"{name}: iter={m.best_iteration} scale={scale:.3f} prec@1000 rate={results.loc[f'LGB {name} 위험률','prec@1000']:.4f} count={results.loc[f'LGB {name} 기대 신고 수','prec@1000']:.4f}", flush=True)
        # 부품 모형의 베이스라인(같은 라벨로)도 표에
        if name != "all":
            for bname, col in BASELINES[2:]:
                results.loc[f"{bname} [{name}]"] = evaluate(v.assign(y_next=v.y), col)
    results.round(4).to_csv(ROOT / "output/tables/risk_eval_valid.csv")
    pd.concat(shap_rows).round(5).to_csv(ROOT / "output/tables/risk_shap_valid.csv", index=False)
    pd.concat(cal_rows).round(4).to_csv(ROOT / "output/tables/risk_calibration_valid.csv", index=False)
    json.dump(spec, open(ROOT / "output/models/risk_spec_valid.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    pd.set_option("display.width", 220)
    print(results.round(3).to_string())
    s = pd.concat(shap_rows)
    print(s[s.model == "all"].sort_values("mean_abs_contrib", ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    main()
