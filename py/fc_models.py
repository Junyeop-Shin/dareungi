"""단계 1C의 예측 모형들 — 모두 "원점별 앞으로 7일 합계"를 같은 형식으로 돌려준다

돌려주는 표의 형식(모든 모형 공통): origin(원점 날짜), part(부품 열 이름), model(모형 이름), pred(예측), q10·q90(80% 예측구간, 없으면 빈칸)

모형
  S1   구조 나이브      : 최근 4주 신고율 × 대여량 예측. 날씨·요일이 신고율에 주는 효과는 쓰지 않는다
  GLM  음이항 회귀      : 하루씩 '신고율'을 예측해 대여량 예측과 곱하고 7일을 더한다(1B의 설명 모형을 예측용으로 바꾼 것)
  LGB  LightGBM         : 원점마다 한 행을 만들어 7일 합계를 바로 맞힌다(트리 여러 개를 더하는 방식. 변수 간 상호작용을 스스로 찾는다)
  ARIMAX 시계열 모형    : 어제·그제 값과의 관계(자기상관)에 달력·날씨를 더한 전통적 시계열 모형

공통 규칙: 학습에는 학습 구간만 쓴다. 미래의 대여량·신고 건수는 쓰지 않는다. 날씨는 시나리오에 따른다(py/fc.py 참고).
"""
import itertools
import warnings

import lightgbm as lgb      # torch(py/fc_dl.py)보다 먼저 불러야 한다(맥에서 순서가 바뀌면 프로세스가 죽는다)
import numpy as np
import pandas as pd

from fc import PARTS, TRAIN_DAYS, INTERVENTIONS, fit_nb_fc, future_frame, sum7_fwd, sum_back

# ---------------------------------------------------------------------------
# 음이항 회귀: 신고율 × 대여량 예측
# ---------------------------------------------------------------------------

# 신고율 모형의 설명변수. lvl = 그 부품의 최근 4주 신고율(연도 수준 대신 '요즘 수준'을 알려 준다)
GLM_CAL = "np.log(lvl) + C(dow) + is_holiday + after_off + bridge + fs1 + fc1 + fs2 + fc2"
GLM_WX = " + cold_wave + winter_anom + tavg + snow_any + np.log1p(rain_mm) + np.log1p(rain_lag1)"     # 체크포인트 C의 날씨 묶음
GLM_EXTRA = " + hot + freeze_thaw"                                                                    # 넣은 것·뺀 것을 비교할 변수


def fit_glm(m, part, weather=True, extra=False):
    """부품 하나의 신고율 모형을 학습 구간의 날들로 맞춘다. 대여건수는 노출(offset).
    weather=False면 날씨 변수를 뺀다(시나리오 D). extra=True면 더위·얼었다 녹은 날을 더 넣는다."""
    tr = m[m.date.between(*TRAIN_DAYS) & (m.bad == 0)].assign(lvl=lambda t: t["lvl_" + part]).dropna(subset=["lvl"])
    rhs = GLM_CAL + (GLM_WX if weather else "") + (GLM_EXTRA if (weather and extra) else "")
    return fit_nb_fc(f"{part} ~ {rhs}", tr, offset=np.log(tr.rentals))


def predict_glm(fit, f, part, expo_hat, nsim=1000, seed=42):
    """맞춘 모형으로 '미래 7일 표'(f)의 각 날 기대 건수를 구해 원점별로 더한다.
    expo_hat: 각 행의 대여량 예측값. 예측구간은 음이항 분포에서 nsim번 뽑아 7일 합계의 10%·90% 지점으로 만든다
    (대여량 예측의 오차와 계수의 불확실성은 들어 있지 않아 실제보다 좁게 나온다)."""
    res, alpha = fit
    g = f.assign(lvl=f["lvl_" + part])
    mu = np.asarray(res.predict(g, offset=np.log(expo_hat))).reshape(-1, 7)
    size = 1.0 / alpha
    sims = np.random.default_rng(seed).negative_binomial(size, size / (size + mu), size=(nsim,) + mu.shape).sum(axis=2)
    oi = f.oi.to_numpy().reshape(-1, 7)[:, 0]
    return pd.DataFrame({"oi": oi, "pred": mu.sum(axis=1), "q10": np.quantile(sims, 0.1, axis=0), "q90": np.quantile(sims, 0.9, axis=0)})


