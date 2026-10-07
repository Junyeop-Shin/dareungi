"""선택 → 고정 → 테스트 — 단계 2B-6 (런북 06)

[무엇을]
  (1) 고정: 검증으로 고른 설정(설명변수, 하이퍼파라미터, 모형별 반복 수, 보정 배율, 등급 백분위, 이유 문구)을
      output/models/risk_spec.json 에 적는다. **테스트 전에 깃에 커밋한다.**
  (2) 테스트(--test): 사람이 승인한 뒤 **한 번만**. 학습 + 검증(2024-01 ~ 2025-12)으로 다시 학습하고(반복 수 = 검증에서 정한 값),
      2026-01 ~ 06 테스트 행을 채점해 베이스라인과 나란히 risk_eval_test.csv 에 저장한다. 결과를 보고 설정을 바꾸지 않는다.
[입력]  output/models/risk_spec_valid.json (py/risk_lgb.py), data/processed/panel/bike_week.parquet
[출력]  output/models/risk_spec.json, (--test) output/models/risk_{name}_final.txt, output/tables/risk_eval_test.csv,
        risk_calibration_test.csv, output/models/risk_scores_test.parquet
[실행]  .venv/bin/python py/risk_freeze.py           # 고정만
        .venv/bin/python py/risk_freeze.py --test    # 테스트(승인 뒤 한 번)
"""
import json
import sys
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from risk_eval import BASELINES, evaluate, load_panel   # noqa: E402
from risk_lgb import BASE, FEATS, TARGETS, prep, score   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "output/models/risk_spec.json"
REASONS = {
    "km_since_rep": "마지막 신고 이후 주행거리가 깁니다", "trips_since_rep": "마지막 신고 이후 대여가 많았습니다",
    "climb_since_rep": "오르막 주행이 많았습니다", "sh_wet": "비 온 뒤 젖은 노면 주행이 많았습니다", "sh_rain": "비 오는 중 주행이 많았습니다",
    "sham_2w": "최근 빌리자마자 반납된 기록이 있습니다", "rep_13w": "최근 3개월 안에 신고된 적이 있습니다",
    "age_days": "도입된 지 오래된 자전거입니다", "km_total": "누적 주행거리가 깁니다", "reloc_per_trip": "재배치 이동이 잦았습니다",
    "freeze_days_since_rep": "얼었다 녹은 날이 많았습니다", "rain_days_since_rep": "비 온 날이 많았습니다",
    "speed_med": "주행 속도 패턴이 평소와 다릅니다", "last_repaired": "직전 신고 뒤 수거·정비 기록이 없습니다",
}


