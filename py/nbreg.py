"""음이항 회귀(신고 건수 ~ 설명변수, 노출은 offset) 도우미 — 단계 1B·1C에서 함께 쓴다

[이 모형이 하는 일]
맞히려는 값은 하루 신고 '건수'다. 건수는 이용량(노출)에 비례해 늘어나므로
    log(기대 신고 건수) = log(노출) + b0 + b1·x1 + b2·x2 + …
로 둔다. log(노출)의 계수를 1로 고정해 넣는 것을 offset이라고 한다.
그러면 exp(b1)은 "x1이 1 늘 때 신고율(신고 ÷ 노출)이 몇 배가 되는가"로 읽힌다. 이 값을 발생률비(IRR)라고 부른다.
  예) 월요일의 IRR 1.10 = 다른 조건이 같을 때 월요일 신고율이 기준 요일의 1.10배

[왜 포아송이 아니라 음이항인가]
포아송 모형은 "분산 = 평균"을 가정한다. 실제 신고 건수는 평균보다 훨씬 크게 흩어진다(과산포).
음이항 모형은 분산 = 평균 + alpha·평균² 으로 두어 흩어짐을 alpha 하나로 받아 준다(alpha = 0이면 포아송).

[왜 HAC 표준오차인가]
일별 자료는 오늘의 오차가 어제의 오차와 닮는다(자기상관). 이를 무시하면 계수의 불확실성이 실제보다 작게 나와
효과가 없는 변수도 '유의'하게 보인다. HAC는 이 자기상관을 감안해 표준오차를 넓혀 준다(여기서는 앞뒤 14일까지).

노트북에서:  from nbreg import fit_nb, irr_table, predict_count
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.optimize import minimize_scalar


def fit_nb(formula, data, expo, maxlags=14):
    """음이항 회귀를 맞춘다.

    formula : "신고건수열 ~ 설명변수들" 형태의 식 (예: "total_faults ~ C(dow) + cold_wave")
    data    : 식에 나오는 열과 노출 열에 빈칸이 없는 표
    expo    : 노출로 쓸 열 이름 (예: "rentals")
    maxlags : HAC 표준오차가 감안할 자기상관의 길이(일)

    방법: alpha(흩어짐의 크기)를 여러 값으로 바꿔 가며 모형을 맞춰 보고, 자료를 가장 잘 설명하는(로그우도가 가장 큰) alpha를 고른다.
          그 alpha로 한 번 더 맞추면서 HAC 표준오차를 구한다.

    돌려주는 것: res(적합 결과. res.params가 계수), alpha, aic(작을수록 좋은 모형. 변수 개수가 많으면 벌점이 붙는다)
    """
    off = np.log(data[expo])

    def negll(log_alpha):
        fam = sm.families.NegativeBinomial(alpha=float(np.exp(log_alpha)))
        return -smf.glm(formula, data=data, family=fam, offset=off).fit().llf

    alpha = float(np.exp(minimize_scalar(negll, bounds=(-9, 2), method="bounded", options={"xatol": 1e-3}).x))
    res = smf.glm(formula, data=data, family=sm.families.NegativeBinomial(alpha=alpha), offset=off) \
             .fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    aic = -2 * res.llf + 2 * (len(res.params) + 1)          # +1은 alpha도 추정한 값이기 때문
    return res, alpha, aic


def irr_table(res):
    """계수를 '신고율이 몇 배가 되는가'(IRR)로 바꾼 표. lo·hi는 95% 신뢰구간(HAC), p는 '효과가 없다'는 가정에서의 p값"""
    ci = np.exp(res.conf_int())
    return pd.DataFrame({"irr": np.exp(res.params), "lo": ci[0], "hi": ci[1], "p": res.pvalues})


def predict_count(res, data, expo):
    """적합한 모형으로 data의 각 날 기대 신고 건수를 계산한다(그날의 노출을 안다고 보고 곱한다)"""
    return res.predict(data, offset=np.log(data[expo]))
