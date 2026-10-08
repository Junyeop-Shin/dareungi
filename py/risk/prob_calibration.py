"""'다음 주에 한 번이라도 신고될 확률'을 화면에 %로 보여 줘도 되는지 확인한다(예측 확률 vs 실제 비율).

확률 = 1 − exp(−기대 신고 수). 기대 신고 수 = exp(raw) × 보정 배율 × 최근 4주 평균 km(weekly.py와 같은 식)이고,
포아송 가정에서 다음 주 신고가 1건 이상일 확률이다. 예측 확률 10분위(상위는 더 잘게)마다 평균 예측 확률과 실제로
다음 주에 신고된 비율을 비교한다. 지금 등급(위험률 백분위)별 실제 신고 비율, 위험률 순위와 확률 순위의 주별
precision@1000도 같이 본다.

  - 운영 확인: 최종 모형(risk_spec.json, *_final)을 테스트 뒤 주(2026-07-06 ~ 09-21, 다음 주까지 집계 완료)에 적용.
    운영 DB panel(API 수집분으로 만든 패널)을 쓴다. 모형이 처음 보는 자료다
  - 참고: 검증 단계 모형(risk_spec_valid.json, risk_all.txt)을 2025-07 ~ 12에 적용. 보정 배율을 이 구간에서 정해서
    전체 수준은 맞게 나온다(구간별 모양만 참고)

입력: ops.duckdb panel·bike_info, data/processed/panel/bike_week.parquet, output/models/risk_spec*.json, risk_all*.txt
출력: output/tables/rb_19_prob_calibration.csv, rb_19_prob_by_grade.csv(지금 등급과 확률 기준 등급)
실행: .venv/bin/python py/risk/prob_calibration.py
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from common import connect               # noqa: E402
from risk_eval import load_panel         # noqa: E402
from risk_lgb import prep                # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CUTS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0]


def scores(df, spec_file):
    spec = json.load(open(ROOT / "output/models" / spec_file, encoding="utf-8"))
    df = prep(df)
    df["bike_type"] = pd.Categorical(df.bike_type.astype(str), categories=["새싹자전거", "일반자전거"])
    m = spec["models"]["all"]
    raw = lgb.Booster(model_file=str(ROOT / "output/models" / m["file"])).predict(df[spec["features"]], raw_score=True)
    df["rate"] = np.exp(raw) * 1000 * m["calibration_scale"]
    df["expected"] = np.exp(raw) * m["calibration_scale"] * df.km_4w / 4
    df["prob"] = 1 - np.exp(-df.expected)
    ranked = df.km_4w > 0
    df["pct"] = df.groupby("wk").rate.rank(pct=True).where(ranked)
    df["grade"] = np.select([~ranked, df.pct >= 0.95, df.pct >= 0.8, df.pct >= 0.5], ["산정 제외", "점검 권장", "주의", "보통"], "낮음")
    pp = df.groupby("wk").prob.rank(pct=True)                    # 같은 백분위 기준을 확률에 적용한 등급(비교용)
    df["grade_prob"] = np.select([pp >= 0.95, pp >= 0.8, pp >= 0.5], ["점검 권장", "주의", "보통"], "낮음")
    df["hit"] = df.y_next > 0
    return df


def live_panel():
    con = connect()
    d = con.execute("""SELECT p.*, b.bike_type, b.age_known, CASE WHEN b.age_known THEN date_diff('day', CAST(b.first_dt AS DATE), p.wk) END AS age_days,
                              b.km_pre + p.km_cum AS km_total, b.trips_pre
                       FROM panel p JOIN bike_info b USING (bike_id)
                       WHERE p.wk BETWEEN DATE '2026-07-06' AND DATE '2026-09-21' AND p.y_next IS NOT NULL""").df()
    con.close()
    d["wk"] = pd.to_datetime(d.wk)
    return d


def prec_at(df, col, k=1000):
    return float(np.mean([g.nlargest(k, col).hit.mean() for _, g in df.groupby("wk")]))


def main():
    sets = {"운영(2026-07 ~ 09, 최종 모형)": scores(live_panel(), "risk_spec.json")}
    va = load_panel()
    sets["참고(2025-07 ~ 12, 검증 단계 모형)"] = scores(va[va.split == "valid"].copy(), "risk_spec_valid.json")
    cal, grade = [], []
    for name, d in sets.items():
        d = d.assign(bin=pd.cut(d.prob.rank(pct=True), CUTS, labels=[f"{a:.0%} ~ {b:.0%}" for a, b in zip(CUTS[:-1], CUTS[1:])], include_lowest=True))
        c = d.groupby("bin", observed=True).agg(자전거_주=("hit", "size"), 예측_확률=("prob", "mean"), 실제_비율=("hit", "mean"))
        c["실제 ÷ 예측"] = c.실제_비율 / c.예측_확률
        cal.append(c.assign(기간=name).set_index("기간", append=True).swaplevel())
        g = d.groupby("grade").agg(자전거_주=("hit", "size"), 실제_비율=("hit", "mean"), 예측_확률=("prob", "mean"), km_4w_중앙=("km_4w", "median"))
        gp = d.groupby("grade_prob").agg(자전거_주=("hit", "size"), 실제_비율=("hit", "mean"), 예측_확률=("prob", "mean"), km_4w_중앙=("km_4w", "median"))
        g = pd.concat([g.rename(index=lambda x: f"지금(위험률): {x}"), gp.rename(index=lambda x: f"확률 기준: {x}")])
        g.loc["주당 상위 1,000대: 위험률 순", ["실제_비율"]] = prec_at(d, "rate")
        g.loc["주당 상위 1,000대: 확률 순", ["실제_비율"]] = prec_at(d, "prob")
        g.loc["전체", ["자전거_주", "실제_비율", "예측_확률"]] = [len(d), d.hit.mean(), d.prob.mean()]
        grade.append(g.assign(기간=name).set_index("기간", append=True).swaplevel())
    cal, grade = pd.concat(cal), pd.concat(grade)
    cal.round(4).to_csv(ROOT / "output/tables/rb_19_prob_calibration.csv")
    grade.round(4).to_csv(ROOT / "output/tables/rb_19_prob_by_grade.csv")
    pd.set_option("display.width", 200)
    print("예측 확률 구간(백분위)별 예측 vs 실제"); print(cal.round(3).to_string())
    print("\n지금 등급(위험률 백분위)별 실제 다음 주 신고 비율, 순위 기준별 상위 1,000대 적중"); print(grade.round(3).to_string())


if __name__ == "__main__":
    main()
