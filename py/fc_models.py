"""1C 예측 모형. 모두 원점별 앞으로 7일 합계를 같은 형식으로 돌려준다.

출력 형식: origin, part, model, pred, q10·q90(80% 예측구간, 없으면 빈칸)

  S1     구조 나이브  최근 4주 신고율 × 대여량 예측. 날씨·요일의 신고율 효과는 안 쓴다
  GLM    음이항 회귀  날마다 신고율을 예측해 대여량 예측과 곱하고 7일을 더한다(1B 설명 모형의 예측판)
  LGB    LightGBM     원점마다 한 행으로 7일 합계를 바로 맞힌다
  ARIMAX 시계열       자기상관에 달력·날씨를 외생변수로 더한 모형

학습은 학습 구간만 쓰고, 미래 대여량·신고 건수는 쓰지 않는다. 날씨 시나리오는 py/fc.py 참고.
"""
import itertools
import warnings

import lightgbm as lgb      # torch(fc_dl)보다 먼저 import해야 한다. 맥에서 순서가 바뀌면 프로세스가 죽는다
import numpy as np
import pandas as pd

from fc import TRAIN_DAYS, INTERVENTIONS, fit_nb_fc, sum7_fwd, sum_back

# ---------------------------------------------------------------------------
# 음이항 회귀: 신고율 × 대여량 예측
# ---------------------------------------------------------------------------

# 신고율 모형의 설명변수. lvl은 그 부품의 최근 4주 신고율로, 연도 더미 대신 현재 수준을 준다
GLM_CAL = "np.log(lvl) + C(dow) + is_holiday + after_off + bridge + fs1 + fc1 + fs2 + fc2"
GLM_WX = " + cold_wave + winter_anom + tavg + snow_any + np.log1p(rain_mm) + np.log1p(rain_lag1)"     # 1B에서 고른 날씨 변수
GLM_EXTRA = " + hot + freeze_thaw"                                                                    # 넣고 빼며 비교할 변수


def fit_glm(m, part, weather=True, extra=False, end=TRAIN_DAYS[1]):
    """부품 하나의 신고율 모형을 학습 구간으로 맞춘다. 대여건수가 offset.

    weather=False면 날씨 변수를 뺀다(시나리오 D). extra=True면 hot, freeze_thaw를 더한다.
    end: 학습 마지막 날. 테스트 때는 2024-12-31까지 넣어 다시 맞춘다.
    """
    tr = m[m.date.between(TRAIN_DAYS[0], end) & (m.bad == 0)].assign(lvl=lambda t: t["lvl_" + part]).dropna(subset=["lvl"])
    rhs = GLM_CAL + (GLM_WX if weather else "") + (GLM_EXTRA if (weather and extra) else "")
    return fit_nb_fc(f"{part} ~ {rhs}", tr, offset=np.log(tr.rentals))


def fit_glm_rows(rows, part, weather=True):
    """신고율 모형을 미래 7일 표 형식의 학습 행(원점 × lead, 날씨는 예보)으로 맞춘다.

    실측으로 학습한 fit_glm과 달리, 예보로 학습하면 예보를 적당히 덜 믿는 계수가 나온다(대여량 모형에서 확인).
    rows: fc.future_frame(m, 학습 원점, "B") 결과. 신고 건수와 대여건수(offset)는 실제 값
    """
    tr = rows[rows.bad == 0].assign(lvl=lambda t: t["lvl_" + part]).dropna(subset=["lvl"])
    rhs = GLM_CAL + (GLM_WX if weather else "")
    return fit_nb_fc(f"{part} ~ {rhs}", tr, offset=np.log(tr.rentals))


def predict_glm(fit, f, part, expo_hat, nsim=1000, seed=42):
    """미래 7일 표(f)의 날별 기대 건수를 원점별로 더한다.

    expo_hat: 행별 대여량 예측. 예측구간은 음이항 분포에서 nsim번 뽑은 7일 합계의 10%·90% 분위수다.
    대여량 예측 오차와 계수 불확실성은 빠져 있어서 실제보다 좁다.
    """
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
LGB_EXTRA = ["w_hot_days", "w_freeze_days"] + INTERVENTIONS                                                         # 넣고 빼며 비교할 변수


