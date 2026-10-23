"""A: 지금의 자전거별 점검 순위가 실제 수리를 얼마나 잡는지 채점한다(검증 구간 2025-07 ~ 12).

왜: 자전거별 모형은 "다음 주 이용자 신고"를 정답으로 학습하고 채점했다. 신고는 실제 고장의 일부만 잡고(2025년에
신고 성향이 내려간 것으로 추정), 공단 정비의 3분의 2는 신고 없이 이뤄진다(docs/notes/08). 같은 점수를
"다음 주에 실제로 손봤나"로 다시 채점하면, 이 순위가 정비 현장의 판단과도 이어지는지 알 수 있다.
모형과 설정은 바꾸지 않는다. 새 모형을 만들면 이 표가 비교 기준이 된다.

정답(다음 주 = 그 주 월요일 + 7일부터 7일):
  수리      다음 주에 공단 정비 또는 수리점(우리동네 자전거방) 작업이 있었나
  공단 정비 다음 주에 공단 정비 작업이 있었나
  신고 없던 수리  다음 주 수리 가운데 앞선 14일 안에 같은 자전거의 신고가 없던 작업이 있었나(신고 뒤 기간 14일, 사람이 정함)
  신고      지금 모형의 정답(다음 주 신고 수 > 0). 같은 행으로 다시 계산해 견준다
점검 기록은 2025년 것을 받지 못해 정답에 쓰지 않는다.

두 번째 채점: 그 주 끝에서 최근 14일 안에 신고가 없던 자전거만 놓고 다시 줄을 세운다. 대시보드의 '점검 권장'이
최근 신고 없는 자전거를 대상으로 하기 때문이고, '신고 없던 수리'는 정의상 최근 신고된 자전거를 빼므로 전체로
채점하면 최근 신고를 높이 치는 점수가 불리하다.

점수는 검증 단계 모형(output/models/risk_all.txt, 학습 구간으로 학습하고 검증 구간으로 조기 종료)으로 낸다.
테스트 구간(2026-01 ~ 06)은 읽지 않는다(load_panel 기본값).

입력: data/processed/panel/bike_week.parquet, output/models/risk_all.txt, data/interim/maint/maint_jobs.parquet, data/processed/fail_std.parquet
출력: output/tables/maint_a_valid.csv   대상(전체 / 최근 14일 신고 없음) × 점수 × 정답별 precision@500·1000·2000, recall@1000, lift@5%
      output/tables/maint_a_base.csv    정답별 주당 양성 자전거 수와 비율(무작위로 골랐을 때의 적중률)
실행: .venv/bin/python py/maint/maint_score_a.py
"""
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "py/risk"))
from risk_eval import BASELINES, evaluate, load_panel   # noqa: E402
from risk_lgb import prep, score                         # noqa: E402


def next_week_flag(p, jobs):
    """패널 행(자전거, 주 월요일)마다 다음 주(월요일 + 7 ~ 13일)에 작업이 있었으면 1"""
    wk = jobs.assign(wk=(jobs.done.dt.normalize() - pd.to_timedelta(jobs.done.dt.weekday.astype("int64"), unit="D")) - pd.Timedelta(days=7))
    hit = wk[["bike_id", "wk"]].drop_duplicates().assign(flag=1)      # 작업 주의 앞 주 = 그 작업을 '다음 주'로 보는 패널 행
    return p[["bike_id", "wk"]].merge(hit, on=["bike_id", "wk"], how="left").flag.fillna(0).astype(int).to_numpy()


def main():
    p = prep(load_panel())
    va = p[p.split == "valid"].copy()
    va["B0"] = np.random.default_rng(42).random(len(va))                # risk_eval.py와 같은 씨앗
    model = lgb.Booster(model_file=str(ROOT / "output/models/risk_all.txt"))
    va["risk_rate"], va["risk_count"] = score(model, va)

    jobs = pd.read_parquet(ROOT / "data/interim/maint/maint_jobs.parquet")
    rep = jobs[jobs.kind.isin(["정비", "수리점"])][["kind", "bike_id", "done"]].sort_values("done")
    f = pd.read_parquet(ROOT / "data/processed/fail_std.parquet", columns=["bike", "reg_ts", "keep"])
    f = f[f.keep].assign(bike_id=lambda d: d.bike.astype("int64"), reg_ts=lambda d: d.reg_ts.astype("datetime64[ns]"))
    x = pd.merge_asof(rep, f[["bike_id", "reg_ts"]].drop_duplicates().sort_values("reg_ts"),
                      left_on="done", right_on="reg_ts", by="bike_id", direction="backward")
    rep = rep.assign(after_report=((x.done - x.reg_ts).dt.total_seconds() / 86400).le(14).to_numpy())

    labels = {"신고(지금 정답)": (va.y_next > 0).astype(int).to_numpy(),
              "수리(공단 정비+수리점)": next_week_flag(va, rep),
              "공단 정비": next_week_flag(va, rep[rep.kind == "정비"]),
              "신고 없던 수리": next_week_flag(va, rep[~rep.after_report])}
    scores = [("모형 기대 신고 수", "risk_count"), ("모형 위험률", "risk_rate")] + BASELINES

    quiet = (va.days_since_rep.isna() | (va.days_since_rep > 14)).to_numpy()   # 그 주 끝에서 최근 14일 안에 신고 없음
    rows, base = [], []
    for target, mask in (("전체", np.ones(len(va), bool)), ("최근 14일 신고 없음", quiet)):
        for lab, y in labels.items():
            d = va.assign(y_next=y)[mask]
            pos = d.groupby("wk").y_next.agg(["sum", "size"])
            base.append({"대상": target, "정답": lab, "주당 양성 자전거": round(pos["sum"].mean(), 1), "주당 자전거": round(pos["size"].mean(), 1),
                         "양성 비율(무작위 적중률)": round((pos["sum"] / pos["size"]).mean(), 4)})
            for name, col in scores:
                ev = evaluate(d, col)
                rows.append({"대상": target, "정답": lab, "점수": name, **{k: round(ev[k], 4) for k in ("prec@500", "prec@1000", "prec@2000", "recall@1000", "lift@5%")}})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/tables/maint_a_valid.csv", index=False)
    pd.DataFrame(base).to_csv(ROOT / "output/tables/maint_a_base.csv", index=False)
    pd.set_option("display.width", 200)
    print(pd.DataFrame(base).to_string(index=False))
    for target in ("전체", "최근 14일 신고 없음"):
        print("---", target); print(out[out["대상"] == target].pivot(index="점수", columns="정답", values="prec@1000").round(4).to_string())


if __name__ == "__main__":
    main()
