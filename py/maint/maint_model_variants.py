"""신고 없이 손볼 자전거 모형: 테스트를 열기 전에 다섯 가지 판(V1 ~ V5)을 검증 구간으로 견주고 하나를 골라 고정한다.

왜: 테스트(2026-01 ~ 06)는 한 번만 연다. 그 전에 손볼 수 있는 것을 정해 둔 목록만큼만 해 보고, 정해 둔 규칙으로 고른다
(결정 로그 2026-10-23). 시도를 늘릴수록 검증 성적이 우연히 부풀므로 목록 밖의 시도는 하지 않고, 해 본 것은 모두 표에 남긴다.

잣대(고르는 기준): 검증 2025-07 ~ 12에서 최근 14일 신고 없고 그 주에 대여된 자전거 가운데 주마다 상위 1,000대의
다음 주 실제 수리(공단 정비 + 수리점) 적중률. 추천을 그 주에 대여된 자전거로 한정하기로 한 결정을 따른다.
고르는 규칙: 가장 높은 판을 고르되, 지금 판(V0)보다 0.5%p 이상 높지 않으면 더 단순한 V0를 둔다.

판:
  V0  지금 판(maint_model.py의 위치 뺀 판). 학습 행 = 모든 자전거·주
  V1  학습 행을 그 주에 대여된 자전거로만
  V2  V1 + 자기 평소 대비 변화 변수 3개(지난 4주 평균 대비 이번 주 대여 수·주행거리, 지난 4주 중앙값 대비 속도). 지난 주까지만 쓴다
  V3  V2 + 학습 정답을 '다음 2주 안 수리'로(채점은 다음 주 그대로)
  V4  V2를 주마다 순위를 맞히는 순위 학습(LightGBM rank_xendcg, 주가 한 묶음)으로
  V5  V1 ~ V4 가운데 가장 나은 판에 잎 수 × 최소 행 수 9조합
위치 변수(출발 대여소 군집, 재배치)는 모든 판에서 뺀다. 정비 이력 변수는 운영에서 채울 수 없어 쓰지 않는다.

입력: data/processed/panel/bike_week.parquet, data/interim/maint/maint_jobs.parquet, data/processed/fail_std.parquet,
      output/models/risk_spec_valid.json, output/models/repair_noloc.txt(V0)
출력: output/tables/maint_variants_valid.csv  판별 잣대 값과 보조 지표
      output/models/repair_final.txt, repair_spec.json  고른 판(이미 있으면 다시 쓰지 않는다)
실행: .venv/bin/python py/maint/maint_model_variants.py   (20 ~ 40분)
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
from risk_eval import evaluate, load_panel   # noqa: E402
from risk_lgb import BASE, prep              # noqa: E402
from maint_model import LOC, labels          # noqa: E402

CHANGE = ["n_trips_ratio", "km_ratio", "speed_ratio"]
MARGIN = 0.005
SPEC = ROOT / "output/models/repair_spec.json"


def add_change(p):
    """자기 평소 대비 변화: 같은 자전거의 지난 4주(이번 주 제외) 평균·중앙값과 견준다. +1은 0으로 나누지 않으려고"""
    p = p.sort_values(["bike_id", "wk"]).copy()
    g = p.groupby("bike_id")
    for c, out in (("n_trips", "n_trips_ratio"), ("km", "km_ratio")):
        prev = g[c].shift(1)
        p[out] = p[c] / (prev.groupby(p.bike_id).rolling(4, min_periods=1).mean().reset_index(level=0, drop=True) + 1)
    sp = g["speed_med"].shift(1)
    p["speed_ratio"] = p.speed_med / sp.groupby(p.bike_id).rolling(4, min_periods=1).median().reset_index(level=0, drop=True)
    return p


def flag_weeks(p, jobs, offsets):
    """패널 행(자전거, 주)마다 offsets(1 = 다음 주, 2 = 다다음 주) 가운데 한 주라도 작업이 있었으면 1"""
    mon = jobs.done.dt.normalize() - pd.to_timedelta(jobs.done.dt.weekday.astype("int64"), unit="D")
    hit = pd.concat([pd.DataFrame({"bike_id": jobs.bike_id, "wk": mon - pd.Timedelta(days=7 * k)}) for k in offsets]).drop_duplicates()
    return p[["bike_id", "wk"]].merge(hit.assign(flag=1), on=["bike_id", "wk"], how="left").flag.fillna(0).astype(int).to_numpy()


def fit_binary(tr, va, feats, ylab, nl, md):
    params = {**BASE, "objective": "binary", "num_leaves": nl, "min_data_in_leaf": md}
    dtr = lgb.Dataset(tr[feats], label=tr[ylab], categorical_feature=["bike_type"], free_raw_data=False)
    dva = lgb.Dataset(va[feats], label=va[ylab], reference=dtr)
    return lgb.train(params, dtr, num_boost_round=5000, valid_sets=[dva], callbacks=[lgb.early_stopping(200, verbose=False)])


def chunked(d, size=5000, seed=42):
    """LightGBM 순위 학습은 한 묶음이 1만 행을 넘을 수 없다(한 주는 2만 ~ 3만 대). 주마다 무작위로 섞어 size대씩 나눈 묶음을 만든다.
    shortcut: 주 전체의 순위가 아니라 무작위 5천 대 묶음 안의 순위를 배운다. 묶음 제한이 풀리면 주 단위로 바꾼다"""
    r = np.random.default_rng(seed).random(len(d))
    d = d.assign(_r=r).sort_values(["wk", "_r"])
    d["_g"] = d.groupby("wk").cumcount() // size
    return d.sort_values(["wk", "_g"]), d.groupby(["wk", "_g"], sort=True).size().to_numpy()


def fit_rank(tr, va, feats, ylab, nl, md):
    """주마다 순위를 맞히는 학습(rank_xendcg). 묶음은 chunked()로 만든다"""
    (tr, gtr), (va, gva) = chunked(tr), chunked(va)
    params = {**BASE, "objective": "rank_xendcg", "metric": "ndcg", "eval_at": [1000], "num_leaves": nl, "min_data_in_leaf": md}
    dtr = lgb.Dataset(tr[feats], label=tr[ylab], group=gtr, categorical_feature=["bike_type"], free_raw_data=False)
    dva = lgb.Dataset(va[feats], label=va[ylab], group=gva, reference=dtr)
    return lgb.train(params, dtr, num_boost_round=5000, valid_sets=[dva], callbacks=[lgb.early_stopping(200, verbose=False)])


def main():
    if SPEC.exists():
        raise SystemExit(f"{SPEC.name}이 이미 있다 — 고정한 판은 다시 고르지 않는다")
    spec_risk = json.load(open(ROOT / "output/models/risk_spec_valid.json", encoding="utf-8"))
    base_feats = [c for c in spec_risk["features"] if c not in LOC]
    nl0, md0 = spec_risk["num_leaves"], spec_risk["min_data_in_leaf"]

    p = add_change(prep(load_panel()))
    jobs = pd.read_parquet(ROOT / "data/interim/maint/maint_jobs.parquet")
    rep = jobs[jobs.kind.isin(["정비", "수리점"])]
    p["y1"] = labels(p)["수리"]                 # 다음 주 수리
    p["y2"] = flag_weeks(p, rep, (1, 2))       # 다음 2주 안 수리(V3 학습용)
    tr, va = p[p.split == "train"], p[p.split == "valid"]
    tr_act, va_act = tr[tr.n_trips > 0], va[va.n_trips > 0]
    target = va[(va.days_since_rep.isna() | (va.days_since_rep > 14)) & (va.n_trips > 0)].copy()   # 잣대 대상

    def judge(name, model, feats, note):
        target["s"] = model.predict(target[feats])
        ev = evaluate(target.assign(y_next=target.y1), "s")
        row = {"판": name, "설명": note, "잣대: 상위 1,000대 다음 주 수리 적중률": round(ev["prec@1000"], 4),
               "prec@500": round(ev["prec@500"], 4), "prec@2000": round(ev["prec@2000"], 4), "recall@1000": round(ev["recall@1000"], 4),
               "반복": model.best_iteration}
        print(row, flush=True)
        return row

    rows, models = [], {}
    v0 = lgb.Booster(model_file=str(ROOT / "output/models/repair_noloc.txt"))
    v0.best_iteration = v0.current_iteration()
    rows.append(judge("V0", v0, base_feats, "지금 판(학습 행 = 모든 자전거·주)")); models["V0"] = (v0, base_feats, "binary", "y1", nl0, md0)
    f2 = base_feats + CHANGE
    for name, (fitter, feats, ylab, note) in {
        "V1": (fit_binary, base_feats, "y1", "학습 행 = 그 주 대여된 자전거"),
        "V2": (fit_binary, f2, "y1", "V1 + 평소 대비 변화 3개"),
        "V3": (fit_binary, f2, "y2", "V2 + 학습 정답 다음 2주 안 수리"),
        "V4": (fit_rank, f2, "y1", "V2를 순위 학습(rank_xendcg, 주를 무작위 5천 대 묶음으로 나눔)으로"),
    }.items():
        m = fitter(tr_act, va_act, feats, ylab, nl0, md0)
        rows.append(judge(name, m, feats, note)); models[name] = (m, feats, "rank" if fitter is fit_rank else "binary", ylab, nl0, md0)

    best14 = max(["V1", "V2", "V3", "V4"], key=lambda k: next(r for r in rows if r["판"] == k)["잣대: 상위 1,000대 다음 주 수리 적중률"])
    _, feats_b, kind_b, ylab_b, _, _ = models[best14]
    for nl in (31, 63, 127):
        for md in (200, 500, 2000):
            name = f"V5 {best14} nl={nl} md={md}"
            m = (fit_rank if kind_b == "rank" else fit_binary)(tr_act, va_act, feats_b, ylab_b, nl, md)
            rows.append(judge(name, m, feats_b, f"{best14}의 설정 바꾸기")); models[name] = (m, feats_b, kind_b, ylab_b, nl, md)

    tab = pd.DataFrame(rows)
    key = "잣대: 상위 1,000대 다음 주 수리 적중률"
    top = tab.sort_values(key, ascending=False).iloc[0]
    v0score = tab.loc[tab["판"] == "V0", key].iloc[0]
    chosen = top["판"] if top[key] >= v0score + MARGIN else "V0"
    tab["고름"] = tab["판"] == chosen
    tab.to_csv(ROOT / "output/tables/maint_variants_valid.csv", index=False)

    m, feats, kind, ylab, nl, md = models[chosen]
    m.save_model(str(ROOT / "output/models/repair_final.txt"))
    spec = {"fixed_on": pd.Timestamp.today().strftime("%Y-%m-%d"), "variant": chosen, "model": "repair_final.txt",
            "label": "다음 주(월요일 시작) 실제 수리(공단 정비 또는 우리동네 자전거방 작업) 여부", "train_label": ylab,
            "objective": kind, "features": feats, "change_features": "n_trips_ratio·km_ratio = 이번 주 ÷ (지난 4주 평균 + 1), speed_ratio = 이번 주 속도 중앙값 ÷ 지난 4주 속도 중앙값의 중앙값",
            "params": {"num_leaves": nl, "min_data_in_leaf": md, "best_iteration": int(m.best_iteration)},
            "train_rows": "V0이면 모든 자전거·주, 그 밖에는 그 주에 대여된 자전거·주", "split": {"train": "2024-01-01 ~ 2025-06-30", "valid": "2025-07-01 ~ 2025-12-31", "test": "2026-01-01 ~ 2026-06-30"},
            "target_rows": "최근 14일 신고 없고 그 주에 대여된 자전거", "valid_prec1000": float(tab.loc[tab["판"] == chosen, key].iloc[0]),
            "test_rule": "같은 대상·잣대로 2026 상반기를 한 번 채점. 같은 구간의 단순 줄 세우기(B0 ~ B4) 최고값의 1.2배 이상이면 통과. 결과를 보고 고치지 않는다"}
    json.dump(spec, open(SPEC, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    pd.set_option("display.width", 220)
    print(tab.to_string(index=False)); print("고른 판:", chosen)


if __name__ == "__main__":
    main()
