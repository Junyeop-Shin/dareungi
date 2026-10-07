"""해석 모형 — 단계 2B-4 (런북 06): "무엇이 위험을 높이나"를 계수로 말한다

[무엇을]
예측이 아니라 해석용. 자전거 5,000대 표본(학습 + 검증 기간, 다음 주 주행이 있는 행)으로
  (a) 음이항 회귀 + 주행거리 offset + 자전거 단위 군집 표준오차 → IRR(1 표준편차 또는 단위당 신고율 배수). 전체 + 부품별
  (b) Cox 비례위험 모형, 시간축 = 마지막 신고 이후 주행거리(start = km_since_rep, stop = start + km_next), 사건 = 다음 주 신고,
      차종으로 층화, 자전거 단위 군집 보정 → 위험비(HR). "같은 거리를 달렸을 때 무엇이 더 위험한가"
가설(런북 06): H1 누적 거리 +, H2 오르막 + (체인·페달), H3 비·젖은 노면 + (체인), H4 출퇴근/여가 부품별 부호 차이,
              H5 재배치 + (안장·페달), H6 수리된 뒤 −, H7 나이 +. 결과가 반대여도 그대로 적는다.
[주의] sh_* 는 w주(이번 주) 값이다. 엄밀히는 다음 주 주행 특성이 맞지만, 패널에 없어 이번 주 값을 대리로 쓴다(한계).
       나이(age_days)는 나이를 아는 자전거(2022-03 이후 번호)만 있다 → 나이 항은 그 부분집합으로 따로 맞춘다.
[입력]  data/processed/panel/bike_week.parquet (테스트 행 제외)
[출력]  output/tables/risk_irr_nb.csv (음이항 IRR, 전체·부품별), risk_hr_cox.csv (Cox HR), risk_hypotheses.csv (가설 표)
[실행]  .venv/bin/python py/risk_interpret.py   (약 3 ~ 5분)
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.optimize import minimize_scalar
from lifelines import CoxPHFitter

sys.path.append(str(Path(__file__).resolve().parent))
from risk_eval import load_panel   # noqa: E402
from risk_lgb import prep          # noqa: E402

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
N_BIKES = 5000
Z = ["climb_per_km", "descent_per_km", "reloc_per_trip"]                       # 표준화해 1 표준편차당 효과로 읽는 변수
RHS = ("np.log1p(km_since_rep) + z_climb_per_km + z_descent_per_km + sh_rain + sh_wet + sh_commute + sh_leisure"
       " + z_reloc_per_trip + last_repaired + C(month)")
TARGETS = {"all": "y_next", "tire": "y_tire_next", "chain": "y_chain_next", "saddle": "y_saddle_next", "pedal": "y_pedal_next"}
LABELS = {"np.log1p(km_since_rep)": "H1 마지막 신고 이후 km(log1p, 1단위당)", "z_climb_per_km": "H2 km당 오르막(1 SD당)",
          "z_descent_per_km": "km당 내리막(1 SD당)", "sh_rain": "H3 비 오는 중 주행 비중(0 → 1)", "sh_wet": "H3 젖은 노면 주행 비중(0 → 1)",
          "sh_commute": "H4 출퇴근 비중(0 → 1)", "sh_leisure": "H4 여가 비중(0 → 1)", "z_reloc_per_trip": "H5 대여당 재배치(1 SD당)",
          "last_repaired": "H6 직전 신고가 수리됨(48h+ 공백)", "z_age_days": "H7 나이(1 SD당, 나이 아는 자전거만)"}


def sample(p):
    ids = p.bike_id.drop_duplicates().sample(N_BIKES, random_state=42)
    ps = p[p.bike_id.isin(ids) & (p.km_next > 0)].copy()
    ps = ps.dropna(subset=["climb_per_km", "descent_per_km", "reloc_per_trip", "sh_rain", "sh_wet", "sh_commute", "sh_leisure"])
    ps["last_repaired"] = ps.last_repaired.fillna(0)
    for c in Z + ["age_days"]:
        ps[f"z_{c}"] = (ps[c] - ps[c].mean()) / ps[c].std()
    return ps


def nb_irr(ps, target, rhs):
    f = f"{target} ~ {rhs}"
    off = np.log(ps.km_next)
    # alpha(과산포)는 GLM 우도를 직접 최대화해 찾는다(discrete NegativeBinomial 적합은 큰 표에서 발산하는 일이 있다 — py/nbreg.py와 같은 방식)
    def negll(log_alpha):
        return -smf.glm(f, data=ps, family=sm.families.NegativeBinomial(alpha=float(np.exp(log_alpha))), offset=off).fit().llf
    alpha = float(np.exp(minimize_scalar(negll, bounds=(-6, 3), method="bounded", options={"xatol": 1e-2}).x))
    res = smf.glm(f, data=ps, family=sm.families.NegativeBinomial(alpha=alpha), offset=off).fit(cov_type="cluster", cov_kwds={"groups": ps.bike_id})
    ci = res.conf_int()
    return pd.DataFrame({"term": res.params.index, "irr": np.exp(res.params.values), "lo": np.exp(ci[0].values), "hi": np.exp(ci[1].values), "p": res.pvalues.values})


def main():
    p = prep(load_panel())
    ps = sample(p)
    print("표본 행", len(ps), "자전거", ps.bike_id.nunique(), flush=True)
    rows = []
    for name, target in TARGETS.items():
        r = nb_irr(ps, target, RHS).assign(model=name, subset="전체 표본")
        rows.append(r[~r.term.str.startswith("C(month)") & (r.term != "Intercept")])
        # 나이 항: 나이를 아는 자전거만
        pa = ps[ps.age_known == 1]
        ra = nb_irr(pa, target, RHS + " + z_age_days").assign(model=name, subset="나이 아는 자전거")
        rows.append(ra[ra.term == "z_age_days"])
        print(name, "완료", flush=True)
    irr = pd.concat(rows, ignore_index=True)
    irr["뜻"] = irr.term.map(LABELS)
    irr.round(4).to_csv(ROOT / "output/tables/risk_irr_nb.csv", index=False)

    # Cox: 시간축 = 마지막 신고 이후 km
    cp = ps.assign(start=ps.km_since_rep, stop=ps.km_since_rep + ps.km_next, event=(ps.y_next > 0).astype(int))
    cp = cp[cp.stop > cp.start]
    use = ["bike_id", "bike_type", "start", "stop", "event", "z_climb_per_km", "z_descent_per_km", "sh_rain", "sh_wet", "sh_commute", "sh_leisure", "z_reloc_per_trip", "last_repaired"]
    cph = CoxPHFitter(penalizer=0.01)
    cph.fit(cp[use].dropna(), duration_col="stop", event_col="event", entry_col="start", strata=["bike_type"], cluster_col="bike_id")
    hr = cph.summary[["exp(coef)", "exp(coef) lower 95%", "exp(coef) upper 95%", "p"]].reset_index().rename(
        columns={"covariate": "term", "exp(coef)": "hr", "exp(coef) lower 95%": "lo", "exp(coef) upper 95%": "hi"})
    hr["뜻"] = hr.term.map(LABELS)
    hr.round(4).to_csv(ROOT / "output/tables/risk_hr_cox.csv", index=False)

    pd.set_option("display.width", 220)
    piv = irr.pivot_table(index="뜻", columns="model", values="irr")[list(TARGETS)]
    print(piv.round(3).to_string())
    print(hr.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
