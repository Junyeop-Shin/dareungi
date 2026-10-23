"""신고 없이 손볼 자전거 모형의 테스트(2026-01 ~ 06)를 한 번 채점한다. 사람이 승인한 뒤 한 번만 돌린다.

고정한 판(output/models/repair_spec.json, repair_final.txt)을 그대로 쓰고, 결과를 보고 모형을 고치지 않는다.
대상과 잣대는 검증과 같다: 최근 14일 신고 없고 그 주에 대여된 자전거 가운데 주마다 상위 1,000대의 다음 주 실제 수리
(공단 정비 + 우리동네 자전거방) 적중률. 통과: 같은 구간 단순 줄 세우기(B0 ~ B4) 최고값의 1.2배 이상.
참고로 지금 위험 모형(신고로 학습, risk_all_final.txt)도 같은 대상·정답으로 채점한다.

2026 상반기의 수리점 기록은 3월 13일부터 있다(사업 운영 기간). 달별 적중률을 함께 낸다.

입력: data/processed/panel/bike_week.parquet(테스트 행 포함), output/models/repair_spec.json, repair_final.txt, risk_all_final.txt,
      data/interim/maint/maint_jobs.parquet, data/processed/fail_std.parquet
출력: output/tables/maint_model_test.csv        점수별 precision@500·1000·2000, recall@1000, 통과 여부
      output/tables/maint_model_test_month.csv  달별 상위 1,000대 적중률(모형, 최고 단순 점수, 무작위)
실행: .venv/bin/python py/maint/maint_model_test.py --test   (결과 파일이 있으면 다시 돌리지 않는다)
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
from risk_lgb import prep                                # noqa: E402
from maint_model import labels                           # noqa: E402
from maint_model_variants import add_change              # noqa: E402

OUT = ROOT / "output/tables/maint_model_test.csv"


def main():
    if "--test" not in sys.argv:
        raise SystemExit("테스트 채점은 사람이 승인한 뒤 --test로 한 번만 돌린다")
    if OUT.exists():
        raise SystemExit(f"{OUT.name}이 이미 있다 — 테스트는 다시 돌리지 않는다")
    spec = json.load(open(ROOT / "output/models/repair_spec.json", encoding="utf-8"))
    print("테스트 구간을 엽니다 — 사람이 승인한 한 번뿐인 실행이어야 합니다.", flush=True)
    p = add_change(prep(load_panel(include_test=True)))
    p["y1"] = labels(p)["수리"]
    te = p[(p.split == "test") & (p.days_since_rep.isna() | (p.days_since_rep > 14)) & (p.n_trips > 0)].copy()
    te = te[te.wk <= pd.Timestamp("2026-06-22")]           # 다음 주가 6월 안에 끝나는 주까지(정비 자료가 6월 30일에 끝난다)
    te["model"] = lgb.Booster(model_file=str(ROOT / "output/models" / spec["model"])).predict(te[spec["features"]])
    risk_feats = json.load(open(ROOT / "output/models/risk_spec.json", encoding="utf-8"))["features"]
    te["risk_count"] = np.exp(lgb.Booster(model_file=str(ROOT / "output/models/risk_all_final.txt")).predict(te[risk_feats], raw_score=True)) * te.km_4w.to_numpy() / 4
    te["B0"] = np.random.default_rng(42).random(len(te))
    d = te.assign(y_next=te.y1)

    rows = []
    for name, col in [("새 모형(고정한 판)", "model"), ("지금 위험 모형", "risk_count")] + BASELINES:
        ev = evaluate(d, col)
        rows.append({"점수": name, **{k: round(ev[k], 4) for k in ("prec@500", "prec@1000", "prec@2000", "recall@1000")}})
    out = pd.DataFrame(rows)
    best_base = out[out["점수"].isin([n for n, _ in BASELINES])].sort_values("prec@1000", ascending=False).iloc[0]
    bar = round(best_base["prec@1000"] * 1.2, 4)
    model_v = out.loc[out["점수"] == "새 모형(고정한 판)", "prec@1000"].iloc[0]
    out["통과선(단순 점수 최고 × 1.2)"] = bar
    out["통과"] = np.where(out["점수"] == "새 모형(고정한 판)", model_v >= bar, pd.NA)
    out.to_csv(OUT, index=False)

    mo = []
    for m, g in d.groupby(d.wk.dt.to_period("M")):
        r = {"달": str(m), "주 수": g.wk.nunique(), "다음 주 수리 비율": round(g.y1.mean(), 4)}
        for name, col in (("새 모형", "model"), (best_base["점수"], dict(BASELINES).get(best_base["점수"])), ("무작위", "B0")):
            r[name] = round(evaluate(g, col)["prec@1000"], 4)
        mo.append(r)
    pd.DataFrame(mo).to_csv(ROOT / "output/tables/maint_model_test_month.csv", index=False)
    pd.set_option("display.width", 200)
    print(out.to_string(index=False)); print(pd.DataFrame(mo).to_string(index=False))
    print(f"단순 점수 최고 {best_base['점수']} {best_base['prec@1000']:.4f} → 통과선 {bar:.4f}, 새 모형 {model_v:.4f}")


if __name__ == "__main__":
    main()