def freeze():
    v = json.load(open(ROOT / "output/models/risk_spec_valid.json", encoding="utf-8"))
    spec = {
        "version": str(date.today()), "fixed_on": str(date.today()),
        "label": "다음 주(월요일 시작) 이용자 고장신고 건수(48시간 중복 병합). 실제 고장보다 적게 잡힌다(2025년부터 신고 성향 약 30% 하락)",
        "split": {"train": "2024-01-01 ~ 2025-06-30", "valid": "2025-07-01 ~ 2025-12-31", "test": "2026-01-01 ~ 2026-06-30"},
        "features": v["features"], "params": {**BASE, "num_leaves": v["num_leaves"], "min_data_in_leaf": v["min_data_in_leaf"]},
        "offset": "log(다음 주 km). 예측은 raw_score(1km당 신고율의 로그)",
        "scores": {"risk_rate": "exp(raw) × 1000 = 1,000km당 기대 신고(점검 우선순위 기본값)", "risk_count": "exp(raw) × 최근 4주 평균 km = 기대 신고 수(수거 관점)"},
        "models": {k: {"file": f"risk_{k}_final.txt", "best_iteration": m["best_iteration"], "calibration_scale": m["calibration_scale"]} for k, m in v["models"].items()},
        "primary_metric": "precision@1000 (보조 500·2000, lift@5%)", "best_baseline_valid": {"name": "B3 최근 13주 신고", "prec@1000": 0.082, "lift@5%": 1.45},
        "grade_by_weekly_percentile": {"점검 권장": 0.95, "주의": 0.80, "보통": 0.50},
        "rank_rule": "최근 4주 주행이 0인 자전거는 위험률 순위에서 제외(점검해도 신고가 날 수 없음). 기대 신고 수 순위는 전체",
        "alert_rule": "같은 대여소 반납 2 ~ 3분 헛대여가 연속 2회 → 경보(24시간 안 중복 제외). 검증 적중 46%(7일), 기준선의 5.2배",
        "reason_phrases": REASONS,
        "label_note": "이용자 고장신고 기준. 실제 고장보다 적게 잡힌다. 안전을 보증하지 않는다",
    }
    json.dump(spec, open(SPEC, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("고정:", SPEC)
    return spec


def test(spec):
    print("테스트 구간을 엽니다 — 사람의 승인을 받은 한 번뿐인 실행이어야 합니다.", flush=True)
    p = prep(load_panel(include_test=True))
    tv = p[p.split.isin(["train", "valid"]) & (p.km_next > 0)]
    te = p[p.split == "test"]
    results = pd.DataFrame()
    te_scores = te[["bike_id", "wk"]].copy()
    cal_rows = []
    for name, target in TARGETS.items():
        m = spec["models"][name]
        dtr = lgb.Dataset(tv[FEATS], label=tv[target], init_score=np.log(tv.km_next), categorical_feature=["bike_type"])
        model = lgb.train(spec["params"], dtr, num_boost_round=m["best_iteration"])
        model.save_model(str(ROOT / "output/models" / m["file"]))
        rate, cnt = score(model, te)
        v = te.assign(risk_rate=rate, risk_count=cnt, y=te[target])
        te_scores[f"rate_{name}"], te_scores[f"count_{name}"] = rate, cnt
        for col, lab in (("risk_rate", "위험률"), ("risk_count", "기대 신고 수")):
            results.loc[f"LGB {name} {lab}", list(evaluate(v.assign(y_next=v.y), col))] = pd.Series(evaluate(v.assign(y_next=v.y), col))
        # 위험률 순위에서 최근 4주 주행 0인 자전거 제외한 판
        v2 = v.assign(risk_rate=np.where(v.km_4w > 0, v.risk_rate, -np.inf))
        results.loc[f"LGB {name} 위험률(4주 주행 있는 자전거만)", list(evaluate(v2.assign(y_next=v2.y), "risk_rate"))] = pd.Series(evaluate(v2.assign(y_next=v2.y), "risk_rate"))
        bl = BASELINES if name == "all" else BASELINES[1:]
        if name == "all":
            v["B0"] = np.random.default_rng(42).random(len(v))
        for bname, bcol in bl:
            results.loc[f"{bname}" + ("" if name == "all" else f" [{name}]"), list(evaluate(v.assign(y_next=v.y), bcol))] = pd.Series(evaluate(v.assign(y_next=v.y), bcol))
        vv = v[v.km_next > 0]
        pred = np.exp(model.predict(vv[FEATS], raw_score=True)) * vv.km_next * m["calibration_scale"]
        vv = vv.assign(pred=pred, dec=pd.qcut(vv.risk_rate, 10, labels=False, duplicates="drop"))
        g = vv.groupby("dec").agg(pred=("pred", "sum"), y=("y", "sum"), km=("km_next", "sum"), n=("y", "size")).reset_index()
        g["pred_per_1000km"], g["actual_per_1000km"] = g.pred / g.km * 1000, g.y / g.km * 1000
        g["model"] = name; cal_rows.append(g)
        print(f"{name}: 보정 뒤 예측 합/실제 합 = {pred.sum() / vv.y.sum():.3f}", flush=True)
    results.round(4).to_csv(ROOT / "output/tables/risk_eval_test.csv")
    pd.concat(cal_rows).round(4).to_csv(ROOT / "output/tables/risk_calibration_test.csv", index=False)
    te_scores.to_parquet(ROOT / "output/models/risk_scores_test.parquet", index=False)
    pd.set_option("display.width", 220)
    print(results.round(3).to_string())


if __name__ == "__main__":
    spec = freeze()
    if "--test" in sys.argv:
        test(spec)