def predict_s1(f, part, expo_hat):
    """구조 나이브: 최근 4주 신고율 × 대여량 예측의 7일 합계"""
    v = (f["lvl_" + part].to_numpy() * np.asarray(expo_hat)).reshape(-1, 7).sum(axis=1)
    return pd.DataFrame({"oi": f.oi.to_numpy().reshape(-1, 7)[:, 0], "pred": v})


# ---------------------------------------------------------------------------
# LightGBM: 원점마다 한 행, 7일 합계를 바로 맞힌다
# ---------------------------------------------------------------------------

LGB_BASE = ["lw", "l4w", "ly", "lw_expo", "rate4w", "rain_lw", "n_off", "n_hol", "doy_s", "doy_c", "expo_hat"]   # 과거 값·달력·대여량 예측
LGB_WX = ["w_tavg", "w_tmax", "w_tmin", "w_rain_days", "w_rain_sum", "w_cold_days", "w_winter_anom", "w_snow_days"]  # 목표 주의 날씨 요약
LGB_EXTRA = ["w_hot_days", "w_freeze_days"] + INTERVENTIONS                                                         # 넣은 것·뺀 것을 비교할 변수


def week_feats(m, part, f, expo_hat):
    """원점별 특성 표를 만든다.
      lw, l4w  : 지난주 합계, 최근 4주의 주 평균        ly : 작년 같은 주 합계
      lw_expo  : 지난주 대여 합계                        rate4w : 최근 4주 신고율(bad인 날 제외 없이 단순 계산)
      rain_lw  : 지난주 강수 합계                        n_off, n_hol : 목표 주의 쉬는 날·공휴일 수(미리 안다)
      doy_s/c  : 계절 위치(1년 주기 사인·코사인)         expo_hat : 목표 주 대여량 예측 합계
      w_*      : 목표 주의 날씨 요약(시나리오 값)        개입 변수 : 원점 날짜의 값
    """
    x = m[part]
    hist = pd.DataFrame({
        "oi": np.arange(len(m)), "lw": sum_back(x, 7), "l4w": sum_back(x, 28) / 4, "ly": sum7_fwd(x).shift(364),
        "lw_expo": sum_back(m.rentals, 7), "rate4w": sum_back(x, 28) / sum_back(m.rentals, 28),
        "rain_lw": sum_back(m.rain_mm, 7), "n_off": sum7_fwd(m.is_off), "n_hol": sum7_fwd(m.is_holiday),
        "doy_s": np.sin(2 * np.pi * m.doy / 365.25), "doy_c": np.cos(2 * np.pi * m.doy / 365.25)})
    for c in INTERVENTIONS:
        hist[c] = m[c].to_numpy()
    g = f.assign(expo_hat=np.asarray(expo_hat)).groupby("oi")
    wk = g.agg(w_tavg=("tavg", "mean"), w_tmax=("tmax", "max"), w_tmin=("tmin", "min"),
               w_rain_days=("rain_any", "sum"), w_rain_sum=("rain_mm", "sum"), w_cold_days=("cold_wave", "sum"),
               w_winter_anom=("winter_anom", "mean"), w_snow_days=("snow_any", "sum"),
               w_hot_days=("hot", "sum"), w_freeze_days=("freeze_thaw", "sum"), expo_hat=("expo_hat", "sum")).reset_index()
    return wk.merge(hist, on="oi")


