"""신고 없이 손볼 자전거 모형(위치 뺀 판)이 '그 주에 대여된 자전거'에서 무엇으로 점수를 올리는지 본다.

왜: 대여되지 않은 자전거는 이미 수거된 것인지 고장 나 서 있는 것인지 가를 수 없어서, 추천은 그 주에 대여된
(지금 거리에 있는) 자전거로 하는 것이 목적에 맞다. 기사에게 왜 이 자전거를 가져가는지 설명하고, 같은 원인을
줄이려면 그 자전거들에서 점수를 올리는 변수와 방향을 알아야 한다.

보는 것(검증 2025-07 ~ 12, 최근 14일 신고 없고 그 주에 대여된 자전거):
  기여     변수별 평균 |SHAP|와 몫(LightGBM pred_contrib)
  방향     변수 값과 그 변수의 SHAP의 순위 상관. 양수면 값이 클수록 점수를 올린다
  상위와 나머지  주마다 점수 상위 1,000대와 나머지의 변수 중앙값
  구간별 실제 수리  기여 상위 변수마다 값을 다섯 구간으로 나눠 다음 주 실제 수리 비율(모형과 상관없는 자료 그대로의 관계)

입력: output/models/repair_noloc.txt, data/processed/panel/bike_week.parquet 외(maint_model.py와 같음)
출력: output/tables/maint_explain_feat.csv     변수별 기여·방향·상위/나머지 중앙값
      output/tables/maint_explain_quintile.csv 기여 상위 변수의 다섯 구간별 다음 주 수리 비율
실행: .venv/bin/python py/maint/maint_model_explain.py
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "py/risk"))
sys.path.append(str(ROOT / "py/maint"))
from risk_eval import load_panel     # noqa: E402
from risk_lgb import prep            # noqa: E402
from maint_model import LOC, labels  # noqa: E402


def main():
    feats = [c for c in json.load(open(ROOT / "output/models/risk_spec_valid.json", encoding="utf-8"))["features"] if c not in LOC]
    p = prep(load_panel())
    p["y_rep"] = labels(p)["수리"]
    va = p[p.split == "valid"]
    a = va[(va.days_since_rep.isna() | (va.days_since_rep > 14)) & (va.n_trips > 0)].copy()
    m = lgb.Booster(model_file=str(ROOT / "output/models/repair_noloc.txt"))
    a["s"] = m.predict(a[feats])
    a["top"] = a.groupby("wk").s.rank(ascending=False, method="first") <= 1000

    samp = a.sample(min(200_000, len(a)), random_state=42)
    contrib = pd.DataFrame(m.predict(samp[feats], pred_contrib=True)[:, :-1], columns=feats, index=samp.index)
    num = [f for f in feats if f != "bike_type"]
    rows = []
    for f in feats:
        mean_abs = contrib[f].abs().mean()
        direction = samp[f].rank().corr(contrib[f].rank()) if f in num else np.nan
        rows.append({"변수": f, "평균 |기여|": round(mean_abs, 5), "방향(값↔기여 순위상관)": round(direction, 2) if pd.notna(direction) else None,
                     "상위 1,000대 중앙값": a.loc[a.top, f].median() if f in num else None, "나머지 중앙값": a.loc[~a.top, f].median() if f in num else None})
    feat = pd.DataFrame(rows)
    feat["몫"] = (feat["평균 |기여|"] / feat["평균 |기여|"].sum()).round(4)
    feat = feat.sort_values("몫", ascending=False)
    feat.to_csv(ROOT / "output/tables/maint_explain_feat.csv", index=False)

    qrows = []
    for f in [x for x in feat["변수"] if x in num][:10]:
        q = pd.qcut(a[f].rank(method="first"), 5, labels=["1(가장 낮음)", "2", "3", "4", "5(가장 높음)"])
        g = a.groupby(q, observed=True).agg(구간_최소=(f, "min"), 구간_최대=(f, "max"), 다음주_수리비율=("y_rep", "mean"), 자전거주=("y_rep", "size"))
        qrows.append(g.reset_index(names="구간").assign(변수=f))
    quint = pd.concat(qrows)[["변수", "구간", "구간_최소", "구간_최대", "다음주_수리비율", "자전거주"]].round(4)
    quint.to_csv(ROOT / "output/tables/maint_explain_quintile.csv", index=False)
    if "bike_type" in feats:
        print(a.groupby("bike_type", observed=True).y_rep.agg(["mean", "size"]).round(4).to_string())
    pd.set_option("display.width", 220)
    print(f"대상 자전거·주 {len(a):,}, 다음 주 수리 비율 {a.y_rep.mean():.4f}, 상위 1,000대 {a[a.top].y_rep.mean():.4f}")
    print(feat.head(14).to_string(index=False)); print(quint.to_string(index=False))


if __name__ == "__main__":
    main()
