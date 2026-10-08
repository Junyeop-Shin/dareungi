"""자전거 위험 모형의 평가 함수와 베이스라인(런북 06, 2B-2).

자전거×주 패널에서 "다음 주에 신고될 자전거를 얼마나 잘 골라내는가"를 잰다.
  precision@K  주마다 점수 상위 K대 중 다음 주에 신고된 비율. K는 주당 점검 여력(500~2,000대)
  recall@K     그 주 신고된 자전거 중 상위 K대에 든 비율
  lift@f       점수 상위 f(5%, 20%)의 1km당 신고율 / 전체 1km당 신고율. 다음 주에 탄 자전거만 본다.
               이용량 효과를 뺀 위험률 기준
베이스라인은 B0 무작위, B1 최근 4주 km, B2 마지막 신고 이후 km, B3 최근 13주 신고, B4 최근 2주 헛대여다.

구간은 학습 2024-01-01 ~ 2025-06-30, 검증 2025-07-01 ~ 12-31, 테스트 2026-01-01 ~.
load_panel()은 기본으로 테스트 행을 빼고 돌려준다. 테스트 채점은 risk_freeze.py --test에서 한 번만 한다.

입력: data/processed/panel/bike_week.parquet
출력: output/tables/risk_eval_valid.csv (베이스라인 행. risk_lgb.py가 모형 행을 더한다), risk_split_info.csv
실행: .venv/bin/python py/risk_eval.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPLITS = {"train": ("2024-01-01", "2025-06-30"), "valid": ("2025-07-01", "2025-12-31"), "test": ("2026-01-01", "2026-12-31")}
KS = (500, 1000, 2000)


def load_panel(include_test=False):
    """패널을 읽고 split 열(train/valid/test)을 붙인다. 테스트 행은 include_test=True일 때만 포함"""
    p = pd.read_parquet(ROOT / "data/processed/panel/bike_week.parquet")
    p["wk"] = pd.to_datetime(p.wk)
    p["split"] = np.select([p.wk.between(*SPLITS["train"]), p.wk.between(*SPLITS["valid"])], ["train", "valid"], "test")
    return p if include_test else p[p.split != "test"].copy()


def at_k(df, score, K):
    rows = []
    for _, g in df.groupby("wk"):
        top = g.nlargest(K, score)
        hit = int((top.y_next > 0).sum())
        rows.append({"precision": hit / K, "recall": hit / max(int((g.y_next > 0).sum()), 1)})
    return pd.DataFrame(rows).mean()


def rate_lift(df, score, frac):
    vals = []
    for _, g in df[df.km_next > 0].groupby("wk"):
        top = g.nlargest(max(int(len(g) * frac), 1), score)
        vals.append((top.y_next.sum() / top.km_next.sum()) / (g.y_next.sum() / g.km_next.sum()))
    return float(np.mean(vals))


def evaluate(df, score):
    """점수 열 하나에 대해 lift·precision·recall을 모두 계산한다. 결측 점수는 꼴찌로 둔다"""
    d = df.assign(**{score: df[score].fillna(-np.inf)})
    r = {"lift@5%": rate_lift(d, score, 0.05), "lift@20%": rate_lift(d, score, 0.20)}
    for K in KS:
        s = at_k(d, score, K)
        r[f"prec@{K}"], r[f"recall@{K}"] = float(s["precision"]), float(s["recall"])
    return r


BASELINES = [("B0 무작위", "B0"), ("B1 노출만(km_4w)", "km_4w"), ("B2 마지막 신고 이후 km", "km_since_rep"),
             ("B3 최근 13주 신고", "rep_13w"), ("B4 최근 2주 헛대여", "sham_2w")]


def main():
    p = load_panel()
    va = p[p.split == "valid"].copy()
    va["B0"] = np.random.default_rng(42).random(len(va))
    base = {name: evaluate(va, col) for name, col in BASELINES}
    tab = pd.DataFrame(base).T
    tab.index.name = "모형"
    tab.round(4).to_csv(ROOT / "output/tables/risk_eval_valid.csv")
    info = pd.DataFrame([{"split": s, "rows": int((p.split == s).sum()), "bikes": int(p[p.split == s].bike_id.nunique()),
                          "weeks": int(p[p.split == s].wk.nunique()), "y_pos": float((p[p.split == s].y_next > 0).mean()),
                          "y_pos_per_week": float(p[p.split == s].groupby("wk").apply(lambda g: (g.y_next > 0).sum()).mean())} for s in ("train", "valid")])
    info.round(4).to_csv(ROOT / "output/tables/risk_split_info.csv", index=False)
    pd.set_option("display.width", 200)
    print(info.to_string(index=False)); print(tab.round(3).to_string())


if __name__ == "__main__":
    main()