def week_feats(m, part, f, expo_hat):
    """원점별 특성 표를 만든다.

      lw, l4w  지난주 합계, 최근 4주 주평균      ly       작년 같은 주 합계
      lw_expo  지난주 대여 합계                  rate4w   최근 4주 신고율(bad인 날도 포함)
      rain_lw  지난주 강수 합계                  n_off, n_hol  목표 주의 쉬는 날·공휴일 수
      doy_s/c  1년 주기 사인·코사인              expo_hat 목표 주 대여량 예측 합계
      w_*      목표 주 날씨 요약(시나리오 값)    개입 변수는 원점 날짜의 값
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
    """학습 원점(tr)으로 LightGBM을 맞추고 va를 예측한다.

    tr, va: week_feats() 표에 y(실제 7일 합계)를 붙인 것. feats: 쓸 특성 목록.

    트리 개수는 검증 구간을 보지 않고 정한다. 학습 원점을 hold_from(행 번호)에서 나눠 앞으로 맞추고
    뒤쪽 오차로 early stopping(100회)한 뒤, 그 개수로 학습 원점 전체를 다시 맞춘다.
    음이항 등 다른 모형과 같은 조건에서 검증 점수를 비교하려는 것이다.
    80% 구간은 10%·90% 분위수 모형 두 개로 만든다.
    반환: (예측 표, 특성 중요도, 트리 개수)
    """
    params = dict(objective="poisson", learning_rate=0.03, num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, seed=seed, verbose=-1)
    a, h = tr[tr.oi < hold_from], tr[tr.oi >= hold_from]
    probe = lgb.train(params, lgb.Dataset(a[feats], a.y), num_boost_round=3000,
                      valid_sets=[lgb.Dataset(h[feats], h.y)], callbacks=[lgb.early_stopping(100, verbose=False)])
    rounds = max(int(probe.best_iteration * len(tr) / len(a)), 50)      # 늘어난 학습 행 비율만큼 트리도 늘린다
    model = lgb.train(params, lgb.Dataset(tr[feats], tr.y), num_boost_round=rounds)
    out = pd.DataFrame({"oi": va.oi.to_numpy(), "pred": model.predict(va[feats])})
    if quantiles:
        for q, name in ((0.1, "q10"), (0.9, "q90")):
            qm = lgb.train({**params, "objective": "quantile", "alpha": q}, lgb.Dataset(tr[feats], tr.y), num_boost_round=rounds)
            out[name] = qm.predict(va[feats])
    imp = pd.Series(model.feature_importance("gain"), index=feats).sort_values(ascending=False)
    return out, imp / imp.sum(), rounds


# ---------------------------------------------------------------------------
# ARIMAX: 날씨는 실측값(시나리오 A)으로만
# ---------------------------------------------------------------------------

def _arima_exog(m):
    dow = pd.get_dummies(pd.Categorical(m.dow, categories=range(1, 8)), prefix="dow", drop_first=True).set_axis(m.index)
    return pd.concat([dow, m[["is_holiday", "after_off", "fs1", "fc1", "fs2", "fc2", "tavg", "cold_wave", "rain_any", "snow_any"]]], axis=1).astype(float)


def arimax_forecast(m, part, origins_i):
    """부품 하나에 ARIMAX를 맞추고 원점별 7일 합계를 예측한다.

    - 차수 (p, d, q)는 p·q 0~2, d 0~1 격자에서 학습 구간 AIC가 가장 낮은 것
    - 계수는 학습 구간에서 한 번만 추정하고, 원점마다 전날까지의 실제 값으로 상태만 갱신해 7일을 예측
    - bad인 날은 결측으로 둔다(상태공간 모형이라 건너뛴다)
    - 미래 날씨는 실측값(시나리오 A)이라 ARIMAX에 가장 유리한 조건이다
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
        allr = best.apply(y[s0:end], exog=X[s0:end])                    # 같은 계수로 마지막 원점까지 상태만 갱신
        preds = [float(np.clip(allr.predict(start=i - s0, end=i - s0 + 6, dynamic=True), 0, None).sum()) for i in origins_i]
    return pd.DataFrame({"oi": np.asarray(origins_i), "pred": preds}), best.specification["order"]
