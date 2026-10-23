"""신고 없이 손볼 자전거 모형: 정답을 '다음 주 실제 수리(공단 정비 + 수리점)'로 바꾼 자전거별 모형.

왜: A(maint_score_a.py)에서 지금 위험 모형은 최근 신고가 없는 자전거의 수리를 무작위만큼밖에 못 잡았다
(4.25% 대 4.11%). 공단 정비의 3분의 2는 신고 없이 이뤄지므로, 그 판단을 배우는 모형을 따로 만든다.
입력은 지금 위험 모형과 같은 운영 자료 변수라 정비 자료가 끊긴 뒤에도 점수를 낼 수 있다(정답은 학습에만 쓴다).

정해 둔 것(결정 로그 2026-10-23, 결과를 보기 전에 정함):
  잣대        최근 14일 신고 없는 자전거 가운데 주마다 상위 1,000대의 다음 주 수리 적중률, 검증 2025-07 ~ 12
  넘어야 할 값  단순 줄 세우기 최고값 4.78%(마지막 신고 이후 km 순)의 1.2배 = 5.74%
  위치 점검    위치 변수를 넣은 판과 뺀 판을 함께 채점한다. 수리 기록은 직원이 보러 간 자전거에서 생기므로
               모형이 자전거 상태가 아니라 직원 동선(자주 가는 구역)을 배웠을 수 있다. 뺀 판이 비슷하면 뺀 판을 쓴다
  설정        지금 위험 모형의 잎 수·최소 행 수를 그대로 쓴다(검증 구간을 보며 고르지 않는다). 정답이 있다·없다라
               이진 분류로 학습하고, 타지 않은 자전거도 수리되므로 주행거리 offset은 두지 않는다

위치 변수: 출발 대여소 군집 비중(sh_cl0 ~ 4), 대여당 재배치(reloc_per_trip. 트럭이 옮긴 기록이라 동선과 겹친다)
구간: 학습 2024-01 ~ 2025-06, 검증 2025-07 ~ 12. 테스트 구간은 읽지 않는다.

입력: data/processed/panel/bike_week.parquet, data/interim/maint/maint_jobs.parquet, data/processed/fail_std.parquet,
      output/models/risk_spec_valid.json(변수 목록과 설정)
출력: output/models/repair_withloc.txt, repair_noloc.txt
      output/tables/maint_model_valid.csv  대상 × 정답 × 점수별 precision@500·1000·2000, recall@1000, 잣대 통과 여부
      output/tables/maint_model_shap.csv   판별 변수별 평균 |기여|와 위치 변수 몫
      output/tables/maint_model_active.csv 이번 주 대여 여부로 나눈 점검. 상위 1,000대 가운데 이번 주 대여 0건인 자전거의 몫,
                                           이번 주 대여된 자전거만으로 다시 줄을 세운 적중률. 대여가 없던 자전거는 이미 수거돼
                                           거리에 없을 수 있어(추천해도 쓸모없음) 따로 본다(결과를 본 뒤 더한 점검, 2026-10-23)
실행: .venv/bin/python py/maint/maint_model.py   (10 ~ 20분)
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
from risk_eval import BASELINES, evaluate, load_panel   # noqa: E402
from risk_lgb import BASE, prep                          # noqa: E402
from maint_score_a import next_week_flag                 # noqa: E402

BAR = 0.0478 * 1.2          # 넘어야 할 값(5.74%)
LOC = ["sh_cl0", "sh_cl1", "sh_cl2", "sh_cl3", "sh_cl4", "reloc_per_trip"]


def labels(p):
    """다음 주 수리, 다음 주 신고 없던 수리(앞선 14일 안 같은 자전거 신고 없음), 다음 주 신고"""
    jobs = pd.read_parquet(ROOT / "data/interim/maint/maint_jobs.parquet")
    rep = jobs[jobs.kind.isin(["정비", "수리점"])][["kind", "bike_id", "done"]].sort_values("done")
    f = pd.read_parquet(ROOT / "data/processed/fail_std.parquet", columns=["bike", "reg_ts", "keep"])
    f = f[f.keep].assign(bike_id=lambda d: d.bike.astype("int64"), reg_ts=lambda d: d.reg_ts.astype("datetime64[ns]"))
    x = pd.merge_asof(rep, f[["bike_id", "reg_ts"]].drop_duplicates().sort_values("reg_ts"),
                      left_on="done", right_on="reg_ts", by="bike_id", direction="backward")
    quiet_job = ~((x.done - x.reg_ts).dt.total_seconds() / 86400).le(14).to_numpy()
    return {"수리": next_week_flag(p, rep), "신고 없던 수리": next_week_flag(p, rep[quiet_job]), "신고": (p.y_next > 0).astype(int).to_numpy()}


def fit(tr, va, feats, spec):
    params = {**BASE, "objective": "binary", "num_leaves": spec["num_leaves"], "min_data_in_leaf": spec["min_data_in_leaf"]}
    dtr = lgb.Dataset(tr[feats], label=tr.y_rep, categorical_feature=["bike_type"], free_raw_data=False)
    dva = lgb.Dataset(va[feats], label=va.y_rep, reference=dtr)
    return lgb.train(params, dtr, num_boost_round=5000, valid_sets=[dva], callbacks=[lgb.early_stopping(200, verbose=False)])


def main():
    spec = json.load(open(ROOT / "output/models/risk_spec_valid.json", encoding="utf-8"))
    feats = spec["features"]
    assert set(LOC) <= set(feats)
    p = prep(load_panel())
    y = labels(p)
    p["y_rep"] = y["수리"]
    tr, va = p[p.split == "train"], p[p.split == "valid"].copy()
    va["B0"] = np.random.default_rng(42).random(len(va))      # risk_eval.py와 같은 씨앗
    ys = {k: v[(p.split == "valid").to_numpy()] for k, v in y.items()}

    versions = {"위치 넣은 판": ("withloc", feats), "위치 뺀 판": ("noloc", [c for c in feats if c not in LOC])}
    shap = []
    for name, (key, fs) in versions.items():
        m = fit(tr, va, fs, spec)
        m.save_model(str(ROOT / f"output/models/repair_{key}.txt"))
        va[key] = m.predict(va[fs])
        samp = va.sample(200_000, random_state=42)
        c = np.abs(m.predict(samp[fs], pred_contrib=True)[:, :-1]).mean(axis=0)
        shap.append(pd.DataFrame({"판": name, "변수": fs, "평균 |기여|": c.round(5), "몫": (c / c.sum()).round(4), "위치 변수": [f in LOC for f in fs]}))
        print(f"{name}: 반복 {m.best_iteration}", flush=True)
    shap = pd.concat(shap)
    shap.to_csv(ROOT / "output/tables/maint_model_shap.csv", index=False)

    quiet = (va.days_since_rep.isna() | (va.days_since_rep > 14)).to_numpy()
    scores = [("새 모형(위치 넣은 판)", "withloc"), ("새 모형(위치 뺀 판)", "noloc"), ("지금 위험 모형", None)] + BASELINES
    risk = lgb.Booster(model_file=str(ROOT / "output/models/risk_all.txt"))
    va["risk_count"] = np.exp(risk.predict(va[feats], raw_score=True)) * va.km_4w.to_numpy() / 4
    rows = []
    for target, mask in (("최근 14일 신고 없음", quiet), ("전체", np.ones(len(va), bool))):
        for lab, yy in ys.items():
            d = va.assign(y_next=yy)[mask]
            for sname, col in scores:
                ev = evaluate(d, col or "risk_count")
                rows.append({"대상": target, "정답": lab, "점수": sname, **{k: round(ev[k], 4) for k in ("prec@500", "prec@1000", "prec@2000", "recall@1000")}})
    out = pd.DataFrame(rows)
    out["잣대 통과(5.74% 이상)"] = np.where((out["대상"] == "최근 14일 신고 없음") & (out["정답"] == "수리"), out["prec@1000"] >= round(BAR, 4), pd.NA)
    out.to_csv(ROOT / "output/tables/maint_model_valid.csv", index=False)

    # 이번 주 대여 여부로 나눠 보기(잣대 대상: 최근 14일 신고 없음, 정답: 다음 주 수리)
    q = va[quiet].assign(y_next=ys["수리"][quiet])
    act = []
    for sname, col in scores:
        col = col or "risk_count"
        top = q.groupby("wk", group_keys=False)[["n_trips", "y_next", col]].apply(lambda g: g.nlargest(1000, col))
        a = q[q.n_trips > 0]
        act.append({"점수": sname, "상위 1,000대 중 이번 주 대여 0건 몫": round((top.n_trips == 0).mean(), 3),
                    "적중률(전체 대상)": round(top.y_next.mean(), 4), "적중률(이번 주 대여된 자전거만으로 다시 세움)": round(evaluate(a, col)["prec@1000"], 4)})
    act = pd.DataFrame(act)
    act.loc[len(act)] = {"점수": "(참고) 대상 전체", "상위 1,000대 중 이번 주 대여 0건 몫": round((q.n_trips == 0).mean(), 3),
                         "적중률(전체 대상)": round(q.groupby("wk").y_next.mean().mean(), 4),
                         "적중률(이번 주 대여된 자전거만으로 다시 세움)": round(q[q.n_trips > 0].groupby("wk").y_next.mean().mean(), 4)}
    act.to_csv(ROOT / "output/tables/maint_model_active.csv", index=False)

    pd.set_option("display.width", 220)
    print(act.to_string(index=False))
    for target in ("최근 14일 신고 없음", "전체"):
        print("---", target); print(out[out["대상"] == target].pivot(index="점수", columns="정답", values="prec@1000").to_string())
    print(shap.groupby("판").apply(lambda d: d[d["위치 변수"]]["몫"].sum(), include_groups=False).rename("위치 변수 몫").to_string())
    print(shap[shap["판"] == "위치 넣은 판"].sort_values("몫", ascending=False).head(12).to_string(index=False))


if __name__ == "__main__":
    main()
