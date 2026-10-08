"""신고 건수 음이항 회귀 도우미(노출은 offset). 1B·1C에서 쓴다.

모형: log(기대 신고 건수) = log(노출) + b0 + b1·x1 + ...
log(노출)은 계수 1로 고정(offset)하므로 exp(b)는 신고율(신고/노출)의 배수, 즉 IRR로 읽는다.
예를 들어 월요일 IRR 1.10은 다른 조건이 같을 때 기준 요일보다 신고율이 1.10배라는 뜻이다.

신고 건수는 평균보다 훨씬 크게 흩어져서(과산포) 포아송 대신 음이항(분산 = 평균 + alpha·평균²)을 쓴다.
일별 오차는 자기상관이 있어 그냥 두면 표준오차가 작게 나오므로 HAC(기본 14일)로 보정한다.

사용: from nbreg import fit_nb, irr_table, predict_count
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.optimize import minimize_scalar


def fit_nb(formula, data, expo, maxlags=14):
    """음이항 회귀를 맞춘다.

    formula : 예) "total_faults ~ C(dow) + cold_wave"
    data    : 식에 나오는 열과 노출 열에 결측이 없는 표
    expo    : 노출 열 이름(예: "rentals")
    maxlags : HAC 표준오차의 시차(일)

    alpha는 로그우도를 최대로 하는 값을 1차원 탐색으로 찾고, 그 alpha로 다시 맞추며 HAC 표준오차를 구한다.

    반환: res(적합 결과), alpha, aic
    """
    off = np.log(data[expo])

    def negll(log_alpha):
        fam = sm.families.NegativeBinomial(alpha=float(np.exp(log_alpha)))
        return -smf.glm(formula, data=data, family=fam, offset=off).fit().llf

    alpha = float(np.exp(minimize_scalar(negll, bounds=(-9, 2), method="bounded", options={"xatol": 1e-3}).x))
    res = smf.glm(formula, data=data, family=sm.families.NegativeBinomial(alpha=alpha), offset=off) \
             .fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    aic = -2 * res.llf + 2 * (len(res.params) + 1)          # +1은 alpha
    return res, alpha, aic


def irr_table(res):
    """계수를 IRR로 바꾼 표. lo·hi는 95% 신뢰구간(HAC), p는 p값"""
    ci = np.exp(res.conf_int())
    return pd.DataFrame({"irr": np.exp(res.params), "lo": ci[0], "hi": ci[1], "p": res.pvalues})


def predict_count(res, data, expo):
    """data 각 날의 기대 신고 건수. 그날 노출을 아는 것으로 보고 offset에 넣는다"""
    return res.predict(data, offset=np.log(data[expo]))
