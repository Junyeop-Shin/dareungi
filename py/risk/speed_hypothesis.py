"""느리게 달린 자전거가 대여 한 번당으로도 더 자주 신고되는 이유 가설 점검(검증 구간 2025-07 ~ 12).

가설 A(고장): 바퀴 바람이 빠졌거나 체인이 뻑뻑해 실제로 느리게 달린다.
가설 B(이용자): 쉬어 가는 여가 이용자가 시간이 있어 신고를 더 한다.
가설 C(다른 요인): 자전거 나이·차종·계절·지난 신고 이력이 속도와 같이 움직인다.

  T1 부품별: 속도 10분위별 대여 1,000건당 부품 신고. A라면 타이어·체인에서 느린 쪽이 특히 높고 안장·페달은 덜해야 한다
  T2 조건 맞추기: 포아송 회귀(다음 주 신고 수, offset = log(다음 주 대여 수))에 여가·왕복·출퇴근 비중, 차종, 나이 알려짐,
     달, 지난 신고 뒤 기간, 신고 기록 없음, km당 오르막·내리막, 출발 대여소 군집 비중을 넣고 속도 5분위 효과를 본다. 남으면 B·C만으로는 설명이 안 된다.
     표준오차는 자전거 단위로 묶는다(같은 자전거의 주들이 닮아서)
  T3 같은 자전거끼리: 그 자전거의 직전 4주 평균 속도보다 이번 주가 얼마나 느린가. 자전거·대여소·주 이용자층이 크게
     바뀌지 않으니, 평소보다 갑자기 느려졌을 때 신고가 늘면 A를 지지한다. 여가 비중 변화도 같이 본다

검증 마지막 주는 다음 주가 테스트 구간이라 뺀다. 결과는 가설의 지지 여부일 뿐 원인을 확정하지 않는다.

입력: data/processed/panel/bike_week.parquet
출력: output/tables/rb_14_speed_parts.csv, rb_15_speed_glm.csv, rb_16_speed_within.csv, rb_16b_speed_within_glm.csv
실행: .venv/bin/python py/risk/speed_hypothesis.py  (2 ~ 4분)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from risk_eval import load_panel   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PARTS = {"y_next": "전체", "y_tire_next": "타이어", "y_chain_next": "체인", "y_saddle_next": "안장", "y_pedal_next": "페달"}


def prepare():
    p = load_panel().sort_values(["bike_id", "wk"])
    g = p.groupby("bike_id")
    p["n_trips_next"] = g.n_trips.shift(-1)                  # 패널은 빈 주까지 채운 격자라 다음 행 = 다음 주
    sp = p.speed_med.where(p.n_trips >= 3)                    # 대여가 너무 적은 주의 속도는 흔들려서 기준선에서 뺀다
    p["speed_base"] = sp.groupby(p.bike_id).transform(lambda s: s.shift(1).rolling(4, min_periods=2).mean())
    p["leis_base"] = p.sh_leisure.where(p.n_trips >= 3).groupby(p.bike_id).transform(lambda s: s.shift(1).rolling(4, min_periods=2).mean())
    v = p[(p.split == "valid") & (p.wk < p[p.split == "valid"].wk.max()) & (p.n_trips > 0) & (p.n_trips_next > 0) & p.speed_med.notna()].copy()
    v["month"] = v.wk.dt.month
    v["log_days"] = np.log1p(v.days_since_rep.fillna(9999).clip(upper=400))
    v["left_censored"] = v.left_censored.astype(int)
    v["age_known"] = v.age_known.astype(int)
    return v


def t1_parts(v):
    v = v.assign(dec=pd.qcut(v.speed_med, 10, labels=False) + 1)
    t = v.groupby("dec").apply(lambda g: pd.Series({c: g[c].sum() / g.n_trips_next.sum() * 1000 for c in PARTS}), include_groups=False).rename(columns=PARTS)
    t.loc["가장 느린 10% ÷ 가장 빠른 10%"] = t.loc[1] / t.loc[10]
    t.loc["가장 느린 10% ÷ 5 ~ 6분위"] = t.loc[1] / t.loc[[5, 6]].mean()
    t.round(4).to_csv(ROOT / "output/tables/rb_14_speed_parts.csv")
    return t


def t2_glm(v):
    v = v.assign(sq=(pd.qcut(v.speed_med, 5, labels=["1 가장 느림", "2", "3", "4", "5 가장 빠름"])).astype(str))
    v = v.assign(climb_per_km=v.climb_m / v.km.where(v.km > 0), descent_per_km=v.descent_m / v.km.where(v.km > 0))
    v = v.dropna(subset=["sh_leisure", "sh_round", "sh_commute", "bike_type", "climb_per_km", "descent_per_km", "sh_cl0"])   # 식에서 빠지는 행과 묶음 단위를 맞춘다
    v = v.assign(bike_type=v.bike_type.astype(str))
    rhs = ("C(sq, Treatment('3')) + sh_leisure + sh_round + sh_commute + C(bike_type) + age_known + C(month) + log_days + left_censored"
           " + climb_per_km + descent_per_km + sh_cl0 + sh_cl1 + sh_cl2 + sh_cl3")            # 언덕·출발 동네도 속도를 바꾸니 같이 맞춘다
    rows = []
    for col, name in PARTS.items():
        res = smf.glm(f"{col} ~ {rhs}", data=v, family=sm.families.Poisson(), offset=np.log(v.n_trips_next)) \
                 .fit(cov_type="cluster", cov_kwds={"groups": v.bike_id.to_numpy()})
        for lv in ("1 가장 느림", "2", "4", "5 가장 빠름"):
            k = f"C(sq, Treatment('3'))[T.{lv}]"
            lo, hi = res.conf_int().loc[k]
            rows.append({"부품": name, "속도 5분위(3분위 대비)": lv, "배수": np.exp(res.params[k]), "95% 하한": np.exp(lo), "95% 상한": np.exp(hi)})
        rows.append({"부품": name, "속도 5분위(3분위 대비)": "여가 비중 0 → 1", "배수": np.exp(res.params["sh_leisure"]),
                     "95% 하한": np.exp(res.conf_int().loc["sh_leisure", 0]), "95% 상한": np.exp(res.conf_int().loc["sh_leisure", 1])})
    t = pd.DataFrame(rows).set_index(["부품", "속도 5분위(3분위 대비)"])
    t.round(4).to_csv(ROOT / "output/tables/rb_15_speed_glm.csv")
    return t


def t3_within(v):
    w = v[v.speed_base.notna() & (v.n_trips >= 3)].copy()
    w["chg"] = w.speed_med / w.speed_base - 1
    w["구간"] = pd.cut(w.chg, [-np.inf, -0.2, -0.1, -0.03, 0.03, 0.1, np.inf],
                      labels=["20% 넘게 느려짐", "10 ~ 20% 느려짐", "3 ~ 10% 느려짐", "평소와 비슷(±3%)", "3 ~ 10% 빨라짐", "10% 넘게 빨라짐"])
    t = w.groupby("구간", observed=True).apply(lambda g: pd.Series({
        "자전거_주": len(g), "여가 비중 변화": (g.sh_leisure - g.leis_base).mean(),
        **{f"{n} 대여 1,000건당": g[c].sum() / g.n_trips_next.sum() * 1000 for c, n in PARTS.items()}}), include_groups=False)
    t.round(4).to_csv(ROOT / "output/tables/rb_16_speed_within.csv")
    return t


def t3_glm(v):
    """T3를 회귀로: 평소 대비 속도 변화 구간(기준 = 평소와 비슷) + 여가 비중 변화·여가 비중·달·지난 신고 뒤 기간 등, 대여당 포아송"""
    w = v[v.speed_base.notna() & v.leis_base.notna() & (v.n_trips >= 3)].dropna(subset=["sh_leisure", "sh_round", "bike_type"]).copy()
    w["chg"] = pd.cut(w.speed_med / w.speed_base - 1, [-np.inf, -0.2, -0.1, -0.03, 0.03, 0.1, np.inf],
                      labels=["a20% 넘게 느려짐", "b10 ~ 20% 느려짐", "c3 ~ 10% 느려짐", "d평소와 비슷", "e3 ~ 10% 빨라짐", "f10% 넘게 빨라짐"]).astype(str)
    w["leis_chg"] = w.sh_leisure - w.leis_base
    w["bike_type"] = w.bike_type.astype(str)
    rhs = "C(chg, Treatment('d평소와 비슷')) + leis_chg + sh_leisure + sh_round + C(bike_type) + C(month) + log_days + left_censored"
    rows = []
    for col, name in PARTS.items():
        res = smf.glm(f"{col} ~ {rhs}", data=w, family=sm.families.Poisson(), offset=np.log(w.n_trips_next)) \
                 .fit(cov_type="cluster", cov_kwds={"groups": w.bike_id.to_numpy()})
        ci = res.conf_int()
        for lv in ("a20% 넘게 느려짐", "b10 ~ 20% 느려짐", "c3 ~ 10% 느려짐", "e3 ~ 10% 빨라짐", "f10% 넘게 빨라짐"):
            k = f"C(chg, Treatment('d평소와 비슷'))[T.{lv}]"
            rows.append({"부품": name, "평소 대비": lv[1:], "배수": np.exp(res.params[k]), "95% 하한": np.exp(ci.loc[k, 0]),
                         "95% 상한": np.exp(ci.loc[k, 1]), "p": res.pvalues[k]})
    t = pd.DataFrame(rows).set_index(["부품", "평소 대비"])
    t.round(4).to_csv(ROOT / "output/tables/rb_16b_speed_within_glm.csv")
    return t


def main():
    v = prepare()
    pd.set_option("display.width", 220)
    print(f"검증 자전거-주 {len(v):,}개")
    print("\nT1 속도 10분위별 대여 1,000건당 신고"); print(t1_parts(v).round(3).to_string())
    print("\nT3 같은 자전거, 직전 4주 평균 속도 대비"); print(t3_within(v).round(3).to_string())
    print("\nT2 조건을 맞춘 포아송 회귀(대여당), 속도 5분위 배수"); print(t2_glm(v).round(3).to_string())
    print("\nT3 회귀(같은 자전거 평소 대비, 대여당)"); print(t3_glm(v).round(4).to_string())


if __name__ == "__main__":
    main()