def fit_predict_lgb(tr, va, feats, hold_from, quantiles=True, seed=42):
    """LightGBM을 학습 원점(tr)으로 맞추고 검증 원점(va)을 예측한다.
    tr, va: week_feats() 표에 y(실제 7일 합계)를 붙인 것. feats: 쓸 특성 이름 목록.

    나무 개수를 정하는 방법(검증 구간을 보지 않는다):
      1) 학습 원점을 둘로 나눈다. hold_from(행 번호)보다 앞은 맞추는 데, 뒤는 '언제 멈출지' 정하는 데 쓴다.
      2) 뒤쪽의 오차가 100번 연속 나아지지 않으면 멈추고, 그때의 나무 개수를 기록한다.
      3) 그 개수로 학습 원점 전체를 다시 맞춘다.
    이렇게 해야 검증 점수가 다른 모형(음이항 등)과 같은 조건이 된다.
    돌려주는 것: (예측 표, 특성 중요도, 나무 개수). 80% 구간은 10%·90% 분위수를 직접 맞히는 모형 두 개로 만든다."""
    params = dict(objective="poisson", learning_rate=0.03, num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, seed=seed, verbose=-1)
    a, h = tr[tr.oi < hold_from], tr[tr.oi >= hold_from]
    probe = lgb.train(params, lgb.Dataset(a[feats], a.y), num_boost_round=3000,
                      valid_sets=[lgb.Dataset(h[feats], h.y)], callbacks=[lgb.early_stopping(100, verbose=False)])
    rounds = max(int(probe.best_iteration * len(tr) / len(a)), 50)      # 자료가 늘어난 만큼 나무도 조금 더
    model = lgb.train(params, lgb.Dataset(tr[feats], tr.y), num_boost_round=rounds)
    out = pd.DataFrame({"oi": va.oi.to_numpy(), "pred": model.predict(va[feats])})
    if quantiles:
        for q, name in ((0.1, "q10"), (0.9, "q90")):
            qm = lgb.train({**params, "objective": "quantile", "alpha": q}, lgb.Dataset(tr[feats], tr.y), num_boost_round=rounds)
            out[name] = qm.predict(va[feats])
    imp = pd.Series(model.feature_importance("gain"), index=feats).sort_values(ascending=False)
    return out, imp / imp.sum(), rounds


# ---------------------------------------------------------------------------
# ARIMAX: 전통적 시계열 모형(날씨는 실측값 = 시나리오 A로만)
# ---------------------------------------------------------------------------

def _arima_exog(m):
    dow = pd.get_dummies(pd.Categorical(m.dow, categories=range(1, 8)), prefix="dow", drop_first=True).set_axis(m.index)
    return pd.concat([dow, m[["is_holiday", "after_off", "fs1", "fc1", "fs2", "fc2", "tavg", "cold_wave", "rain_any", "snow_any"]]], axis=1).astype(float)


def arimax_forecast(m, part, origins_i):
    """부품 하나에 ARIMAX를 맞추고 원점별 7일 합계를 예측한다.
    - 차수 (p, d, q)는 작은 격자(p·q는 0~2, d는 0~1)에서 학습 구간 AIC가 가장 낮은 것으로 고른다.
    - 계수는 학습 구간에서 한 번만 추정하고, 원점마다 '그 전날까지의 실제 값'으로 상태만 갱신해 7일을 내다본다.
    - bad인 날(운영 중단, 신고 누락)은 빈칸으로 둔다(이 모형은 빈칸을 건너뛸 수 있다).
    - 미래 7일의 날씨는 실측값을 쓴다(시나리오 A). ARIMAX에 가장 유리한 조건이다.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    s0 = int(np.where(m.date >= TRAIN_DAYS[0])[0][0]); s1 = int(np.where(m.date <= TRAIN_DAYS[1])[0][-1]) + 1
    end = int(max(origins_i)) + 7
    y = m[part].astype(float).where(m.bad == 0).to_numpy()
    X = _arima_exog(m).to_numpy()
    best = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for order in itertools.product([0, 1, 2], [0, 1], [0, 1, 2]):
            try:
                r = SARIMAX(y[s0:s1], exog=X[s0:s1], order=order).fit(disp=False, maxiter=200)
                if best is None or r.aic < best.aic:
                    best = r
            except Exception:
                pass
        allr = best.apply(y[s0:end], exog=X[s0:end])                    # 같은 계수로 검증 구간 끝까지 상태만 갱신
        preds = [float(np.clip(allr.predict(start=i - s0, end=i - s0 + 6, dynamic=True), 0, None).sum()) for i in origins_i]
    return pd.DataFrame({"oi": np.asarray(origins_i), "pred": preds}), best.specification["order"]
